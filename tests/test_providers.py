"""Tests for the model backends (app/providers/).

Nothing here talks to Ollama or AWS: httpx.MockTransport plays Ollama, and a small fake class
plays the AWS client. So the tests run anywhere, instantly, and never cost money.
"""

import io
import json

import httpx
import pytest
from botocore.exceptions import ClientError

from app.config import Settings
from app.providers.base import ProviderError
from app.providers.bedrock import BedrockEmbedder, BedrockLLM
from app.providers.factory import get_embedder, get_llm
from app.providers.ollama import OllamaEmbedder, OllamaLLM

# --- Ollama -------------------------------------------------------------------------------


def ollama_client(handler) -> httpx.Client:
    return httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))


def make_llm(handler) -> OllamaLLM:
    return OllamaLLM(
        "http://ollama.test", "llama3.1:8b", timeout=5, num_ctx=8192, client=ollama_client(handler)
    )


def make_embedder(handler, dim=3) -> OllamaEmbedder:
    return OllamaEmbedder(
        "http://ollama.test", "mxbai", dim=dim, timeout=5, client=ollama_client(handler)
    )


def test_ollama_generate_sends_the_request_and_reads_text_and_usage():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content), path=request.url.path)
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "Paris."},
                "prompt_eval_count": 27,
                "eval_count": 3,
            },
        )

    result = make_llm(handler).generate(
        [{"role": "user", "content": "Capital of France?"}], temperature=0.2
    )

    assert result.text == "Paris."
    assert (result.usage.input_tokens, result.usage.output_tokens) == (27, 3)
    assert seen["path"] == "/api/chat"
    assert seen["stream"] is False
    assert seen["options"] == {"temperature": 0.2, "num_ctx": 8192}
    assert seen["messages"] == [{"role": "user", "content": "Capital of France?"}]


def test_ollama_not_running_gives_an_actionable_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderError, match="Is the Ollama app running"):
        make_llm(handler).generate([{"role": "user", "content": "hi"}])


def test_ollama_missing_model_tells_you_to_pull_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'x' not found"})

    with pytest.raises(ProviderError, match="ollama pull"):
        make_llm(handler).generate([{"role": "user", "content": "hi"}])


def test_ollama_embed_returns_one_vector_per_text_and_refuses_silent_truncation():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]})

    vectors = make_embedder(handler).embed(["a", "b"])

    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert seen["input"] == ["a", "b"]
    assert seen["truncate"] is False


def test_ollama_embed_of_nothing_makes_no_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    assert make_embedder(handler).embed([]) == []


def test_ollama_embed_with_the_wrong_size_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})  # 2 numbers, expected 3

    with pytest.raises(ProviderError, match="EMBEDDING_DIM"):
        make_embedder(handler, dim=3).embed(["a"])


# --- Bedrock (with a fake AWS client) -----------------------------------------------------


class FakeBedrockClient:
    """Records what we send and returns a canned reply shaped like the real AWS answers."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.converse_kwargs = None
        self.invoke_kwargs = None

    def converse(self, **kwargs):
        if self.error:
            raise self.error
        self.converse_kwargs = kwargs
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": "Paris."}]}},
            "usage": {"inputTokens": 27, "outputTokens": 3},
        }

    def invoke_model(self, **kwargs):
        if self.error:
            raise self.error
        self.invoke_kwargs = kwargs
        return {"body": io.BytesIO(json.dumps({"embedding": [0.1, 0.2, 0.3]}).encode())}


AWS_ERROR = ClientError(
    {"Error": {"Code": "AccessDeniedException", "Message": "no access"}}, "Converse"
)


def test_bedrock_generate_splits_the_system_prompt_and_reads_usage():
    client = FakeBedrockClient()
    llm = BedrockLLM("some-model-id", "eu-central-1", client=client)

    result = llm.generate(
        [
            {"role": "system", "content": "Answer briefly."},
            {"role": "user", "content": "Capital of France?"},
        ]
    )

    assert result.text == "Paris."
    assert (result.usage.input_tokens, result.usage.output_tokens) == (27, 3)
    sent = client.converse_kwargs
    assert sent["modelId"] == "some-model-id"
    assert sent["system"] == [{"text": "Answer briefly."}]
    assert sent["messages"] == [{"role": "user", "content": [{"text": "Capital of France?"}]}]
    assert sent["inferenceConfig"] == {"temperature": 0.0}


def test_bedrock_embed_asks_for_the_configured_dimension():
    client = FakeBedrockClient()
    embedder = BedrockEmbedder("titan-id", dim=1024, region="eu-central-1", client=client)

    assert embedder.embed(["a text"]) == [[0.1, 0.2, 0.3]]

    body = json.loads(client.invoke_kwargs["body"])
    assert body == {"inputText": "a text", "dimensions": 1024, "normalize": True}


def test_bedrock_errors_become_provider_errors():
    llm = BedrockLLM("some-model-id", "eu-central-1", client=FakeBedrockClient(AWS_ERROR))
    with pytest.raises(ProviderError, match="some-model-id"):
        llm.generate([{"role": "user", "content": "hi"}])


# --- Factory ------------------------------------------------------------------------------


def test_factory_picks_ollama_by_default():
    settings = Settings(_env_file=None)
    assert isinstance(get_llm(settings), OllamaLLM)
    assert isinstance(get_embedder(settings), OllamaEmbedder)


def test_factory_picks_bedrock_when_asked():
    # Building the objects must not contact AWS or need credentials.
    settings = Settings(_env_file=None, provider="bedrock")
    assert isinstance(get_llm(settings), BedrockLLM)
    assert isinstance(get_embedder(settings), BedrockEmbedder)
