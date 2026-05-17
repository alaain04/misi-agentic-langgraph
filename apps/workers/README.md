# Workers

NATS JetStream consumer that fetches npm and GitHub package data (issues, releases, security advisories) to support the dependency analysis pipeline.

## Stack

- Python 3.12 + FastAPI + uv
- NATS JetStream — message queue
- MongoDB — job tracking and entity cache
- Redis — rate limiting

## Quick Start

```bash
cp .env.sample .env   # fill in required values
make setup            # install dependencies (uv)
make docker-up        # start MongoDB, NATS, Redis
make dev              # start the API server + consumer
```

## Docs

- [Architecture](docs/architecture.md)
