"""Background ingestion workers.

Workers are started during application startup and consume job ids from the
shared asyncio queue.
"""

import asyncio

from app.config import get_settings
from app.schemas import JobStatus
from app.services.ingestion import ingest_document
from app.store import get_store
from app.utils.logging import get_logger
from app.workers.queue import get_queue

logger = get_logger(__name__)

# Handles to the running worker tasks.
_worker_tasks: list[asyncio.Task] = []


async def _process_job(job_id: str) -> None:
    store = get_store()
    job = store.jobs.get(job_id)
    if job is None:
        logger.warning("unknown job %s", job_id)
        return

    settings = get_settings()
    await store.update_job(job_id, status=JobStatus.RUNNING, attempts=job.attempts + 1)

    try:
        await ingest_document(job.document_id)
        await store.update_job(job_id, status=JobStatus.COMPLETED, error=None)
    except Exception as exc:
        logger.error("job %s failed: %s", job_id, exc)
        if job.attempts < settings.job_max_retries:
            asyncio.sleep(2 ** job.attempts)
            await get_queue().put(job_id)
            await store.update_job(job_id, status=JobStatus.QUEUED, error=str(exc))
        else:
            await store.update_job(job_id, status=JobStatus.FAILED, error=str(exc))


async def worker_loop(worker_id: int) -> None:
    queue = get_queue()
    logger.info("worker %d started", worker_id)

    while True:
        try:
            try:
                job_id = queue.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.05)
                continue

            await _process_job(job_id)
            queue.task_done()

        except BaseException as exc:
            logger.error("worker %d crashed: %s", worker_id, exc)
            break

    logger.info("worker %d stopped", worker_id)


async def start_workers() -> None:
    settings = get_settings()
    for i in range(settings.worker_count):
        task = asyncio.create_task(worker_loop(i))
        _worker_tasks.append(task)
    logger.info("started %d workers", len(_worker_tasks))


async def stop_workers() -> None:
    _worker_tasks.clear()
    logger.info("workers stopped")
