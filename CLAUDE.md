# CLAUDE.md — rules for AI sessions in this repo

This is a **learning / demo repository**: a RAG assistant over public ClinicalTrials.gov data
that runs locally (Ollama) and on AWS (Bedrock). Understanding matters more than speed.

## Start here
- The plan and the reasons behind the main design choices live in `docs/roadmap.md`.
  Read it at the start of a session and update it when a decision changes.

## Documentation is part of "done"
- Every change ships with its matching guide in `docs/` updated **in the same commit**.
- Guides are plain English for someone new to RAG, Docker, evals and AWS. Explain jargon the
  first time it appears and add it to `docs/glossary.md`.
- Every guide uses the same sections, in this order:
  1. **What we're doing**
  2. **Why**
  3. **How it works**
  4. **Try it yourself** (exact commands + the output you should expect)
  5. **Common problems**
- Every "Try it yourself" command must actually run as written.
- Code comments explain *why*, not *what*, and point to the relevant guide when useful
  (e.g. `# See docs/05-retrieval.md — why we fuse two rankings`).

## Workflow
- One small branch per step (`step-NN-<topic>`), branched from an up-to-date `main`.
- Before declaring a step done: `uv run pre-commit run --all-files` and `uv run pytest` pass.
- Progress is tracked in the checklist in `docs/roadmap.md`. Tick a step's box in the same commit
  that finishes it, and add a line to the roadmap's decision log when a decision is made or changed.
- **Never** push, open PRs, or touch AWS resources without asking the user first.
- Prefer small, readable code over clever abstractions. No LangChain/LlamaIndex: the RAG loop
  is written by hand on purpose so every step is visible.

## Project conventions
- Python 3.12 managed by `uv`. Lint/format with `ruff`.
- Settings come only from environment variables via `app/config.py`.
- Provider-specific code (Ollama, Bedrock) lives only in `app/providers/`.
- Embedding dimension is 1024 in every environment (see `docs/03-chunking-and-embeddings.md`).
- The README keeps the "not medical advice" disclaimer.
