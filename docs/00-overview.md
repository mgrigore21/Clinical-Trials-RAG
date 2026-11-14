# 00 - Overview: the big picture

## What we're doing

We are building a small assistant that answers questions about clinical trials, for example
*"Which phase 3 trials test GLP-1 drugs for obesity?"*. It answers from real, public data from
[ClinicalTrials.gov](https://clinicaltrials.gov) and **shows which trials it used**, so you can check it.

It runs on your laptop first, and later on AWS.

> **Not medical advice.** This is a learning project. Do not use it to make medical decisions.

## Why

A plain AI chatbot answers from memory. It can sound sure of itself while being wrong
(this is called a **hallucination**), and it can't tell you where an answer came from. For
clinical trials that is a bad fit: the data changes all the time and exact details matter.

**RAG** (Retrieval-Augmented Generation) fixes this by splitting the job in two:

1. **Retrieve**: first *look up* the few trial records that are relevant to the question.
2. **Generate**: then ask the AI to write an answer *using only those records*, and to cite them.

We write this loop by hand, without LangChain or LlamaIndex, so every step is visible and you can
learn what is really happening. (The reasons are in [roadmap.md](roadmap.md).)

## How it works

```
 BEFORE any question (ingestion, done once and re-run to refresh)
 ┌────────────────┐   ┌─────────┐   ┌─────────┐   ┌──────────────────────┐
 │ ClinicalTrials │ → │ split   │ → │ turn    │ → │ store in a database  │
 │ .gov API       │   │ into    │   │ chunks  │   │ (Postgres + pgvector)│
 │ (download)     │   │ chunks  │   │ into    │   └──────────────────────┘
 └────────────────┘   └─────────┘   │ numbers │
                                    └─────────┘
 WHEN someone asks a question
 ┌──────────┐   ┌───────────────────┐   ┌─────────────────┐   ┌──────────────┐
 │ question │ → │ retrieve the most │ → │ AI writes an    │ → │ answer with  │
 │          │   │ relevant chunks   │   │ answer from     │   │ citations    │
 └──────────┘   └───────────────────┘   │ ONLY those      │   │ [NCT0123...] │
                                        └─────────────────┘   └──────────────┘
```

Words you will meet (all explained again in the [glossary](glossary.md)):

- **Chunk**: a small piece of a trial record (its summary, its eligibility rules, ...). Small
  pieces are easier to search than whole records.
- **Embedding**: a list of numbers that captures the *meaning* of a piece of text. Texts with similar
  meaning get similar numbers, which lets the computer search by meaning.
- **NCT ID**: the unique ID of a trial, like `NCT01234567`. We use it as the citation.

### The two environments

| | Locally (your Mac) | On AWS (later) |
|---|---|---|
| AI model that writes answers | Ollama (`llama3.1:8b`) | Amazon Bedrock |
| AI model that makes embeddings | Ollama (`mxbai-embed-large`) | Bedrock (Titan v2) |
| Database | Postgres + pgvector in Docker | Postgres + pgvector on AWS |

The code that is specific to one of them lives only in `app/providers/`. The rest of the app
doesn't know or care which one it is talking to.

### Folder map

| Folder / file | What it is |
|---|---|
| `app/` | The web service. `api/` has the endpoints, `rag/` the retrieve + generate logic, `providers/` the Ollama/Bedrock adapters, `config.py` the settings |
| `ingestion/` | Download, split, embed and store the trial data |
| `evals/` | The test questions and the scoring that tell us whether the assistant is any good |
| `tests/` | Automatic tests for the code |
| `docs/` | These guides. One per step |
| `CLAUDE.md` | Rules for AI coding assistants working in this repo |
| `infra/`, `.github/` | Cloud infrastructure and automation. Not created yet (later phases) |

Not every folder has content yet. We build the project in small steps. The checklist in
[roadmap.md](roadmap.md) shows which are done.

## Try it yourself

```bash
# 1. See the folders described above
ls

# 2. See which steps are done and what comes next
head -50 docs/roadmap.md
```

You should see `app`, `docs`, `evals`, `ingestion`, `tests` (plus files like `Makefile` and
`pyproject.toml`), and a checklist where step 01 is the one in progress.

## Common problems

- **"Where is the AI code?"** Not written yet. Step 01 only sets up the tools, and the code arrives
  step by step. See the checklist in [roadmap.md](roadmap.md).
- **"Why so many folders for so little code?"** They are placeholders for what comes later, so the
  structure stays stable as the project grows.
