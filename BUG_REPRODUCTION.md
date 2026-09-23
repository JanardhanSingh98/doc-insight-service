# Bug Reproduction Guide

How to make every seeded defect in `doc-insight-service` visible and measurable.

> ⚠️ This file and `repro_bugs.py` are an **answer-key artifact**. Do not ship them
> in the candidate-facing copy of the repo.

---

## 1. Setup

The virtualenv lives one level above the service directory.

```bash
cd doc-insight-service
source ../.venv/bin/activate
```

If you don't have one yet:

```bash
python -m venv ../.venv
source ../.venv/bin/activate
pip install -e ".[dev]"
```

### Why the test suite used to fail to import

`pyproject.toml` sets `testpaths = ["tests"]` but `tests/` has no `__init__.py`, so
pytest put `tests/` on `sys.path` instead of the repo root and `from app.main import app`
blew up with `ModuleNotFoundError: No module named 'app'`.

This is **not** a seeded bug. It is fixed by the `pythonpath` line:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths    = ["tests"]
pythonpath   = ["."]
```

---

## 2. The three ways to surface bugs

| Method | Bugs surfaced | Command |
|---|---|---|
| Existing test suite | 2 of 23 | `python -m pytest -q` |
| Reproduction harness | **23 of 23** | `python repro_bugs.py` |
| Manual / live server | varies | see §5 |

### 2.1 Existing test suite

```bash
python -m pytest -q
# 2 failed, 5 passed, 1 skipped
```

Only BUG-18 (`test_tags_do_not_leak_between_documents`) and BUG-02
(`test_search_returns_json_serialisable_body`) fail. Everything else is invisible
because **BUG-24 rewrites unhandled exceptions into `HTTP 200 {"data": null}`**, so
broken endpoints look healthy from the outside.

### 2.2 Reproduction harness (recommended)

```bash
python repro_bugs.py                 # run every probe
python repro_bugs.py BUG-07          # run a single probe
python repro_bugs.py BUG-07 BUG-20   # run a subset
python -W ignore::ResourceWarning repro_bugs.py   # quieter output
```

Expected result:

```
==============================================================================
  23/23 bugs reproduced
==============================================================================
```

Each probe prints the bug ID, priority, source location, and concrete evidence —
a timing measurement, an object count, or a real traceback.

#### How the harness works

- **No lifespan.** The ASGI client is built with
  `ASGITransport(app=app, raise_app_exceptions=False)` and is *not* wrapped in
  `LifespanManager`. Workers never start, so queued jobs stay queued and background
  side effects remain observable.
- **`raise_app_exceptions=False`** mirrors a real uvicorn server: the app's own
  exception handler runs and returns its (wrong) `200 {"data": null}` body instead of
  the probe crashing. This is what makes BUG-24's masking effect visible.
- **Three evidence styles**, depending on the bug:
  - *Behavioural* — hit the API and assert on the observable outcome (BUG-01, 05, 07, 20, 21).
  - *Instrumented* — monkeypatch a constructor and count objects (BUG-09, BUG-13).
  - *Static* — `inspect.getsource()` on the offending function to prove the pattern
    is present when the runtime effect is a non-event, e.g. "a timeout that never fires"
    (BUG-03, 11, 12, 14, 15, 16).

Probes are registered with `@probe(bug_id, severity, location)` and each returns
`(reproduced: bool, evidence: str)`.

---

## 3. Bug index

23 defects across 5 categories. IDs are non-contiguous (they run up to BUG-24).

### Category A — Blocking the event loop

| ID | Sev | Location | Symptom |
|---|---|---|---|
| BUG-01 | P1 | `app/api/documents.py:create_document`, `app/main.py:timing_middleware` | `time.sleep(2)` / `time.sleep(0.5)` freeze the whole loop |
| BUG-08 | P1 | `app/services/embeddings.py:embed_text` | CPU-bound `_hash_embed` (2000 × 32 iterations) runs on the loop |
| BUG-15 | P2 | `app/api/documents.py:upload_document` | Unbounded `await file.read()` + blocking `open().write()` |

### Category B — Coroutines used incorrectly

| ID | Sev | Location | Symptom |
|---|---|---|---|
| BUG-02 | P1 | `app/api/search.py:search` | `cached = cache.get(key)` missing `await`; coroutine is always truthy |
| BUG-05 | P1 | `app/api/documents.py:upload_document` | `add_task(ingest_document(id))` passes a coroutine, not a callable |
| BUG-10 | P2 | `embeddings.warm_up`, `worker._process_job` | `asyncio.sleep(...)` without `await` — retry backoff never happens |
| BUG-21 | P2 | `app/utils/chunking.py:summarize_sync` | `asyncio.run()` called from inside a running loop |
| BUG-22 | P2 | `app/api/jobs.py:ingest_now` | Coroutine handed to `loop.run_in_executor` |

### Category C — Concurrency control

| ID | Sev | Location | Symptom |
|---|---|---|---|
| BUG-03 | P1 | `ingestion.ingest_document`, `search.batch_search` | `asyncio.gather` with no ceiling and no `return_exceptions` |
| BUG-07 | P1 | `app/store.py:save_chunks` | Check-then-act across an `await`; `self._lock` declared but never used |
| BUG-13 | P1 | `ingestion._embed_chunk` | `asyncio.Semaphore` constructed *inside* the per-chunk function |
| BUG-19 | P2 | `embeddings.embed_batch`, `vector_store.rerank` | Sequential awaits; rerank re-embeds every hit then discards the result |
| BUG-20 | P1 | `store.delete_document`, `ingestion.reindex_all` | `del` while iterating `.items()` |

### Category D — Background processing & lifecycle

| ID | Sev | Location | Symptom |
|---|---|---|---|
| BUG-04 | P2 | `app/api/jobs.py:trigger_reindex` | `create_task()` with no strong reference — task can be GC'd |
| BUG-11 | P1 | `app/workers/worker.py:worker_loop` | `break` out of `while True` on any exception — pool dies silently |
| BUG-12 | P2 | `app/workers/worker.py:worker_loop` | `get_nowait()` busy-poll; `task_done()` skipped on exception |
| BUG-16 | P1 | `app/workers/worker.py:worker_loop` | `except BaseException` swallows `CancelledError` — shutdown hangs |
| BUG-17 | P1 | `worker.stop_workers`, `main.lifespan` | `_worker_tasks.clear()` without cancel/await; LLM client never closed |

### Category E — Resource management & error handling

| ID | Sev | Location | Symptom |
|---|---|---|---|
| BUG-06 | P3 | `app/utils/chunking.py:chunk_text` | `separators.append(" ")` mutates the default list on every call |
| BUG-09 | P1 | `app/services/llm_client.py:complete` | New `httpx.AsyncClient` per call, never closed — FD exhaustion |
| BUG-14 | P2 | `app/services/llm_client.py:complete` | No timeout, no retry, no backoff |
| BUG-18 | P2 | `app/store.py:normalize_tags` | Mutable default `base_tags=["ingested"]` accumulates across calls |
| BUG-24 | P1 | `main.unhandled_exception_handler`, `LLMClient.health` | Returns HTTP 200 for every unhandled exception; bare `except:` |

**BUG-24 is the amplifier.** It masks BUG-02, BUG-20, BUG-21, and BUG-22. Fix it first
and four other bugs become visible as 500s in normal use.

---

## 4. Per-bug reproduction

Run any of these with `python repro_bugs.py <BUG-ID>` for the automated version.
The snippets below are the manual equivalents.

### BUG-01 — `time.sleep()` on the event loop

```python
import asyncio, time
from httpx import ASGITransport, AsyncClient
from app.main import app

async def main():
    t0 = time.perf_counter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://p") as c:
        async def health():
            await asyncio.sleep(0.05)      # let the POST start first
            await c.get("/health")
            return time.perf_counter() - t0
        _, at = await asyncio.gather(
            c.post("/documents", json={"title": "block", "content": "x " * 200}),
            health(),
        )
    print(f"/health completed {at*1000:.0f}ms after it was issued")

asyncio.run(main())
```

**Observed:** `/health completed 2530ms after it was issued.`
Key detail: measure from when the request was *issued*, not from when `await c.get()`
returns — the loop is frozen, so a naive stopwatch reports ~2ms.

### BUG-08 — CPU-bound work on the loop

Run a heartbeat task that records the gap between consecutive `asyncio.sleep(0)`
resumptions while 20 `embed_text()` calls run.

**Observed:** `20 embed_text() calls took 119ms and the loop went 119.5ms without yielding.`

Gotcha: `embed_text` is `async` but contains **no `await`**, so awaiting it never yields.
The heartbeat can't record the stall until you `await asyncio.sleep(0.01)` *after* the
CPU loop finishes.

### BUG-15 — unbounded read + blocking write

Static: `inspect.getsource(upload_document)` shows `await file.read()` with no size cap
and a synchronous `open(...).write(...)`.

**Observed:** `unbounded file.read()=True, blocking open().write()=True`

### BUG-02 — missing `await` on the cache

```python
from app.services.cache import get_cache
import inspect

unawaited = get_cache().get("k")
print(inspect.iscoroutine(unawaited), bool(unawaited))   # True True
unawaited.close()
```

**Observed:** coroutine is always truthy, so the cache-hit branch fires every time and
`search()` returns a coroutine object in the response body. Secondary defect: `if cached:`
should be `if cached is not None:` — an empty result list is a legitimate cached value.

### BUG-10 — `asyncio.sleep()` without `await`

```python
import warnings, asyncio
from app.services.embeddings import warm_up

with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    asyncio.run(warm_up())
print([str(x.message) for x in w])
```

**Observed:** `["coroutine 'sleep' was never awaited"]`. Same pattern in
`worker._process_job`, where it means retries fire instantly with no backoff.

### BUG-05 — coroutine passed to `add_task`

```bash
curl -F "file=@README.md" http://127.0.0.1:8000/documents/upload
curl http://127.0.0.1:8000/documents/<id>
```

**Observed:** upload returns `202` but `chunk_count=0`. The document is never indexed —
`add_task` needs `(callable, *args)`, not an already-created coroutine.

### BUG-21 — `asyncio.run()` inside a running loop

```bash
curl -i http://127.0.0.1:8000/documents/<id>/preview
```

**Observed:** `RuntimeError: asyncio.run() cannot be called from a running event loop`
in the server log — but the client sees `HTTP 200 {"data": null}` thanks to BUG-24.

### BUG-22 — coroutine passed to `run_in_executor`

```bash
curl -i http://127.0.0.1:8000/jobs/documents/<id>/ingest-sync
```

**Observed:** `coroutine passed to run_in_executor=True; endpoint returned 200 {'data': None}`.
`TypeError: 'coroutine' object is not callable` is masked by BUG-24.

### BUG-03 — unbounded `gather`

Static: `ingest_document` gathers one task per chunk with no semaphore and no
`return_exceptions=True`. A 10k-chunk document opens 10k concurrent embeddings; one
failure cancels the rest and loses the other results.

**Observed:** `unbounded fan-out in ingest_document=True, return_exceptions absent everywhere=True`

### BUG-13 — semaphore created per call

Subclass `asyncio.Semaphore` with a counting `__init__`, patch it into
`app.services.ingestion`, then embed 3 chunks concurrently.

**Observed:** `3 distinct Semaphore objects created for 3 chunks` — each call gets full
permits, so `max_concurrent_embeddings` is dead config.

Gotcha: hold **strong references** to the created semaphores. Collecting `id()` alone
gives false negatives because CPython recycles ids after GC.

### BUG-19 — sequential batch + wasted rerank

**Observed:** `embed_batch(4)=25ms vs single=6ms` — latency is the sum, not the max.
`rerank` re-embeds every hit and then throws the vector away via `+ 0 * len(chunk_vector)`.

### BUG-07 — check-then-act race

Run several `save_chunks()` calls concurrently against the same store.

**Observed:** `total_chunks_indexed=7, expected=12 (5 lost)`. The counter is read, the
coroutine yields at `await asyncio.sleep(0)`, and the write lands on a stale value.
`self._lock` exists on the store but is never acquired.

### BUG-20 — mutating a dict while iterating it

```python
store.delete_document(doc_id)
```

**Observed:** `RuntimeError: dictionary changed size during iteration`. Same class of
bug in `reindex_all`, which iterates `store.documents` while concurrent inserts land.

### BUG-11 — worker pool dies on first error

Static: the `except` clause inside `while True` ends in `break`.

**Observed:** after one transient error the pool is gone, while `/health` still reports
green. Jobs queue up forever with no alert.

### BUG-16 — `CancelledError` swallowed

Start a worker task, cancel it, await it.

**Observed:** `except BaseException=True, re-raises CancelledError=False, task cancelled cleanly=False`.
`CancelledError` inherits from `BaseException`, not `Exception`, so a broad
`except BaseException` catches shutdown signals and the process hangs.

### BUG-17 — shutdown leaks tasks and sockets

```python
await start_workers()
await stop_workers()
```

**Observed:** `2/2 worker tasks still running after stop_workers(); llm client closed in lifespan=False`.
`stop_workers()` only calls `_worker_tasks.clear()` — it never cancels or awaits them.

### BUG-12 — busy-poll queue + broken `join()`

**Observed:** `busy-polling=True (adds up to 50ms latency per job); task_done() in finally=False`.
Using `get_nowait()` + `asyncio.sleep(0.05)` instead of `await queue.get()` burns CPU and
adds latency; skipping `task_done()` on the exception path breaks `queue.join()` permanently.

### BUG-04 — fire-and-forget task

**Observed:** `asyncio.create_task(reindex_all())` result is discarded. The loop keeps only
a weak reference, so the task can be garbage-collected mid-flight, and any exception
surfaces only as `Task exception was never retrieved` at interpreter shutdown.

### BUG-09 — new HTTP client per call

Patch `httpx.AsyncClient` with a counting subclass and call `complete()` three times.

**Observed:** `3 new AsyncClient objects created for 3 complete() calls`. The pooled
`self._client` is never used and the per-call clients are never closed — you can see the
leak directly as `ResourceWarning: unclosed <socket.socket ...>` at the end of a run.

### BUG-14 — no timeout or retry

Static inspection of `complete()`.

**Observed:** `timeout configured=False, retry/backoff present=False` — a hung upstream
ties up the request forever.

### BUG-24 — exceptions rewritten as HTTP 200

```bash
curl -i http://127.0.0.1:8000/documents/<id>/preview
```

**Observed:** `/preview raised internally but returned HTTP 200 {'data': None}; bare 'except:' in LLMClient.health=True`.

This is the highest-leverage fix: it hides BUG-02, BUG-20, BUG-21 and BUG-22 from both
clients and monitoring.

### BUG-18 — mutable default argument

```python
from app.store import normalize_tags
print(normalize_tags(["one"]))   # ['ingested', 'one']
print(normalize_tags(["two"]))   # ['ingested', 'one', 'two']  ← leaked
```

**Observed:** document B inherits document A's tags.

Gotcha: because the default list is module-global state, reset it before probing or the
evidence is polluted by earlier calls in the same process.

### BUG-06 — growing default separator list

```python
from app.utils.chunking import chunk_text
for _ in range(5):
    chunk_text("some text")
print(chunk_text.__defaults__)
```

**Observed:** `separators default grew 3 -> 8 after 5 calls` — an unbounded memory leak
and an ever-growing split cost.

---

## 5. Reproducing against a live server

Some bugs (real socket leaks, the mock LLM gateway, true request concurrency) only show
up against a listening server. `settings.llm_base_url` defaults to
`http://127.0.0.1:8000/mock`, which the app itself serves, so the server must actually be
bound to port 8000.

```bash
uvicorn app.main:app --port 8000
```

Then in another shell:

```bash
# BUG-01 — loop freeze
curl -s -X POST localhost:8000/documents \
  -H 'content-type: application/json' \
  -d '{"title":"block","content":"x x x"}' &
time curl -s localhost:8000/health          # >2s

# BUG-05 — upload accepted but never indexed
ID=$(curl -s -F "file=@README.md" localhost:8000/documents/upload | jq -r .data.id)
curl -s localhost:8000/documents/$ID | jq .data.chunk_count   # 0

# BUG-21 / BUG-24 — internal error returned as 200
curl -i localhost:8000/documents/$ID/preview

# BUG-09 — FD leak
lsof -p $(pgrep -f 'uvicorn app.main:app') | grep -c TCP   # climbs per request
```

Watch the server log while doing this: the tracebacks are there even though every HTTP
response says `200`.

---

## 6. Suggested fix order

1. **BUG-24** — stop masking failures. Four other bugs become visible immediately.
2. **BUG-01, BUG-08** — get blocking calls off the event loop (`asyncio.to_thread`).
3. **BUG-02, BUG-05, BUG-21, BUG-22, BUG-10** — the missing/misused `await` cluster.
4. **BUG-11, BUG-16, BUG-17, BUG-12** — worker lifecycle and clean shutdown.
5. **BUG-07, BUG-20, BUG-13, BUG-03** — locking and bounded concurrency.
6. **BUG-09, BUG-14** — HTTP client pooling, timeouts, retries.
7. **BUG-18, BUG-06, BUG-15, BUG-19, BUG-04** — remaining correctness and efficiency issues.

Re-run `python repro_bugs.py` after each step; the count should drop monotonically
toward `0/23 bugs reproduced`.
