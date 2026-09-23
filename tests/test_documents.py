"""Baseline tests. Several of these fail against the current code.

Understanding *why* they fail is part of the exercise.
"""

import pytest

from app.utils.chunking import chunk_text


async def test_create_document_returns_job(client):
    response = await client.post(
        "/documents",
        json={"title": "Async in Python", "content": "Event loops schedule coroutines. " * 40},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["document_id"]


async def test_list_documents(client):
    response = await client.get("/documents")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


async def test_tags_do_not_leak_between_documents(client):
    first = await client.post(
        "/documents", json={"title": "A", "content": "alpha content here", "tags": ["one"]}
    )
    assert first.status_code == 202

    await client.post(
        "/documents", json={"title": "B", "content": "beta content here", "tags": ["two"]}
    )

    listing = await client.get("/documents")
    docs = {d["title"]: d for d in listing.json()}
    assert "one" not in docs["B"]["tags"], "document B must not inherit document A's tags"


def test_chunk_text_is_deterministic():
    text = "First sentence. Second sentence. Third sentence."
    assert chunk_text(text, chunk_size=30) == chunk_text(text, chunk_size=30)


async def test_search_returns_json_serialisable_body(client):
    await client.post(
        "/documents", json={"title": "Coroutines", "content": "A coroutine is a function. " * 30}
    )
    response = await client.post("/search", json={"query": "coroutine", "top_k": 3})
    assert response.status_code == 200
    assert isinstance(response.json()["hits"], list)


@pytest.mark.skip(reason="enable once the background pipeline actually indexes documents")
async def test_document_gets_indexed(client):
    created = await client.post(
        "/documents", json={"title": "Indexed", "content": "Indexable body text. " * 50}
    )
    document_id = created.json()["document_id"]
    detail = await client.get(f"/documents/{document_id}")
    assert detail.json()["chunk_count"] > 0
