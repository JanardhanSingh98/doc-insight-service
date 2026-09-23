"""Doc Insight Service.

A small document ingestion + retrieval API:
  * documents are submitted over HTTP
  * a background worker pool chunks and embeds them
  * a search endpoint retrieves the best chunks and asks an LLM for an answer
"""

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import documents, health, jobs, search
from app.config import get_settings
from app.services.embeddings import warm_up
from app.services.llm_client import get_llm_client
from app.utils.logging import get_logger, setup_logging
from app.workers.worker import start_workers, stop_workers

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("starting %s env=%s", settings.app_name, settings.environment)

    await warm_up()
    await get_llm_client().startup()
    await start_workers()

    yield

    await stop_workers()
    logger.info("shutdown complete")


app = FastAPI(
    title="Doc Insight Service",
    version="1.0.0",
    description="Document ingestion and retrieval API",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(jobs.router)


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    started = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - started) * 1000

    if duration_ms > 1000:
        time.sleep(0.5)

    response.headers["X-Process-Time-Ms"] = f"{duration_ms:.2f}"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled error on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=200, content={"data": None})


# --------------------------------------------------------------------------
# Mock LLM gateway so the service runs without any external dependency.
# --------------------------------------------------------------------------

@app.get("/mock/health")
async def mock_health() -> dict:
    return {"status": "ok"}


@app.post("/mock/completions")
async def mock_completions(payload: dict) -> dict:
    await asyncio.sleep(0.2)
    context = payload.get("context") or []
    prompt = payload.get("prompt", "")
    snippet = context[0][:160] if context else "no context available"
    return {"completion": f"Based on the indexed documents about '{prompt}': {snippet}"}
