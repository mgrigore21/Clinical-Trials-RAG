"""The two things the rest of the app needs from an AI backend, and nothing more.

Why: the retrieval and generation code should not care whether the model runs on your Mac
(Ollama) or on AWS (Bedrock). It talks to these two interfaces; only the classes in
ollama.py and bedrock.py know the difference. See docs/07-providers.md.
"""

from dataclasses import dataclass
from typing import Protocol, TypedDict


class Message(TypedDict):
    """One turn of a conversation. role is "system", "user" or "assistant"."""

    role: str
    content: str


@dataclass(frozen=True)
class Usage:
    """How many tokens a call used. Needed later to track cost and speed (monitoring phase)."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Generation:
    text: str
    usage: Usage


class ProviderError(Exception):
    """Anything that went wrong talking to a model backend, with a message a human can act on."""


class LLMProvider(Protocol):
    """Writes text. Used to generate answers (and, in the evaluation phase, to judge them)."""

    def generate(self, messages: list[Message], temperature: float = 0.0) -> Generation: ...


class EmbeddingProvider(Protocol):
    """Turns texts into vectors (lists of numbers). One vector per text, in the same order."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...
