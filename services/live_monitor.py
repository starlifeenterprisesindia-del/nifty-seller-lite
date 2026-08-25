from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import TYPE_CHECKING, Any

import pandas as pd

from config import CONFIG, IST_TIMEZONE

if TYPE_CHECKING:
    from services.dhan_client import DhanClient


@dataclass(frozen=True)
class FastQuote:
    label: str
    last_price: float | None
    baseline: float | None
    last_trade_time: str

    @property
    def change(self) -> float | None:
        if self.last_price is None or self.baseline is None:
            return None
        return self.last_price - self.baseline


@dataclass(frozen=True)
class LiveImpulse:
    direction: str
    state: str
    score: float
    change_5s: float | None
    change_15s: float | None
    change_30s: float | None
    change_60s: float | None
    premium_shock: str
    reasons: tuple[str, ...]


def _sample_at_or_before(
    history: list[dict[str, Any]], now_ts: float, age_seconds: int
) -> dict[str, Any] | None:
    target = now_ts - age_seconds
    eligible = [item for item in history if float(item.get("captured_ts", 0.0)) <= target]
    return eligible[-1] if eligible else None


def calculate_live_impulse(
    rows: list[FastQuote],
    history: list[dict[str, Any]],
    *,
    captured_ts: float,
) -> LiveImpulse:
    """Classify a quote-speed impulse before a candle closes.

    This is an early-warning lane, not an order signal.  It uses only observed
    quote changes and never manufactures OI, volume or candle confirmation.
    """
    prices = {item.label: item.last_price for item in rows if item.last_price is not None}
    spot = prices.get("NIFTY Live")
    if spot is None:
        return LiveImpulse("MIXED", "DATA UNAVAILABLE", 0.0, None, None, None, None, "NONE", ())

    def change(age: int) -> float | None:
        prior = _sample_at_or_before(history, captured_ts, age)
        if prior is None:
            return None
        old = prior.get("prices", {}).get("NIFTY Live")
        return None if old is None else float(spot) - float(old)

    changes = {age: change(age) for age in (5, 15, 30, 60)}
    weighted = 0.0
    available_weight = 0.0
    for age, weight, scale in ((5, 0.15, 4.0), (15, 0.25, 9.0), (30, 0.30, 16.0), (60, 0.30, 28.0)):
        value = changes[age]
        if value is None:
            continue
        weighted += max(-1.0, min(1.0, value / scale)) * weight
        available_weight += weight
    directional = weighted / available_weight if available_weight else 0.0

    # Premium movement confirms speed but cannot create a NIFTY direction alone.
    premium_reasons: list[str] = []
    premium_shock = "NONE"
    previous = _sample_at_or_before(history, captured_ts, 15)
    if previous is not None:
        previous_prices = previous.get("prices", {})
        for label, current in prices.items():
            if label == "NIFTY Live":
                continue
            old = previous_prices.get(label)
            if old is None or float(old) <= 0:
                continue
            points = float(current) - float(old)
            pct = points / float(old) * 100.0
            if abs(points) >= 8.0 or abs(pct) >= 20.0:
                premium_reasons.append(f"{label} {points:+.1f} ({pct:+.0f}%) /15s")
    if premium_reasons:
        premium_shock = "PREMIUM SHOCK"

    magnitude = min(100.0, abs(directional) * 100.0)
    same_sign = [value for value in changes.values() if value is not None and abs(value) >= 1.0]
    persistent = len(same_sign) >= 2 and all(value > 0 for value in same_sign) or (
        len(same_sign) >= 2 and all(value < 0 for value in same_sign)
    )
    direction = "BULLISH" if directional >= 0.18 else "BEARISH" if directional <= -0.18 else "MIXED"
    if magnitude >= 75 and persistent:
        state = "MAJOR MOVE CONFIRMED"
    elif magnitude >= 48:
        state = "FAST MOVE WATCH"
    elif magnitude >= 25:
        state = "WARMING UP"
    else:
        state = "STABLE"
    reasons = [
        f"NIFTY {value:+.1f}/{age}s"
        for age, value in changes.items()
        if value is not None
    ]
    reasons.extend(premium_reasons[:2])
    return LiveImpulse(
        direction=direction,
        state=state,
        score=round(magnitude, 1),
        change_5s=changes[5],
        change_15s=changes[15],
        change_30s=changes[30],
        change_60s=changes[60],
        premium_shock=premium_shock,
        reasons=tuple(reasons[:5]),
    )


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _extract(response: dict[str, Any], segment: str, security_id: int | str) -> dict[str, Any]:
    data = response.get("data") or {}
    segment_data = data.get(segment) or {}
    return segment_data.get(str(security_id)) or segment_data.get(int(security_id)) or {}


def _atm_rows(frame: pd.DataFrame, spot: float) -> list[pd.Series]:
    if frame.empty or not {"strike", "side", "security_id"}.issubset(frame.columns):
        return []
    usable = frame.dropna(subset=["strike", "side", "security_id"]).copy()
    if usable.empty:
        return []
    strikes = sorted(float(value) for value in usable["strike"].unique())
    atm = min(strikes, key=lambda value: abs(value - spot))
    rows: list[pd.Series] = []
    for side in ("CE", "PE"):
        match = usable[(usable["strike"] == atm) & (usable["side"].str.upper() == side)]
        if not match.empty:
            rows.append(match.iloc[0])
    return rows


def fetch_fast_quotes(client: "DhanClient", snapshot: Any) -> list[FastQuote]:
    """Fetch one lightweight quote batch without rebuilding the One-Brain snapshot."""
    spot = _number(getattr(snapshot, "nifty_quote", {}).get("last_price"))
    if spot is None:
        return []

    rows = _atm_rows(getattr(snapshot, "option_chain", pd.DataFrame()), spot)
    grouped: dict[str, list[int]] = {
        CONFIG.nifty.exchange_segment: [int(CONFIG.nifty.security_id)]
    }
    option_ids = [int(row["security_id"]) for row in rows]
    if option_ids:
        grouped["NSE_FNO"] = option_ids
    response = client.market_quote(grouped)

    result: list[FastQuote] = []
    nifty = _extract(response, CONFIG.nifty.exchange_segment, CONFIG.nifty.security_id)
    result.append(
        FastQuote(
            label="NIFTY Live",
            last_price=_number(nifty.get("last_price")),
            baseline=spot,
            last_trade_time=str(nifty.get("last_trade_time") or ""),
        )
    )
    for row in rows:
        security_id = int(row["security_id"])
        quote = _extract(response, "NSE_FNO", security_id)
        result.append(
            FastQuote(
                label=f"{float(row['strike']):,.0f} {str(row['side']).upper()}",
                last_price=_number(quote.get("last_price")),
                baseline=_number(row.get("last_price")),
                last_trade_time=str(quote.get("last_trade_time") or ""),
            )
        )
    return result


def monitor_timestamp() -> str:
    return datetime.now(ZoneInfo(IST_TIMEZONE)).strftime("%H:%M:%S IST")
