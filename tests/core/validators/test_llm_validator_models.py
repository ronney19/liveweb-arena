from liveweb_arena.core.validators.llm_validator import (
    OPENAI_VALIDATION_MODELS,
    VALIDATION_MODELS,
    _get_validation_models,
)


class _DummyLLMClient:
    def __init__(self, base_url: str):
        self._base_url = base_url


def test_validation_models_use_env_override(monkeypatch):
    monkeypatch.setenv("VALIDATION_MODELS", "model-a, model-b ,model-c")
    client = _DummyLLMClient("https://api.openai.com/v1")
    assert _get_validation_models(client) == ["model-a", "model-b", "model-c"]


def test_validation_models_use_openai_defaults_when_on_openai(monkeypatch):
    monkeypatch.delenv("VALIDATION_MODELS", raising=False)
    client = _DummyLLMClient("https://api.openai.com/v1")
    assert _get_validation_models(client) == OPENAI_VALIDATION_MODELS


def test_validation_models_use_project_defaults_for_other_providers(monkeypatch):
    monkeypatch.delenv("VALIDATION_MODELS", raising=False)
    client = _DummyLLMClient("https://llm.chutes.ai/v1")
    assert _get_validation_models(client) == VALIDATION_MODELS
