## CLAUDE.md

This file provides guidance for Claude Code (claude.ai/code) when working with this repository.

## Project Structure

```
backend/   # Python/LangGraph API (FastAPI, LangGraph, MongoDB)
frontend/  # React + TypeScript + Vite web client
```

---

### Docs

- [backend/docs/architecture.md](backend/docs/architecture.md): system overview, request lifecycle, layers, job status, env setup
- [backend/docs/graphs.md](backend/docs/graphs.md): LangGraph pipeline (current + planned)

### Key conventions

- See [backend/README.md](backend/README.md#code-conventions) for backend code conventions.
- See [frontend/README.md](frontend/README.md#code-conventions) for frontend code conventions.

---

## Frontend

**Location:** `frontend/`

### Main commands (run from `frontend/`):

```bash
pnpm install        # install dependencies
pnpm dev            # start Vite dev server
pnpm build          # build for production
pnpm lint           # run ESLint
pnpm format         # run Prettier
```

### Stack

- React 18 + TypeScript
- Vite (dev/build tooling)
- ESLint, Prettier, Husky (lint/format/pre-commit)
- Tailwind CSS (optional, see config)

### Component implementation

Wrap new UI components in the `frontend/src/` directory. Use idiomatic React patterns (function components, hooks, etc). Place shared assets in `frontend/src/assets/`. Main entry: `frontend/src/App.tsx`.

---

## Integration

- The backend exposes a REST API (`/analyze`, etc.) for the frontend to consume.
- Develop and test backend and frontend independently; connect via HTTP (see backend docs for endpoints).

---

## Contribution

- Follow async/await and type safety best practices in both backend and frontend.
- Add/modify components in `frontend/src/` and backend logic in `backend/src/`.
- See respective README.md files for more details and advanced usage.
