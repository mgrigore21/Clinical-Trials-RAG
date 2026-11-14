import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_are_local(monkeypatch):
    monkeypatch.delenv("PROVIDER", raising=False)
    settings = Settings(_env_file=None)  # ignore any local .env so the test is repeatable
    assert settings.provider == "ollama"
    assert settings.embedding_dim == 1024


def test_environment_variables_override_defaults(monkeypatch):
    monkeypatch.setenv("PROVIDER", "bedrock")
    monkeypatch.setenv("TOP_K", "3")
    settings = Settings(_env_file=None)
    assert settings.provider == "bedrock"
    assert settings.top_k == 3


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("PROVIDER", "openai")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
