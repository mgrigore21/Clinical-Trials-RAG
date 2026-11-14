# Clinical-Trials-RAG

A RAG assistant (Retrieval-Augmented Generation) that answers questions about public clinical-trial
data from [ClinicalTrials.gov](https://clinicaltrials.gov) and cites the trials it used. It runs
locally (Ollama) and, later, on AWS (Bedrock).

This is a **learning / demo repository**. The RAG loop is written by hand, without LangChain or
LlamaIndex, and every step comes with a plain-English guide.

> **Not medical advice.** This project is for research and demonstration only. Do not use its
> answers to make medical decisions. Always consult a qualified professional.

## Where to start

1. [docs/00-overview.md](docs/00-overview.md): the big picture
2. [docs/roadmap.md](docs/roadmap.md): the plan, the progress checklist and the decision log
3. [docs/01-setup.md](docs/01-setup.md): install the tools and run the checks
4. [docs/glossary.md](docs/glossary.md): every term explained

## Quick start

```bash
make setup   # install Python libraries, the git hook and a .env file
make test    # run the tests
make help    # list all shortcuts
```

You also need Docker Desktop and Ollama; [docs/01-setup.md](docs/01-setup.md) walks through it.

## Guides

| Guide | Status |
|---|---|
| [00 Overview](docs/00-overview.md) | done |
| [01 Setup](docs/01-setup.md) | done |
| [02 Getting the data](docs/02-getting-the-data.md) | done |
| 03 Chunking and embeddings | planned |
| 04 The database | planned |
| 05 Retrieval | planned |
| 06 Generation and citations | planned |
| 07 Providers | planned |
| 08 Running locally | planned |
| 09 Evaluation | planned |

The live checklist is in [docs/roadmap.md](docs/roadmap.md).

## License

MIT, see [LICENSE](LICENSE).
