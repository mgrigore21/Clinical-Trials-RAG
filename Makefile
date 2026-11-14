# Short names for the commands you'll run most. `make help` lists them.
.PHONY: help setup lint format test fetch fetch-sample check-models

help: ## Show this list
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

setup: ## Install Python deps and the git pre-commit hook
	uv sync
	uv run pre-commit install
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")

lint: ## Run all pre-commit checks on every file
	uv run pre-commit run --all-files

format: ## Auto-format Python code
	uv run ruff format .
	uv run ruff check --fix .

test: ## Run the test suite
	uv run pytest

fetch: ## Download the full trial dataset (a few minutes, cached afterwards)
	uv run python -m ingestion.fetch

fetch-sample: ## Download a tiny sample (20 trials per topic) to try things out
	uv run python -m ingestion.fetch --limit 20

check-models: ## Check that the configured AI models respond
	uv run python -m app.providers
