"""LLM provider adapters — each provider talks through its OWN official SDK.

No base_url shims: `anthropic` speaks the Anthropic Messages API, `openrouter`
speaks OpenRouter's Chat API via the official `openrouter` package. Both are
normalised to a single `Completion` so ranker.py never branches on provider.

Model ids are NOT portable between providers:
    anthropic   → "claude-sonnet-4-6"
    openrouter  → "anthropic/claude-sonnet-4.6"
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..config import LLM_PROVIDERS, env

log = logging.getLogger(__name__)

_REFERER = "https://github.com/eb-swing-desk"
_TITLE = "EB Swing Desk"
_TIMEOUT_S = 60.0

# Sampling params (temperature/top_p/top_k) were removed on these Claude models —
# the Anthropic API returns 400 if they are sent. Matched on the bare model name,
# so both "claude-opus-4-8" and "anthropic/claude-opus-4.8" are covered.
_NO_SAMPLING = ("opus-4-8", "opus-4.8", "opus-4-7", "opus-4.7",
                "sonnet-5", "fable-5", "mythos-5")


class LLMError(RuntimeError):
    """Any provider failure. Callers degrade to deterministic ordering."""


class LLMBudgetError(LLMError):
    """The provider refused the request because `max_tokens` exceeds what the
    account can pay for. `affordable` is the ceiling it will accept, if the
    provider told us. Note max_tokens is a *reservation*: the reply usually
    needs far less, so retrying at `affordable` normally succeeds.
    """

    def __init__(self, message: str, affordable: int | None = None):
        super().__init__(message)
        self.affordable = affordable


@dataclass
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None      # OpenRouter reports this; Anthropic does not

    def usage_str(self) -> str:
        s = f"{self.input_tokens} in / {self.output_tokens} out"
        return f"{s} · ${self.cost_usd:.6f}" if self.cost_usd else s


def spec_for(settings: dict) -> tuple[str, dict]:
    provider = settings.get("llm_provider", "anthropic")
    return provider, LLM_PROVIDERS.get(provider, LLM_PROVIDERS["anthropic"])


def resolve_api_key(settings: dict) -> str:
    """The credential for the selected provider, from that provider's own env var."""
    _, spec = spec_for(settings)
    return (getattr(env, spec["key_env"], "") or "").strip()


def supports_temperature(model: str) -> bool:
    return not any(tag in model for tag in _NO_SAMPLING)


# --- adapters ------------------------------------------------------------------
def _complete_anthropic(key: str, model: str, system: str, user: str, max_tokens: int,
                        temperature: float) -> Completion:
    import anthropic

    client = anthropic.Anthropic(api_key=key, timeout=_TIMEOUT_S, max_retries=1)
    kwargs: dict = {}
    if supports_temperature(model):
        kwargs["temperature"] = temperature
    r = client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}], **kwargs,
    )
    return Completion(
        text="".join(b.text for b in r.content if b.type == "text"),
        input_tokens=r.usage.input_tokens,
        output_tokens=r.usage.output_tokens,
    )


def _affordable_from_402(msg: str) -> int | None:
    """OpenRouter's 402 names the ceiling it will accept:
    'You requested up to 1312 tokens, but can only afford 1143'."""
    m = re.search(r"can only afford (\d+)", msg)
    return int(m.group(1)) if m else None


def _complete_openrouter(key: str, model: str, system: str, user: str, max_tokens: int,
                         temperature: float) -> Completion:
    from openrouter import OpenRouter
    from openrouter.errors import PaymentRequiredResponseError

    kwargs: dict = {}
    if supports_temperature(model):
        kwargs["temperature"] = temperature
    try:
        with OpenRouter(api_key=key) as client:
            res = client.chat.send(
                model=model,
                # OpenRouter's Chat API is OpenAI-shaped: the system prompt is a message,
                # not a top-level argument as in the Anthropic Messages API.
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                max_tokens=max_tokens,
                http_referer=_REFERER,          # first-class params, not raw headers
                x_open_router_title=_TITLE,
                timeout_ms=int(_TIMEOUT_S * 1000),
                **kwargs,
            )
    except PaymentRequiredResponseError as e:
        # `max_tokens` is a RESERVATION — OpenRouter checks the balance against it
        # before generating, and rejects even though the reply would cost far less.
        raise LLMBudgetError(str(e.message), _affordable_from_402(str(e.message))) from e

    if not res.choices:
        raise LLMError("OpenRouter returned no choices")
    u = res.usage
    return Completion(
        text=res.choices[0].message.content or "",
        input_tokens=getattr(u, "prompt_tokens", 0) or 0,
        output_tokens=getattr(u, "completion_tokens", 0) or 0,
        cost_usd=getattr(u, "cost", None),
    )


_ADAPTERS = {"anthropic": _complete_anthropic, "openrouter": _complete_openrouter}


def complete(settings: dict, system: str, user: str, max_tokens: int,
             temperature: float = 0.2) -> Completion:
    """Single entry point. Raises LLMError on a missing key or unknown provider."""
    provider, spec = spec_for(settings)
    key = resolve_api_key(settings)
    if not key:
        raise LLMError(f"{spec['key_env'].upper()} is not set in backend/.env")
    adapter = _ADAPTERS.get(provider)
    if adapter is None:
        raise LLMError(f"unknown llm_provider '{provider}'")

    model = settings["llm_model"]
    log.debug("llm call: provider=%s model=%s max_tokens=%d", provider, model, max_tokens)
    return adapter(key, model, system, user, max_tokens, temperature)


def preflight(settings: dict) -> str:
    """Config problems catchable without spending a request. '' means looks-fine."""
    provider, spec = spec_for(settings)
    key_var = spec["key_env"].upper()
    key = resolve_api_key(settings)
    model = settings["llm_model"]

    if not key:
        return f"{key_var} is not set in backend/.env"
    if provider not in _ADAPTERS:
        return f"Unknown llm_provider '{provider}'"
    prefix = spec.get("key_prefix", "")
    if prefix and not key.startswith(prefix):
        return (f"{key_var} does not look like a {provider} key "
                f"(expected prefix '{prefix}'). Check llm_provider.")
    if provider == "openrouter" and "/" not in model:
        return (f"Model '{model}' is an Anthropic id. OpenRouter needs a namespaced "
                f"id, e.g. 'anthropic/claude-sonnet-4.6'.")
    if provider == "anthropic" and "/" in model:
        return (f"Model '{model}' is an OpenRouter id. Anthropic needs a bare id, "
                f"e.g. 'claude-sonnet-4-6'.")
    return ""


def test_connection(settings: dict) -> dict:
    """One cheap round-trip. Never raises — the scan path degrades silently on LLM
    failure, so this is the only place a bad key/model can surface to the user."""
    provider, spec = spec_for(settings)
    out = {"ok": False, "provider": provider, "model": settings["llm_model"],
           "key_env": spec["key_env"].upper(), "detail": ""}

    problem = preflight(settings)
    if problem:
        out["detail"] = problem
        return out
    try:
        c = complete(settings, "Reply with exactly one word.", "Say OK", max_tokens=16)
        out["ok"] = True
        out["detail"] = f"replied {c.text.strip()!r} · {c.usage_str()}"
    except LLMBudgetError as e:
        # A 16-token probe was refused → the balance is genuinely spent. Say so
        # plainly rather than echoing the provider's wall of text.
        out["budget_exhausted"] = True
        out["affordable_tokens"] = e.affordable
        out["detail"] = (f"Out of credits — the provider will only allow "
                         f"{e.affordable or '?'} output tokens. Top up at "
                         f"openrouter.ai/settings/credits.")
    except Exception as e:                       # noqa: BLE001 — surface anything
        out["detail"] = f"{type(e).__name__}: {str(e)[:300]}"
    return out


__all__ = ["Completion", "LLMError", "LLMBudgetError", "complete", "preflight",
           "test_connection", "resolve_api_key", "spec_for", "supports_temperature"]
