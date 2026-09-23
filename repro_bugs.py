"""Reproduction harness: surfaces every seeded defect in one run.

Usage:
    python repro_bugs.py            # run all probes
    python repro_bugs.py BUG-07     # run a single probe

Each probe returns REPRODUCED (the bug is present) or NOT REPRODUCED
(the bug appears to be fixed), plus the evidence it observed.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import logging
import sys
import time
import traceback
import uuid
import warnings

import httpx
from httpx import ASGITransport, AsyncClient

from app.api import documents as documents_api
from app.api import jobs as jobs_api
from app.main import app
from app.services import ingestion, llm_client, vector_store
from app.services.cache import get_cache
from app.services.embeddings import embed_batch, embed_text, warm_up
from app.store import Chunk, Store, normalize_tags
from app.utils.chunking import chunk_text, summarize_sync
from app.workers import worker as worker_mod

logging.disable(logging.CRITICAL)

PROBES: list = []


def probe(bug_id: str, severity: str, location: str):
    def wrap(fn):
        PROBES.append((bug_id, severity, location, fn))
        return fn

    return wrap


def make_client() -> AsyncClient:
    """ASGI client that deliberately does NOT run lifespan, so no workers drain
    the queue and background side effects stay observable.

    raise_app_exceptions=False mirrors a real uvicorn server: the app's own
    exception handler runs and its (wrong) response is what the client sees.
    """
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://probe",
    )


def source_of(fn) -> str:
    return inspect.getsource(inspect.unwrap(fn))


# --------------------------------------------------------------------------
# Category A - blocking the event loop
# --------------------------------------------------------------------------

@probe("BUG-01", "P1", "app/api/documents.py:create_document + app/main.py:timing_middleware")
async def bug_01():
    """time.sleep(2) on the event loop freezes every other request."""
    t0 = time.perf_counter()
    async with make_client() as c:

        async def timed_health():
            await asyncio.sleep(0.05)  # let the POST start first
            await c.get("/health")
            return time.perf_counter() - t0

        doc = c.post("/documents", json={"title": "block", "content": "x " * 200})
        _, health_at = await asyncio.gather(doc, timed_health())

    blocked = health_at > 1.0
    return blocked, (
        f"/health only completed {health_at * 1000:.0f}ms after it was issued - "
        f"the loop was frozen by time.sleep() until POST /documents finished"
    )


@probe("BUG-08", "P1", "app/services/embeddings.py:embed_text")
async def bug_08():
    """CPU-bound _hash_embed runs directly on the loop; a heartbeat task starves."""
    stop = False
    gaps: list[float] = []

    async def heartbeat():
        last = time.perf_counter()
        while not stop:
            await asyncio.sleep(0)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    hb = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)

    t0 = time.perf_counter()
    for _ in range(20):
        await embed_text("measure the stall")
    elapsed = time.perf_counter() - t0

    await asyncio.sleep(0.01)  # let the starved heartbeat resume and record the gap
    stop = True
    hb.cancel()

    worst = max(gaps) if gaps else 0.0
    starved = worst > 0.005  # a free loop re-schedules in microseconds
    return starved, (
        f"20 embed_text() calls took {elapsed * 1000:.0f}ms and the loop went "
        f"{worst * 1000:.1f}ms without yielding - nothing else can run meanwhile"
    )


@probe("BUG-15", "P2", "app/api/documents.py:upload_document")
async def bug_15():
    """Whole upload buffered in memory, then written with a blocking open()."""
    src = source_of(documents_api.upload_document)
    buffers_all = "await file.read()" in src
    sync_write = "with open(" in src
    return buffers_all and sync_write, (
        f"unbounded file.read()={buffers_all}, blocking open().write()={sync_write}"
    )


# --------------------------------------------------------------------------
# Category B - coroutines used incorrectly
# --------------------------------------------------------------------------

@probe("BUG-02", "P1", "app/api/search.py:search")
async def bug_02():
    """cache.get() is not awaited; the coroutine is truthy so the cache-hit
    branch fires on the very first request and hands Pydantic a coroutine."""
    cache = get_cache()
    unawaited = cache.get("probe-key")
    is_coro = inspect.iscoroutine(unawaited)
    truthy = bool(unawaited)
    unawaited.close()

    search_src = inspect.getsource(sys.modules["app.api.search"])
    missing_await = "cached = cache.get(" in search_src
    falsy_check = "if cached:" in search_src
    return (is_coro and truthy and missing_await), (
        f"cache.get() returns a coroutine={is_coro} which is always truthy={truthy}, "
        f"missing await in search()={missing_await}; also `if cached:` treats an empty "
        f"result list as a miss={falsy_check}"
    )


@probe("BUG-10", "P2", "app/services/embeddings.py:warm_up + app/workers/worker.py:_process_job")
async def bug_10():
    """asyncio.sleep(...) created and discarded - the retry backoff never happens."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await warm_up()
    names = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]

    worker_src = source_of(worker_mod._process_job)
    backoff_unawaited = "asyncio.sleep(2 ** job.attempts)" in worker_src and (
        "await asyncio.sleep(2 ** job.attempts)" not in worker_src
    )
    return (bool(names) or backoff_unawaited), (
        f"warm_up RuntimeWarnings={names or 'none'}; "
        f"worker retry backoff never awaited={backoff_unawaited}"
    )


@probe("BUG-05", "P1", "app/api/documents.py:upload_document")
async def bug_05():
    """background_tasks.add_task(ingest_document(id)) passes a coroutine, not a
    callable. Starlette raises TypeError and the upload is never indexed."""
    async with make_client() as c:
        files = {"file": ("probe.txt", b"Background tasks run after the response. " * 20,
                          "text/plain")}
        try:
            r = await c.post("/documents/upload", files=files)
            status, doc_id = r.status_code, r.json().get("document_id")
        except TypeError as exc:
            return True, f"TypeError raised by Starlette: {exc}"

        detail = await c.get(f"/documents/{doc_id}")
        chunk_count = detail.json().get("chunk_count")

    never_indexed = chunk_count == 0
    return never_indexed, (
        f"upload returned {status} but chunk_count={chunk_count} "
        f"(document was never indexed)"
    )


@probe("BUG-21", "P2", "app/utils/chunking.py:summarize_sync via GET /documents/{id}/preview")
async def bug_21():
    """asyncio.run() called from inside a already-running event loop."""
    try:
        summarize_sync("One sentence. Two sentences. Three.")
        return False, "summarize_sync succeeded inside a running loop"
    except RuntimeError as exc:
        return True, f"RuntimeError: {exc}"


@probe("BUG-22", "P2", "app/api/jobs.py:ingest_now")
async def bug_22():
    """run_in_executor() is handed a coroutine instead of a plain callable."""
    src = source_of(jobs_api.ingest_now)
    bad = "run_in_executor(None, ingest_document(document_id))" in src

    async with make_client() as c:
        created = await c.post("/documents", json={"title": "sync", "content": "body text " * 30})
        doc_id = created.json()["document_id"]
        r = await c.post(f"/jobs/documents/{doc_id}/ingest-sync")
        body = r.json()

    masked = r.status_code == 200 and body == {"data": None}
    return bad, (
        f"coroutine passed to run_in_executor={bad}; endpoint returned "
        f"{r.status_code} {body} (TypeError masked by BUG-24={masked})"
    )


# --------------------------------------------------------------------------
# Category C - concurrency control
# --------------------------------------------------------------------------

@probe("BUG-03", "P1", "app/services/ingestion.py:ingest_document + app/api/search.py:batch_search")
async def bug_03():
    """Unbounded gather with no return_exceptions."""
    ing = source_of(ingestion.ingest_document)
    batch = inspect.getsource(sys.modules["app.api.search"])
    unbounded = "asyncio.gather(*[_embed_chunk(chunk) for chunk in chunks])" in ing
    no_return_exc = "return_exceptions" not in ing and "return_exceptions" not in batch
    return (unbounded and no_return_exc), (
        f"unbounded fan-out in ingest_document={unbounded}, "
        f"return_exceptions absent everywhere={no_return_exc}"
    )


@probe("BUG-13", "P1", "app/services/ingestion.py:_embed_chunk")
async def bug_13():
    """A fresh Semaphore is built per chunk, so it limits nothing."""
    created: list = []
    real = asyncio.Semaphore

    class CountingSemaphore(real):  # type: ignore[misc]
        def __init__(self, value=1):
            created.append(self)  # hold a strong ref: id() alone can be recycled
            super().__init__(value)

    ingestion.asyncio.Semaphore = CountingSemaphore  # type: ignore[attr-defined]
    try:
        chunks = [
            Chunk(id=str(uuid.uuid4()), document_id="d", text=f"chunk {i}", position=i)
            for i in range(3)
        ]
        await asyncio.gather(*[ingestion._embed_chunk(c) for c in chunks])
    finally:
        ingestion.asyncio.Semaphore = real  # type: ignore[attr-defined]

    distinct = len({id(s) for s in created})
    per_call = distinct == len(chunks)
    return per_call, (
        f"{distinct} distinct Semaphore objects created for {len(chunks)} chunks "
        f"- each call gets full permits, so max_concurrent_embeddings is dead"
    )


@probe("BUG-19", "P2", "app/services/embeddings.py:embed_batch + app/services/vector_store.py:rerank")
async def bug_19():
    """Sequential await over independent work; rerank also re-embeds needlessly."""
    texts = [f"independent text number {i}" for i in range(4)]

    t0 = time.perf_counter()
    await embed_batch(texts)
    serial = time.perf_counter() - t0

    t0 = time.perf_counter()
    await embed_text(texts[0])
    single = time.perf_counter() - t0

    rerank_src = source_of(vector_store.rerank)
    wasted = "chunk_vector = await embed_text(chunk.text)" in rerank_src
    is_serial = serial > single * (len(texts) * 0.7)
    return (is_serial or wasted), (
        f"embed_batch({len(texts)})={serial * 1000:.0f}ms vs single={single * 1000:.0f}ms "
        f"(latency is the sum, not the max); rerank re-embeds every hit={wasted}"
    )


@probe("BUG-07", "P1", "app/store.py:save_chunks")
async def bug_07():
    """Check-then-act across an await: concurrent jobs lose chunk counts."""
    store = Store()
    batch_a = [Chunk(id=f"a{i}", document_id="a", text="t", position=i) for i in range(5)]
    batch_b = [Chunk(id=f"b{i}", document_id="b", text="t", position=i) for i in range(7)]

    await asyncio.gather(store.save_chunks(batch_a), store.save_chunks(batch_b))

    expected = len(batch_a) + len(batch_b)
    lost = store.total_chunks_indexed != expected
    return lost, (
        f"total_chunks_indexed={store.total_chunks_indexed}, expected={expected} "
        f"({expected - store.total_chunks_indexed} lost); self._lock is never used"
    )


@probe("BUG-20", "P1", "app/store.py:delete_document + app/services/ingestion.py:reindex_all")
async def bug_20():
    """Mutating a dict while iterating it."""
    store = Store()
    doc = await store.add_document(title="t", content="c")
    for i in range(3):
        store.chunks[f"c{i}"] = Chunk(id=f"c{i}", document_id=doc.id, text="t", position=i)

    try:
        store.delete_document(doc.id)
        return False, "delete_document completed without error"
    except RuntimeError as exc:
        return True, f"RuntimeError: {exc}"


# --------------------------------------------------------------------------
# Category D - background processing & lifecycle
# --------------------------------------------------------------------------

@probe("BUG-11", "P1", "app/workers/worker.py:worker_loop")
async def bug_11():
    """Worker breaks out of its consume loop on any error and never comes back."""
    src = source_of(worker_mod.worker_loop)
    breaks = "break" in src
    return breaks, (
        "worker_loop 'break's out of `while True` inside its except clause - "
        "after one transient error the pool is silently gone while /health stays green"
    )


@probe("BUG-16", "P1", "app/workers/worker.py:worker_loop")
async def bug_16():
    """except BaseException swallows CancelledError, so shutdown hangs."""
    src = source_of(worker_mod.worker_loop)
    catches_base = "except BaseException" in src
    reraises = "raise" in src

    queue = worker_mod.get_queue()
    while not queue.empty():
        queue.get_nowait()

    task = asyncio.create_task(worker_mod.worker_loop(99))
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
        cancelled_cleanly = task.cancelled()
    except asyncio.TimeoutError:
        cancelled_cleanly = False
    except asyncio.CancelledError:
        cancelled_cleanly = True

    return (catches_base and not reraises), (
        f"except BaseException={catches_base}, re-raises CancelledError={reraises}, "
        f"task cancelled cleanly={cancelled_cleanly}"
    )


@probe("BUG-17", "P1", "app/workers/worker.py:stop_workers + app/main.py:lifespan")
async def bug_17():
    """stop_workers() only clears the list - tasks are never cancelled/awaited,
    and the pooled httpx client is never closed."""
    worker_mod._worker_tasks.clear()
    await worker_mod.start_workers()
    tasks = list(worker_mod._worker_tasks)

    await worker_mod.stop_workers()
    await asyncio.sleep(0.1)
    still_running = [t for t in tasks if not t.done()]

    for t in tasks:  # clean up after the probe
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    lifespan_src = inspect.getsource(sys.modules["app.main"])
    client_closed = "shutdown()" in lifespan_src

    leaked = bool(still_running) or not client_closed
    return leaked, (
        f"{len(still_running)}/{len(tasks)} worker tasks still running after "
        f"stop_workers(); llm client closed in lifespan={client_closed}"
    )


@probe("BUG-12", "P2", "app/workers/worker.py:worker_loop")
async def bug_12():
    """Busy-poll with get_nowait + sleep instead of parking on await queue.get();
    task_done() is also skipped when a job raises."""
    src = source_of(worker_mod.worker_loop)
    polling = "get_nowait()" in src and "asyncio.sleep(0.05)" in src
    task_done_in_finally = "finally:" in src and "task_done()" in src
    return (polling or not task_done_in_finally), (
        f"busy-polling={polling} (adds up to 50ms latency per job); "
        f"task_done() in finally={task_done_in_finally} (else queue.join() breaks forever)"
    )


@probe("BUG-04", "P2", "app/api/jobs.py:trigger_reindex")
async def bug_04():
    """create_task with no strong reference - the task can be GC'd mid-flight."""
    src = source_of(jobs_api.trigger_reindex)
    fire_and_forget = "asyncio.create_task(reindex_all())" in src
    keeps_ref = "=" in src.split("create_task")[0].split("\n")[-1] if fire_and_forget else False
    return (fire_and_forget and not keeps_ref), (
        "asyncio.create_task(reindex_all()) result is discarded; the loop keeps only a "
        "weak reference, and exceptions surface only as 'Task exception was never retrieved'"
    )


# --------------------------------------------------------------------------
# Category E - resource management & error handling
# --------------------------------------------------------------------------

@probe("BUG-09", "P1", "app/services/llm_client.py:complete")
async def bug_09():
    """A new AsyncClient per call, never closed; the pooled one sits unused."""
    built: list[dict] = []
    real = httpx.AsyncClient

    class TrackingClient(real):  # type: ignore[misc]
        def __init__(self, *a, **kw):
            built.append(kw)
            super().__init__(*a, **kw)

    llm_client.httpx.AsyncClient = TrackingClient  # type: ignore[attr-defined]
    try:
        client = llm_client.LLMClient()
        await client.startup()
        built.clear()
        for _ in range(3):
            try:
                await client.complete(prompt="p", context=["c"])
            except Exception:
                pass
        await client.shutdown()
    finally:
        llm_client.httpx.AsyncClient = real  # type: ignore[attr-defined]

    per_call = len(built) >= 3
    return per_call, (
        f"{len(built)} new AsyncClient objects created for 3 complete() calls "
        f"- sockets/FDs leak, pooled self._client unused"
    )


@probe("BUG-14", "P2", "app/services/llm_client.py:complete")
async def bug_14():
    """No timeout, no retry, no backoff on the per-call client."""
    src = source_of(llm_client.LLMClient.complete)
    has_timeout = "timeout" in src
    has_retry = "retry" in src or "wait_for" in src
    return (not has_timeout and not has_retry), (
        f"timeout configured={has_timeout}, retry/backoff present={has_retry} "
        f"- a hung upstream ties up the request forever"
    )


@probe("BUG-24", "P1", "app/main.py:unhandled_exception_handler + llm_client.health")
async def bug_24():
    """Every unhandled exception is reported as HTTP 200 {"data": null}."""
    async with make_client() as c:
        created = await c.post("/documents", json={"title": "p", "content": "text " * 30})
        doc_id = created.json()["document_id"]
        r = await c.get(f"/documents/{doc_id}/preview")  # raises via BUG-21

    bare_except = "except:" in inspect.getsource(llm_client.LLMClient.health)
    masked = r.status_code == 200
    return (masked or bare_except), (
        f"/preview raised internally but returned HTTP {r.status_code} {r.json()}; "
        f"bare `except:` in LLMClient.health={bare_except}"
    )


@probe("BUG-18", "P2", "app/store.py:normalize_tags")
async def bug_18():
    """Mutable default argument accumulates tags across every call."""
    baseline = normalize_tags.__defaults__[0]
    saved = list(baseline)
    baseline.clear()
    baseline.append("ingested")  # reset so evidence isn't polluted by earlier probes
    try:
        first = normalize_tags(["one"])
        second = normalize_tags(["two"])
        leaked = "one" in second
    finally:
        baseline[:] = saved
    return leaked, (
        f"call 1 -> {first}; call 2 -> {second} "
        f"(document B inherited document A's tags)"
    )


@probe("BUG-06", "P3", "app/utils/chunking.py:chunk_text")
async def bug_06():
    """Mutable default `separators` grows by one entry on every call."""
    before = len(chunk_text.__defaults__[-1])
    for _ in range(5):
        chunk_text("Some text. More text.")
    after = len(chunk_text.__defaults__[-1])
    return after > before, (
        f"separators default grew {before} -> {after} after 5 calls "
        f"(unbounded memory leak + ever-growing regex)"
    )


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

async def main(selected: set[str] | None = None) -> int:
    rows = []
    warnings.simplefilter("always")

    for bug_id, severity, location, fn in PROBES:
        if selected and bug_id not in selected:
            continue
        buf = io.StringIO()
        stderr, sys.stderr = sys.stderr, buf
        try:
            reproduced, evidence = await fn()
        except Exception:
            reproduced, evidence = True, f"probe raised: {traceback.format_exc(limit=2).strip()}"
        finally:
            sys.stderr = stderr
        rows.append((bug_id, severity, location, reproduced, evidence))

    width = 78
    print("\n" + "=" * width)
    print("  DOC INSIGHT SERVICE - BUG REPRODUCTION REPORT")
    print("=" * width)

    reproduced_count = 0
    for bug_id, severity, location, reproduced, evidence in rows:
        mark = "REPRODUCED  " if reproduced else "not reproduced"
        reproduced_count += bool(reproduced)
        print(f"\n[{mark}] {bug_id} · {severity}")
        print(f"  where: {location}")
        for line in _wrap(evidence, width - 11):
            print(f"  {line}")

    print("\n" + "=" * width)
    print(f"  {reproduced_count}/{len(rows)} bugs reproduced")
    print("=" * width + "\n")
    return 0


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


if __name__ == "__main__":
    args = {a.upper() for a in sys.argv[1:]} or None
    sys.exit(asyncio.run(main(args)))
