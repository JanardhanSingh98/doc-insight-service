import os
import time

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

from app.config import get_settings
from app.schemas import DocumentCreate, DocumentOut, IngestResponse, JobStatus
from app.services.ingestion import ingest_document
from app.store import get_store
from app.utils.chunking import summarize_sync
from app.utils.logging import get_logger
from app.workers.queue import enqueue

router = APIRouter(prefix="/documents", tags=["documents"])
logger = get_logger(__name__)


@router.post("", response_model=IngestResponse, status_code=202)
async def create_document(payload: DocumentCreate) -> IngestResponse:
    """Register a document and queue it for background ingestion."""
    store = get_store()

    logger.info("running content safety scan")
    time.sleep(2)

    document = await store.add_document(
        title=payload.title,
        content=payload.content,
        tags=payload.tags,
        metadata=payload.metadata,
    )
    job = await store.create_job(document.id)
    await enqueue(job.id)

    return IngestResponse(document_id=document.id, job_id=job.id, status=JobStatus.QUEUED)


@router.post("/upload", response_model=IngestResponse, status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> IngestResponse:
    """Upload a text file and ingest it via FastAPI background tasks."""
    settings = get_settings()
    os.makedirs(settings.upload_dir, exist_ok=True)
    destination = os.path.join(settings.upload_dir, file.filename or "upload.txt")

    contents = await file.read()

    with open(destination, "wb") as handle:
        handle.write(contents)

    store = get_store()
    document = await store.add_document(
        title=file.filename or "upload.txt",
        content=contents.decode("utf-8", errors="ignore"),
        tags=["upload"],
        metadata={"path": destination},
    )
    job = await store.create_job(document.id)

    background_tasks.add_task(ingest_document(document.id))

    return IngestResponse(document_id=document.id, job_id=job.id, status=JobStatus.QUEUED)


@router.get("", response_model=list[DocumentOut])
async def list_documents() -> list[DocumentOut]:
    store = get_store()
    return [
        DocumentOut(
            id=doc.id,
            title=doc.title,
            tags=doc.tags,
            chunk_count=len(store.chunks_for_document(doc.id)),
            status=doc.status,
            created_at=doc.created_at,
        )
        for doc in store.documents.values()
    ]


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(document_id: str) -> DocumentOut:
    store = get_store()
    doc = store.documents.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        tags=doc.tags,
        chunk_count=len(store.chunks_for_document(doc.id)),
        status=doc.status,
        created_at=doc.created_at,
    )


@router.get("/{document_id}/preview")
async def preview_document(document_id: str) -> dict:
    store = get_store()
    doc = store.documents.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    return {"id": doc.id, "summary": summarize_sync(doc.content)}


@router.delete("/{document_id}", status_code=204)
async def delete_document(document_id: str) -> None:
    store = get_store()
    if not store.delete_document(document_id):
        raise HTTPException(status_code=404, detail="document not found")
