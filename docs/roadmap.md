# Roadmap

The plan for this project: what we build, in which order, and why we made the main choices.
This is a living document. When a decision changes, update it in the same commit.

> This is a roadmap, not a step guide, so it does not use the five-section guide format.
> The numbered guides (`01-setup.md`, `02-...`) do.

## The goal

A RAG assistant (Retrieval-Augmented Generation: the AI looks up relevant documents first, then
answers using only what it found) over public clinical-trial data from ClinicalTrials.gov. It runs:

- **locally**, with Ollama for the language model and Postgres + pgvector for search, and
- **on AWS**, with Bedrock for the models, built with Terraform, CI/CD, an evaluation gate and monitoring.

It is a learning repo: understanding each step matters more than speed.

## Progress

This checklist is the single source of truth for "where are we". Each line is one step: one branch
(`step-NN-<topic>`), one guide, one local commit. Tick the box **in the same commit** that finishes
the step, so the box and the code can never disagree. Any new session should start by reading this.

A step is done when its guide is written, every "Try it yourself" command runs, and
`uv run pre-commit run --all-files` and `uv run pytest` pass.

### Phase 1 - Local app
- [x] **step-01-setup** - repo skeleton, uv, ruff, pre-commit, `config.py`, `CLAUDE.md`, this roadmap
  - [x] `CLAUDE.md`, `pyproject.toml`, `uv.lock`, `.python-version`
  - [x] `.pre-commit-config.yaml`, `Makefile`, `.env.example`, `.dockerignore`, `.gitignore` additions
  - [x] `app/config.py` and `tests/test_config.py` written
  - [x] `docs/roadmap.md`
  - [x] pytest, ruff and pre-commit confirmed passing
  - [x] `docs/00-overview.md`, `docs/01-setup.md`, `docs/glossary.md`, README with disclaimer
  - [x] Docker Desktop and Ollama installed, models pulled (embedding size verified: 1024)
  - [x] committed locally
- [x] **step-02-data** - fetch trials from the ClinicalTrials.gov API, cache them (guide `02`)
- [x] **step-03-providers** - `LLMProvider` / `EmbeddingProvider`, Ollama adapter, Bedrock adapter (guide `07`)
- [ ] **step-04-database** - Postgres + pgvector schema, indexes, `app/db.py` (guide `04`)
- [ ] **step-05-ingestion** - chunking, embedding, idempotent upsert, `ingestion/run.py` (guide `03`)
- [ ] **step-06-retrieval** - vector + full-text search fused with RRF (guide `05`)
- [ ] **step-07-generation** - grounded prompt, citations, invalid-citation check (guide `06`)
- [ ] **step-08-api-docker** - `/ask`, `/health`, Dockerfile, docker compose (guide `08`)

### Phase 2 - Evaluation
- [ ] **step-09-eval-dataset** - question set with gold answers, retrieval metrics (guide `09`, part 1)
- [ ] **step-10-eval-judge** - LLM-as-judge, hand-scored calibration set, agreement report (guide `09`, part 2)
- [ ] **step-11-eval-gate** - thresholds, `make eval`, non-zero exit on failure (guide `09`, part 3)

### Later (outlined only, not planned in detail)
- [ ] Agent + MCP extension (after the Phase 2 baseline)
- [ ] Phase 3: CI and the eval gate on pull requests
- [ ] Phases 4-5: Terraform and AWS deployment
- [ ] Phases 6-7: monitoring and polish

## Main design decisions

1. **No LangChain or LlamaIndex.** We write the retrieval and generation loop by hand over
   `psycopg` and pgvector, with our own provider adapters. Every step stays visible, and evaluation,
   tracing and cost counting are simpler. The folder is still called `app/rag/`.
2. **Ollama runs natively on the Mac, not in docker compose.** Docker on macOS cannot use the GPU,
   so a containerised Ollama is very slow. Compose runs `postgres` (`pgvector/pgvector:pg16`) and
   `app`; the app reaches Ollama at `http://host.docker.internal:11434`.
3. **Hybrid retrieval.** Vector search plus Postgres full-text search, combined with RRF
   (Reciprocal Rank Fusion). Drug names and trial IDs like "GLP-1" or "NCT0123..." are keyword-heavy
   and pure embeddings handle them badly. The cost is one extra SQL query and no extra infrastructure.
4. **Same embedding size everywhere: 1024.** Local `mxbai-embed-large` (1024) and Bedrock Titan Text
   Embeddings v2 (configurable to 1024) share one database schema. Vectors from different models are
   never mixed: each environment ingests with its own model. The size comes from `EMBEDDING_DIM`.
5. **Models.** Generator `llama3.1:8b` (fits comfortably in 32 GB; `qwen2.5:14b` is the upgrade
   option). The judge uses the same Ollama server with a larger model. All configurable via env.

## Phase 1 - Local app

**Tools to install (ask first):** Docker Desktop, Ollama, then pull `llama3.1:8b` and `mxbai-embed-large`.

**Repo skeleton:** `pyproject.toml` (uv, Python 3.12), `Makefile`, `.env.example`,
`.pre-commit-config.yaml` (ruff + whitespace checks), `.gitignore`, `.dockerignore`, `CLAUDE.md`.

**Key files**
- `app/config.py` - settings from environment variables (`PROVIDER=ollama|bedrock`, `DATABASE_URL`,
  `OLLAMA_BASE_URL`, `LLM_MODEL`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, `TOP_K`, `AWS_REGION`, timeouts).
- `app/providers/` - `base.py` defines `LLMProvider.generate(messages) -> (text, usage)` and
  `EmbeddingProvider.embed(texts) -> vectors`; `factory.py` picks one from settings; `ollama.py`
  (httpx to `/api/chat` and `/api/embed`); `bedrock.py` (boto3, written now, used in Phase 4).
  Token usage is returned from both, because monitoring needs it later.
- `app/db.py` + `ingestion/schema.sql` - tables `trials` (nct_id, title, phase, status, conditions,
  interventions, sponsor, raw JSON, content_hash) and `chunks` (id, nct_id, section, text,
  `embedding vector(1024)`, full-text `tsv`); HNSW index on embeddings, GIN index on `tsv`.
- `ingestion/fetch.py` - ClinicalTrials.gov API v2 with page tokens. A coherent slice
  (obesity, type 2 diabetes, cardiometabolic, plus a few unrelated areas as distractors), about 3-5k
  trials, cached in `data/raw/*.jsonl` (git-ignored) so re-runs don't hit the API.
- `ingestion/chunk.py` - structure-aware chunks per trial: summary, description, eligibility,
  outcomes. Each chunk starts with `NCT ID | title | phase` so search carries the trial's identity.
- `ingestion/run.py` - fetch, chunk, embed in batches, upsert; safe to re-run via `content_hash`.
- `app/rag/retrieve.py` - vector query + full-text query, RRF fusion, top-k, grouped by trial.
- `app/rag/generate.py` - prompt that answers only from the context and cites `[NCT########]`;
  afterwards, any cited ID that was not actually retrieved is stripped or flagged.
- `app/api/main.py` - `POST /ask {question, top_k?}` returns answer, citations, retrieved chunks,
  timings and token usage; `GET /health` checks the database.
- `Dockerfile` (multi-stage, uv, non-root) and `docker-compose.yml` (postgres with healthcheck,
  app, and a one-shot `ingest` service behind a compose profile).
- `tests/` - unit tests (chunking, RRF, citation checking, provider factory, config) and an
  integration test for `/ask` with fake providers and a real pgvector container.

**Done when:** `docker compose up`, run ingestion, then
`curl -X POST localhost:8000/ask -d '{"question":"Which phase 3 trials test GLP-1 drugs for obesity?"}'`
returns a cited answer whose NCT IDs exist in the database.

## Phase 2 - Evaluation

- `evals/dataset.jsonl` - 50-100 questions: `{id, question, expected_nct_ids, reference_answer, type}`.
  List-style questions get their gold answers from SQL on the ingested data (not from an LLM).
  Single-trial questions are drafted by an LLM and reviewed by hand. Types: single-trial lookup,
  multi-trial filter, eligibility, comparison, and a few **unanswerable** questions where the right
  answer is "not in the data".
- `evals/retrieval.py` - recall@k, hit rate and MRR against `expected_nct_ids`.
- `evals/judge.py` - LLM-as-judge with rubric prompts and JSON output: **faithfulness** (are the
  claims supported by the retrieved text?) and **correctness** (vs the reference answer), plus a
  deterministic **citation validity** check (cited IDs are a subset of retrieved IDs).
- `evals/calibration/` - 15-20 answers scored by hand; `calibrate.py` reports judge-vs-human
  agreement (accuracy and Cohen's kappa). The judge is only trusted if it clears a stated bar.
- `evals/thresholds.yaml` - minimums for each metric, set after a first baseline run.
- `evals/run.py` (`make eval`) - runs the dataset against the live `/ask`, writes
  `evals/reports/<timestamp>.json` and `report.md`, and **exits non-zero if any threshold fails**.
  Phase 3 will gate pull requests on this.

**Done when:** `make eval` prints scores, writes the report, and the judge-calibration agreement is documented.

## Documentation plan

One plain-English guide per step, in the same commit as its code. Each follows
*What we're doing, Why, How it works, Try it yourself, Common problems*.

| Guide | Topic |
|---|---|
| `00-overview.md` | What the project does, the big picture (question, retrieve, generate, cited answer), folder map |
| `01-setup.md` | Installing Docker, Ollama and uv; pre-commit and linting |
| `02-getting-the-data.md` | ClinicalTrials.gov, its API, pagination, what a trial record looks like |
| `03-chunking-and-embeddings.md` | Why we split documents, what embeddings are, why 1024 dimensions |
| `04-the-database.md` | Postgres + pgvector, the two tables, what the indexes do |
| `05-retrieval.md` | Vector vs keyword search, RRF with a worked example |
| `06-generation-and-citations.md` | The prompt, why we cite NCT IDs, catching made-up citations |
| `07-providers.md` | The adapter pattern: Ollama locally, Bedrock on AWS |
| `08-running-locally.md` | docker compose step by step, expected output, common errors |
| `09-evaluation.md` | Recall and MRR, LLM-as-judge, calibration, thresholds |
| `glossary.md` | Every term, explained once |

## How we use AI agents while building

- **Pair programming, one step at a time.** One small branch per step; review the code and its
  guide before merging. Ask "why?" freely.
- **`CLAUDE.md`** keeps the rules the same across sessions.
- **Eval-driven improvement (from Phase 2).** With `make eval` in place, give the agent a goal such
  as "raise faithfulness without lowering recall". It changes prompt, chunking or retrieval, reruns the
  eval and keeps only what helps. Log each experiment and its scores in `docs/09-evaluation.md`.
- **Review.** Run `/code-review` before each merge. In Phase 3, optionally add the Claude Code
  GitHub Action next to the eval gate.

## Planned extension: agent + MCP

Only after the Phase 2 baseline exists (without a baseline we cannot show it is better), and at the
latest in Phase 7. Plain RAG treats exact constraints ("phase 3", "recruiting", "how many") as fuzzy
hints; an agent with tools can apply them exactly.

- Tools in `app/agent/tools.py`: `search_trials(query, phase?, status?, condition?)`,
  `get_trial(nct_id)`, `count_trials(filters)`.
- `POST /agent` next to `/ask`, with a step limit and per-call timeouts. `LLMProvider` gains
  `generate_with_tools(...)` (Ollama `llama3.1` and Bedrock Converse both support tool calling).
- `make eval --target ask|agent` compares both side by side (quality, latency, tool calls, tokens).
- An MCP server (`mcp_server/`) exposes the same tools so Claude Desktop or Claude Code can query the database.
- Guides: `10-agents-and-tools.md` and `11-mcp.md`.

## Open questions for later phases

- **Phase 3 CI runner:** GitHub-hosted runners (about 4 vCPU, 16 GB, no GPU) cannot run `llama3.1:8b`
  at a usable speed. Decide then: a small CPU model with a small ingest subset, or a hosted API for
  generation and judging. `evals/` is designed so model and dataset size are configurable.
- **Phases 4-5:** pgvector on RDS Postgres in public subnets vs a Postgres container on Fargate
  (cost vs "destroy nightly"). Before anything: AWS account, budget alert, Bedrock model access.
- **Phases 6-7:** tracing and cost fields are already part of the `/ask` response, so monitoring is additive.

## Checking that it works end to end

1. `make setup`, then `pre-commit run --all-files` and `uv run pytest` pass.
2. `docker compose up -d`, then `docker compose run --rm ingest`; row counts in `trials` and `chunks` are in the thousands.
3. The GLP-1 / obesity `curl` above returns a cited answer; spot-check the NCT IDs on clinicaltrials.gov.
4. Every "Try it yourself" command in `docs/` runs as written.
5. `make eval` produces the report; deliberately break retrieval (for example `TOP_K=1`) and confirm the thresholds fail with a non-zero exit.

## Decision log

The design decisions above are the starting point. Add a line here whenever a decision is made or
changed along the way: date, what, and why in one sentence. Do not rewrite old lines; add a new
one that says what changed.

| Date | Decision | Why |
|---|---|---|
| 2026-11-14 | MIT license | Short, familiar, fine for a portfolio/learning project |
| 2026-11-14 | One plain-English guide per step, shipped in the same commit as the code | The repo exists to learn from |
| 2026-11-14 | Install Docker Desktop and Ollama from the official downloads, not Homebrew | Homebrew wasn't installed and its installer needs sudo; the official installers need no extra tool |
| 2026-11-14 | Fetch 9 capped topics (4 core + 5 distractors), interventional studies only, phase 2/3 for the broad topics: 3,408 unique trials | The API has 15k+ obesity trials alone; a capped, focused slice downloads in seconds and still supports the GLP-1 demo question |
| 2026-11-14 | Request only the fields we need (`fields=`) | Full records are mostly hospital location lists; the slice is 21 MB instead of hundreds |
| 2026-11-14 | Limited (`--limit`) downloads go to `data/raw-sample/` | A sample must never be mistaken for the full dataset by later steps |
| 2026-11-14 | Only about half of trials have a detailed description | Chunking (step 05) must handle missing sections |
| 2026-11-14 | Embedder sends `truncate: false`; chunks must stay under about 1,500 characters | `mxbai-embed-large` reads at most 512 tokens (measured about 0.23 tokens per character, 0.31 worst case) and Ollama otherwise cuts longer text silently. Step 05 chunking must respect this |
| 2026-11-14 | Set the Ollama context window explicitly (`OLLAMA_NUM_CTX=8192`) | Ollama's default window silently truncates long prompts, which would make answers use partial context |
| 2026-11-14 | `LLM_MODEL` / `EMBEDDING_MODEL` hold Ollama names locally and Bedrock model IDs on AWS | One setting name, a value per environment; no extra provider-specific settings to keep in sync |
| 2026-11-14 | Keep progress as a checklist in this file, not a Ralph-style autonomous loop | We want to understand each step; a loop runs unattended and skips review. Revisit for eval-driven tuning in Phase 2 |
