# Frontend

React + TypeScript web client for the misi-agentic dependency analysis tool.

## Quick Start

```bash
pnpm install   # install dependencies
pnpm dev       # start Vite dev server
pnpm build     # build for production
pnpm lint      # run ESLint
```

## Docs

- [Code Conventions](docs/code-conventions.md)

## Architecture

- Components: `src/components/`
- Hooks: `src/hooks/`
- Shared utilities: `src/lib/`
- API client: `src/api/`
- Entry point: `src/App.tsx`

## API Integration

The backend exposes a REST API consumed by this client. See [docs/api.md](../../docs/api.md) for the full contract.
