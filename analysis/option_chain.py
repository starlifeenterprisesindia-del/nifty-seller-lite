from __future__ import annotations

from typing import Any
from math import isfinite

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
        if not isfinite(number):
            return None
        if lower is not None and number < lower:
            return None
        if upper is not None and number > upper:
            return None
        return number

    delta = _valid(greeks.get("delta"), lower=-1.0, upper=1.0)
    if delta is not None and (
        (side.upper() == "CE" and delta <= 0) or (side.upper() == "PE" and delta >= 0)
    ):
        delta = None
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
    return (float(spot) if spot is not None else None), validate_greeks(frame)


def validate_greeks(frame: pd.DataFrame) -> pd.DataFrame:
    """Conservative consistency screen, not a replacement pricing model.

    Keep quotes/OI even when Greeks cannot safely rank a trade. Pair tolerances
    flag a model/input mismatch; they do not establish which vendor is right.
    """
    frame = frame.copy()
    fields = ["delta", "gamma", "theta", "vega", "implied_volatility"]
    for field in fields:
        if field not in frame:
            frame[field] = float("nan")
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
        if f"source_{field}" not in frame:
            frame[f"source_{field}"] = frame[field]
    finite = (
        frame[fields]
        .apply(lambda col: col.map(lambda v: pd.notna(v) and isfinite(float(v))))
        .all(axis=1)
    )
    valid = (
        finite
        & frame.gamma.gt(0)
        & frame.vega.gt(0)
        & frame.theta.lt(0)
        & frame.implied_volatility.gt(0)
        & frame.delta.abs().between(0.001, 1)
    )
    valid &= (frame.side.eq("CE") & frame.delta.gt(0)) | (
        frame.side.eq("PE") & frame.delta.lt(0)
    )
    frame["greeks_quality"] = "UNAVAILABLE"
    frame.loc[valid, "greeks_quality"] = "READY"
    for _, pair in frame.groupby("strike"):
        ce, pe = pair[pair.side.eq("CE")], pair[pair.side.eq("PE")]
        if len(ce) != 1 or len(pe) != 1:
            continue
        c, p = ce.iloc[0], pe.iloc[0]
        ivs = [c.implied_volatility, p.implied_volatility]
        suspicious_iv = (
            all(pd.notna(v) and v > 0 for v in ivs) and max(ivs) / min(ivs) > 1.35
        )
        suspicious_delta = (
            pd.notna(c.delta)
            and pd.notna(p.delta)
            and abs(c.delta - p.delta - 1) > 0.15
        )
        if suspicious_iv or suspicious_delta:
            frame.loc[pair.index, "greeks_quality"] = "MODEL MISMATCH"
    frame.loc[frame.greeks_quality.ne("READY"), ["delta", "gamma", "theta", "vega"]] = (
        float("nan")
    )
    return frame


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
