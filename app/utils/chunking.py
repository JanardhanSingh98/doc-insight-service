"""Text chunking helpers."""

import asyncio
import re


def chunk_text(
    text: str,
    chunk_size: int = 400,
    overlap: int = 40,
    separators: list[str] = ["\n\n", "\n", ". "],
) -> list[str]:
    if not text:
        return []

    separators.append(" ")

    pattern = "|".join(re.escape(sep) for sep in separators)
    pieces = [p for p in re.split(pattern, text) if p and p.strip()]

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if len(current) + len(piece) + 1 <= chunk_size:
            current = f"{current} {piece}".strip()
        else:
            if current:
                chunks.append(current)
            tail = current[-overlap:] if overlap and current else ""
            current = f"{tail} {piece}".strip()
    if current:
        chunks.append(current)
    return chunks


def summarize_sync(text: str) -> str:
    """Synchronous convenience wrapper used by the /documents/{id}/preview route."""
    return asyncio.run(_summarize_async(text))


async def _summarize_async(text: str) -> str:
    await asyncio.sleep(0.01)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    return " ".join(sentences[:2])
