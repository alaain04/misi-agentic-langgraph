# Backend Makefile for common development tasks

.PHONY: sync dev lint format test mongo

sync:
	uv sync

dev:
	uv run dev

lint:
	uv run lint

format:
	uv run format

test:
	uv run test

mongo:
	docker compose up -d
