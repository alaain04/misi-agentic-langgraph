# Frontend Documentation Index

Welcome to the frontend! Use this index to navigate the main documentation and conventions for UI development.

## 📚 Index

- [Component Architecture](#component-architecture)
- [Directory Structure](#directory-structure)
- [Development Flow](#development-flow)
- [Code Conventions](#code-conventions)
- [API Integration](#api-integration)

## Component Architecture

- Components live in `src/components/`, hooks in `src/hooks/`, and shared code in `src/lib/`.
- Main entry: `src/App.tsx`.

## Directory Structure

- See `src/` for all UI code. Assets in `src/assets/`.

## Development Flow

- Use `pnpm dev` for local development. See [../CLAUDE.md](../CLAUDE.md#frontend) for commands.
- Follow idiomatic React patterns (function components, hooks, etc).

## Code Conventions

See [CODE_CONVENTIONS.md](CODE_CONVENTIONS.md) for frontend code conventions.

**Agents:** When implementing frontend features, always follow the conventions and architecture described here and in [CODE_CONVENTIONS.md](CODE_CONVENTIONS.md).

## API Integration

- The backend exposes a REST API (`/analyze`, etc.) for the frontend to consume. Use `src/api/` for API logic.

# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react/README.md) uses [Babel](https://babeljs.io/) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type aware lint rules:

- Configure the top-level `parserOptions` property like this:

```js
export default tseslint.config({
  languageOptions: {
    // other options...
    parserOptions: {
      project: ['./tsconfig.node.json', './tsconfig.app.json'],
      tsconfigRootDir: import.meta.dirname,
    },
  },
})
```

- Replace `tseslint.configs.recommended` to `tseslint.configs.recommendedTypeChecked` or `tseslint.configs.strictTypeChecked`
- Optionally add `...tseslint.configs.stylisticTypeChecked`
- Install [eslint-plugin-react](https://github.com/jsx-eslint/eslint-plugin-react) and update the config:

```js
// eslint.config.js
import react from 'eslint-plugin-react'

export default tseslint.config({
  // Set the react version
  settings: { react: { version: '18.3' } },
  plugins: {
    // Add the react plugin
    react,
  },
  rules: {
    // other rules...
    // Enable its recommended rules
    ...react.configs.recommended.rules,
    ...react.configs['jsx-runtime'].rules,
  },
})
```
