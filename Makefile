# Short names for the commands you'll run most. `make help` lists them.
.PHONY: help setup lint format test fetch fetch-sample check-models db-up db-down db-init db-status db-psql db-reset ingest ingest-sample search

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

db-up: ## Start Postgres (with pgvector) in Docker and wait until it is ready
	docker compose up -d --wait postgres

db-down: ## Stop Postgres (your data is kept)
	docker compose stop postgres

db-init: ## Create the tables and indexes (safe to repeat)
	uv run python -m app.db init

db-status: ## Show database versions, row counts and indexes
	uv run python -m app.db status

db-psql: ## Open a SQL prompt inside the database
	docker compose exec postgres psql -U rag trials

db-reset: ## DELETE ALL DATABASE DATA and start from an empty database
	docker compose down -v
	$(MAKE) db-up
	$(MAKE) db-init

ingest: ## Chunk, embed and store all downloaded trials (several minutes the first time)
	uv run python -m ingestion.run

ingest-sample: ## Same, but only for the small sample download (about a minute)
	uv run python -m ingestion.run --sample

search: ## Search the trials: make search Q="your question" [ARGS="--phase PHASE3 --mode vector"]
	uv run python -m app.rag.retrieve "$(Q)" $(ARGS)
