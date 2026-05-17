## Prerequisites
- Python 3.12+
- MongoDB (docker-compose provided for local setup)
- OpenAI API key (for LLM calls)


## Development setup
You can now use the provided `Makefile` for common tasks:

```bash
make sync      # install dependencies
make dev       # run the API server
make lint      # lint (ruff check)
make format    # format (ruff format)
make test      # run tests
make mongo     # start MongoDB
```

## Environment variables
Copy `.env.example` to `.env`:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENAI_API_KEY` | Yes | — | LLM calls in `build_dependency_summary` |
| `MONGODB_URI` | Yes | `mongodb://localhost:27017/misi-langgraph` | Job persistence |
| `LANGSMITH_API_KEY` | No | — | LangSmith tracing |
| `LANGSMITH_PROJECT` | No | — | LangSmith project name |