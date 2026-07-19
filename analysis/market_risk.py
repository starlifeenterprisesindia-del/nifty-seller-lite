from __future__ import annotations

from datetime import datetime
from typing import Any

from models import VixContext


def calculate_vix_context(
    quote: dict[str, Any] | None,
    captured_at: datetime,
) -> VixContext:
    quote = quote or {}
    last = quote.get("last_price")
    previous_close = (quote.get("ohlc") or {}).get("close")
    if last is None:
        return VixContext(
            as_of=captured_at,
            last_price=None,
            previous_close=float(previous_close)
            if previous_close is not None
            else None,
            change_pct=None,
            regime="UNAVAILABLE",
            movement="UNAVAILABLE",
            seller_environment="UNAVAILABLE",
            status="UNAVAILABLE",
        )

    last_value = float(last)
    previous_value = float(previous_close) if previous_close not in (None, 0) else None
    change = (
        (last_value - previous_value) / previous_value * 100.0
        if previous_value is not None
        else None
    )
    if last_value < 11.0:
        regime = "LOW"
    elif last_value < 14.0:
        regime = "NORMAL"
    elif last_value < 18.0:
        regime = "ELEVATED"
    else:
        regime = "HIGH"

    if change is None:
        movement = "UNKNOWN"
    elif change >= 3.0:
        movement = "RISING FAST"
    elif change >= 1.0:
        movement = "RISING"
    elif change <= -3.0:
        movement = "FALLING FAST"
    elif change <= -1.0:
        movement = "FALLING"
    else:
        movement = "STABLE"

    if regime == "HIGH" or movement == "RISING FAST":
        environment = "HIGH PREMIUM / HIGH GAP RISK"
    elif regime == "ELEVATED" or movement == "RISING":
        environment = "GOOD PREMIUM / TIGHTER RISK CONTROL"
    elif regime == "LOW" and movement in {"STABLE", "FALLING", "FALLING FAST"}:
        environment = "THIN PREMIUM / COMPLACENCY RISK"
    else:
        environment = "BALANCED PREMIUM ENVIRONMENT"

    return VixContext(
        as_of=captured_at,
        last_price=round(last_value, 4),
        previous_close=round(previous_value, 4) if previous_value is not None else None,
        change_pct=round(change, 4) if change is not None else None,
        regime=regime,
        movement=movement,
        seller_environment=environment,
        status="READY",
    )
