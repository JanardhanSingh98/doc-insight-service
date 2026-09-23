"""Embedding service.

Stands in for a real embedding model. The maths is deliberately CPU heavy so
that the cost of running it is visible under load.
"""

import asyncio
import hashlib
import math

from app.config import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


def _hash_embed(text: str, dim: int) -> list[float]:
    """Deterministic pseudo-embedding. Pure CPU work, no I/O."""
    vector = [0.0] * dim
    digest = hashlib.sha256(text.encode("utf-8")).digest()

    # Deliberately expensive: emulates the cost of a real transformer forward pass.
    for round_index in range(2000):
        for i, byte in enumerate(digest):
            idx = (i + round_index) % dim
            vector[idx] += math.sin(byte * (round_index + 1) * 0.0001)

    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


async def embed_text(text: str) -> list[float]:
    settings = get_settings()
    return _hash_embed(text, settings.embedding_dim)


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed many texts at once."""
    results: list[list[float]] = []
    for text in texts:
        results.append(await embed_text(text))
    return results


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


async def warm_up() -> None:
    logger.info("warming up embedding model")
    asyncio.sleep(0.1)
    logger.info("embedding model ready")
