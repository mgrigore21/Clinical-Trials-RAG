# Glossary

Plain-English definitions, in alphabetical order. Each term is also explained in the guide where
it first appears. When a new guide introduces a term, add it here in the same commit.

**`.env` file**: a text file of environment variables for local development. Never committed to git.
([01](01-setup.md))

**Adapter**: a small wrapper that hides one service's details behind a common, simple shape. Our Ollama and Bedrock classes are adapters, so the rest of the app doesn't know which one it uses. ([07](07-providers.md))

**Amazon Bedrock**: Amazon's service for running AI models on AWS. The AWS counterpart of Ollama in this project; reached with the `boto3` library. ([07](07-providers.md))

**API**: a way for programs (not people) to ask a service for data. ClinicalTrials.gov has one; we use it to download trials. ([02](02-getting-the-data.md))

**Branch**: a separate line of work in git. We use one small branch per step (`step-NN-<topic>`)
so each step can be reviewed on its own. ([01](01-setup.md))

**Cache**: a saved copy we reuse instead of fetching the same thing again. Our downloaded trials in `data/raw/` are one. ([02](02-getting-the-data.md))

**Chunk**: a small piece of a document, such as a trial's eligibility section. We search chunks
instead of whole documents because they are easier to match to a question. ([00](00-overview.md))

**ClinicalTrials.gov**: a public US government website and database listing clinical studies
from around the world. Our data source. ([00](00-overview.md))

**Container**: a lightweight, isolated box that runs one program with everything it needs. Docker
creates and runs them. ([01](01-setup.md))

**Context window**: how much text a model can read at once (the prompt plus its answer), counted in tokens. Text beyond it is cut off. ([07](07-providers.md))

**Cosine similarity**: a number that says how close two embeddings are in meaning: near 1 for similar meaning, lower for unrelated. ([07](07-providers.md))

**Distractor**: data that is deliberately unrelated to the main theme (for us: asthma, depression, ...), so retrieval must pick the right trials instead of returning whatever exists. ([02](02-getting-the-data.md))

**Docker / Docker Desktop**: the tool that runs containers. Docker Desktop is the Mac application.
([01](01-setup.md))

**docker compose**: a way to start several containers together (for us: the database and the app)
from a single file. ([01](01-setup.md))

**Embedding**: a list of numbers representing the meaning of a piece of text. Similar meanings give
similar numbers, so the computer can search by meaning. Ours have 1024 numbers each.
([00](00-overview.md))

**Environment variable**: a named value in a program's surroundings (for example `PROVIDER=ollama`).
Used so the same code can behave differently locally and on AWS. ([01](01-setup.md))

**Formatter**: a tool that rewrites code into one consistent style (ruff does this). ([01](01-setup.md))

**Git hook**: a script git runs automatically at a certain moment, such as just before a commit.
([01](01-setup.md))

**Hallucination**: when an AI states something that sounds right but is made up. RAG with citations
makes it easier to spot. ([00](00-overview.md))

**Interface (Protocol)**: a written-down list of the methods something must have, so different classes can be used interchangeably. `LLMProvider` and `EmbeddingProvider` are ours. ([07](07-providers.md))

**Interventional study**: a trial that tests a treatment, as opposed to one that only observes people. ([02](02-getting-the-data.md))

**JSON**: plain text organised as nested lists and labelled values. The format most APIs use. ([02](02-getting-the-data.md))

**JSONL (JSON lines)**: a file with one JSON object per line. We store one trial per line. ([02](02-getting-the-data.md))

**Linter**: a tool that reads code and flags likely bugs and messy patterns (ruff does this).
([01](01-setup.md))

**LLM (large language model)**: the kind of AI that writes text, like Llama or Claude. ([00](00-overview.md))

**Makefile / `make`**: a file of named shortcuts for commands. `make test` runs the tests.
([01](01-setup.md))

**Mock**: a pretend version of something (like the ClinicalTrials.gov server) used in tests, so tests are fast, free and repeatable. ([02](02-getting-the-data.md))

**NCT ID**: the unique identifier of a trial on ClinicalTrials.gov, like `NCT01234567`. We use it as
the citation. ([00](00-overview.md))

**Ollama**: a program that runs AI models on your own computer and answers requests at `http://localhost:11434`. ([01](01-setup.md), [07](07-providers.md))

**Pagination**: receiving a long list one page at a time. Each response includes a "next page token", a bookmark we send back to continue. ([02](02-getting-the-data.md))

**pgvector**: an add-on for the Postgres database that stores embeddings and finds the most similar
ones. ([00](00-overview.md))

**Phase (of a trial)**: a trial's stage. Phase 1 checks safety in a few people, phase 2 whether it works, phase 3 compares it with the standard treatment in many people, phase 4 watches it after approval. ([02](02-getting-the-data.md))

**Postgres**: a widely used database. ([00](00-overview.md))

**pre-commit**: the tool that runs our automatic checks before each commit. ([01](01-setup.md))

**pytest**: the tool that runs our automatic tests. ([01](01-setup.md))

**Query parameter**: a filter written after the `?` in a web address (`?query.cond=obesity`). ([02](02-getting-the-data.md))

**RAG (Retrieval-Augmented Generation)**: first look up relevant documents, then have the AI answer
using only those. ([00](00-overview.md))

**Retry with backoff**: trying a failed request again after waiting, with longer waits each time (2s, 4s, 8s), to give a busy server room to recover. ([02](02-getting-the-data.md))

**ruff**: a fast Python linter and formatter. ([01](01-setup.md))

**Temperature**: how random a model's writing is. 0 means "always the most likely next word", so answers are repeatable; higher means more varied. ([07](07-providers.md))

**Token**: a piece of a word, roughly 3 to 4 characters of English. Models read, write, are limited and are billed in tokens. ([07](07-providers.md))

**Truncate**: to cut text off at a limit. Ollama does this quietly to over-long embedding input unless told not to, which is why we turn it off. ([07](07-providers.md))

**uv**: a fast tool that installs Python and our libraries and keeps them in a virtual environment.
([01](01-setup.md))

**Virtual environment (`.venv/`)**: a private folder holding this project's Python libraries, so they
don't interfere with other projects. ([01](01-setup.md))
