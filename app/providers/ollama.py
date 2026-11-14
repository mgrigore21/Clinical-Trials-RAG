"""Ollama backend: models running on your own computer. See docs/07-providers.md."""

import httpx

from app.providers.base import Generation, Message, ProviderError, Usage


def _post(client: httpx.Client, url: str, payload: dict) -> dict:
    """POST to Ollama and turn the usual failures into messages that say what to do."""
    try:
        response = client.post(url, json=payload)
        response.raise_for_status()
    except httpx.ConnectError as exc:
        raise ProviderError(
            f"Cannot reach Ollama at {client.base_url}. Is the Ollama app running?"
        ) from exc
    except httpx.TimeoutException as exc:
        raise ProviderError(f"Ollama did not answer in time ({url}). Try a smaller model.") from exc
    except httpx.HTTPStatusError as exc:
        # Ollama explains itself in the body, e.g. "model 'x' not found".
        detail = exc.response.text.strip()
        hint = " Pull it first: `ollama pull <name>`." if exc.response.status_code == 404 else ""
        raise ProviderError(f"Ollama returned {exc.response.status_code}: {detail}.{hint}") from exc
    return response.json()


class OllamaLLM:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float,
        num_ctx: int,
        client: httpx.Client | None = None,
    ):
        self.model = model
        self.num_ctx = num_ctx
        self.client = client or httpx.Client(base_url=base_url, timeout=timeout)

    def generate(self, messages: list[Message], temperature: float = 0.0) -> Generation:
        data = _post(
            self.client,
            "/api/chat",
            {
                "model": self.model,
                "messages": messages,
                "stream": False,  # one complete answer instead of word-by-word chunks
                "options": {
                    "temperature": temperature,
                    # Ollama's default window is small. A prompt holding several retrieved trials
                    # that doesn't fit is cut off silently, so the model answers from partial
                    # context without telling anyone. We set it explicitly.
                    "num_ctx": self.num_ctx,
                },
            },
        )
        return Generation(
            text=data["message"]["content"],
            usage=Usage(
                input_tokens=data.get("prompt_eval_count", 0),
                output_tokens=data.get("eval_count", 0),
            ),
        )


class OllamaEmbedder:
    def __init__(
        self,
        base_url: str,
        model: str,
        dim: int,
        timeout: float,
        client: httpx.Client | None = None,
    ):
        self.model = model
        self.dim = dim
        self.client = client or httpx.Client(base_url=base_url, timeout=timeout)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        data = _post(
            self.client,
            "/api/embed",
            {
                "model": self.model,
                "input": texts,
                # By default Ollama quietly cuts any text longer than the model's limit
                # (512 tokens for mxbai-embed-large) and still reports success: the end of a
                # long chunk would simply never be searchable. We prefer a loud error.
                "truncate": False,
            },
        )
        vectors = data["embeddings"]
        # The database column has a fixed size. Catch a wrong model here, with a clear message,
        # not later as a confusing database error. See docs/07-providers.md.
        if vectors and len(vectors[0]) != self.dim:
            raise ProviderError(
                f"Model {self.model} returns {len(vectors[0])} numbers per text, but "
                f"EMBEDDING_DIM is {self.dim}. They must match."
            )
        return vectors
