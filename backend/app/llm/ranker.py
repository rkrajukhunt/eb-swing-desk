"""Claude ranking layer — additive only, never a source of price truth.

Contract enforced here:
  1. JSON parse failure → one retry → fall back to deterministic ordering.
  2. Every echoed price level must equal the engine's value (tol 0.01) or the
     echo is discarded and tampering is logged. Engine values are ALWAYS what
     the UI displays; the echo exists purely as an integrity check.
  3. Hallucinated ids are dropped; dropped candidates are re-appended in
     composite_score order.
  4. rationale / regime_note are sanitized (HTML stripped) before storage.
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy import select

from ..config import DISCLAIMER
from ..database import db_session
from ..engine.regime import Regime
from ..engine.strategies import STRATEGY_LABELS
from ..models import ScanRun, Signal
from ..services.settings_store import get_settings
from . import providers

log = logging.getLogger(__name__)

PRICE_FIELDS = ("entry", "stop_loss", "target_2r", "target_3r", "risk_reward")
TOLERANCE = 0.01

SYSTEM_PROMPT = """You are a technical-analysis ranking assistant for swing trading. You are given \
pre-computed indicators and price levels for pre-filtered stock candidates. \

HARD RULES:
1. You MUST NOT invent, calculate, estimate, or alter ANY price, level, or \
indicator value. Only reference numbers present in the input.
2. entry, stop_loss, target_2r, target_3r, risk_reward for each stock in your \
output MUST be copied EXACTLY from the input. If you change any of these, the \
response is invalid.
3. You only decide RANK ORDER and write short rationale/warnings based on the \
supplied numbers and market_regime.
4. Respect the regime: if market_regime is 'bearish', down-rank aggressive \
breakout longs and say so in warnings.
5. Output ONLY valid JSON matching the schema below. No markdown, no code fences, \
no preamble, no trailing text.
6. rationale: max 2 sentences, plain English, reference actual indicator values.
7. If a candidate has conflicting signals (e.g. rsi_divergence != 'none', or \
weekly_trend disagrees with strategy), you MUST list it in 'conflicts'.
8. Do not give financial advice or certainty. This is educational analysis only."""

OUTPUT_SCHEMA_HINT = """Required output schema (return exactly this shape):
{
  "ranked": [
    {
      "id": "<symbol>",
      "rank": 1,
      "conviction": "high | medium | low",
      "entry": 0.0,
      "stop_loss": 0.0,
      "target_2r": 0.0,
      "target_3r": 0.0,
      "risk_reward": 0.0,
      "rationale": "max 2 sentences referencing supplied numbers",
      "conflicts": [],
      "regime_note": "one sentence"
    }
  ],
  "disclaimer": "string"
}"""

_TAG_RE = re.compile(r"<[^>]*>")


def sanitize(text: str | None, limit: int = 1000) -> str | None:
    if text is None:
        return None
    return _TAG_RE.sub("", str(text)).strip()[:limit]


def build_payload(signals: list[Signal], regime: Regime, max_candidates: int) -> dict:
    top = signals[:max_candidates]
    return {
        "market_regime": regime.label,
        "nifty_trend": {
            "above_ema200": bool(regime.detail.get("above_ema200")),
            "adx": regime.detail.get("adx"),
        },
        "candidates": [
            {
                "id": sig.symbol,
                "symbol": sig.symbol,
                "strategy_matched": STRATEGY_LABELS.get(sig.strategy, sig.strategy),
                "composite_score": sig.composite_score,
                "close": sig.entry,
                "entry": sig.entry,
                "stop_loss": sig.stop_loss,
                "target_2r": sig.target_2r,
                "target_3r": sig.target_3r,
                "risk_reward": sig.risk_reward,
                "pct_risk": sig.pct_risk,
                "indicators": sig.indicators,
            }
            for sig in top
        ],
    }


def test_connection() -> dict:
    """Round-trip the configured provider so a bad key/model surfaces in the UI.
    The scan path swallows LLM errors by design, so this is the only signal."""
    return providers.test_connection(get_settings())


# A ranked entry costs ~350 output tokens (rationale + conflicts + regime_note).
# Reserve 400 each so a verbose reply is never truncated mid-object — a truncated
# reply is unparseable JSON, which silently degrades to deterministic ordering.
TOKENS_PER_CANDIDATE = 400
TOKENS_OVERHEAD = 512
MAX_TOKENS_CEILING = 16_384      # must exceed 512 + 400 × llm_max_candidates
# Below this, the reply cannot possibly hold `n` complete objects — retrying at a
# smaller budget would only buy a truncated (and billed) response.
MIN_TOKENS_PER_CANDIDATE = 200


def budget_for(n_candidates: int) -> int:
    return min(MAX_TOKENS_CEILING, TOKENS_OVERHEAD + TOKENS_PER_CANDIDATE * max(n_candidates, 1))


def _call_claude(payload: dict, model: str) -> str:
    n = len(payload.get("candidates", []))
    max_tokens = budget_for(n)
    user = OUTPUT_SCHEMA_HINT + "\n\nINPUT:\n" + json.dumps(payload, separators=(",", ":"))
    settings = get_settings()

    try:
        return providers.complete(settings, SYSTEM_PROMPT, user, max_tokens).text
    except providers.LLMBudgetError as e:
        # `max_tokens` is a reservation, not a bill. A credit-capped account can
        # reject 1312 while the reply only needs ~700 — retry at the ceiling it
        # named, but only if that still fits `n` complete objects.
        floor = MIN_TOKENS_PER_CANDIDATE * max(n, 1)
        if not e.affordable or e.affordable < floor:
            raise
        log.warning("LLM budget capped at %d tokens (wanted %d) — retrying at the cap",
                    e.affordable, max_tokens)
        
        tight_user = user
        if e.affordable < 400 * max(n, 1):
            tight_user += (
                "\n\nCRITICAL: You are running under extreme token budget constraints (max output tokens: %d). "
                "You MUST keep the 'rationale' and 'regime_note' extremely brief (maximum of 5 words each) "
                "and omit all conflicts to prevent output truncation. Write as little as possible."
            ) % e.affordable

        return providers.complete(settings, SYSTEM_PROMPT, tight_user, e.affordable).text


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) and isinstance(data.get("ranked"), list) else None
    except (json.JSONDecodeError, ValueError):
        return None


def validate_and_apply(data: dict, signals: list[Signal], max_candidates: int) -> list[dict]:
    """Applies validated LLM output onto Signal ORM rows (in-memory).

    Returns a list of tamper/hallucination log entries. Display prices are
    never taken from the LLM.
    """
    issues: list[dict] = []
    by_symbol = {s.symbol: s for s in signals[:max_candidates]}
    seen: set[str] = set()
    rank = 0

    for item in data.get("ranked", []):
        if not isinstance(item, dict):
            continue
        sym = str(item.get("id", "")).upper()
        sig = by_symbol.get(sym)
        if sig is None:
            issues.append({"type": "hallucinated_symbol", "id": sym})
            continue
        if sym in seen:
            continue
        # Integrity check: echoed numbers must equal engine values (tol 0.01)
        tampered = []
        engine_values = {
            "entry": sig.entry, "stop_loss": sig.stop_loss,
            "target_2r": sig.target_2r, "target_3r": sig.target_3r,
            "risk_reward": sig.risk_reward,
        }
        for f in PRICE_FIELDS:
            try:
                echoed = float(item.get(f))
                eng_val = engine_values[f]
                if eng_val is None or abs(echoed - eng_val) > TOLERANCE:
                    tampered.append(f)
            except (TypeError, ValueError):
                tampered.append(f)
        if tampered:
            issues.append({"type": "price_tampering", "id": sym, "fields": tampered})
            log.warning("LLM tampered with %s on %s — engine values retained", tampered, sym)

        seen.add(sym)
        rank += 1
        sig.llm_rank = rank
        conviction = str(item.get("conviction", "")).lower()
        sig.llm_conviction = conviction if conviction in ("high", "medium", "low") else None
        sig.llm_rationale = sanitize(item.get("rationale"))
        conflicts = item.get("conflicts")
        sig.llm_conflicts = [sanitize(c, 200) for c in conflicts if isinstance(c, str)] \
            if isinstance(conflicts, list) else []
        sig.llm_regime_note = sanitize(item.get("regime_note"), 500)

    # Any candidate Claude dropped: append at the end in composite_score order
    for sym, sig in by_symbol.items():
        if sym not in seen:
            rank += 1
            sig.llm_rank = rank
            issues.append({"type": "dropped_by_llm", "id": sym})
    return issues


def rank_with_claude(scan_run_id: int, regime: Regime) -> None:
    settings = get_settings()
    problem = providers.preflight(settings)
    if problem:
        log.info("LLM ranking skipped — %s", problem)
        return

    with db_session() as s:
        signals = list(s.execute(
            select(Signal).where(Signal.scan_run_id == scan_run_id)
            .order_by(Signal.composite_score.desc())
        ).scalars())
        if not signals:
            return
        max_c = int(settings["llm_max_candidates"])
        candidates = signals[:max_c]

        all_ranked_data = []
        i = 0
        batch_size = len(candidates)

        while i < len(candidates):
            chunk = candidates[i:i + batch_size]
            payload = build_payload(chunk, regime, len(chunk))

            data = None
            attempt = 1
            budget_error = False
            while attempt <= 2:
                try:
                    raw = _call_claude(payload, settings["llm_model"])
                except providers.LLMBudgetError as e:
                    if e.affordable and batch_size > 1:
                        max_allowed = e.affordable // MIN_TOKENS_PER_CANDIDATE
                        if 0 < max_allowed < batch_size:
                            log.warning(
                                "LLM credits insufficient for batch of %d candidates (need ~%d), but can afford %d. "
                                "Reducing batch size to %d and retrying.",
                                batch_size,
                                MIN_TOKENS_PER_CANDIDATE * batch_size,
                                e.affordable, max_allowed
                            )
                            batch_size = max_allowed
                            budget_error = True
                            break  # break out of attempt loop to re-slice chunk with new batch_size

                    log.warning(
                        "LLM ranking unavailable — insufficient credits for batch of %d candidates "
                        "(provider allows %s tokens, need ~%d). Falling back to composite_score order.",
                        len(payload["candidates"]), e.affordable or "?",
                        MIN_TOKENS_PER_CANDIDATE * len(payload["candidates"]))
                    return
                except Exception as e:
                    log.warning("Claude call failed (attempt %d): %s", attempt, e)
                    attempt += 1
                    continue
                data = _parse_json(raw)
                if data is not None:
                    break
                log.warning("Claude returned unparseable JSON (attempt %d). Raw response: %r", attempt, raw)
                attempt += 1

            if budget_error:
                continue

            if data is None:
                log.warning("LLM ranking unavailable — falling back to composite_score order")
                return  # deterministic order remains; never crash

            all_ranked_data.extend(data.get("ranked", []))
            i += batch_size

        issues = validate_and_apply({"ranked": all_ranked_data}, signals, max_c)
        if issues:
            log.warning("LLM validation issues: %s", issues)
        run = s.get(ScanRun, scan_run_id)
        run.llm_used = True


__all__ = ["rank_with_claude", "validate_and_apply", "build_payload", "sanitize",
           "test_connection", "DISCLAIMER"]
