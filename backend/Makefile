# Backend Makefile for common development tasks

.PHONY: sync dev lint format test mongo graph debug

sync:
	uv sync

dev:
	uv run dev

qa:
	uv run format && uv run lint && uv run test
	
lint:
	uv run format && uv run lint

format:
	uv run format

test:
	uv run test

mongo:
	docker compose up -d
