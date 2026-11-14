# 07 - Providers: one app, two AI backends

(Built in Step 03. The number `07` is its place in the final reading order; see the
[roadmap](roadmap.md).)

## What we're doing

Writing the code that talks to the AI models, in a way that lets the same app run on your laptop
(**Ollama**) and on AWS (**Amazon Bedrock**) without changing anything except settings.

The app needs only two abilities from an AI backend:

1. **Generate text**: given a conversation, write the next reply. This writes answers.
2. **Embed text**: turn texts into lists of numbers that capture their meaning. This makes search work.

The code is in `app/providers/`:

| File | Role |
|---|---|
| `base.py` | The two abilities, written down as interfaces, plus small data types |
| `ollama.py` | The local backend |
| `bedrock.py` | The AWS backend (written now, used on AWS in a later phase) |
| `factory.py` | Reads the settings and hands out the right backend |
| `__main__.py` | A quick "do my models work?" check |

## Why

- **The same code must run in two places.** Retrieval, generation and evaluation shouldn't contain
  `if running_on_aws:` everywhere. They ask for "an LLM" and "an embedder" and use them.
- **This is the adapter pattern.** An **adapter** wraps a specific service behind a common, simple
  shape. Each adapter hides the details of its service: the web addresses, the request format and
  how token counts are reported. Everything provider-specific lives in this one folder, which is the
  rule in `CLAUDE.md`.
- **Easy to test.** Because the app only depends on the two abilities, tests can hand it a pretend
  backend. No real model, no cost, no waiting.
- **Fail early, with clear messages.** Most "AI app" bugs are boring configuration problems: the app
  isn't running, the model isn't downloaded, the vector is the wrong size. We turn them into errors that say
  exactly what to do.

## How it works

### The two interfaces

In `base.py`, `LLMProvider` and `EmbeddingProvider` are written as **Protocols**. A Protocol (an
**interface**) says "anything that has a `generate` method like this counts as an `LLMProvider`". The
classes in `ollama.py` and `bedrock.py` never need to declare it; they just have the right methods.

```python
class LLMProvider(Protocol):
    def generate(self, messages: list[Message], temperature: float = 0.0) -> Generation: ...


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

- A **message** is `{"role": "system" | "user" | "assistant", "content": "..."}`. The `system` message
  holds the instructions ("answer only from the trials below"), and `user` holds the question.
- **Temperature** controls randomness. 0 means "always take the most likely next word", so
  the same question gives (nearly) the same answer. We default to 0: repeatable answers make the
  evaluation phase meaningful.
- Every answer comes back with a **`Usage`**: the number of **tokens** read and written. A token is a
  piece of a word (roughly 3 to 4 characters of English). Models are limited, timed and billed in tokens,
  so we record them from day one.

### Ollama (`ollama.py`)

Ollama is a small web server on your Mac (`http://localhost:11434`). We call two of its endpoints
(addresses): `/api/chat` to write text and `/api/embed` to embed. Three details are worth knowing, because
each hides a quiet failure:

**1. The context window (`OLLAMA_NUM_CTX`).** A model can only read so much at once: its **context
window**, measured in tokens (the prompt *plus* the answer it writes). Our prompts will hold several
retrieved trials. If the prompt is too long for the window, it gets cut off *silently* and the model
answers from partial information without telling anyone. So we set the window explicitly: 8,192 tokens by
default, which is comfortable for `llama3.1:8b` with 32 GB of RAM.

**2. The embedding model has a hard limit of 512 tokens.** `mxbai-embed-large` can't read more than that
at once. By default Ollama just **truncates** (cuts off) longer text and still reports success. The end of a long
chunk would never be searchable and nobody would know. We send `truncate: false` so Ollama refuses
instead: you get a loud error, not a silent loss. This also gives the next steps a size budget. On
real trial text we measured about 0.23 tokens per character (at worst 0.31), so 512 tokens is about
1,660 characters in the worst case. **Chunks must stay well under about 1,500 characters** (see the
chunking guide).

**3. The vector size must match.** `EMBEDDING_DIM` (1024) is the number of numbers per vector, and the
database column will have exactly that size. If you switch to a model that outputs a different size, the
embedder stops immediately with `Model ... returns N numbers per text, but EMBEDDING_DIM is 1024`.

Also: `"stream": False` asks for the whole answer at once instead of word by word, and `embed` sends a
whole list of texts in one request, which is much faster than one request per text.

### Bedrock (`bedrock.py`)

Bedrock is Amazon's service for running models. It uses the `boto3` library (AWS's Python toolkit).

- **Text**: the **Converse** API. It wants the system prompt separate from the conversation, and every
  piece of text wrapped in a "content block" (`[{"text": "..."}]`). The adapter does this translation so the
  rest of the app never sees it.
- **Embeddings**: Amazon **Titan Text Embeddings v2** through `invoke_model`. It can output 256, 512 or
  1024 numbers; we ask for **1024** so the same database works locally and on AWS. It embeds one text per call.
- **Errors**: AWS errors are caught and re-raised as our own `ProviderError` that names the model.

This file is written now so the two backends sit side by side, but it's only used for real on AWS in
Phase 4. The tests use a fake AWS client, so nothing in this step contacts AWS, needs credentials or
costs money. With Bedrock, `LLM_MODEL` and `EMBEDDING_MODEL` hold Bedrock **model IDs** instead of
Ollama names (the same setting, a different value per environment).

### The factory (`factory.py`)

`get_llm()` and `get_embedder()` read the settings and return the matching backend:

| `PROVIDER` | `get_llm()` | `get_embedder()` |
|---|---|---|
| `ollama` (default) | `OllamaLLM` | `OllamaEmbedder` |
| `bedrock` | `BedrockLLM` | `BedrockEmbedder` |

It's the only file that knows both exist. Moving the project to AWS is therefore a settings change.

### Tests (`tests/test_providers.py`)

There are 11 tests, all fast and offline. Ollama is played by `httpx.MockTransport`, a pretend server. AWS
is played by a small fake class that records what it was sent and returns an answer shaped like the real
one. They check, among other things: the exact request sent, that `truncate: false` is set, that the system
prompt is split correctly for Bedrock, that the errors say what to do, and that the factory picks the right
class.

## Try it yourself

Ollama must be running (the llama icon in the menu bar) with both models pulled (see the
[setup guide](01-setup.md)).

### 1. Check that your models work

```bash
make check-models
```

Expected, with timings and the answer's wording varying a little:

```
uv run python -m app.providers
provider: ollama
embedding: mxbai-embed-large -> 1024 numbers (0.1s)
answer (llama3.1:8b, 3.0s, 24 tokens in, 51 out):
  A phase 3 clinical trial is a large-scale, late-stage trial that aims to confirm the efficacy and safety
  of a new treatment or drug in a larger population, ...
```

The first call after Ollama has been idle can take 10 seconds or more while it loads the model into memory.

### 2. See that embeddings capture meaning

Embeddings of similar texts are close together. **Cosine similarity** measures that closeness: 1 means identical
direction (same meaning), values near 0 mean unrelated.

```bash
uv run python - <<'EOF'
from math import sqrt

from app.providers.factory import get_embedder

texts = [
    "semaglutide injections for weight loss in obese adults",
    "a GLP-1 drug to treat obesity",
    "inhaled corticosteroid for asthma in children",
]
a, b, c = get_embedder().embed(texts)


def cosine(x, y):
    dot = sum(i * j for i, j in zip(x, y))
    return dot / (sqrt(sum(i * i for i in x)) * sqrt(sum(j * j for j in y)))


print(f"obesity drug  vs  GLP-1 obesity drug : {cosine(a, b):.2f}")
print(f"obesity drug  vs  asthma inhaler     : {cosine(a, c):.2f}")
EOF
```

Expected (close to):

```
obesity drug  vs  GLP-1 obesity drug : 0.72
obesity drug  vs  asthma inhaler     : 0.48
```

The two obesity sentences share almost no words, yet they score clearly higher together than with the asthma one.
That is **search by meaning**, and it's the foundation of retrieval.

### 3. Break things on purpose

Each of these shows an error message, and what to do about it.

```bash
OLLAMA_BASE_URL=http://localhost:9999 uv run python -m app.providers
```

Expected: `FAILED: Cannot reach Ollama at http://localhost:9999. Is the Ollama app running?`

```bash
LLM_MODEL=nope:1b uv run python -m app.providers
```

Expected: the embedding line works, then:

```
FAILED: Ollama returned 404: {"error":"model 'nope:1b' not found"}. Pull it first: `ollama pull <name>`.
```

```bash
EMBEDDING_DIM=512 uv run python -m app.providers
```

Expected: `FAILED: Model mxbai-embed-large returns 1024 numbers per text, but EMBEDDING_DIM is 512. They must match.`

### 4. Run the tests

```bash
make test
```

Expected: `22 passed` (11 from earlier steps and 11 new ones for providers).

## Common problems

- **`Cannot reach Ollama ... Is the Ollama app running?`**: open the Ollama app and try again. Check
  with `curl http://localhost:11434/api/tags`.
- **`model '...' not found`**: pull it with `ollama pull llama3.1:8b` (or `mxbai-embed-large`), and
  check that `LLM_MODEL` / `EMBEDDING_MODEL` in `.env` match a name in `ollama list`.
- **`returns N numbers per text, but EMBEDDING_DIM is ...`**: the embedding model and `EMBEDDING_DIM`
  disagree. `mxbai-embed-large` gives 1024. If you change the model, the database must be
  rebuilt with the new size, and vectors from different models must never be mixed.
- **`the input length exceeds the context length`** (HTTP 400 from `/api/embed`): a text is longer than the
  embedding model's 512-token limit. Make the chunk smaller. This is intentional; see "The embedding model has
  a hard limit" above.
- **The first answer is slow (10 s or more)**: Ollama loads the model into memory on first use and unloads it
  after a few idle minutes. Later answers are faster.
- **Answers get slower or the Mac struggles with a bigger `OLLAMA_NUM_CTX`**: a larger window needs more memory.
  Keep 8192 unless you have a reason.
- **With `PROVIDER=bedrock` it fails with an AWS error**: expected for now. Bedrock needs an AWS account,
  credentials and model access, which come in Phase 4. Nothing in the local steps needs it.
