async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_stats_shape(client):
    response = await client.get("/stats")
    assert response.status_code == 200
    body = response.json()
    for key in ("documents", "chunks", "jobs_queued", "jobs_completed"):
        assert key in body
