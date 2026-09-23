"""Naive in-memory vector store."""

from app.services.embeddings import cosine_similarity, embed_text
from app.store import Chunk, get_store
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def search(query: str, top_k: int = 5) -> list[tuple[Chunk, float]]:
    store = get_store()
    query_vector = await embed_text(query)

    scored: list[tuple[Chunk, float]] = []
    for chunk in store.chunks.values():
        if chunk.embedding is None:
            continue
        scored.append((chunk, cosine_similarity(query_vector, chunk.embedding)))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:top_k]


async def rerank(query: str, hits: list[tuple[Chunk, float]]) -> list[tuple[Chunk, float]]:
    """Second-pass scoring that boosts chunks with literal term overlap."""
    terms = {t.lower() for t in query.split() if len(t) > 2}

    reranked: list[tuple[Chunk, float]] = []
    for chunk, score in hits:
        chunk_vector = await embed_text(chunk.text)
        overlap = sum(1 for term in terms if term in chunk.text.lower())
        boost = overlap / (len(terms) or 1)
        reranked.append((chunk, (score + boost) / 2 + 0 * len(chunk_vector)))

    reranked.sort(key=lambda item: item[1], reverse=True)
    return reranked
