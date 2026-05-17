.PHONY: help install lint lint-fix format format-check typecheck build preview dev

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

clean: ## Remove build artifacts and caches
	rm -rf dist node_modules/.cache .eslintcache

install: ## Install dependencies
	pnpm install --frozen-lockfile --ignore-scripts

lint: ## Run ESLint
	pnpm lint

lint-fix: ## Fix ESLint issues
	pnpm lint:fix

format: ## Run Prettier
	pnpm format

format-check: ## Check Prettier formatting
	pnpm format:check

typecheck: ## Run TypeScript type check
	pnpm type-check

build: ## Build for production
	pnpm build

preview: ## Preview production build
	pnpm preview

dev: ## Start dev server
	pnpm dev
