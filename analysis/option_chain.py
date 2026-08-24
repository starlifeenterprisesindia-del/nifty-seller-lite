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


def _flatten_side(
    strike: float, side: str, data: dict[str, Any] | None
) -> dict[str, Any]:
    data = data or {}
    row: dict[str, Any] = {"strike": strike, "side": side.upper()}
    for field in OPTION_FIELDS:
        row[field] = data.get(field)
    # Keep DhanHQ as the single Greeks source. Different brokers can legitimately
    # disagree because their IV model and snapshot timing differ; never invent a
    # local value merely to force a match. Invalid zero/signed values are safer as
    # unavailable than as strike-selection inputs.
    greeks = data.get("greeks") or {}
    try:
        valid_iv = float(data.get("implied_volatility")) > 0
    except (TypeError, ValueError):
        valid_iv = False

    def _valid(value: Any, *, lower: float | None = None, upper: float | None = None):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if lower is not None and number < lower:
            return None
        if upper is not None and number > upper:
            return None
        return number

    delta = _valid(greeks.get("delta"), lower=-1.0, upper=1.0)
    if not valid_iv and delta == 0.0:
        delta = None
    gamma = _valid(greeks.get("gamma"), lower=0.0) if valid_iv else None
    theta = _valid(greeks.get("theta"), upper=0.0) if valid_iv else None
    vega = _valid(greeks.get("vega"), lower=0.0) if valid_iv else None
    row["delta"] = delta
    row["gamma"] = gamma if gamma is not None and gamma > 0 else None
    row["theta"] = theta if theta is not None and theta < 0 else None
    row["vega"] = vega if vega is not None and vega > 0 else None
    return row


def option_chain_to_frame(
    response: dict[str, Any],
) -> tuple[float | None, pd.DataFrame]:
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
