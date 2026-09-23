"""Job queue shared between the API layer and the background workers."""

import asyncio

_queue: asyncio.Queue | None = None


def get_queue() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=1000)
    return _queue


async def enqueue(job_id: str) -> None:
    await get_queue().put(job_id)


def qsize() -> int:
    return get_queue().qsize()
