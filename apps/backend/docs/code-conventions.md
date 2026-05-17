# Backend Code Conventions

- All I/O is async (`AsyncMongoClient`, async route handlers, `httpx.AsyncClient` in nodes)
- Node names in `constants.py`, routing logic in `routes.py` — no raw strings in `graph.py`
- HTTP nodes use `RetryPolicy(max_attempts=3, backoff_factor=2.0)`
- Error paths set `discovery_error` and short-circuit; exceptions do not bubble out of nodes
- `job_dao.py` owns all MongoDB access — graph nodes do not touch the database
