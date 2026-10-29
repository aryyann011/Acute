# ACUTE — LLM Handoff File

> **Purpose:** paste this file into a new LLM session and continue working with zero context loss.
> It records: project identity, user protocol, environment, architecture, verified state,
> roadmap position, deferred decisions, and repo state.
>
> **Last updated:** 2026-10-30 — after Phase 1 Decisions 1+2 (transactional apply) landed.
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
agent/main.py               Typer CLI. Commands: index (working), fix (working end-to-end,
                            incl. transactional patch application).
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
agent/tools/coder.py        CodePatch/MultiFilePatch schemas; Coder -> Gemini 2.5 Flash,
                            response_schema=MultiFilePatch, returns parsed model or None
agent/tools/patcher.py      CodePatcher: snapshot_file / patch_file / restore_file;
                            tree-sitter query finds target function node; byte splice;
                            VS Code diff view (skipped when create_backup=False)
agent/core/planner.py, state.py, prompts.py   EMPTY placeholders (Phase 2 targets)
agent/tools/executor.py     2-line stub (test runner lands here in Phase 2)
agent/rag/retriever.py      EMPTY placeholder (unused; decide fate in cleanup)
agent/core/coder.py         EMPTY duplicate of tools/coder.py (decide fate in cleanup)
harness/runner.py           EMPTY placeholder (Phase 3 benchmark)
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

## 6. Key data shapes

- **Chunk dict (everywhere in the pipeline):**
  `{filepath: str(abs), name: str, type: str, start_line: int, end_line: int, content: str}`
  — plus `vector: list[float]` after `VectorEmbedder.process_chunks`.
- **Qdrant point:** `PointStruct(id=uuid4 str, vector=384 floats, payload=chunk dict)`.
  `search()` returns list of payloads (chunk dicts).
- **Coder output:** `MultiFilePatch{edits: [CodePatch{filepath, target_function,
  explanation, fixed_code}]}` — pydantic, from Gemini structured output.
- **Patcher API now:** `snapshot_file(file_path) -> Path|None`,
  `patch_file(file_path, target_function, new_code, create_backup=True) -> bool`,
  `restore_file(file_path, backup_path) -> bool`.

## 7. Roadmap position & next steps

- **Phase 0 (stabilize): DONE.** All one-line fixes landed in 4 commits on branch
  `phase0-fixes` (pushed; PR not yet opened/merged as of last update).
- **Phase 1 (close the loop): PARTIAL.** DONE: Decision 1 — apply-loop lives in `fix()`
  (CLI orchestrates); Decision 2 — all-or-nothing transaction: snapshot every target file
  once BEFORE any edit, abort+rollback all on any failure.
  **NEXT (user has NOT yet approved these — explain simply first):**
  - Decision 3: indentation normalization (dedent LLM code, re-indent to file's detected
    unit; wrong relative-indent gets REJECTED, not repaired). Explain before fixing.
  - Decision 4: `--dry-run` flag with difflib unified-diff preview.
  - Decision 5: verification pass — assert-scripts + one sandbox Gemini run.
  Note: until Decision 3 lands, patches apply raw and are NOT syntax-checked; rollback
  only triggers on patcher failures (function not found / file missing), so a bad-indent
  Gemini output can currently write a broken file — know this risk going into tests.
- **Phase 2 (agent loop): NOT STARTED.** state machine (state.py), test runner adapter
  (executor.py: detect pytest/npm test, subprocess w/ timeout, capture stderr),
  feedback injection, max_attempts=3, no-progress detection, run JSONL artifacts.
- **Phase 3 (benchmark): NOT STARTED.** custom 10–20 bug suite first (real repos w/ tests),
  metrics: retrieval hit@k, apply rate, pass@attempts, latency, tokens. pytest dep lands here.
- **Phase 4 (retrieval quality): NOT STARTED.** delta-sync (SHA-256 manifest, deterministic
  uuid5 IDs, delete-by-filepath, payload indexes), AST-boundary sub-chunking (replace token
  sliding-window), query_embed usage, filepath/lang filters, token-budgeted context
  (Gemini count_tokens, NOT tiktoken), dedup overlapping sub-chunks.
- **Phase 5 (production polish): NOT STARTED.** Dockerfile, FastAPI service, Qdrant server
  container, K8s (kind/k3s) deploy per user's interest in the "agents on K8s" angle, CI,
  README w/ metrics table, DECISIONS.md/ADRs.

## 8. Deferred decisions (with the working recommendation)

| # | Decision | Recommendation on the table | Status |
|---|---|---|---|
| 1 | Apply-loop location | main.py orchestrates (accepted) | DONE |
| 2 | Batch failure policy | all-or-nothing + rollback (accepted) | DONE |
| 3 | Indentation strategy | dedent + re-indent; reject wrong-relative | pending user re-explanation |
| 4 | Dry-run view | difflib unified diff per edit | pending |
| 5 | Phase 1 verification depth | unit asserts + one sandbox E2E call | pending |
| 6 | .ts support | parsed with JS grammar today; either drop .ts or add tree-sitter-typescript | unpicked |
| 7 | core/coder.py vs tools/coder.py, retriever.py vs vector_store.py | delete empty duplicates, keep one source of truth | unpicked |
| 8 | Untracked fixtures (test_patcher.py, dummy_target.py) | fold tests into real suite in Phase 3 | unpicked |
| 9 | agent/tools/executor.py dirty stub in working tree | will be written properly in Phase 2 | parked |

## 9. Repo / git state

- Branch `phase0-fixes` current working branch. Commits on it:
  1. `deps: add missing google-genai and python-dotenv`
  2. `fix: repair rag modules - missing Any import, stale qdrant search api, typos`
  3. `fix: resolve coder syntax, define MultiFilePatch, restore patcher signature, wire fix()`
  4. `docs: add codebase walkthrough and intro readme`
  5. (Phase 1 D1+D2: transactional patch application + handoff/summary docs)
- `main` on GitHub is behind (884ef68); user has NOT opened/merged the PR yet.
- Branch pattern fits their history (feature branches + PR merges).
- `docs/WALKTHROUGH.md` exists: code-level walkthrough doc (function-by-function);
  `docs/PROJECT_SUMMARY.md`: user-facing project narrative; this file: handoff.

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

1. If mid-Phase 1: re-explain Decision 3 (indentation) in plain language with the
   class/fixed-code example, then implement 3 → 4 → 5 per §7.
2. After Phase 1: open PR for `phase0-fixes` (user hasn't yet), merge to main,
   branch `phase2-loop` for Phase 2.
3. Keep the user protocol in §2 strictly. Update §5/§6/§7/§9 of this file at every
   milestone. Update `PROJECT_SUMMARY.md` alongside.
