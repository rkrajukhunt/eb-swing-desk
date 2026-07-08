"""LLM provider adapters.

Each provider talks through its own official SDK — there is no base_url shim, so
the things that can silently break are: the wrong key for the provider, a model
id in the wrong dialect, and sampling params sent to a model that rejects them.
All three degrade to deterministic ordering without an exception, so pin them.
"""
from __future__ import annotations

import pytest

from app.config import LLM_PROVIDERS
from app.llm import providers


def _settings(**over) -> dict:
    base = {"llm_provider": "anthropic", "llm_model": "claude-sonnet-4-6"}
    base.update(over)
    return base


def _clear_keys(monkeypatch):
    monkeypatch.setattr(providers.env, "anthropic_api_key", "")
    monkeypatch.setattr(providers.env, "openrouter_api_key", "")


# --- registry ------------------------------------------------------------------
def test_only_sdk_backed_providers_exist():
    """A provider with no adapter would raise LLMError at scan time."""
    assert set(LLM_PROVIDERS) == {"anthropic", "openrouter"}
    for pid in LLM_PROVIDERS:
        assert pid in providers._ADAPTERS


def test_no_base_url_anywhere_in_registry():
    for pid, spec in LLM_PROVIDERS.items():
        assert not any("base_url" in k for k in spec), f"{pid} still carries a base_url"
        assert spec["sdk"] in ("anthropic", "openrouter")


@pytest.mark.parametrize("pid,spec", LLM_PROVIDERS.items())
def test_presets_match_provider_id_dialect(pid, spec):
    """OpenRouter ids are namespaced; Anthropic ids are not. Mixing them 404s."""
    for m in spec["models"]:
        if pid == "openrouter":
            assert "/" in m, f"{m} needs a namespace for OpenRouter"
        else:
            assert "/" not in m, f"{m} must not be namespaced for Anthropic"


# --- key resolution -------------------------------------------------------------
def test_each_provider_reads_its_own_key(monkeypatch):
    monkeypatch.setattr(providers.env, "anthropic_api_key", "sk-ant-aaa")
    monkeypatch.setattr(providers.env, "openrouter_api_key", "sk-or-v1-bbb")
    assert providers.resolve_api_key(_settings(llm_provider="anthropic")) == "sk-ant-aaa"
    assert providers.resolve_api_key(_settings(llm_provider="openrouter")) == "sk-or-v1-bbb"


def test_keys_never_cross_providers(monkeypatch):
    """An sk-or key must never be handed to the Anthropic SDK, or vice-versa."""
    monkeypatch.setattr(providers.env, "anthropic_api_key", "")
    monkeypatch.setattr(providers.env, "openrouter_api_key", "sk-or-v1-bbb")
    assert providers.resolve_api_key(_settings(llm_provider="anthropic")) == ""


# --- sampling params ------------------------------------------------------------
@pytest.mark.parametrize("model", [
    "claude-opus-4-8", "anthropic/claude-opus-4.8",
    "claude-sonnet-5", "anthropic/claude-sonnet-5",
    "claude-opus-4-7", "claude-fable-5",
])
def test_temperature_withheld_from_models_that_reject_it(model):
    """Anthropic removed temperature/top_p/top_k on these — sending it is a 400."""
    assert providers.supports_temperature(model) is False


@pytest.mark.parametrize("model", [
    "claude-sonnet-4-6", "anthropic/claude-sonnet-4.6",
    "claude-haiku-4-5", "anthropic/claude-haiku-4.5",
])
def test_temperature_sent_to_models_that_accept_it(model):
    assert providers.supports_temperature(model) is True


def test_every_anthropic_preset_is_classified():
    """Guards against adding a preset whose sampling support we never considered."""
    for spec in LLM_PROVIDERS.values():
        for m in spec["models"]:
            assert isinstance(providers.supports_temperature(m), bool)


# --- preflight ------------------------------------------------------------------
def test_preflight_clean_config(monkeypatch):
    monkeypatch.setattr(providers.env, "openrouter_api_key", "sk-or-v1-x")
    assert providers.preflight(
        _settings(llm_provider="openrouter", llm_model="anthropic/claude-sonnet-4.6")) == ""


def test_preflight_missing_key_names_the_right_var(monkeypatch):
    _clear_keys(monkeypatch)
    assert "OPENROUTER_API_KEY is not set" in providers.preflight(_settings(llm_provider="openrouter"))
    assert "ANTHROPIC_API_KEY is not set" in providers.preflight(_settings(llm_provider="anthropic"))


def test_preflight_rejects_wrong_key_prefix(monkeypatch):
    monkeypatch.setattr(providers.env, "anthropic_api_key", "sk-or-v1-oops")
    assert "sk-ant-" in providers.preflight(_settings(llm_provider="anthropic"))


def test_preflight_rejects_bare_model_on_openrouter(monkeypatch):
    monkeypatch.setattr(providers.env, "openrouter_api_key", "sk-or-v1-x")
    out = providers.preflight(_settings(llm_provider="openrouter", llm_model="claude-sonnet-4-6"))
    assert "namespaced" in out


def test_preflight_rejects_namespaced_model_on_anthropic(monkeypatch):
    monkeypatch.setattr(providers.env, "anthropic_api_key", "sk-ant-x")
    out = providers.preflight(
        _settings(llm_provider="anthropic", llm_model="anthropic/claude-sonnet-4.6"))
    assert "bare id" in out


def test_preflight_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(providers.env, "anthropic_api_key", "sk-ant-x")
    assert "Unknown llm_provider" in providers.preflight(_settings(llm_provider="wat"))


# --- complete() dispatch --------------------------------------------------------
def test_complete_raises_without_key(monkeypatch):
    _clear_keys(monkeypatch)
    with pytest.raises(providers.LLMError, match="ANTHROPIC_API_KEY"):
        providers.complete(_settings(), "sys", "user", 64)


def test_complete_dispatches_to_the_selected_adapter(monkeypatch):
    monkeypatch.setattr(providers.env, "openrouter_api_key", "sk-or-v1-x")
    seen = {}

    def fake(key, model, system, user, max_tokens, temperature):
        seen.update(key=key, model=model, max_tokens=max_tokens)
        return providers.Completion(text="{}", input_tokens=1, output_tokens=2, cost_usd=0.001)

    monkeypatch.setitem(providers._ADAPTERS, "openrouter", fake)
    c = providers.complete(
        _settings(llm_provider="openrouter", llm_model="anthropic/claude-sonnet-4.6"),
        "sys", "user", 777)
    assert seen == {"key": "sk-or-v1-x", "model": "anthropic/claude-sonnet-4.6", "max_tokens": 777}
    assert c.text == "{}" and "$0.001" in c.usage_str()


def test_test_connection_never_raises(monkeypatch):
    monkeypatch.setattr(providers.env, "openrouter_api_key", "sk-or-v1-x")

    def boom(*a, **k):
        raise RuntimeError("network is down")

    monkeypatch.setitem(providers._ADAPTERS, "openrouter", boom)
    out = providers.test_connection(
        _settings(llm_provider="openrouter", llm_model="anthropic/claude-sonnet-4.6"))
    assert out["ok"] is False
    assert "network is down" in out["detail"]
    assert out["key_env"] == "OPENROUTER_API_KEY"


# --- credit / max_tokens budget --------------------------------------------------
def test_affordable_parsed_from_402_message():
    msg = ("This request requires more credits, or fewer max_tokens. You requested "
           "up to 1312 tokens, but can only afford 1143. To increase, visit ...")
    assert providers._affordable_from_402(msg) == 1143
    assert providers._affordable_from_402("some unrelated failure") is None


def test_budget_error_carries_affordable():
    e = providers.LLMBudgetError("nope", 1143)
    assert isinstance(e, providers.LLMError) and e.affordable == 1143


def test_test_connection_reports_exhausted_credits(monkeypatch):
    monkeypatch.setattr(providers.env, "openrouter_api_key", "sk-or-v1-x")

    def broke(*a, **k):
        raise providers.LLMBudgetError("no credits", 12)

    monkeypatch.setitem(providers._ADAPTERS, "openrouter", broke)
    out = providers.test_connection(
        _settings(llm_provider="openrouter", llm_model="anthropic/claude-sonnet-4.6"))
    assert out["ok"] is False
    assert out["budget_exhausted"] is True and out["affordable_tokens"] == 12
    assert "Out of credits" in out["detail"]
