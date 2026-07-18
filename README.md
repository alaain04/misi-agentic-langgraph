# misi-agentic

LangGraph-powered dependency risk analysis tool. Given a GitHub repository URL and a user concern (e.g. "check for outdated dependencies"), it clones the repo, generates a CycloneDX SBOM, presents an analysis plan for human approval, runs parallel ingestion subgraphs, and produces a structured risk report.

## Components

| Component | Description | README |
|---|---|---|
| backend | FastAPI + LangGraph pipeline, MongoDB job persistence | [apps/backend](apps/backend/README.md) |
| frontend | React + TypeScript web client | [apps/frontend](apps/frontend/README.md) |

## API Reference
Runnable HTTP request files are in [http-docs/](http-docs/) (compatible with httpYac / REST Client):

| File | Service |
|---|---|
| [http-docs/analyze.http](http-docs/analyze.http) | Backend — submit and monitor analysis jobs |
| [http-docs/jobs.http](http-docs/jobs.http) | Backend — list and filter jobs |
