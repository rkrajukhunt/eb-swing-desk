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

from ..config import DISCLAIMER, env
from ..database import db_session
from ..engine.regime import Regime
from ..engine.strategies import STRATEGY_LABELS
from ..models import ScanRun, Signal
from ..services.settings_store import get_settings

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


def _call_claude(payload: dict, model: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=env.anthropic_api_key, timeout=60.0, max_retries=1)
    # max_tokens sized for ~20 candidates × ~120 tokens each + overhead
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=0.2,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": OUTPUT_SCHEMA_HINT + "\n\nINPUT:\n" + json.dumps(payload, separators=(",", ":")),
        }],
    )
    return next(b.text for b in response.content if b.type == "text")


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
            except (TypeError, ValueError):
                tampered.append(f)
                continue
            if abs(echoed - engine_values[f]) > TOLERANCE:
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
    if not env.anthropic_api_key:
        log.info("ANTHROPIC_API_KEY not set — skipping LLM ranking")
        return

    with db_session() as s:
        signals = list(s.execute(
            select(Signal).where(Signal.scan_run_id == scan_run_id)
            .order_by(Signal.composite_score.desc())
        ).scalars())
        if not signals:
            return
        max_c = int(settings["llm_max_candidates"])
        payload = build_payload(signals, regime, max_c)

        data = None
        for attempt in (1, 2):  # parse failure → retry once
            try:
                raw = _call_claude(payload, settings["llm_model"])
            except Exception as e:
                log.warning("Claude call failed (attempt %d): %s", attempt, e)
                continue
            data = _parse_json(raw)
            if data is not None:
                break
            log.warning("Claude returned unparseable JSON (attempt %d)", attempt)

        if data is None:
            log.warning("LLM ranking unavailable — falling back to composite_score order")
            return  # deterministic order remains; never crash

        issues = validate_and_apply(data, signals, max_c)
        if issues:
            log.warning("LLM validation issues: %s", issues)
        run = s.get(ScanRun, scan_run_id)
        run.llm_used = True


__all__ = ["rank_with_claude", "validate_and_apply", "build_payload", "sanitize", "DISCLAIMER"]
