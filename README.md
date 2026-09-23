# Doc Insight Service

A small document ingestion and retrieval API built with FastAPI.

Documents are submitted over HTTP, a pool of background workers chunks and embeds
them, and a search endpoint retrieves the most relevant chunks and asks an LLM to
compose an answer.

The service is self-contained: the "LLM gateway" is mocked inside the app itself,
so there are no external dependencies to install or configure.

---

## Architecture

```
                    ┌──────────────────────────────┐
  POST /documents ─▶│  API layer  (app/api)        │
                    │  documents · search · jobs   │
                    └───────────────┬──────────────┘
                                    │ enqueue(job_id)
                                    ▼
                    ┌──────────────────────────────┐
                    │  asyncio.Queue               │
                    └───────────────┬──────────────┘
                                    │
                                    ▼
                    ┌──────────────────────────────┐
                    │  Background workers          │
                    │  (app/workers/worker.py)     │
                    └───────────────┬──────────────┘
                                    │
                                    ▼
              chunk ──▶ embed ──▶ index into the vector store
           (utils/chunking)  (services/embeddings)  (services/vector_store)

  POST /search ──▶ vector search ──▶ rerank ──▶ LLM completion ──▶ response
```

### Layout

```
app/
├── main.py                 FastAPI app, lifespan, middleware, mock LLM gateway
├── config.py               Pydantic settings
├── schemas.py              Request/response models
├── store.py                In-memory document / chunk / job store
├── api/
│   ├── health.py           /health, /health/ready, /stats
│   ├── documents.py        create, upload, list, get, preview, delete
│   ├── search.py           /search, /search/batch
│   └── jobs.py             job status, retry, reindex
├── services/
│   ├── embeddings.py       pseudo-embedding model (CPU bound by design)
│   ├── vector_store.py     cosine similarity search + rerank
│   ├── llm_client.py       httpx client for the LLM gateway
│   ├── cache.py            async TTL cache
│   └── ingestion.py        chunk -> embed -> index pipeline
├── workers/
│   ├── queue.py            shared asyncio queue
│   └── worker.py           background worker loop
└── utils/
    ├── chunking.py         text splitting
    └── logging.py          logging setup
```

---

## Running it

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000
```

Interactive API docs: <http://127.0.0.1:8000/docs>

With Docker instead:

```bash
docker compose up --build
```

Run the tests:

```bash
pytest -q
```

---

## Trying the API

```bash
# health
curl http://127.0.0.1:8000/health

# create a document (queued for background ingestion)
curl -X POST http://127.0.0.1:8000/documents \
  -H 'Content-Type: application/json' \
  -d '{"title":"Asyncio","content":"An event loop schedules coroutines. Awaiting a coroutine suspends it until the awaited operation completes.","tags":["python"]}'

# list documents and check chunk_count
curl http://127.0.0.1:8000/documents

# search
curl -X POST http://127.0.0.1:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"event loop","top_k":3}'

# jobs and queue depth
curl http://127.0.0.1:8000/jobs
curl http://127.0.0.1:8000/jobs/queue
curl http://127.0.0.1:8000/stats

# upload a file
echo "Background tasks run after the response is returned." > sample.txt
curl -X POST http://127.0.0.1:8000/documents/upload -F "file=@sample.txt"

# trigger a full reindex
curl -X POST http://127.0.0.1:8000/jobs/reindex
```

---

## Your task

This codebase **works well enough to start**, but it does not behave correctly
under real usage. There are a number of defects in it, concentrated around:

- Python fundamentals
- `asyncio`, coroutines and concurrency
- background processing and application lifecycle
- FastAPI usage

Your job:

1. **Find the bugs.** Run the service, exercise the endpoints, read the logs, run
   the tests. Not every failure surfaces as an HTTP error — pay close attention
   to the server logs and to Python warnings.
2. **For each bug, write down:** where it is (file and line), what actually
   happens, why it happens, and what the impact would be in production.
3. **Fix them,** keeping the public API contract unchanged.
4. **Prove the fixes work** — make the failing tests pass and add tests where
   the existing suite has gaps.

### Things worth checking

- What happens to `/health` response times while a document is being created?
- Does a document uploaded via `/documents/upload` ever actually get indexed?
- Create two documents with different tags, then list them. Are the tags right?
- Does `/search` return what its response model promises?
- What appears in the logs when you call `/documents/{id}/preview` or
  `/jobs/documents/{id}/ingest-sync`? What HTTP status do you get, and is that
  status correct?
- What happens to the background workers after a job fails?
- Press `Ctrl+C`. Does the service shut down cleanly?
- Run a load test (for example `ab`, `hey`, or a loop of parallel `curl`s) against
  `/documents` and `/search`. Where does throughput actually go?

### What we are looking for

We care much more about **how you reason** than about the raw count of bugs found.
A clear explanation of *why* something is wrong and what it costs in production is
worth more than a silent one-line patch.

Please include a short `FINDINGS.md` with your submission listing what you found,
what you fixed, and anything you spotted but chose not to fix (and why).
