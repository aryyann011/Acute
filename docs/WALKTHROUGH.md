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

and Acute runs four stages against your repository:

1. **Retrieval** — from a pre-built local database of your code, find the snippets most
   *semantically similar* to your bug description (not keyword matching — meaning matching).
2. **Context assembly** — validate those snippets and wrap them in one clean XML document.
3. **Generation** — send bug + context to an LLM (Gemini 2.5 Flash) and receive a *strictly
   structured JSON* patch describing exactly which function in which file must be rewritten.
4. **Patching** — locate that function in the real file by its **byte position in the AST**,
   cut the old bytes, splice in the new bytes, keep a `.bak` backup, show a diff.

The value proposition: the LLM never sees (and never rewrites) your whole file, and could
never hallucinate the "rest" of the file — it only rewrites one function and Acute does the
surgery.

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
      │ QdrantDB      │ ────────────────▶ │ memory.py    │ ──────▶ │ Gemini   │
      │ .search()     │                   │ (validate +  │         │ (coder)  │
      └───────────────┘                   │  wrap XML)   │         │ JSON out │
                                          └──────────────┘         └────┬─────┘
                                                                        │ JSON
                                                                        ▼
                                             ┌──────────────────────────────────┐
                                             │ patcher.py                       │
                                             │ parse file → find function node  │
                                             │ → exact start_byte..end_byte     │
                                             │ → .bak backup → splice bytes     │
                                             └──────────────────────────────────┘
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
def generate_patch(self, bug_report: str, xml_context: str) -> MultiFilePatch | None:
    system_prompt = "You are a senior software engineer... CRITICAL RULES: return only raw code..."
    user_prompt   = f"# Bug Report\n{bug_report}\n\n# Codebase Context (XML)\n{xml_context}"
    response = self.client.models.generate_content(
        model="gemini-2.5-flash",
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=MultiFilePatch,   # strict structured output
        ),
    )
    return response.parsed   # Pydantic model, or None on any failure
```

Key design points:
- **`temperature=0.0`-style determinism is the goal for a code-fixer.**
- **`response_schema=MultiFilePatch`** — Gemini *must* emit JSON matching the model. This
  is why the class fields are ordered `explanation` before `fixed_code`: we are nudging the
  model to reason *before* writing code (poor-man's chain-of-thought).
- Key-from-`.env`: `GEMINI_API_KEY` loaded via `python-dotenv`.
- On any exception → `logger.error` + `None`; the CLI exits cleanly.
- **Deferred (medium):** retry with backoff on transient 429/503 errors, and fence-stripping
  fallback if `response.parsed` is ever empty.

### Step 2.5 — main-loop displays proposals
```python
for edit in patch.edits:
    logger.info(f"Proposed edit -> {edit.filepath} :: {edit.target_function} ...")
```
Deference note: applying edits automatically **is the deliberate next build step**; it is
not wired yet (see §7).

### Step 2.6 — `agent/tools/patcher.py` — the surgeon

**Role:** perform the byte-exact edit with zero collateral damage. Signature (restored):

```python
def patch_file(self, file_path: str, target_function: str, new_code: str) -> bool
```

Step by step, what actually happens on disk:

1. `shutil.copy2(file_path, file_path + ".bak")` — the do-over insurance.
2. `open(path, "rb").read()` — get the file as **raw bytes** (never as text; encodings and
   mid-file CRLF/LF mix-ups are dodged entirely by working on bytes).
3. `self.parser.parse(file_bytes)` — build the AST directly from bytes.
4. Find the target node with a named S-expression query:
   ```
   (function_definition name: (identifier) @name
     (#eq? @name "verify_jwt"))
   @target_node
   ```
5. Extract the exact byte span: `start_byte` / `end_byte` from `QueryCursor.captures(...)`.
6. Splice:
   ```python
   patched_bytes = file_bytes[:start_byte] + new_code.encode("utf-8") + file_bytes[end_byte:]
   ```
7. `open(path, "wb").write(patched_bytes)` — whole file rewritten with the new slice in place.
8. Launch `code --diff <.bak> <file>` so you can eyeball the change.

Guarantees this approach gives you:
- **Surgical:** nothing outside `[start_byte:end_byte)` changes, not even whitespace.
- **Self-healing input handling:** because the first byte of a function node is at the `def`
  keyword, whatever indentation the original had at that position is *preserved by the
  splice itself* — only lines *inside* `new_code` depend on the LLM getting indentation
  right (that normalizer is deferred).
- **Verified by `test_patcher.py`** on `dummy_target.py`: after patching, `do_not_touch_this`
  must still be intact — and it is. Round-trip verified.

- **Deferred (medium):** in-memory syntax validation of `new_code` before writing to disk,
  indentation normalization, Python `.pyc` sanity check after patch, class-level patching,
  `.bak` overwrite-on-second-patch semantics.

---

## 6. Files map (who owns what)

```
agent/
├── __init__.py
├── main.py               CLI (Typer). Commands: index, fix. Orchestrator.
├── rag/                  STAGE 1 — everything about "reading the codebase"
│   ├── scanner.py        which files exist (git-tracked, right extension)
│   ├── ingest.py         split them into AST-aware chunks
│   ├── embedder.py       text -> 384-dim vectors (local, free)
│   ├── vector_store.py   persist + search vectors (Qdrant local)
│   └── retriever.py      (EMPTY placeholder — planned thin wrapper around QdrantDB; not used)
├── core/                 the "model / orchestration" layer
│   ├── memory.py         Pydantic validation + XML context assembly
│   ├── prompts.py        (EMPTY placeholder — prompts will be centralized here)
│   ├── planner.py        (EMPTY placeholder — future multi-step planner)
│   ├── state.py          (EMPTY placeholder — future agent state machine)
│   └── coder.py          (EMPTY — duplicate of tools/coder.py; decide later, keep ONE)
├── tools/                concrete "effectors" that touch the world
│   ├── coder.py          Gemini client + MultiFilePatch schema
│   ├── patcher.py        tree-sitter byte-splice surgery on real files
│   └── executor.py       (2-line stub — will become the test-runner adapter)
harness/
└── runner.py             (EMPTY placeholder — future evaluation harness)
```

**Cleanup todos (decision needed, not urgent):** `core/coder.py` vs `tools/coder.py` — keep
one. The five `EMPTY placeholders` come alive exactly when Phase 2 (`state machine` +
test-runner) starts.

---

## 7. Status: what works, what's deferred

### Verified working (as of today)
| Capability | Evidence |
|---|---|
| All 8 modules compile + import | `py_compile` + `import agent.main` pass |
| `uv sync` clean | `google-genai 2.33`, `python-dotenv` + everything resolves |
| `acute index` end-to-end | logs: `Successfully indexed N chunks into the database` |
| `QdrantDB.search` via `query_points` | returns real chunks from `.agent_db` |
| `ContextManager.assemble_xml_context` | produced valid `<codebase_context>` from 3 live chunks |
| `patcher.patch_file` round-trip | `test_patcher.py` → file patched, `.bak` created, non-target function intact |
| Embedding at query time | `embed_text` produced a 384-dim vector from raw text |

### Deferred (small)
- XML-escape `filepath`/`name` in `memory.py` (`quoteattr`)
- Token-budget guard in context assembly
- Drop `.ts` or add `tree-sitter-typescript` (`ingest.py` currently parses TS with the JS grammar)
- `CodeChunk` schema mismatch check: chunk key is `name` — confirm it matches everywhere

### Deferred (medium — these are the *next* build targets, not junk to ignore)
| # | Item | Why it matters |
|---|---|---|
| 1 | Wire `fix()` to actually *apply* `patch.edits` + auto-run tests | makes it a real agent, not a proposal generator |
| 2 | Sliding-window chunk info loss (`embedder.py`) | retrieval quality |
| 3 | Collection existence check + absolute `.agent_db` path | robustness when re-running |
| 4 | Pre-write syntax validation + indentation normalization in `patcher.py` | correctness of surgery |
| 5 | Retry/backoff (tenacity) + JSON-fence stripping in `coder.py` | reliability against 429/503 |
| 6 | Delta-sync (file hashing, deterministic IDs, delete-by-filepath) | speed of re-indexing |
| 7 | Markdown-fence defense at the payload level | prompt hygiene |
| 8 | Test-runner adapter (`executor.py`), state machine (`state.py`) | Phases 2–3 |

### Not started (0%)
- Agentic self-healing loop (`state.py`, `executor.py`)
- Evaluation harness (`harness/runner.py`)
- README / architecture diagram / CI

---

## 8. How to run

```powershell
# from repo root
uv sync                     # install deps
acute index                 # (re)build .agent_db from git-tracked *.py/js/ts files
acute fix "your bug here"   # retrieve -> assemble -> LLM-judge -> show proposed edits
```

You need `GEMINI_API_KEY` in `.env` (already present, git-ignored).

For the patcher-only test:
```powershell
uv run python test_patcher.py
```
It splices a known-good fake LLM response into `dummy_target.py` and verifies
`do_not_touch_this` survives the surgery.

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
