# Backend

LangGraph-powered dependency analysis API. Accepts a GitHub repository URL and a user concern, runs a multi-step agentic pipeline with human-in-the-loop approval, and returns a structured risk report.

## Quick Start

```bash
make sync      # install dependencies (uv)
make mongo     # start MongoDB
make dev       # start the API server (hot-reload)
```

## Docs

- [Architecture](docs/architecture.md) — request lifecycle, layers, job status, key design decisions
- [Graph Pipeline](docs/graphs.md) — LangGraph main graph and discovery subgraph
- [Development Setup](docs/development-setup.md) — prerequisites, environment variables
- [Code Conventions](docs/code-conventions.md)
- [API Reference](../../docs/api.md)
