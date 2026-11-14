# ACUTE — LLM Handoff File

> **Purpose:** paste this file into a new LLM session and continue working with zero context loss.
> It records: project identity, user protocol, environment, architecture, verified state,
> roadmap position, deferred decisions, and repo state.
>
> **Last updated:** 2026-11-08 — Phase 2 COMPLETE (repair loop + executor + recorder +
> API backoff; all committed & pushed on `phase1-safeguards`: `31e7148`, `c217b01`,
> `2a15893`, `071b367`, `9895302`, `df8d520`, `520adb1`, `b045721`). Live E2Es ×2 green
> (one-shot fixes; retry branch stub-proven). Docs refreshed. PR → main pending user.
> Update this file at every milestone.

---

## 1. What this project is

**Acute** — a CLI agent that fixes bugs autonomously. User runs `acute index` once (builds a
local vector index over a git-tracked codebase), then `acute fix "<bug description>"`
which retrieves the most relevant code chunks, sends them + the bug to Gemini 2.5 Flash,
receives a strict-JSON `MultiFilePatch`, and applies edits by AST-guided byte-splicing.
It is the user's resume project; depth of understanding is its core deliverable.

## 2. THE USER — protocol for working with them (read this first)

- The user built Acute with Gemini's help, forgets details after breaks, and **must
  genuinely understand every decision** — it's a resume project to be defended in interviews.
- **Explain before building.** For every phase: present the problem, concept, and options
  → user approves → then code. Use PLAIN language; the user has said clearly that jargon-heavy
  explanations fail ("you are really bad at explaining" after a jargon-dense decision sheet).
  Explain with concrete scenarios, pictures of file states, before/after examples.
- **Questions must be simple.** Multiple-choice decision questions confused the user once
  (all five answered "I don't understand"); re-explain decisions with real examples instead.
- The user prefers: decisions made together, planning done fast, code done fast.
  They said: "planning I can't spend so much time on anymore; still want to understand and
  make every major decision / understand the reason behind every decision."
- Small typo-level fixes: just do them without ceremony. Medium+ work: explain first.
- Keep the user's own understanding alive: after significant work, offer short teach-backs.
- Commits: small, genuine, to-the-point messages; PR-based workflow; they ask about
  branching — recommend branches, they agreed once ("new branch right?").
- Two living docs must be updated as the project progresses:
  `docs/HANDOFF.md` (this file) and `docs/PROJECT_SUMMARY.md` (user-facing narrative).

## 3. Environment

- Windows 11, PowerShell 5.1 (no `&&`; use `cmd1; if ($?) { cmd2 }`). Git repo at
  `C:\Users\aryan\Documents\Acute`, remote `github.com/aryyann011/Acute`.
- Python 3.12 + `uv` package manager (`.venv` in repo). Run things as `uv run python ...`
  or `uv run acute ...`.
- `.env` contains `GEMINI_API_KEY` (git-ignored).
- Tools: typer CLI, loguru logging (stderr at INFO + `logs/agent_run.log` DEBUG),
  tree-sitter (+ python & javascript grammars), fastembed (BAAI/bge-small-en-v1.5, 384-dim,
  local), qdrant-client 1.19 (local disk mode in `.agent_db/`), google-genai 2.33,
  pydantic, python-dotenv.
- **qdrant-client caveat:** the old `client.search()` API is REMOVED in 1.19; use
  `client.query_points(collection_name=..., query=vector, limit=...)` and read
  `response.points[i].payload`.

## 4. Architecture map (who does what)

```
agent/main.py               Typer CLI. Commands: index (working), fix (--dry-run / --attempts
                            1-5 default 3 / --no-tests). Owns: embed → search → XML → first
                            Gemini call → dry-run branch; delegates real runs to state.run_fix_loop;
                            exit code 0 iff tests-passed / applied-untested / no-tests.
agent/rag/scanner.py        git ls-files -> filtered file list (.py/.js/.ts)
agent/rag/ingest.py         CodeChunker: tree-sitter AST -> one chunk per function/method/
                            arrow-fn + file-header imports; class context prepended
agent/rag/embedder.py       VectorEmbedder: chunk text -> 384-dim via FastEmbed BGE;
                            token sliding-window (max 500, overlap 100) for oversized fns;
                            embed_text() for bug reports at query time
agent/rag/vector_store.py   QdrantDB: local Qdrant in .agent_db/, collection "codebase",
                            Distance.COSINE; recreate_collection + upsert; search() via
                            query_points returning payloads
agent/core/memory.py        CodeChunk (pydantic) validation firewall; ContextManager
                            assembles XML <codebase_context> doc, drops malformed chunks
agent/core/prompts.py       SYSTEM_PROMPT + first_attempt_prompt + retry_prompt
                            (retry carries previous patch + verbatim failure tail +
                            "don't return an identical edit" instructions)
agent/core/state.py         run_fix_loop: per attempt stop-gates first (no-progress:
                            identical code tuples; stuck-echo: same first-error-line twice,
                            extracted with priority Error->FAILED->assert because pytest
                            prints the assert-source echo before the real exception),
                            lazy snapshots (patcher.snapshot_file), sequential
                            patch_file(create_backup=False), TestRunner verdict branching;
                            restores repo byte-identical on every negative outcome except
                            leave-for-review ones; RunOutcome{fixed, reason, attempts_used,
                            records[AttemptRecord]}; reason vocab: tests-passed | no-tests |
                            applied-untested | no-progress | stuck-on-same-error |
                            attempts-exhausted | apply-rejected | patch-generation-failed |
                            snapshot-failed
agent/tools/coder.py        CodePatch/MultiFilePatch schemas; Coder -> Gemini 2.5 Flash,
                            prompts from core.prompts, MAX_API_ATTEMPTS=3 with 2s/4s backoff
                            (added after a real 503 killed an E2E run); returns parsed model
                            or None
agent/tools/executor.py     TestRunner: detect_test_command (pytest signals -> [sys.executable,
                            -m, pytest, -q]; package.json test script -> npm via shutil.which;
                            else None) + run(timeout=60): subprocess cwd=repo, captured output,
                            TimeoutExpired -> timed_out verdict; TestResult{ran, passed,
                            command, output(last ~200 lines), elapsed_seconds, timed_out}
agent/tools/patcher.py      CodePatcher: snapshot_file / patch_file / restore_file;
                            tree-sitter query finds target function node; FOUR GATES before
                            any write (Phase 1 D3): textwrap.dedent incoming code -> ast.parse
                            gate -> re-indent by file's own prefix (_line_prefix_at) -> ast.parse
                            the WHOLE spliced file in memory; dry_run branch prints difflib
                            unified diff instead of writing; VS Code diff view (skipped when
                            create_backup=False or dry_run)
harness/recorder.py         record_run: runs/<timestamp>.json per fix() run (git-ignored):
                            bug, flags, reason, attempts_used, per-attempt records with
                            truncated failure tails; never raises
verify_phase1.py            6-case guarantee suite (no LLM; surgical patch, indent gate,
                            broken/garbage rejection, transaction rollback, dry-run no-write).
agent/core/planner.py       EMPTY placeholder (Phase C agentic planner)
agent/rag/retriever.py      EMPTY placeholder (unused; decide fate in cleanup)
agent/core/coder.py         EMPTY duplicate of tools/coder.py (decide fate in cleanup)
harness/runner.py           EMPTY placeholder (Phase B benchmark driver)
test_patcher.py             scratch round-trip test for patcher (untracked)
dummy_target.py / .bak      scratch fixture (untracked)
```

## 5. Verified current state (as of last update)

ALL of these were actually executed and verified — not assumed:

- Every module compiles and imports cleanly (`py_compile` + imports).
- `acute index` works end-to-end (model loads, chunks embedded, Qdrant upserts OK).
- Retrieval works: real bug-like query returned relevant chunks from `.agent_db`.
- `ContextManager.assemble_xml_context` produced valid XML from live chunks.
- `test_patcher.py` round-trip: patch applied surgically, `do_not_touch_this` survived.
- **Phase 1 Decision 1+2 verified:** transaction simulation where edit 1 succeeds, edit 2
  fails (missing function) → BOTH files restored byte-identical to originals; file still compiles.
- **Phase 2 verified (all actually executed):**
  - Executor sandbox 4/4: passing suite; failing suite (tail contains the real error
    text); no-test-signal repo → ran=False honestly; 30s-hanging test killed at 2s
    (timed_out=True).
  - Repair-loop sandbox 5/5 (real patcher gates + real pytest): fail-then-fix (attempt 2
    informed by feedback, tests green, patch on disk); identical-patch → no-progress, repo
    untouched; 3 distinct failures → attempts-exhausted, repo byte-identical;
    `--no-tests` → applied-untested, patch left; test-less repo → no-tests, patch left.
  - One bug caught by the sandbox and fixed: first-error-line extraction used to grab
    pytest's assert-source echo before the exception, falsely triggering the stuck-gate;
    fixed with priority Error → FAILED → assert.
  - Regression: verify_phase1.py still 6/6 after all Phase 2 changes.
- **Live Gemini E2E ×2 (2026-11-08, temp git repo):** JWT two-dots bug (attempt 1,
  `tests-passed`) and median even-length bug (attempt 1, `tests-passed`); both runs
  recorded to runs/*.json with full attempt records. A third E2E attempt hit a real
  Gemini 503 ("high demand") → motivated the coder backoff fix now in place. The live
  *retry branch* remains stub-proven only (both live fixes were one-shot).

## 6. Key data shapes

- **Chunk dict (everywhere in the pipeline):**
  `{filepath: str(abs), name: str, type: str, start_line: int, end_line: int, content: str}`
  — plus `vector: list[float]` after `VectorEmbedder.process_chunks`.
- **Qdrant point:** `PointStruct(id=uuid4 str, vector=384 floats, payload=chunk dict)`.
  `search()` returns list of payloads (chunk dicts).
- **Coder output:** `MultiFilePatch{edits: [CodePatch{filepath, target_function,
  explanation, fixed_code}]}` — pydantic, from Gemini structured output.
- **Patcher API now:** `snapshot_file(file_path) -> Path|None`,
  `patch_file(file_path, target_function, new_code, create_backup=True, dry_run=False) -> bool`,
  `restore_file(file_path, backup_path) -> bool`.
- **Executor API:** `TestRunner(repo_path)`, `.detect_test_command() -> list[str]|None`,
  `.run(timeout_seconds=60) -> TestResult{ran, passed, command, output, elapsed_seconds, timed_out}`.
- **State API:** `run_fix_loop(first_patch, bug_report, xml_context, coder, patcher, tester,
  max_attempts=3, run_tests=True, test_timeout=60) -> RunOutcome`;
  `AttemptRecord{attempt, applied, test_ran, test_passed, test_timed_out, failure_output,
  first_error_line}`; `RunOutcome{fixed, reason, attempts_used, records}`.
- **Recorder API:** `record_run(outcome, bug_report, no_tests, runs_dir="runs") -> Path|None`.
- **Prompts API:** `SYSTEM_PROMPT`, `first_attempt_prompt(bug, xml) -> str`,
  `retry_prompt(bug, xml, previous_patch, failure_output) -> str`
  (previous_patch is duck-typed to avoid a core↔tools import cycle).

## 7. Roadmap position & next steps

- **Phase 0 (stabilize): DONE.** All one-line fixes landed in 4 commits on branch
  `phase0-fixes` (pushed; PR not yet opened/merged as of last update).
- **Phase 1 (close the loop): COMPLETE — all five decisions built and verified.**
  DONE: Decision 1 — apply-loop lives in `fix()` (CLI orchestrates); Decision 2 —
  all-or-nothing transaction (snapshot every target file once BEFORE any edit, abort +
  rollback all on any failure); Decision 3 — syntax gate + indentation normalization in
  the patcher (textwrap.dedent + ast.parse of incoming code, re-indent by the file's own
  prefix detected from bytes before start_byte, full-file parse gate BEFORE writing —
  invalid code physically cannot reach disk, gate returns False → transaction rolls back);
  Decision 4 — `--dry-run` flag on fix() (same code path + gates, prints difflib unified
  diff, writes nothing, snapshot phase skipped); Decision 5 — `verify_phase1.py` suite
  (6 assert-cases, all green) + **real Gemini end-to-end run** in a temp git repo
  (index -> fix -> patch applied: JWT two-dots check added, unrelated function untouched,
  .bak created).
  Known quirks to remember: `textwrap.dedent` removes uniform prefix only (worked for the
  real Gemini output); Typer makes `index(path: str = ".")` an OPTION, so `acute index .`
  errors — must run `acute index` (defaults to cwd); decorators above the target function
  live OUTSIDE the spliced byte range (preserved, but if Gemini echoes the decorator it
  would duplicate — not yet handled/design decision pending).
- **Phase 2 (agent loop): COMPLETE.** executor.py TestRunner (detection + subprocess +
  60s timer + honest 4-way verdict with output tail); state.py run_fix_loop (stop-gates:
  no-progress on identical code tuples, stuck-echo on repeated first-error-line; lazy
  run-level snapshots; rollback-to-byte-identical on every negative outcome except
  leave-for-review verdicts); prompts.py (first + informed retry templates); coder.py
  gained previous_patch/failure_output params + 3-attempt backoff (2s/4s) after a real
  503 killed an E2E; harness/recorder.py writes runs/<timestamp>.json (git-ignored);
  main.py slimmed: loop delegated, new flags --attempts (1–5, default 3) and --no-tests,
  exit 0 iff tests-passed/applied-untested/no-tests.
- **Phase B (scoreboard): NOT STARTED — next.** Seed 30–50 test-anchored bugs into small
  Python repos; runner script drives `acute fix` per bug; aggregate `runs/*.json` into
  pass rate / pass@attempts / cost-per-fix; publish as a README table. This baseline
  number is what every later phase (C, D) must beat.
- **Phase C (agentic tool loop): NOT STARTED.** Model-called tools: read_file, grep,
  apply_edit (gated), run_tests; bounded turns + context budgeting + cost caps; RAG
  demoted to starting map; the existing gates/rollback become tool-level wrappers.
  Model-swap decision (stay Gemini flash vs stronger frontier model) deferred until
  Phase C's cost-per-turn is visible.
- **Phase D (retrieval quality): NOT STARTED.** delta-sync (SHA-256 manifest, deterministic
  uuid5 IDs, delete-by-filepath, payload indexes), AST-boundary sub-chunking (replace token
  sliding-window), query_embed usage, filepath/lang filters, token-budgeted context
  (Gemini count_tokens, NOT tiktoken), dedup overlapping sub-chunks — driven by Phase C's
  failure patterns.
- **Phase 5 (production polish): NOT STARTED.** Dockerfile, FastAPI service, Qdrant server
  container, K8s (kind/k3s) deploy per user's interest in the "agents on K8s" angle, CI,
  README w/ metrics table, DECISIONS.md/ADRs.

## 8. Deferred decisions (with the working recommendation)

| # | Decision | Recommendation on the table | Status |
|---|---|---|---|
| 1 | Apply-loop location | main.py orchestrates (accepted) | DONE |
| 2 | Batch failure policy | all-or-nothing + rollback (accepted) | DONE |
| 3 | Indentation strategy | dedent + re-indent; reject wrong-relative | DONE (D3) |
| 4 | Dry-run view | difflib unified diff per edit | DONE (D4) |
| 5 | Phase 1 verification depth | unit asserts + one sandbox E2E call | DONE (D5) |
| 6 | .ts support | parsed with JS grammar today; either drop .ts or add tree-sitter-typescript | unpicked (must land before Phase C) |
| 7 | core/coder.py vs tools/coder.py, retriever.py vs vector_store.py | delete empty duplicates, keep one source of truth | unpicked |
| 8 | Untracked fixtures (test_patcher.py, dummy_target.py) | fold tests into real suite in Phase B | unpicked |
| 9 | Temp e2e sandbox repo (opencode temp dir) | contains a copied .env — delete after PR | pending |

## 9. Repo / git state

- Branch `phase1-safeguards` from synced main (e41e0b6, PRs #10/#11 already MERGED by user).
- Pushed to origin/phase1-safeguards: 31e7148 (gates), c217b01 (dry-run), 2a15893
  (verify suite), 071b367 (docs), 9895302 (executor), df8d520 (repair loop), 520adb1
  (recorder), b045721 (API backoff). 8 commits ahead of main; PR → main NOT yet opened
  (part of Phase A close-out, awaiting user's go).
- Untracked (intentional): `test_patcher.py`, `dummy_target.py`, `dummy_target.py.bak`.
- Temp E2E sandbox at `C:\Users\aryan\AppData\Local\Temp\opencode\e2e_repo` holds a copied
  `.env` — destroy it as part of close-out.
- Docs (WALKTHROUGH / PROJECT_SUMMARY / HANDOFF) refreshed for Phase 2 in the pending
  docs commit.

## 10. Known pitfalls / gotchas

- **Relative paths:** `.agent_db` is relative to CWD — running from another directory
  misses the DB; scanner needs a git repo.
- **Windows line endings:** git prints CRLF/LF warnings on commit; harmless, expected.
- **Qdrant local `__del__` traceback** at interpreter shutdown after searches — harmless,
  portalocker unlock quirk in one-liner runs; not a pipeline failure.
- **Embedder loads models on first use per process** (~15–40s; HF cache in AppData temp).
  There's a benign symlink warning on Windows.
- **Coder import cost:** google-genai client init requires GEMINI_API_KEY; missing key
  logs an error but the client is still constructed (degraded — fails at call time).
- **CodeChunker instance state:** `self.chunks` never cleared; constructing twice in one
  process would duplicate (CLI usage is fine).
- Loguru: never call `logger.info()` with no message.

## 11. How to continue (recommended openings, in order)

1. Phase A close-out, in progress: docs commit + push, open PR `phase1-safeguards` →
   `main` (user merges), delete the temp e2e sandbox (contains a copied .env).
2. Phase B (scoreboard) next: seed-bug suite design → user approves design → build
   runner + aggregation → first README table. Keep the user protocol in §2 strictly.
3. Update §5/§6/§7/§9 of this file at every milestone; keep PROJECT_SUMMARY.md alongside.
