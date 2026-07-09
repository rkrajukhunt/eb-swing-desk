from __future__ import annotations

from .base import BrokerAdapter
from .yahoo import YahooAdapter

_instances: dict[str, BrokerAdapter] = {}


def get_adapter(name: str) -> BrokerAdapter:
    """Singleton per broker so auth sessions persist across requests."""
    if name not in _instances:
        if name == "yahoo":
            _instances[name] = YahooAdapter()
        elif name == "angel_one":
            from .angel_one import AngelOneAdapter

            _instances[name] = AngelOneAdapter()
        elif name == "zerodha":
            from .zerodha import ZerodhaAdapter

            _instances[name] = ZerodhaAdapter()
        else:
            raise ValueError(f"unknown broker '{name}'")
    return _instances[name]


def active_adapter() -> BrokerAdapter:
    from ..services.settings_store import get_settings

    return get_adapter(get_settings()["active_broker"])
