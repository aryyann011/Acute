# Acute — Project Summary (My Notes)

> **Purpose:** this is MY file. If I come back to this project after months, or the night
> before an interview, I read this — not code, not chat logs. It tells me everything:
> what the project is, why each piece exists, what happened so far, and what comes next.
> Language is deliberately simple. Decisions are explained with examples, not jargon.
>
> Last updated: 2026-10-31 (Phase 1 fully complete: Decisions 1–5 built + verified,
> incl. one real Gemini end-to-end run)  ← I should bump this date
> every time it changes, so I always know how fresh it is.

---

# PART 1 — THE PAST: how this project came to be

## 1.1 Why this project exists

Interviewers see hundreds of "I built a chatbot with LangChain" resumes. I wanted a project
that instead says: **"I built a robot that repairs code on its own."** Not a wrapper around
an API call — a system with real engineering problems: how do you search code by *meaning*,
how do you make an LLM answer in a machine-readable shape, and how do you let it modify
files on disk **without ever corrupting them**. That last one is the interesting part, and
it's where most of Acute's design comes from.

One-line pitch (for interviews):
> "Acute is an autonomous bug-fixing agent: it indexes your repo into a vector database,
> retrieves the relevant code for a natural-language bug report, asks Gemini for a
> structured patch, and applies it with AST-guided byte surgery — with watchdogs that
> validate syntax before writing and roll back everything if anything fails."

## 1.2 The journey, honestly

- I planned it by hand, coded it with Gemini's help, in phases — scanner, chunking,
  vector DB, and so on, one git branch per concern (that's why `main`'s history is a
  chain of PRs: `Qdrant`, `VectorEmbedder`, `ingest`, `memory`, `patcher`).
- Then I stepped away, forgot how it all worked, and came back in late 2026.
- First big lesson of the comeback: **my own status notes were outdated.** I thought
  things worked that didn't (the retrieval API I called was deleted in a newer version
  of qdrant-client), and things I thought were half-done were more done than I remembered.
- With opencode I did a **stabilization pass (Phase 0)**: read every file, actually *ran*
  everything instead of assuming, and fixed ~10 one-line-style bugs. Details in §2.2.
- Then started **Phase 1** — making the agent actually *apply* its own patches.

I chose the name Acute: it's from "a cute patching agent" (it's also an angle joke —
surgery needs precision). Either origin works in an interview.

---

# PART 2 — THE PRESENT: what it is today

## 2.1 The system in one picture

```
            INDEXING (once)                                MESSAGING (every fix)
┌───────────────────────────────────────┐        ┌─────────────────────────────────────┐
│ git says which files matter           │        │ user types a bug in plain English   │
│   scanner.py                          │        │        │                            │
│   ↓                                   │        │     embed: text → 384 numbers       │
│ tree-sitter splits files by MEANING   │        │        ↓                            │
│   (one chunk per function, class      │        │  "find the 7 most similar chunks"   │
│    methods, JS arrow fns; imports     │        │        ↓                            │
│    import headers glued on top)       │        │  validate + wrap them in XML        │
│   ↓                                   │        │        ↓                            │
│ embed each chunk → vector             │        │  Gemini must answer STRICT JSON:    │
│   (same 384-dim space)                │        │   file, function, why, new code     │
│   ↓                                   │        │        ↓                            │
│ store in Qdrant (local disk folder)   │        │  find that function BY BYTE RANGE   │
└───────────────────────────────────────┘        │  in the REAL file, splice bytes     │
                                            ←─── │  (with .bak safety + rollback)      │
```

The "aha" of the whole design: a bug report and code chunks are turned into **points in
the same 384-dimensional space** by the same model, so "closest point" becomes
"most relevant code" — no keyword matching, meaning finds you.

## 2.2 What Phase 0 fixed (the comeback stabilization), and why

These were confirmed by actually running the code, not reading it:

| Bug | What it looked like | The fix |
|---|---|---|
| `memory.py` crashed ON IMPORT | `Any` used in a type hint but never imported (NameError) | one-word import fix |
| `memory.py` lost context on 1 bad chunk | a `return` inside the validation loop nuked everything if ANY chunk was malformed | `return` → `continue` (skip the bad chunk, keep the good ones) |
| Duplicate `</codebase_context>` tag | early-exit path returned two opening tags | fixed the string |
| Coder file didn't even compile | two `def generate_patch(...)` lines stacked — a half-refactor artifact | deleted the stray line |
| `MultiFilePatch` didn't exist | the schema Gemini is told to use was never defined | added the 2-class pydantic model |
| Patcher had the wrong signature | someone (me) started converting it to batch mode, left `Multi_patch_file(self, List[Dict[str, any]])` — invalid Python | reverted to `patch_file(file_path, target_function, new_code)` |
| Dead/stale retrieval API | qdrant 1.19 deleted `client.search()` entirely | use `client.query_points()` |
| Dependencies never declared | code imported `google-genai` and `python-dotenv`; `uv sync` didn't install them | added to pyproject + lock |
| Typo'd kwarg worked *by accident* | `mode_name` instead of `model_name` — the library silently ignores unknown kwargs, and the default model was coincidentally the same | `model_name` |
| `fix()` full of undefined names | `embedder`, `embedded_bugReport` etc. from an old refactor | re-wired the flow properly |

Every fix verified by running: imports, compile, retrieval against a real query, and a
patcher round-trip on `dummy_target.py` (the watchdog check there: `do_not_touch_this`
must survive every patch — it did).

All this went to branch `phase0-fixes` in 4 clean commits (deps / rag fixes / coder-patcher-cli fixes / docs). **PR to main is still pending — I need to open it.**

## 2.3 Phase 1 — what was just built (Decisions 1 & 2)

**The gap it closes:** before this, `acute fix` walked the entire journey and then…
printed a summary and quit. A proposal generator, not a patcher. Now it applies them.

### Decision 1 — where the application loop lives

Gemini returns e.g. 3 edits. *Somebody* must loop "hand each edit to the patcher." Those
lines went directly into `fix()` in `main.py`, because that's the file that already
conducts the whole flow. The patcher stays a pure "one file, one function" tool. Why
this matters: in Phase 2, the loop's *brake pedals* (retry, test, rollback) move to a
state machine — the surgical function should never need to change when that happens.

### Decision 2 — all-or-nothing transactions (database thinking, applied to files)

The scenario that forced this: Gemini proposes edits to files A and B. A succeeds. Then
B fails — the function it names isn't in that file. Now disk has A modified, B untouched,
and the run just stops. **A half-patched repo. Worse than no patch at all.**

The fix is exactly what a database does with transactions:
1. AFTER Gemini answers, but BEFORE touching anything: copy every target file to `.bak`.
2. Apply edits one by one.
3. Any failure → copy every `.bak` back over its original. Disk is **byte-identical** to
   before the run.
4. All succeed → keep the `.bak` files around so I can review what changed.

The subtle trap it avoids: my patcher originally made its own `.bak` on every call. In a
batch touching file A twice (two functions in one file), the *second* backup would have
overwritten the *first* with the already-patched version — so a later rollback would
"restore" the half-patched state. Fake safety is worse than none. So during a
transaction, snapshots are taken once up front and the per-call backup is disabled
(`create_backup=False`).

**Verified:** I simulated exactly the failure above — 2 files, edit 1 applies, edit 2
aims at a function that doesn't exist → both files restored **byte-identical** to before
the run, and the patched-then-restored file still compiles. That test ran green.

### The remaining three decisions, now DONE (built and verified)

- **Decision 3 — the safety gates (syntax + indentation), landed:** before anything is
  written to disk the incoming code is: (1) cleaned of whatever uniform indent Gemini
  added on top, (2) parsed as Python — logically broken code is *rejected*, never
  auto-repaired, (3) re-dented using the indentation the real file already has around the
  function (detected from the file itself, not guessed), and (4) only after the ENTIRE
  spliced file parses is it allowed to hit the disk. Net guarantee: invalid code
  physically cannot be written. Verified against: an echo of the function with 8 extra
  leading indent spaces (lands at the correct depth), a body with broken relative indent
  (rejected, file untouched), and pure garbage (rejected, file untouched). One real bug
  found and fixed during building: my first re-indent formula REPLACED lines' relative
  indent with the base indent instead of adding it — caught by the gate test before it
  could ever reach a real file.
- **Decision 4 — `--dry-run`, landed:** `acute fix "..." --dry-run` runs the whole
  pipeline (retrieval, Gemini, gates) but prints a git-style unified diff of what WOULD
  change and writes nothing. Crucially it reuses the exact same code path as the real
  write, so the preview cannot lie.
- **Decision 5 — permanent verification, landed:** `verify_phase1.py` (run: `uv run python
  verify_phase1.py`) — six assert-cases covering the six guarantees above (surgical patch,
  indentation gate, broken-code rejection, garbage rejection, transaction rollback,
  dry-run writes nothing) — 6/6 pass. Plus the milestone test: a REAL Gemini end-to-end
  run in a throwaway git repo — the agent indexed it, understood a JWT-validation bug it
  had never seen, patched the right function with the right logic (two-dots check), left
  the neighboring function untouched, and the file compiled.

## 2.4 Current status at a glance

| Subsystem | Status | Notes |
|---|---|---|
| Index pipeline (scan → chunk → embed → store) | ✅ working | verified via logs + retrieval |
| Retrieval by bug report | ✅ working | via `query_points` |
| Context assembly (validate + XML) | ✅ working | live-tested |
| Gemini patch generation | ✅ working | strict JSON schema |
| Patch application | ✅ transactional + syntax-gated | sandbox-verified + 6/6 suite |
| Indentation / syntax gates (D3) | ✅ working | invalid code cannot reach disk |
| `--dry-run` (D4) | ✅ working | preview = same path as write |
| Verification suite (D5) | ✅ `verify_phase1.py` 6/6 | + real Gemini E2E green |
| Benchmark & metrics | ❌ Phase 3 | this is where "world class" gets proven |
| Delta re-indexing (only changed files) | ❌ Phase 4 | currently nuke-and-rebuild |
| Deployment story (Docker/K8s/FastAPI) | ❌ Phase 5 | |

---

## 2.5 The code map — which file holds which logic, and who calls whom

### The whole cast (every file, one line each)

**The actors (files with real logic):**

| File | What logic lives there |
|---|---|
| `agent/main.py` | the CLI + the orchestration. Two commands: `index` (scan → chunk → embed → store) and `fix` (embed bug → search → XML → Gemini → patch). The TRANSACTION (snapshot-all-first, apply loop, rollback-all-on-failure) and the `--dry-run` branch live here too. |
| `agent/rag/scanner.py` | file discovery: asks git which files it tracks, filters to .py/.js/.ts |
| `agent/rag/ingest.py` | chunking: tree-sitter AST → one clean dict per function/method/arrow-fn (+ imports header) |
| `agent/rag/embedder.py` | vectorization: text → 384 floats (local bge-small); token sliding-window for giant functions; `embed_text` for bug reports |
| `agent/rag/vector_store.py` | persistence + search: local Qdrant in `.agent_db/`, upsert points, `query_points` top-k |
| `agent/core/memory.py` | the LLM's front-door bouncer: pydantic-validates each chunk, assembles them into the XML context document |
| `agent/tools/coder.py` | the Gemini brain: `CodePatch`/`MultiFilePatch` schemas + the API call that forces strict JSON |
| `agent/tools/patcher.py` | the surgeon + the safety gates: indent-prefix detection, dedent, ast-parse gate, re-indent, byte splice, whole-file parse gate, `--dry-run` rendering, snapshot/restore |

**The empty seats (by design, filled in later phases):**
`agent/core/state.py` (state machine), `agent/core/planner.py`, `agent/core/prompts.py`,
`agent/tools/executor.py` (test runner), `harness/runner.py` (benchmark) — plus
`agent/core/coder.py` + `agent/rag/retriever.py`, which are EMPTY duplicates and eventual cleanup targets.

**Everything else:** `verify_phase1.py` (my 6-case safety suite — imports only the patcher, no LLM), `test_patcher.py` (old scratch round-trip, superseded by the suite), `pyproject.toml` (deps), `agent/__init__.py`.

### The two chains (this IS the system)

**Chain A — `acute index` (offline, once):**
`main.index` calls `scanner.get_tracked_files()` → hands paths to `CodeChunker.process_files()` → hands chunk dicts to `VectorEmbedder.process_chunks()` → hands chunk-with-vector dicts to `QdrantDB.insert_chunks()`.

**Chain B — `acute fix "bug"` (the live loop):**
`main.fix` calls `VectorEmbedder().embed_text(bug)` → `QdrantDB().search()` (chunk dicts back)
→ `ContextManager().assemble_xml_context()` (one XML string) → `Coder().generate_patch(bug, xml)`
(a `MultiFilePatch` object) → then EITHER:
- dry-run branch: per edit → `patcher.patch_file(..., dry_run=True)` → prints the diff, writes nothing, done; OR
- real branch: snapshot every unique target file (`patcher.snapshot_file`) → per edit → `patcher.patch_file(..., create_backup=False)` → all succeed = committed; any failure → restore every snapshot (`patcher.restore_file`) → exit 1.

**Inside `patch_file` — the four gates, in order (the heart of Phase 1):**
1. locate the function's byte range via tree-sitter (fail = "no such function") →
2. `_normalize_incoming_code`: dedent Gemini's uniform indent + `ast.parse` the cleaned text (fail = logically broken code) →
3. re-indent using the prefix detected from the file's own bytes (`_line_prefix_at`) →
4. assemble the full spliced file IN MEMORY and `ast.parse` the WHOLE file (fail = would corrupt) →
   only then: write. A dry-run fork sits right before the write: difflib diff + zero bytes to disk.

### Architecture rules I can defend out loud

- `main.py` is the ONLY orchestrator; every other module is a library that knows nothing about the CLI.
- Data shapes are the contract: chunk dicts flow through RAG; a `MultiFilePatch` crosses the LLM boundary; every patch attempt answers with a plain bool + a logged reason.
- Constraint interlocking: the coder cannot write files; the patcher cannot run tests; the CLI cannot do surgery. A failed gate in the patcher surfaces as `False`, which the CLI's transaction converts into a byte-identical rollback.

---

# PART 3 — THE FUTURE: the roadmap to "world class"

The bar I set: not just "it works," but **measurable** — a benchmark with a published
metrics table. Most student projects have no evaluation at all; this is the differentiator.

| Phase | What | Definition of done |
|---|---|---|
| 1 ✅ | Close the loop | apply-loop, transactions, syntax/indent gates, dry-run, 6/6 verification — ALL DONE |
| 2 | Agent loop | after patching, run the repo's tests; on failure revert + re-prompt Gemini with the error; cap at 3 attempts; detect no-progress; log every run to a JSONL file |
| 3 | Benchmark | a runner that chooses a bug set (10–20 real single-file bugs from real repos, each with tests), clones, runs the agent, measures: retrieval hit@k, patch-apply rate, pass@attempts, time/cost per fix |
| 4 | Retrieval quality | delta-sync (hash files, only re-embed changes), smarter chunking of giant functions, metadata filters, token-budgeted context |
| 5 | Production story | Dockerfile, FastAPI endpoint serving `fix`, Qdrant as a server container, deploy on a local K8s (kind) — because there's a cloud-native story to tell if the agent core is solid |

Phase 3 before Phase 4 is deliberate: **measure first, then optimize what the numbers
prove is weak.** No blind tuning.

The realistic target marks I'm chasing (on my own small benchmark, cheap model):
retrieved bug-target function in top-5: >85% · patches apply cleanly: >90% · tests green
within 3 attempts: >70%. A README table showing these numbers is the resume headline.

---

# PART 4 — UNDERSTANDING THE PIECES (plain, no fluff)

## The five concepts that ARE the whole project

1. **AST / tree-sitter** — code as a tree. `def foo():` is a node; the tree can tell me
   exactly which *bytes* of the file that function owns. Tree-sitter never crashes on bad
   code; it marks problems as ERROR nodes. Two files use this: `ingest.py` (to chop code
   by meaning) and `patcher.py` (to find the exact surgical target).
2. **Embeddings** — `text → 384 floats`, where similar meaning → similar floats.
   The bug report and each code chunk are encoded by the SAME model, into the SAME space.
3. **Vector DB (Qdrant)** — instant "top-7 nearest neighbors" over thousands of vectors.
   Local mode = literally a folder `.agent_db/` on disk. No server needed.
4. **Structured output (`response_schema`)** — I give Gemini my pydantic model
   `MultiFilePatch`, it MUST answer matching JSON. No parsing prose, no regex on
   markdown fences. This one design choice deletes a whole category of bugs.
5. **Byte-splicing** — files are just bytes; `file[:start] + new + file[end:]` replaces a
   function with zero collateral damage, because the AST told me the exact start/end bytes.

## Decisions I should be able to defend in an interview

- "Why local embeddings instead of an API?" — free, private, fast enough, and this
  project's value is the system design, not a tiny quality delta from a bigger model.
- "Why whole-function replacement instead of diffs?" — diffs are brittle from LLMs and
  harder to validate; replacing a whole function node cut out by the AST is exact and
  self-contained. (Phase 6 stretch idea: unified-diff protocol.)
- "Why XML to pack context?" — unambiguous boundaries the model parses reliably, cheap,
  easy to validate inbound data before the model ever sees it.
- "Why transactions?" — a code-fixing agent's worst outcome is a half-mutated repo;
  all-or-nothing plus `.bak` snapshots makes every run safe to run on real code.
- "Why measure first?" — because 'it seems smarter now' is not engineering. Benchmarks
  (Phase 3) decide what to optimize in Phase 4.

## Honest known-weaknesses (say these before an interviewer finds them)

- Giant functions are split by blind token windows today, losing line-number identity.
- `.agent_db` lives relative to the current directory; running elsewhere misbehaves.
- `.ts` files are parsed with the JavaScript grammar (TypeScript syntax confuses it).
- No syntax validation before writing yet (that's Decision 3 territory).
- `fix()` and `index` both rebuild things they should reuse (indexing is nuke-and-rebuild).

---

# PART 5 — RUN IT (cheat sheet)

```powershell
uv sync                       # install everything
acute index                   # build/rebuild the vector index of this repo
acute fix "bug text here"     # retrieve → Gemini → transactional patch
uv run python test_patcher.py # surgical round-trip check on dummy_target.py
```

- Requires `GEMINI_API_KEY` in `.env`.
- Indexing takes ~1 min (model load dominates). Fixing is seconds + one Gemini call.
- `.bak` files: kept after successful patches for review; all-or-nothing restore happens
  automatically if any edit in a batch fails.

---

# PART 6 — WHERE THE DOCS LIVE

| File | What it's for |
|---|---|
| `docs/PROJECT_SUMMARY.md` | THIS file — my narrative memory of the project |
| `docs/WALKTHROUGH.md` | deep, file-by-file, function-by-function walkthrough (data shapes, execution order) |
| `docs/HANDOFF.md` | the file I paste into a NEW AI session so it knows everything and continues seamlessly |
| `README.md` | public-facing entry (should grow into: diagram, demo, benchmark table) |

**Rule going forward:** whenever major work lands, bump all three internal docs
(summary date + status, walkthrough if behavior changed, handoff §5–§9).
