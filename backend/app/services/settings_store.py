"""DB-backed settings, merged over DEFAULT_SETTINGS.

Broker API keys are env-only and never pass through here, so nothing secret
can leak to the frontend via GET /api/settings.
"""
from __future__ import annotations

import copy

from ..config import DEFAULT_SETTINGS
from ..database import db_session
from ..models import AppSetting

_KEY = "user_settings"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_settings() -> dict:
    with db_session() as s:
        row = s.get(AppSetting, _KEY)
        stored = row.value if row else {}
    return _deep_merge(DEFAULT_SETTINGS, stored or {})


def update_settings(patch: dict) -> dict:
    # Only accept keys that exist in the defaults (prevents junk / typos)
    def prune(defaults: dict, incoming: dict) -> dict:
        cleaned = {}
        for k, v in incoming.items():
            if k not in defaults:
                continue
            if isinstance(v, dict) and isinstance(defaults[k], dict):
                cleaned[k] = prune(defaults[k], v)
            else:
                cleaned[k] = v
        return cleaned

    patch = prune(DEFAULT_SETTINGS, patch or {})
    with db_session() as s:
        row = s.get(AppSetting, _KEY)
        if row is None:
            row = AppSetting(key=_KEY, value={})
            s.add(row)
        row.value = _deep_merge(row.value or {}, patch)
    return get_settings()
