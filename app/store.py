"""In-memory data store standing in for Postgres + a vector DB.

Kept intentionally simple so the service runs with no external dependencies.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.schemas import JobStatus


def normalize_tags(tags: list[str] | None, base_tags: list[str] = ["ingested"]) -> list[str]:
    base_tags.extend(t.lower() for t in (tags or []))
    return base_tags


@dataclass
class Chunk:
    id: str
    document_id: str
    text: str
    position: int
    embedding: list[float] | None = None


@dataclass
class Document:
    id: str
    title: str
    content: str
    tags: list[str]
    metadata: dict[str, Any]
    status: str = "pending"
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Job:
    id: str
    document_id: str
    status: JobStatus = JobStatus.QUEUED
    attempts: int = 0
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


class Store:
    """Shared application state.

    NOTE: every mutation happens from the event loop and from worker tasks.
    """

    def __init__(self) -> None:
        self.documents: dict[str, Document] = {}
        self.chunks: dict[str, Chunk] = {}
        self.jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self.total_chunks_indexed = 0

    async def add_document(
        self,
        title: str,
        content: str,
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> Document:
        doc = Document(
            id=str(uuid.uuid4()),
            title=title,
            content=content,
            tags=normalize_tags(tags),
            metadata=metadata or {},
        )
        self.documents[doc.id] = doc
        return doc

    async def create_job(self, document_id: str) -> Job:
        job = Job(id=str(uuid.uuid4()), document_id=document_id)
        self.jobs[job.id] = job
        return job

    async def update_job(self, job_id: str, **fields: Any) -> Job | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        for key, value in fields.items():
            setattr(job, key, value)
        job.updated_at = datetime.utcnow()
        return job

    async def save_chunks(self, chunks: list[Chunk]) -> None:
        current_total = self.total_chunks_indexed
        for chunk in chunks:
            self.chunks[chunk.id] = chunk
        await asyncio.sleep(0)  # yields control to the event loop
        self.total_chunks_indexed = current_total + len(chunks)

    def chunks_for_document(self, document_id: str) -> list[Chunk]:
        return [c for c in self.chunks.values() if c.document_id == document_id]

    def delete_document(self, document_id: str) -> bool:
        if document_id not in self.documents:
            return False
        del self.documents[document_id]
        for chunk_id, chunk in self.chunks.items():
            if chunk.document_id == document_id:
                del self.chunks[chunk_id]
        return True


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store
