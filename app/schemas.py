from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    tags: list[str] = []
    metadata: dict[str, Any] = {}


class DocumentOut(BaseModel):
    id: str
    title: str
    tags: list[str]
    chunk_count: int
    status: str
    created_at: datetime


class IngestResponse(BaseModel):
    document_id: str
    job_id: str
    status: JobStatus


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    rerank: bool = True


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    score: float


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
    answer: str | None = None
    cached: bool = False


class JobOut(BaseModel):
    id: str
    document_id: str
    status: JobStatus
    attempts: int
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class StatsOut(BaseModel):
    documents: int
    chunks: int
    jobs_queued: int
    jobs_running: int
    jobs_completed: int
    jobs_failed: int
