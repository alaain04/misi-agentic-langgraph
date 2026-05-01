# Backend Makefile for common development tasks

.PHONY: sync dev lint format test mongo graph

sync:
	uv sync

dev:
	uv run dev

lint:
	uv run format && uv run lint

format:
	uv run format

test:
	uv run test

mongo:
	docker compose up -d

graph:
	uv run python -c "from src.main_graph.graph import main_graph; main_graph.get_graph().draw_mermaid_png(output_file_path='graph.png')"
	@echo "Graph saved to backend/graph.png"
