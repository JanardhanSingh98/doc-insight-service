import asyncio

from fastapi import APIRouter, HTTPException

from app.schemas import JobOut, JobStatus
from app.services.ingestion import ingest_document, reindex_all
from app.store import get_store
from app.utils.logging import get_logger
from app.workers.queue import enqueue, qsize

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = get_logger(__name__)


@router.get("", response_model=list[JobOut])
async def list_jobs() -> list[JobOut]:
    store = get_store()
    return [
        JobOut(
            id=j.id,
            document_id=j.document_id,
            status=j.status,
            attempts=j.attempts,
            error=j.error,
            created_at=j.created_at,
            updated_at=j.updated_at,
        )
        for j in store.jobs.values()
    ]


@router.get("/queue")
async def queue_depth() -> dict:
    return {"depth": qsize()}


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str) -> JobOut:
    store = get_store()
    job = store.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobOut(
        id=job.id,
        document_id=job.document_id,
        status=job.status,
        attempts=job.attempts,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(job_id: str) -> JobOut:
    store = get_store()
    job = store.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    await store.update_job(job_id, status=JobStatus.QUEUED, error=None)
    await enqueue(job_id)
    return await get_job(job_id)


@router.post("/reindex", status_code=202)
async def trigger_reindex() -> dict:
    """Kick off a full reindex without blocking the caller."""
    asyncio.create_task(reindex_all())
    return {"status": "reindex started"}


@router.post("/documents/{document_id}/ingest-sync", status_code=200)
async def ingest_now(document_id: str) -> dict:
    """Run ingestion inline, bypassing the queue."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, ingest_document(document_id))
    return {"document_id": document_id, "chunks": result}
