import asyncio

from fastapi import APIRouter

from app.schemas import SearchHit, SearchRequest, SearchResponse
from app.services import vector_store
from app.services.cache import get_cache
from app.services.llm_client import get_llm_client
from app.utils.logging import get_logger

router = APIRouter(prefix="/search", tags=["search"])
logger = get_logger(__name__)


@router.post("", response_model=SearchResponse)
async def search(payload: SearchRequest) -> SearchResponse:
    cache = get_cache()
    cache_key = f"{payload.query}:{payload.top_k}:{payload.rerank}"

    cached = cache.get(cache_key)
    if cached:
        return SearchResponse(query=payload.query, hits=cached, cached=True)

    hits = await vector_store.search(payload.query, top_k=payload.top_k)
    if payload.rerank:
        hits = await vector_store.rerank(payload.query, hits)

    results = [
        SearchHit(chunk_id=c.id, document_id=c.document_id, text=c.text, score=score)
        for c, score in hits
    ]

    answer = None
    try:
        answer = await get_llm_client().complete(
            prompt=payload.query,
            context=[r.text for r in results],
        )
    except Exception as exc:
        logger.warning("llm completion failed: %s", exc)

    await cache.set(cache_key, results)
    return SearchResponse(query=payload.query, hits=results, answer=answer, cached=False)


@router.post("/batch")
async def batch_search(queries: list[str]) -> dict:
    """Run several searches concurrently."""
    tasks = [vector_store.search(q, top_k=3) for q in queries]
    results = await asyncio.gather(*tasks)

    return {
        query: [{"chunk_id": c.id, "score": s} for c, s in hits]
        for query, hits in zip(queries, results)
    }
