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
