from __future__ import annotations

from typing import Any

import pandas as pd


OPTION_FIELDS = (
    "last_price",
    "oi",
    "previous_oi",
    "volume",
    "previous_volume",
    "previous_close_price",
    "implied_volatility",
    "top_bid_price",
    "top_ask_price",
    "security_id",
)


def _flatten_side(strike: float, side: str, data: dict[str, Any] | None) -> dict[str, Any]:
    data = data or {}
    row: dict[str, Any] = {"strike": strike, "side": side.upper()}
    for field in OPTION_FIELDS:
        row[field] = data.get(field)
    greeks = data.get("greeks") or {}
    for greek in ("delta", "gamma", "theta", "vega"):
        row[greek] = greeks.get(greek)
    return row


def option_chain_to_frame(response: dict[str, Any]) -> tuple[float | None, pd.DataFrame]:
    data = response.get("data") or {}
    spot = data.get("last_price")
    oc = data.get("oc") or {}
    rows: list[dict[str, Any]] = []
    for strike_text, sides in oc.items():
        try:
            strike = float(strike_text)
        except (TypeError, ValueError):
            continue
        sides = sides or {}
        if sides.get("ce"):
            rows.append(_flatten_side(strike, "CE", sides.get("ce")))
        if sides.get("pe"):
            rows.append(_flatten_side(strike, "PE", sides.get("pe")))
    frame = pd.DataFrame(rows)
    if frame.empty:
        return (float(spot) if spot is not None else None), frame
    for col in [item for item in frame.columns if item != "side"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["day_oi_change"] = frame["oi"] - frame["previous_oi"]
    frame["day_price_change"] = frame["last_price"] - frame["previous_close_price"]
    frame = frame.sort_values(["strike", "side"]).reset_index(drop=True)
    return (float(spot) if spot is not None else None), frame


def select_atm_window(
    frame: pd.DataFrame,
    spot: float,
    strikes_each_side: int = 5,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    strikes = sorted(frame["strike"].dropna().unique())
    if not strikes:
        return frame.iloc[0:0].copy()
    atm = min(strikes, key=lambda value: abs(value - spot))
    atm_index = strikes.index(atm)
    low = max(0, atm_index - strikes_each_side)
    high = min(len(strikes), atm_index + strikes_each_side + 1)
    chosen = set(strikes[low:high])
    result = frame[frame["strike"].isin(chosen)].copy()
    result["is_atm"] = result["strike"].eq(atm)
    return result.sort_values(["strike", "side"]).reset_index(drop=True)
