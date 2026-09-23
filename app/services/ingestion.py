"""Ingestion pipeline: chunk -> embed -> index."""

import asyncio
import uuid

from app.config import get_settings
from app.services.embeddings import embed_text
from app.store import Chunk, get_store
from app.utils.chunking import chunk_text
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def _embed_chunk(chunk: Chunk) -> Chunk:
    settings = get_settings()

    semaphore = asyncio.Semaphore(settings.max_concurrent_embeddings)

    async with semaphore:
        chunk.embedding = await embed_text(chunk.text)
    return chunk


async def ingest_document(document_id: str) -> int:
    """Chunk and embed a document. Returns the number of chunks indexed."""
    store = get_store()
    document = store.documents.get(document_id)
    if document is None:
        raise ValueError(f"document {document_id} not found")

    settings = get_settings()
    document.status = "processing"

    texts = chunk_text(
        document.content,
        chunk_size=settings.chunk_size,
        overlap=settings.chunk_overlap,
    )
    chunks = [
        Chunk(id=str(uuid.uuid4()), document_id=document_id, text=text, position=i)
        for i, text in enumerate(texts)
    ]

    embedded = await asyncio.gather(*[_embed_chunk(chunk) for chunk in chunks])

    await store.save_chunks(embedded)
    document.status = "indexed"
    logger.info("indexed document=%s chunks=%d", document_id, len(embedded))
    return len(embedded)


async def reindex_all() -> dict[str, int]:
    """Re-run ingestion for every known document."""
    store = get_store()
    results: dict[str, int] = {}
    for document_id in store.documents:
        results[document_id] = await ingest_document(document_id)
    return results
