"""Pick the right backend from settings. This is the only place that knows both exist.

Why: everything else (retrieval, generation, evaluation) asks the factory for "an LLM" or "an
embedder" and works the same on a laptop and on AWS. See docs/07-providers.md.
"""

from app.config import Settings, get_settings
from app.providers.base import EmbeddingProvider, LLMProvider
from app.providers.bedrock import BedrockEmbedder, BedrockLLM
from app.providers.ollama import OllamaEmbedder, OllamaLLM


def get_llm(settings: Settings | None = None) -> LLMProvider:
    s = settings or get_settings()
    if s.provider == "bedrock":
        return BedrockLLM(model_id=s.llm_model, region=s.aws_region)
    return OllamaLLM(
        base_url=s.ollama_base_url,
        model=s.llm_model,
        timeout=s.request_timeout_s,
        num_ctx=s.ollama_num_ctx,
    )


def get_embedder(settings: Settings | None = None) -> EmbeddingProvider:
    s = settings or get_settings()
    if s.provider == "bedrock":
        return BedrockEmbedder(model_id=s.embedding_model, dim=s.embedding_dim, region=s.aws_region)
    return OllamaEmbedder(
        base_url=s.ollama_base_url,
        model=s.embedding_model,
        dim=s.embedding_dim,
        timeout=s.request_timeout_s,
    )
