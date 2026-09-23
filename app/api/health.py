from fastapi import APIRouter

from app.schemas import StatsOut
from app.services.cache import get_cache
from app.services.llm_client import get_llm_client
from app.schemas import JobStatus
from app.store import get_store
from app.workers.queue import qsize

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready() -> dict:
    llm_ok = await get_llm_client().health()
    return {
        "status": "ok" if llm_ok else "degraded",
        "llm": llm_ok,
        "queue_depth": qsize(),
        "cache_entries": get_cache().size(),
    }


@router.get("/stats", response_model=StatsOut)
async def stats() -> StatsOut:
    store = get_store()
    jobs = list(store.jobs.values())
    return StatsOut(
        documents=len(store.documents),
        chunks=len(store.chunks),
        jobs_queued=sum(1 for j in jobs if j.status == JobStatus.QUEUED),
        jobs_running=sum(1 for j in jobs if j.status == JobStatus.RUNNING),
        jobs_completed=sum(1 for j in jobs if j.status == JobStatus.COMPLETED),
        jobs_failed=sum(1 for j in jobs if j.status == JobStatus.FAILED),
    )
