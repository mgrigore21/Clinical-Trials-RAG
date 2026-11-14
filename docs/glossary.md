# Glossary

Plain-English definitions, in alphabetical order. Each term is also explained in the guide where
it first appears. When a new guide introduces a term, add it here in the same commit.

**Amazon Bedrock**: Amazon's service for using AI models over the internet. It is the AWS
counterpart of Ollama in this project. ([00](00-overview.md))

**Branch**: a separate line of work in git. We use one small branch per step (`step-NN-<topic>`)
so each step can be reviewed on its own. ([01](01-setup.md))

**Chunk**: a small piece of a document, such as a trial's eligibility section. We search chunks
instead of whole documents because they are easier to match to a question. ([00](00-overview.md))

**ClinicalTrials.gov**: a public US government website and database listing clinical studies
from around the world. Our data source. ([00](00-overview.md))

**Container**: a lightweight, isolated box that runs one program with everything it needs. Docker
creates and runs them. ([01](01-setup.md))

**Docker / Docker Desktop**: the tool that runs containers. Docker Desktop is the Mac application.
([01](01-setup.md))

**docker compose**: a way to start several containers together (for us: the database and the app)
from a single file. ([01](01-setup.md))

**Embedding**: a list of numbers representing the meaning of a piece of text. Similar meanings give
similar numbers, so the computer can search by meaning. Ours have 1024 numbers each.
([00](00-overview.md))

**Environment variable**: a named value in a program's surroundings (for example `PROVIDER=ollama`).
Used so the same code can behave differently locally and on AWS. ([01](01-setup.md))

**`.env` file**: a text file of environment variables for local development. Never committed to git.
([01](01-setup.md))

**Formatter**: a tool that rewrites code into one consistent style (ruff does this). ([01](01-setup.md))

**Git hook**: a script git runs automatically at a certain moment, such as just before a commit.
([01](01-setup.md))

**Hallucination**: when an AI states something that sounds right but is made up. RAG with citations
makes it easier to spot. ([00](00-overview.md))

**LLM (large language model)**: the kind of AI that writes text, like Llama or Claude. ([00](00-overview.md))

**Linter**: a tool that reads code and flags likely bugs and messy patterns (ruff does this).
([01](01-setup.md))

**Makefile / `make`**: a file of named shortcuts for commands. `make test` runs the tests.
([01](01-setup.md))

**NCT ID**: the unique identifier of a trial on ClinicalTrials.gov, like `NCT01234567`. We use it as
the citation. ([00](00-overview.md))

**Ollama**: a program that runs AI models on your own computer. ([01](01-setup.md))

**pgvector**: an add-on for the Postgres database that stores embeddings and finds the most similar
ones. ([00](00-overview.md))

**Postgres**: a widely used database. ([00](00-overview.md))

**pre-commit**: the tool that runs our automatic checks before each commit. ([01](01-setup.md))

**pytest**: the tool that runs our automatic tests. ([01](01-setup.md))

**RAG (Retrieval-Augmented Generation)**: first look up relevant documents, then have the AI answer
using only those. ([00](00-overview.md))

**ruff**: a fast Python linter and formatter. ([01](01-setup.md))

**uv**: a fast tool that installs Python and our libraries and keeps them in a virtual environment.
([01](01-setup.md))

**Virtual environment (`.venv/`)**: a private folder holding this project's Python libraries, so they
don't interfere with other projects. ([01](01-setup.md))
