# Acute — Codebase Walkthrough

> **Purpose:** re-learn the whole project in ~30 minutes. This document describes the current
> state of the code as of today (after the typo/one-line fix pass). It shows the exact data
> shapes that flow through the pipeline, what each file is responsible for, what is working,
> and what is explicitly deferred.

---

## 1. What Acute is

Acute is a CLI agent that **automatically fixes bugs in a codebase**. You describe the bug in
plain English:

```
acute fix "null pointer when token is missing in auth module"
```

and Acute runs six stages against your repository:

1. **Retrieval** — from a pre-built local database of your code, find the snippets most
   *semantically similar* to your bug description (not keyword matching — meaning matching).
2. **Context assembly** — validate those snippets and wrap them in one clean XML document.
3. **Generation** — send bug + context to an LLM (Gemini 2.5 Flash) and receive a *strictly
   structured JSON* patch describing exactly which function in which file must be rewritten.
4. **Patching** — locate that function in the real file by its **byte position in the AST**,
   cut the old bytes, splice in the new bytes (behind four validation gates).
5. **Verification** — run the repository's own test suite (`executor.py`) and get an honest
   verdict: passed / failed / timed out / no tests detected.
6. **The repair loop** (`state.py`) — if tests failed, restore the repo to its exact
   pre-run state and ask Gemini again, this time showing it *what it tried before and the
   verbatim test failure*. Bounded attempts, stop-gates against wasted retries, and every
   run recorded to a JSON artifact (`harness/recorder.py`).

The value proposition: the LLM never sees (and never rewrites) your whole file, and could
never hallucinate the "rest" of the file — it only rewrites one function, Acute does the
surgery, and the result is *proven* by tests, not assumed. A repo that cannot be fixed
comes back byte-identical with an honest "I couldn't" verdict.

---

## 2. Architecture in one picture

```
                         STAGE 1: INDEXING (offline, run once)
┌──────────────┐    ┌───────────────┐    ┌──────────────┐    ┌───────────────┐
│ scanner.py   │ ─▶ │ ingest.py     │ ─▶ │ embedder.py  │ ─▶ │ vector_store  │
│ git ls-files │    │ AST chunking  │    │ text→384 vec │    │ Qdrant local  │
│ filter .*py  │    │ tree-sitter   │    │ sliding win  │    │ .agent_db/    │
└──────────────┘    └───────────────┘    └──────────────┘    └───────────────┘

                      STAGE 2: RUNTIME (every `acute fix` call)
          bug text
              │  embed (384-dim vector)
              ▼
      ┌───────────────┐   top-7 chunks    ┌──────────────┐   XML   ┌──────────┐
      │ QdrantDB      │ ────────────────▶ │ memory.py    │ ──────▶ │ coder.py │
      │ .query_points │                   │ (validate +  │         │ Gemini   │
      └───────────────┘                   │  wrap XML)   │         │ JSON out │
                                          └──────────────┘         └────┬─────┘
                                                                        │ MultiFilePatch
                                                                        ▼
                                   ┌─────────────────────────────────────────────┐
                                   │ state.py  run_fix_loop()                    │
                                   │ per attempt: snapshot targets (patcher)     │
                                   │  → apply edits (patcher.py, 4 gates)        │
                                   │  → executor.py runs the test suite          │
                                   │ pass → done · fail → restore + re-prompt    │
                                   │ with previous patch + verbatim test output  │
                                   └───────────────────┬─────────────────────────┘
                                                       ▼
                                            harness/recorder.py
                                            runs/<timestamp>.json artifact
```

---

## 3. Jargon decoder (read this if anything below looks alien)

| Term | What it actually means |
|---|---|
| **AST** | Abstract Syntax Tree — a tree representation of code structure. `def foo():` becomes a node with identifier/name/body children. |
| **Tree-sitter** | A parser generator that builds ASTs for real languages (Python/JS). Key property: it **never fails** on broken code — it marks unparseable regions as `ERROR` nodes instead of raising. |
| **S-expression query** | A pattern to find nodes in the tree, e.g. `(function_definition name: (identifier) @name)` means "give me every function's name node". |
| **Embedding** | A function `text → [384 floats]` where texts with similar *meaning* get similar float lists. "null pointer" ≈ "check for None" as vectors, despite zero shared words. |
| **Qdrant** | A vector database: stores `{vector, payload}` points and answers "top-k nearest neighbors of this vector" instantly. Local disk mode = just a folder (`.agent_db/`). |
| **RAG** | Retrieval-Augmented Generation — don't ask an LLM to memorize your code; retrieve the relevant slice at query time and prepend it to the prompt. |
| **Pydantic** | A data-validation library. `Field(min_length=1)` means "fail unless non-empty string". Used as a *firewall* for data coming out of the DB. |
| **`response_schema`** | Gemini feature: give the model your Pydantic model; it must answer in JSON matching it. This is why we don't need to parse code blocks out of prose. |
| **Byte-splicing** | Files are bytes. If AST tells you "the function `verify_jwt` spans bytes 0–95", you can replace exactly those bytes: `file_bytes[:start] + new_code + file_bytes[end:]`. Nothing else in the file can be harmed. |
| **Delta sync** | "Re-index only what changed" (hash files, compare). Currently **not implemented** — the DB is nuked and rebuilt every `acute index`. |

---

## 4. STAGE 1 — `acute index`: the data journey

`agent/main.py` → `index(path)`:

### Step 1.1 — `root`. `main.py` `index` begins
```python
logger.info(f"Starting ingestion process for directory: {path}")
scanner = RepositoryScanner(path)
valid_files = scanner.get_tracked_files()   # -> list of absolute Path objects
```

### Step 1.2 — `agent/rag/scanner.py` — file discovery

**Role:** decide *which files* go into the index. It uses git as a manifest, so we never
walk `.git`, `node_modules`, `.venv`, etc. manually — git already knows what matters.

```python
class RepositoryScanner:
    ALLOWED_EXTENSIONS = {".py", ".js", ".ts"}
    IGNORED_FILES = {"package-lock.json"}

    def get_tracked_files(self) -> list[Path]:
        repo = Repo(self.repo_path)              # GitPython — attach to the repo
        raw_files = repo.git.ls_files().split('\n')   # CLI: git ls-files
        for file_path in raw_files:
            filter by extension / name ...
            yield full_path = self.repo_path / path_obj
```

- **Data in:** a path string (`.`)
- **Data out:** `list[Path]` of absolute paths, e.g. `[C:\...\Acute\agent\main.py, ...]`
- **Key insight:** `git ls-files` returns only files git *tracks*, so untracked junk is
  automatically excluded. (Known limitation, deferred: files you haven't `git add`-ed yet
  are also excluded.)

### Step 1.3 — `agent/rag/ingest.py` — chunking the AST

**Role:** turn each file into *semantically meaningful* chunks — one chunk per
`function_definition` / method / class-method / arrow-function, plus a header of the file's
top-level imports and some global statements. This is "logical chunking" (by code meaning),
not "split on every 500 characters".

Class `CodeChunker` follows this flow:

| Method | What it does | Data shape |
|---|---|---|
| `_read_file(path)` | file content as UTF-8 string | one file → `str` |
| `_get_parser(path)` | `.py` → Python parser; `.js/*.ts` → JS | file → `Parser` |
| `process_files(paths)` | the orchestrator: parse every file, build chunks | `list[Path]` → `list[dict]` |
| `_extract_chunks(node, ...)` | the recursive AST walker; unwraps classes so each method gets its own chunk | tree + context → writes chunks |
| `_save_chunk(...)` | builds the final chunk dict and appends it | node → `dict` |
| `_get_node_name(node, bytes)` | peels decorators/exports, then finds the `identifier` child; handles JS `const foo = () =>` too | node → `str` |
| `_is_js_arrow_function_assignment(node)` | is this `const foo = () => ...` really a function? | node → `bool` |

One chunk looks like this (this exact dict is what everything downstream consumes):

```python
{
    "filepath":   "C:\\...\\agent\\rag\\ingest.py",   # absolute, str
    "name":       "_get_node_name",                  # function/class name, str
    "type":       "function_definition",             # tree-sitter node type
    "start_line": 40,                                # 1-based
    "end_line":   64,
    "content":    "import tree_sitter_python as tspy\n...\n# --- END GLOBALS ---\n\n"
                  "# Class Context: CodeChunker\n\n    def _get_node_name(...):\n..."
}
```

The `content` is not *just* the function — it is:
1. The file's global/import statements (so the embedding sees what symbols the function uses).
2. `# --- END GLOBALS ---` separator.
3. `# Class Context: <name>` if the function lives inside a class (this is how the embedding
   knows `_get_node_name` belongs to `CodeChunker`).
4. The raw source bytes of the function node itself.

**Subtleties worth remembering:**
- Decorated functions work both ways: a *decorated function* is chunked as a whole; a
  *decorated class* is unwrapped so its inner methods become individual chunks
  (see `_extract_chunks`, the `decorated_definition` branch).
- The JS grammar is used for `.js`, `.ts`, `.jsx`, `.tsx`. **Deferred decision:** this is
  *JavaScript* grammar, not full **TypeScript** — TS-specific syntax will appear as `ERROR`
  nodes. We either add `tree-sitter-typescript` or drop `.ts`; not yet chosen.
- `self.chunks` accumulates on the instance and is never cleared — reusing one chunker
  instance across two runs would duplicate everything. Fine for one-shot CLI use; a known
  quirk for unit tests.

### Step 1.4 — `agent/rag/embedder.py` — text to vectors

**Role:** turn chunk text into 384-dim vectors, using **BAAI/bge-small-en-v1.5** (a small,
free, *local* ONNX model — no API, no cost, runs on CPU).

Class `VectorEmbedder`:

| Member | Meaning |
|---|---|
| `self.tokenizer` | tokenizes text exactly the way that BGE model will see it (needed to split accurately) |
| `self.embedder` | the FastEmbed model wrapper |
| `self.max_tokens = 500` | BGE's practical sequence limit is 512; we chunk just below |
| `self.overlap = 100` | sliding windows overlap by 100 tokens so a function's meaning isn't sliced in half |
| `self.stride` | computed: `max_tokens - overlap` — the distance between the start of one window and the next |

```python
def process_chunks(chunks: list[dict]) -> list[dict]:
    # 1. tokenize each chunk's content
    # 2. if <= 500 tokens: keep as-is
    #    else: sliding-window into several sub-chunks
    #    window 1: tokens 0..500, window 2: 150..650, window 3: 300..800, ...
    # 3. embed all texts in one batch call
    # 4. attach chunk["vector"] = [384 floats]
```

`embed_text(text)` does the same for a single string — it exists specifically to embed the
**bug report** at query time.

- **Data in:** `list[dict]` of chunk dicts (no vector yet)
- **Data out:** same dicts, each with a new key:
  `{"...all chunk keys...", "vector": [0.012, -0.337, ...] * 384}`
- **Deferred (medium) — sliding-window info loss:** a sub-chunk of a giant function loses
  the `start_line/end_line` of *that window* (they still describe the whole function) and
  each window is embedded as an independent duplicate of the same function → potentially
  all 6 windows of one function showing up as separate top-hits.

### Step 1.5 — `agent/rag/vector_store.py` — persistence

**Role:** the memory layer. Qdrant in **local disk mode** — it's a folder-backed
single-process vector store (SQLite-ish backend), no server, no Docker.

Class `QdrantDB`:

| Method | What it does |
|---|---|
| `__init__` | opens/creates `QdrantClient(path=".agent_db")` — *relative to CWD*, a known smell, deferred |
| `recreate_collection()` | nuclear option: delete `codebase` collection, create fresh with `VectorParams(size=384, distance=COSINE)` |
| `insert_chunks(chunks)` | wraps `recreate_collection()`, then converts each dict to a `PointStruct` with a random UUID, and `upsert`s the batch |
| `search(query_vector, limit)` | `client.query_points(...)` — the *current* qdrant API (the old `.search()` method was **removed** in qdrant-client 1.19) — returns only the payloads, i.e. the chunk dicts minus vectors |

- **A Qdrant "point" is:** `{"id": uuid4-string, "vector": [...384 floats], "payload": {chunk dict}}`
- **`Distance.COSINE`** because BGE vectors are unit-normalized — cosine similarity is the
  standard measure of semantic closeness for them.
- **Deferred (medium):** *collection existence check* (re-running index while the collection
  state is odd can crash), *deterministic UUIDs* for upsert-over-delete semantics, and *filter*
  support (e.g. "search only `.py` files").

The repo path this actually reads today: `.agent_db/collection/codebase/storage.sqlite`.

---

## 5. STAGE 2 — `acute fix "<bug>"`: the runtime pipeline

`agent/main.py` → `fix(bug)` orchestrates everything in this order:

### Step 2.1 — embed the request
```python
embedder = VectorEmbedder()
query_vector = embedder.embed_text(bug)   # str -> list[float] (384 numbers)
```
The *same* model as indexing, so the bug vector lives in the *same* space as the code
vectors. That is the whole trick: "closest vector" ≈ "most relevant code".

### Step 2.2 — retrieve
```python
db = QdrantDB()
closest_chunks = db.search(query_vector, limit=7)
```
- **Data out:** up to 7 chunk dicts (the raw payloads, exactly as inserted at index time).

### Step 2.3 — `agent/core/memory.py` — the gatekeeper

**Role:** two jobs before anything touches the LLM:
1. **Validation** — every raw payload dict is forced through a strict Pydantic schema:

```python
class CodeChunk(BaseModel):
    filepath: str = Field(min_length=1)
    name:     str = Field(min_length=1)
    content:  str = Field(min_length=1)
```
   Meaning: if Qdrant somehow returns a payload with a missing or empty `filepath` /
   `content`, that chunk is **dropped**, not sent to the LLM. Garbage-in is caught at the
   boundary. (One corrupted chunk *should* not blank out the whole context — hence
   `continue`, not `return`, on `ValidationError`.)

2. **Formatting** — surviving chunks become one XML document:

```xml
<codebase_context>

<chunk filepath="C:\...\ingest.py" name="_get_node_name">
import tree_sitter_python as tspy
...
    def _get_node_name(node, content_bytes: bytes) -> str:
        ...
</chunk>

<chunk filepath="..." name="...">
...
</chunk>

</codebase_context>
```
Why XML? It's a cheap, unambiguous, well-understood delimiter format that LLMs parse
extremely reliably, and it prevents chunk boundaries from blurring into each other.

- **Data in:** `list[dict]` (validated payloads)
- **Data out:** a single `str` (the XML context document)
- **Deferred (small+medium):** escape `filepath`/`name` values with `quoteattr` (they may
  contain quotes or `&`), and a hard token-budget cap on the final string.

### Step 2.4 — `agent/tools/coder.py` — the LLM brain

**Role:** turn "bug + XML context" into a *structured patch proposal*.

Two schemas drive the whole exchange:

```python
class CodePatch(BaseModel):
    filepath:        str   # which file
    target_function: str   # which function inside it
    explanation:     str   # why (Chain-of-Thought — must come first in the schema)
    fixed_code:      str   # the whole, raw, rewritten function

class MultiFilePatch(BaseModel):
    edits: list[CodePatch]  # a fix may span several functions/files
```

```python
def generate_patch(self, bug_report: str, xml_context: str,
                   previous_patch=None, failure_output: str | None = None) -> MultiFilePatch | None:
    # prompts.py supplies both message bodies:
    #   first attempt  -> first_attempt_prompt(bug, xml)
    #   retry          -> retry_prompt(bug, xml, previous_patch, failure_output)
    #     (carries: the previous patch with its code, the verbatim test-failure tail,
    #      and "do NOT return a functionally identical edit" instructions)
    for api_attempt in range(1, MAX_API_ATTEMPTS + 1):   # MAX_API_ATTEMPTS = 3
        try:
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=MultiFilePatch,   # strict structured output
                ),
            )
            patch_data = response.parsed
            if not patch_data or not patch_data.edits:
                logger.error("Gemini failed to return structured edits.")
                return None
            return patch_data
        except Exception as e:
            logger.error(f"Gemini API call failed (attempt {api_attempt}/{MAX_API_ATTEMPTS}): {e}")
            if api_attempt == MAX_API_ATTEMPTS:
                return None
            time.sleep(2 ** api_attempt)   # backoff: 2s, then 4s
```

Key design points:
- **`temperature=0.0`-style determinism is the goal for a code-fixer.**
- **`response_schema=MultiFilePatch`** — Gemini *must* emit JSON matching the model. This
  is why the class fields are ordered `explanation` before `fixed_code`: we are nudging the
  model to reason *before* writing code (poor-man's chain-of-thought).
- **Prompt templates live in `agent/core/prompts.py`** (moved out of coder.py in Phase 2):
  `SYSTEM_PROMPT`, `first_attempt_prompt`, `retry_prompt`. The retry prompt is what makes
  attempt 2 *informed* instead of a blind coin-flip.
- **Transient-failure backoff (landed after a real 503 killed an E2E run):** the API call
  retries up to 3 times with 2s/4s waits; structured-empty answers return `None` without
  retrying (they are not transient).
- Key-from-`.env`: `GEMINI_API_KEY` loaded via `python-dotenv`.
- On final failure → `logger.error` + `None`; the CLI exits cleanly.
- **Deferred (small):** fence-stripping fallback if `response.parsed` is ever empty.

### Step 2.5 — `agent/core/state.py` — the repair loop (`run_fix_loop`)

After `coder.generate_patch` returns a `MultiFilePatch`, `main.fix` forks:

- **`--dry-run`:** stays in `main.py` — for each edit calls
  `patcher.patch_file(..., dry_run=True)`: identical pipeline and gates, prints a unified
  diff, writes nothing, no loop.
- **Real run:** everything below happens inside `state.run_fix_loop(...)`, which owns the
  apply/test/retry cycle. `main.py` is no longer the orchestrator of application — it
  delegates, passes flags (`max_attempts`, `run_tests`), and maps the outcome to exit codes.

Per attempt, in order:

1. **Stop-gates first** (before any API call on attempts ≥ 2):
   - *No-progress:* if the freshly generated patch's `(filepath, target_function,
     fixed_code)` tuples are identical to the previous attempt's, stop (`no-progress`) —
     the `explanation` field is excluded (Gemini rephrases reasoning every call; only code
     determines behavior).
   - *Stuck-echo:* if the last two attempts failed with the same first-error-line
     (extracted with priority: exception lines containing `Error` → pytest's `FAILED`
     summary → the `assert` echo), stop (`stuck-on-same-error`). The priority order exists
     because pytest prints the test's *source echo* (which contains "assert") before the
     real exception — without it, two genuinely different failures look identical.
2. **Snapshot targets** (`patcher.snapshot_file`) — lazily, once per unique filepath,
   before this attempt's first edit. Run-level `.bak`s taken from a clean disk are what
   make rollback trustworthy (the contaminated-backup trap from Phase 1 applies here too).
3. **Apply edits** — sequential `patch_file(..., create_backup=False)`; any rejection
   (missing function / gate failure) → restore ALL snapshots → stop (`apply-rejected`).
   Invalid code still physically cannot reach the disk.
4. **Run the test suite** (`executor.TestRunner(".").run()`):
   - `passed` → success, return (`tests-passed`), patch stays on disk.
   - `ran=False` (no suite detected) → return (`no-tests`) with the patch *deliberately
     left applied* for manual review — "no tests" must never be reported as success.
   - `failed` → record failure tail, restore all snapshots (repo back to byte-identical
     pre-run state), loop to attempt N+1 where Gemini receives previous patch + failure.
5. **`--no-tests` mode:** skip step 4, apply once, return (`applied-untested`).

Outcome object: `RunOutcome(fixed, reason, attempts_used, records)` — reason vocabulary:
`tests-passed | no-tests | applied-untested | no-progress | stuck-on-same-error |
attempts-exhausted | apply-rejected | patch-generation-failed | snapshot-failed`.
Every attempt leaves an `AttemptRecord` (applied / test_ran / test_passed / timed_out /
failure_output / first_error_line) — the input Phase B's benchmark will count.

**Guarantee carried over from Phase 1 and still true:** every negative outcome except
`no-tests`/`applied-untested`/`tests-passed` restores the repo **byte-identical** to its
state at loop start.

### Step 2.6 — `agent/tools/executor.py` — the honest test runner

`TestRunner(repo_path)` with two methods:

- `detect_test_command()` — inspects the *repo's* footprint in priority order:
  `pytest.ini` / `setup.cfg` / `[tool.pytest` in `pyproject.toml` / a `tests/` dir with
  `test_*.py` → `[sys.executable, "-m", "pytest", "-q"]`; `package.json` with a `"test"`
  script → `[npm, "test"]` (resolved via `shutil.which` for Windows `npm.cmd`); anything
  else → `None` (an honest "nothing to run", not a guess). `sys.executable` (absolute
  interpreter path) instead of bare `pytest` avoids PATH roulette — a machine-wide
  `pytest.exe` from the wrong env cannot hijack the run.
- `run(timeout_seconds=60)` — subprocess with `cwd=` the repo, output captured, hard
  timer. `TimeoutExpired` → `timed_out=True` verdict (distinct from assertion failure);
  missing runner binary → `ran=False`. Output is trimmed to the last ~200 lines (pytest
  prints failures at the end; this tail is what a retry prompt needs).

`TestResult` dataclass: `ran / passed (exit code 0) / command / output (tail) /
elapsed_seconds / timed_out`. Four distinct verdicts — passed, failed, timed-out,
didn't-run — because each has a different remedy downstream.

### Step 2.7 — `harness/recorder.py` — per-run evidence

`record_run(outcome, bug_report, no_tests)` writes `runs/<timestamp>.json`:
timestamp, bug, mode flags, end reason, attempts_used, and per-attempt records
(including truncated failure tails). `runs/` is git-ignored. `record_run` never raises —
a recording failure logs a warning and never affects the repair result. Phase B's
benchmark counts pass rates from these files, not from recollection.

### Step 2.8 — `agent/tools/patcher.py` — the surgeon behind four gates

**Role:** perform the byte-exact edit with zero collateral damage — and since Phase 1's
Decision 3, never write code that isn't syntactically provable.

```python
def patch_file(self, file_path: str, target_function: str, new_code: str,
               create_backup: bool = True, dry_run: bool = False) -> bool
```

What happens, in order:

1. (Optional) `shutil.copy2` → `.bak` do-over insurance (skipped inside transactions, which
   snapshot separately; skipped entirely in dry-run).
2. `open(path, "rb").read()` — the file as **raw bytes** (encodings and CRLF/LF mix-ups are
   dodged by never treating it as text).
3. Parse the AST from bytes; find the target node by S-expression query on the function name;
   read its exact `start_byte` / `end_byte`. (Fail → `False` = "no such function".)
4. **Gate 1 — dedent:** `textwrap.dedent` strips Gemini's uniform leading indent (the
   double-indent trap). Uneven input survives unchanged — it becomes Gate 2's problem.
5. **Gate 2 — ast.parse the incoming code:** Python's official parser judges the cleaned
   text. Logically broken indentation / bad syntax → the edit is REJECTED (never
   auto-repaired), `False` propagates → transaction rolls everything back.
6. **Gate 3 — re-indent from the file's own convention:** `_line_prefix_at` walks the file's
   bytes backward from the splice point (spaces/tabs until newline) to find the indentation
   the file already owns. First line goes in unchanged (it meets that prefix); every other
   non-blank line gets the prefix ADDED in front of its (already-correct relative) indent.
7. **Gate 4 — parse the entire spliced file in memory** (`ast.parse` of the full candidate
   bytes). Fail → reject with line info; nothing was written. Only a fully-parseable file
   proceeds → the disk write (`open(path, "wb")`).
8. **Dry-run fork** sits just before that write: instead of writing, `difflib.unified_diff`
   renders before/after and it returns `True`. Preview shares the write path exactly, so it
   cannot lie.
9. (If a `.bak` was made and not dry-run) launch `code --diff <.bak> <file>` for eyeballing.

Guarantees after Phase 1:
- **Surgical:** nothing outside the replaced byte range changes.
- **Valid code only:** all four gates pass before any write; invalid code physically cannot
  reach the disk.
- **Indentation-correct:** the function lands at the file's own depth (verified by the
  suite asserting actual line depths, not just "it compiles").
- **All-or-nothing in batches:** transaction semantics live in `main.py`; the patcher also
  self-uses snapshot/restore via `snapshot_file` / `restore_file` (added for transactions).
- **Permanently tested by `verify_phase1.py`** (6/6) — plus an old `test_patcher.py`
  round-trip on `dummy_target.py`. And once end-to-end with real Gemini in a sandbox repo
  (JWT two-dots bug fixed correctly, neighbor function untouched).

Known quirk (documented, unfixed): if the original function had decorators, they live OUTSIDE
the spliced byte range (preserved). If Gemini echoes the decorator inside `fixed_code`, it
would duplicate — nobody hit it yet; decision pending. Also: `.bak` files are overwritten on
the next patch of the same file (they represent "state right before THIS run"), and the
patcher is Python-only today (`.py`).

---

## 6. Files map (who owns what)

```
agent/
├── __init__.py
├── main.py               CLI (Typer). Commands: index, fix (--dry-run / --attempts / --no-tests).
│                         Owns: embed → search → XML → first Gemini call → dry-run branch.
│                         Delegates the real run to state.run_fix_loop; maps reason → exit code.
├── rag/                  STAGE 1 — everything about "reading the codebase"
│   ├── scanner.py        which files exist (git-tracked, right extension)
│   ├── ingest.py         split them into AST-aware chunks
│   ├── embedder.py       text -> 384-dim vectors (local, free)
│   ├── vector_store.py   persist + search vectors (Qdrant local, query_points)
│   └── retriever.py      (EMPTY duplicate of vector_store — cleanup candidate)
├── core/                 the "model / orchestration" layer
│   ├── memory.py         Pydantic validation + XML context assembly
│   ├── prompts.py        SYSTEM_PROMPT + first_attempt_prompt + retry_prompt (Phase 2)
│   ├── planner.py        (EMPTY placeholder — future agentic planner, Phase C)
│   ├── state.py          run_fix_loop: attempts, stop-gates, snapshot/restore reuse,
│   │                     verdict mapping, AttemptRecord/RunOutcome dataclasses
│   └── coder.py          (EMPTY duplicate of tools/coder.py; keep ONE)
├── tools/                concrete "effectors" that touch the world
│   ├── coder.py          Gemini client + schemas + prompts.py wiring + 3-attempt backoff
│   ├── patcher.py        byte-splice surgery BEHIND FOUR GATES + snapshot/restore + dry-run diff
│   └── executor.py       TestRunner: command detection, subprocess + 60s timer,
│                         TestResult verdict (ran/passed/timed_out/output-tail)
harness/
├── recorder.py           record_run: runs/<timestamp>.json per fix() run (git-ignored)
└── runner.py             (EMPTY placeholder — Phase B benchmark driver)
verify_phase1.py          6-case guarantee suite (imports ONLY the patcher; no LLM)
loop_test.py              (temp) 5-case repair-loop sandbox — see commit history for location
test_patcher.py           legacy scratch round-trip (superseded by verify_phase1.py)
```

**Cleanup todos (decision needed, not urgent):** `core/coder.py` vs `tools/coder.py` and
`rag/retriever.py` vs `rag/vector_store.py` — delete the empty duplicates, keep one source
of truth.

---

## 7. Status: what works, what's deferred

### Verified working (post-Phase 2)
| Capability | Evidence |
|---|---|
| All 8 modules compile + import | `py_compile` + `import agent.main` pass |
| `uv sync` clean | `google-genai 2.33`, `python-dotenv`, `pytest` (dev) + everything resolves |
| `acute index` end-to-end | logs: `Successfully indexed N chunks into the database` |
| `QdrantDB.search` via `query_points` | returns real chunks from `.agent_db` |
| `ContextManager.assemble_xml_context` | produced valid `<codebase_context>` from live chunks |
| **Syntax + indentation gates (D3)** | `verify_phase1.py` cases 2–4: echo lands at correct depth; broken/garbage rejected, disk untouched |
| **`--dry-run` (D4)** | case 6: clean diff verdict, file byte-identical after |
| **6/6 guarantee suite (D5)** | `uv run python verify_phase1.py` — all pass |
| **TestRunner executor (Phase 2)** | 4 sandbox cases: pass, fail (tail carries the real error text), no-test-signal → `ran=False`, 30s-hanging test killed at 2s (`timed_out`) |
| **Repair loop (Phase 2)** | 5 sandbox cases (real patcher gates + real pytest): fail-then-fix (2 attempts), identical patch → `no-progress` untouched repo, 3 distinct failures → `attempts-exhausted` untouched repo, `--no-tests` → applied-untested, test-less repo → honest `no-tests` |
| **Run recorder (Phase 2)** | `runs/<timestamp>.json` written by live runs, schema validated |
| **API backoff (Phase 2 close-out)** | landed after a real Gemini 503 killed an E2E run; 3 attempts, 2s/4s |
| **Real Gemini E2E ×2** | temp git repo: JWT two-dots bug (attempt 1) + median even-length bug (attempt 1); both `tests-passed`, runs recorded. The live *retry* branch is stub-proven only (5/5 loop suite) — Phase B exercises it statistically |

### Deferred (small)
- XML-escape `filepath`/`name` in `memory.py` (`quoteattr`)
- Token-budget guard in context assembly
- Drop `.ts` or add `tree-sitter-typescript` (`ingest.py` currently parses TS with the JS grammar)
- Decorator-echo duplication guard in `patcher.py`
- `.bak` timestamping (currently overwritten per run)
- Fence-stripping fallback in `coder.py`

### Deferred (medium — next build targets, not junk to ignore)
| # | Item | Why it matters |
|---|---|---|
| 1 | Scoreboard: seed-bug suite + runner + pass-rate table | Phase B — the instrument every later decision is judged by |
| 2 | Agentic tool loop (`read_file`/`grep`/`apply_edit` as model-called tools) | Phase C — the big upgrade |
| 3 | Sliding-window chunk info loss (`embedder.py`) | retrieval quality (Phase D) |
| 4 | Collection existence check + absolute `.agent_db` path | robustness when re-running |
| 5 | Delta-sync (file hashing, deterministic IDs, delete-by-filepath) | speed of re-indexing |

### Not started (0%)
- Agentic tool-calling loop (Phase C)
- Benchmark/scoreboard (Phase B)
- README / architecture diagram / CI

---

## 8. How to run

```powershell
# from repo root
uv sync                          # install deps
acute index                      # (re)build .agent_db from git-tracked *.py/js/ts files
acute fix "your bug here"        # retrieve -> assemble -> Gemini -> repair loop (tests + retry)
acute fix "your bug here" --dry-run   # same pipeline, prints diff, writes NOTHING
acute fix "..." --attempts 5     # widen the retry budget (default 3, max 5)
acute fix "..." --no-tests       # apply the first patch and stop (no test run)
uv run python verify_phase1.py   # 6/6 guarantee suite (no LLM, seconds, temp folders)
```

You need `GEMINI_API_KEY` in `.env` (already present, git-ignored).

For the legacy patcher-only check:
```powershell
uv run python test_patcher.py
```
It splices a known-good fake LLM response into `dummy_target.py` and verifies
`do_not_touch_this` survives the surgery. (Superseded by `verify_phase1.py`, kept for the
raw round-trip.)

---

## 9. Debugging cheat-sheet

| Symptom | Likely cause |
|---|---|
| `ModuleNotFoundError: google.genai` | `uv sync` not run after dependency changes |
| `Qdrant returned 0 matching chunks` | never ran `acute index`, or CWD is outside the repo (DB path is relative!) |
| chunk has empty `name` | function node was anonymous (lambda/arrow) — fallthrough returns `"unknown"` |
| lots of duplicate chunk names in search | sliding-window splitting a giant function into multiple sub-chunks |
| TS file chunks look mangled with `ERROR` nodes | `.ts` is parsed with JS grammar (deferred decision) |
| patcher says "Could not find function X" | name exists in file but not as a `function_definition` (method? arrow fn? rename? check memory of `-target_function` field vs `target_function`) |
