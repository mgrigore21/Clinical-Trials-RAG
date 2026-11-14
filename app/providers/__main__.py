"""A quick "is my model setup working?" check. Run with: uv run python -m app.providers

Why it exists: when something later fails, this tells you in seconds whether the problem is the
models or your own code. See docs/07-providers.md.
"""

import time

from app.config import get_settings
from app.providers.base import ProviderError
from app.providers.factory import get_embedder, get_llm


def main() -> None:
    settings = get_settings()
    print(f"provider: {settings.provider}")
    try:
        start = time.perf_counter()
        vectors = get_embedder().embed(["Which trials test semaglutide for obesity?"])
        print(
            f"embedding: {settings.embedding_model} -> {len(vectors[0])} numbers "
            f"({time.perf_counter() - start:.1f}s)"
        )

        start = time.perf_counter()
        result = get_llm().generate(
            [
                {
                    "role": "user",
                    "content": "In one short sentence: what is a phase 3 clinical trial?",
                }
            ]
        )
        print(
            f"answer ({settings.llm_model}, {time.perf_counter() - start:.1f}s, "
            f"{result.usage.input_tokens} tokens in, {result.usage.output_tokens} out):"
        )
        print(f"  {result.text.strip()}")
    except ProviderError as exc:
        raise SystemExit(f"FAILED: {exc}") from exc


if __name__ == "__main__":
    main()
