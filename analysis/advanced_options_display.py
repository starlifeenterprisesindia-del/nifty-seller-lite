"""Display-only advanced options analytics for Phase-2.

Golden-rule constraints:
- No broker/API calls.
- No mutation of MarketSnapshot or One-Brain scores.
- No disk/history read in the live critical path.
- Calculations run only when the UI panel is opened.

The helpers intentionally derive from the already-validated option-chain snapshot and
existing option-intelligence flow rows.  "Money" and "unusual" scores are relative
activity proxies, not rupee capital or calibrated trade probabilities.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Any

import pandas as pd


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pct_rank(value: float, population: list[float]) -> float:
    vals = sorted(v for v in population if math.isfinite(v))
    if not vals:
        return 0.0
    return 100.0 * sum(v <= value for v in vals) / len(vals)


def _spread_score(spread_pct: float | None) -> float:
    if spread_pct is None:
        return 0.0
    if spread_pct <= 0.75:
        return 100.0
    if spread_pct <= 1.5:
        return 90.0
    if spread_pct <= 3.0:
        return 75.0
    if spread_pct <= 5.0:
        return 58.0
    if spread_pct <= 8.0:
        return 38.0
    if spread_pct <= 12.0:
        return 20.0
    return 5.0


def _grade(score: float) -> str:
    if score >= 85:
        return "A+"
    if score >= 75:
        return "A"
    if score >= 62:
        return "B"
    if score >= 48:
        return "C"
    return "D"


def _tag(score: float) -> str:
    if score >= 85:
        return "VERY HIGH"
    if score >= 70:
        return "HIGH"
    if score >= 50:
        return "MEDIUM"
    return "LOW"


def _clean_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    data = frame.copy()
    for name in (
        "strike", "last_price", "top_bid_price", "top_ask_price", "oi", "volume",
        "implied_volatility", "delta", "gamma", "theta", "vega",
    ):
        if name in data:
            data[name] = pd.to_numeric(data[name], errors="coerce")
    if "side" in data:
        data["side"] = data["side"].astype(str).str.upper()
    return data


def nearest_atm_strike(frame: pd.DataFrame | None, spot: float | None) -> float | None:
    data = _clean_frame(frame)
    spot_value = _finite(spot)
    if data.empty or spot_value is None or "strike" not in data:
        return None
    strikes = data["strike"].dropna().unique()
    if len(strikes) == 0:
        return None
    return float(min(strikes, key=lambda value: abs(float(value) - spot_value)))


def liquidity_board(frame: pd.DataFrame | None, spot: float | None, *, max_rows: int = 12) -> list[dict[str, Any]]:
    """Rank near-ATM contracts with an execution-quality proxy.

    Score = spread quality 50% + relative OI 30% + relative volume 20%.
    This is display/selection context only and does not alter TradePlan scoring.
    """
    data = _clean_frame(frame)
    spot_value = _finite(spot)
    if data.empty or spot_value is None or not {"strike", "side"}.issubset(data.columns):
        return []
    data = data[data["side"].isin(["CE", "PE"])].copy()
    data["distance"] = (data["strike"] - spot_value).abs()
    data = data.sort_values(["distance", "strike", "side"]).head(max(20, max_rows * 2))
    oi_pop = [max(0.0, _finite(v) or 0.0) for v in data.get("oi", pd.Series(dtype=float)).tolist()]
    vol_pop = [max(0.0, _finite(v) or 0.0) for v in data.get("volume", pd.Series(dtype=float)).tolist()]
    rows: list[dict[str, Any]] = []
    for raw in data.to_dict("records"):
        bid, ask, ltp = (_finite(raw.get(k)) for k in ("top_bid_price", "top_ask_price", "last_price"))
        midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None and bid > 0 and ask >= bid else None
        spread_pct = ((ask - bid) / midpoint * 100.0) if midpoint and midpoint > 0 else None
        oi = max(0.0, _finite(raw.get("oi")) or 0.0)
        volume = max(0.0, _finite(raw.get("volume")) or 0.0)
        score = 0.50 * _spread_score(spread_pct) + 0.30 * _pct_rank(oi, oi_pop) + 0.20 * _pct_rank(volume, vol_pop)
        rows.append({
            "strike": round(float(raw["strike"]), 2),
            "side": str(raw["side"]),
            "ltp": round(ltp, 2) if ltp is not None else None,
            "bid": round(bid, 2) if bid is not None else None,
            "ask": round(ask, 2) if ask is not None else None,
            "spread_pct": round(spread_pct, 2) if spread_pct is not None else None,
            "oi": int(round(oi)),
            "volume": int(round(volume)),
            "distance": round(float(raw["distance"]), 1),
            "score": round(score, 1),
            "grade": _grade(score),
        })
    return sorted(rows, key=lambda row: (-row["score"], row["distance"]))[:max_rows]


def unusual_activity(flow_rows: Any, spot: float | None, *, top_n: int = 8) -> list[dict[str, Any]]:
    """Find relative intraday anomalies from already-computed matched flow rows."""
    rows = list(flow_rows or [])
    spot_value = _finite(spot) or 0.0
    cleaned: list[dict[str, Any]] = []
    for raw in rows:
        if str(raw.get("integrity_status", "")).upper() != "READY":
            continue
        strike = _finite(raw.get("strike"))
        if strike is None:
            continue
        cleaned.append({
            **raw,
            "strike_num": strike,
            "oi_abs": abs(_finite(raw.get("oi_delta")) or 0.0),
            "vol_abs": abs(_finite(raw.get("volume_delta")) or 0.0),
            "premium_abs": abs(_finite(raw.get("price_delta")) or 0.0),
            "flow_abs": abs(_finite(raw.get("flow_strength")) or 0.0),
        })
    if not cleaned:
        return []
    oi_pop = [r["oi_abs"] for r in cleaned]
    vol_pop = [r["vol_abs"] for r in cleaned]
    prem_pop = [r["premium_abs"] for r in cleaned]
    flow_pop = [r["flow_abs"] for r in cleaned]
    result: list[dict[str, Any]] = []
    for raw in cleaned:
        distance = abs(raw["strike_num"] - spot_value)
        proximity = max(0.0, 100.0 - min(100.0, distance / 4.0))
        score = (
            0.35 * _pct_rank(raw["oi_abs"], oi_pop)
            + 0.30 * _pct_rank(raw["vol_abs"], vol_pop)
            + 0.20 * _pct_rank(raw["premium_abs"], prem_pop)
            + 0.10 * _pct_rank(raw["flow_abs"], flow_pop)
            + 0.05 * proximity
        )
        if raw["oi_abs"] <= 0 and raw["vol_abs"] <= 0:
            continue
        result.append({
            "strike": round(raw["strike_num"], 2),
            "side": str(raw.get("side") or ""),
            "classification": str(raw.get("classification") or "ACTIVITY"),
            "bias": str(raw.get("directional_bias") or "NEUTRAL"),
            "oi_delta": round(_finite(raw.get("oi_delta")) or 0.0),
            "volume_delta": round(_finite(raw.get("volume_delta")) or 0.0),
            "premium_delta": round(_finite(raw.get("price_delta")) or 0.0, 2),
            "score": round(score, 1),
            "tag": _tag(score),
        })
    return sorted(result, key=lambda row: row["score"], reverse=True)[:top_n]


def straddle_context(frame: pd.DataFrame | None, spot: float | None) -> dict[str, Any]:
    data = _clean_frame(frame)
    atm = nearest_atm_strike(data, spot)
    spot_value = _finite(spot)
    if data.empty or atm is None or spot_value is None:
        return {"status": "UNAVAILABLE"}
    pair = data[data["strike"].eq(atm) & data["side"].isin(["CE", "PE"])]
    by_side = {str(row["side"]): row for row in pair.to_dict("records")}
    if "CE" not in by_side or "PE" not in by_side:
        return {"status": "UNAVAILABLE", "atm": atm}
    ce, pe = by_side["CE"], by_side["PE"]
    ce_price, pe_price = _finite(ce.get("last_price")), _finite(pe.get("last_price"))
    if ce_price is None or pe_price is None or ce_price < 0 or pe_price < 0:
        return {"status": "UNAVAILABLE", "atm": atm}
    premium = ce_price + pe_price
    ce_iv, pe_iv = _finite(ce.get("implied_volatility")), _finite(pe.get("implied_volatility"))
    theta_values = [v for v in (_finite(ce.get("theta")), _finite(pe.get("theta"))) if v is not None]
    return {
        "status": "READY",
        "atm": round(atm, 2),
        "ce_premium": round(ce_price, 2),
        "pe_premium": round(pe_price, 2),
        "combined_premium": round(premium, 2),
        "premium_pct_spot": round(premium / spot_value * 100.0, 2) if spot_value > 0 else None,
        "upper_proxy": round(spot_value + premium, 2),
        "lower_proxy": round(spot_value - premium, 2),
        "ce_iv": round(ce_iv, 2) if ce_iv is not None else None,
        "pe_iv": round(pe_iv, 2) if pe_iv is not None else None,
        "iv_skew_pe_minus_ce": round(pe_iv - ce_iv, 2) if ce_iv is not None and pe_iv is not None else None,
        "combined_theta": round(sum(theta_values), 3) if theta_values else None,
    }


def iv_context(frame: pd.DataFrame | None, spot: float | None, historical_atm_iv: list[float] | None = None) -> dict[str, Any]:
    """Current IV context plus true IVR/IVP only when real historical ATM IV is supplied."""
    data = _clean_frame(frame)
    atm = nearest_atm_strike(data, spot)
    if data.empty or atm is None or "implied_volatility" not in data:
        return {"status": "UNAVAILABLE", "ivr_status": "UNAVAILABLE"}
    pair = data[data["strike"].eq(atm) & data["side"].isin(["CE", "PE"])]
    ivs = [v for v in (_finite(x) for x in pair["implied_volatility"].tolist()) if v is not None and v > 0]
    all_ivs = [v for v in (_finite(x) for x in data["implied_volatility"].tolist()) if v is not None and v > 0]
    current_iv = float(median(ivs)) if ivs else None
    chain_median = float(median(all_ivs)) if all_ivs else None
    hist = [float(v) for v in (historical_atm_iv or []) if _finite(v) is not None and float(v) > 0]
    ivr = ivp = None
    ivr_status = "WARMING UP — NEED >=20 HISTORICAL SESSIONS"
    if current_iv is not None and len(hist) >= 20:
        low, high = min(hist), max(hist)
        if high > low:
            ivr = max(0.0, min(100.0, (current_iv - low) / (high - low) * 100.0))
        ivp = 100.0 * sum(v <= current_iv for v in hist) / len(hist)
        ivr_status = f"READY — {len(hist)} sessions"
    return {
        "status": "READY" if current_iv is not None else "UNAVAILABLE",
        "atm": round(atm, 2),
        "atm_iv": round(current_iv, 2) if current_iv is not None else None,
        "chain_median_iv": round(chain_median, 2) if chain_median is not None else None,
        "iv_rank": round(ivr, 1) if ivr is not None else None,
        "iv_percentile": round(ivp, 1) if ivp is not None else None,
        "history_sessions": len(hist),
        "ivr_status": ivr_status,
    }



def volatility_smile(frame: pd.DataFrame | None, spot: float | None, *, max_strikes: int = 17) -> list[dict[str, Any]]:
    """Current-expiry IV smile/surface rows from the already-fetched option chain."""
    data = _clean_frame(frame)
    spot_value = _finite(spot)
    if data.empty or spot_value is None or not {"strike", "side", "implied_volatility"}.issubset(data.columns):
        return []
    data = data[data["side"].isin(["CE", "PE"])].copy()
    data["distance"] = (data["strike"] - spot_value).abs()
    strikes = (
        data[["strike", "distance"]].drop_duplicates().sort_values(["distance", "strike"]).head(max_strikes)["strike"].tolist()
    )
    keep = data[data["strike"].isin(strikes)]
    rows: list[dict[str, Any]] = []
    for strike in sorted(strikes):
        pair = keep[keep["strike"].eq(strike)]
        by_side = {str(row.get("side")): row for row in pair.to_dict("records")}
        ce, pe = by_side.get("CE", {}), by_side.get("PE", {})
        ce_iv, pe_iv = _finite(ce.get("implied_volatility")), _finite(pe.get("implied_volatility"))
        valid = [v for v in (ce_iv, pe_iv) if v is not None and v > 0]
        mid = float(median(valid)) if valid else None
        rows.append({
            "strike": round(float(strike), 2),
            "moneyness_pct": round((float(strike) / spot_value - 1.0) * 100.0, 3) if spot_value > 0 else None,
            "ce_iv": round(ce_iv, 2) if ce_iv is not None and ce_iv > 0 else None,
            "pe_iv": round(pe_iv, 2) if pe_iv is not None and pe_iv > 0 else None,
            "mid_iv": round(mid, 2) if mid is not None else None,
            "ce_delta": round(_finite(ce.get("delta")), 3) if _finite(ce.get("delta")) is not None else None,
            "pe_delta": round(_finite(pe.get("delta")), 3) if _finite(pe.get("delta")) is not None else None,
            "ce_oi": int(round(max(0.0, _finite(ce.get("oi")) or 0.0))),
            "pe_oi": int(round(max(0.0, _finite(pe.get("oi")) or 0.0))),
            "atm": bool(abs(float(strike) - spot_value) == min(abs(float(x) - spot_value) for x in strikes)),
        })
    return rows


def _nearest_delta_row(data: pd.DataFrame, side: str, target_abs_delta: float, spot: float) -> dict[str, Any] | None:
    subset = data[data["side"].eq(side)].copy()
    if side == "CE":
        subset = subset[subset["strike"] >= spot]
    else:
        subset = subset[subset["strike"] <= spot]
    if subset.empty or "delta" not in subset:
        return None
    subset["delta_abs"] = pd.to_numeric(subset["delta"], errors="coerce").abs()
    subset = subset.dropna(subset=["delta_abs", "implied_volatility"])
    subset = subset[subset["implied_volatility"] > 0]
    if subset.empty:
        return None
    idx = (subset["delta_abs"] - target_abs_delta).abs().idxmin()
    return subset.loc[idx].to_dict()


def skew_metrics(frame: pd.DataFrame | None, spot: float | None) -> dict[str, Any]:
    """25-delta-style skew and smile curvature for the current expiry."""
    data = _clean_frame(frame)
    spot_value = _finite(spot)
    atm = nearest_atm_strike(data, spot_value)
    if data.empty or spot_value is None or atm is None:
        return {"status": "UNAVAILABLE"}
    pair = data[data["strike"].eq(atm) & data["side"].isin(["CE", "PE"])]
    atm_ivs = [v for v in (_finite(v) for v in pair.get("implied_volatility", pd.Series(dtype=float)).tolist()) if v is not None and v > 0]
    atm_iv = float(median(atm_ivs)) if atm_ivs else None
    ce25 = _nearest_delta_row(data, "CE", 0.25, spot_value)
    pe25 = _nearest_delta_row(data, "PE", 0.25, spot_value)
    ce25_iv = _finite((ce25 or {}).get("implied_volatility"))
    pe25_iv = _finite((pe25 or {}).get("implied_volatility"))
    rr = (pe25_iv - ce25_iv) if ce25_iv is not None and pe25_iv is not None else None
    butterfly = ((ce25_iv + pe25_iv) / 2.0 - atm_iv) if ce25_iv is not None and pe25_iv is not None and atm_iv is not None else None
    if rr is None:
        skew_state = "UNAVAILABLE"
    elif rr >= 1.0:
        skew_state = "PUT SKEW"
    elif rr <= -1.0:
        skew_state = "CALL SKEW"
    else:
        skew_state = "BALANCED"
    if butterfly is None:
        smile_state = "UNAVAILABLE"
    elif butterfly >= 1.0:
        smile_state = "WINGS RICH"
    elif butterfly <= -1.0:
        smile_state = "ATM RICH"
    else:
        smile_state = "BALANCED SMILE"
    return {
        "status": "READY" if atm_iv is not None else "UNAVAILABLE",
        "atm": round(atm, 2),
        "atm_iv": round(atm_iv, 2) if atm_iv is not None else None,
        "ce25_strike": round(_finite((ce25 or {}).get("strike")), 2) if _finite((ce25 or {}).get("strike")) is not None else None,
        "ce25_iv": round(ce25_iv, 2) if ce25_iv is not None else None,
        "pe25_strike": round(_finite((pe25 or {}).get("strike")), 2) if _finite((pe25 or {}).get("strike")) is not None else None,
        "pe25_iv": round(pe25_iv, 2) if pe25_iv is not None else None,
        "rr25_put_minus_call": round(rr, 2) if rr is not None else None,
        "butterfly25": round(butterfly, 2) if butterfly is not None else None,
        "skew_state": skew_state,
        "smile_state": smile_state,
    }


def intraday_atm_iv_series(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Build a small same-day ATM-IV trend from saved option-state snapshots."""
    result: list[dict[str, Any]] = []
    for snap in history or []:
        if not isinstance(snap, dict):
            continue
        spot = _finite(snap.get("spot"))
        rows = snap.get("rows") or []
        if spot is None or not isinstance(rows, list):
            continue
        strikes = sorted({
            _finite(row.get("strike")) for row in rows
            if isinstance(row, dict) and _finite(row.get("strike")) is not None
        })
        if not strikes:
            continue
        atm = min(strikes, key=lambda value: abs(float(value) - spot))
        ivs = [
            _finite(row.get("implied_volatility")) for row in rows
            if isinstance(row, dict)
            and _finite(row.get("strike")) == atm
            and str(row.get("side") or "").upper() in {"CE", "PE"}
            and (_finite(row.get("implied_volatility")) or 0) > 0
        ]
        ivs = [v for v in ivs if v is not None]
        if not ivs:
            continue
        result.append({
            "at": str(snap.get("captured_at") or ""),
            "atm": round(float(atm), 2),
            "atm_iv": round(float(sum(ivs) / len(ivs)), 2),
        })
    # Bounded display series: enough to see the day without rendering hundreds of points.
    if len(result) > 90:
        step = max(1, len(result) // 90)
        sampled = result[::step]
        if sampled[-1] != result[-1]:
            sampled.append(result[-1])
        return sampled[-90:]
    return result


def volatility_regime(iv: dict[str, Any], skew: dict[str, Any]) -> dict[str, Any]:
    current = _finite(iv.get("atm_iv"))
    chain = _finite(iv.get("chain_median_iv"))
    ivp = _finite(iv.get("iv_percentile"))
    if current is None:
        return {"status": "UNAVAILABLE", "regime": "UNAVAILABLE"}
    if ivp is not None:
        regime = "HIGH IV" if ivp >= 70 else "LOW IV" if ivp <= 30 else "NORMAL IV"
        basis = "historical percentile"
    elif chain is not None and chain > 0:
        rel = (current / chain - 1.0) * 100.0
        regime = "RELATIVE HIGH" if rel >= 5 else "RELATIVE LOW" if rel <= -5 else "CHAIN NORMAL"
        basis = "current chain only — historical IVP warming up"
    else:
        regime, basis = "CURRENT IV ONLY", "historical context unavailable"
    return {
        "status": "READY",
        "regime": regime,
        "basis": basis,
        "skew_state": skew.get("skew_state"),
        "smile_state": skew.get("smile_state"),
    }


def build_phase8_payload(
    snapshot: Any,
    historical_atm_iv: list[float] | None = None,
    intraday_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    spot = _finite((getattr(snapshot, "nifty_quote", {}) or {}).get("last_price"))
    frame = getattr(snapshot, "option_chain", None)
    base = build_phase2_payload(snapshot, historical_atm_iv=historical_atm_iv)
    smile = volatility_smile(frame, spot)
    skew = skew_metrics(frame, spot)
    base.update({
        "smile": smile,
        "surface": smile,  # current-expiry strike x CE/PE IV surface; no extra expiry fetch.
        "skew": skew,
        "volatility_regime": volatility_regime(base.get("iv", {}), skew),
        "intraday_iv": intraday_atm_iv_series(intraday_history),
        "phase8_note": (
            "Current-expiry IV smile/surface + 25Δ-style skew use the existing option-chain snapshot. "
            "True IV Rank/Percentile uses only persisted real ATM-IV session summaries; no synthetic history."
        ),
    })
    return base

def build_phase2_payload(snapshot: Any, historical_atm_iv: list[float] | None = None) -> dict[str, Any]:
    spot = _finite((getattr(snapshot, "nifty_quote", {}) or {}).get("last_price"))
    frame = getattr(snapshot, "option_chain", None)
    options = getattr(snapshot, "option_intelligence", None)
    return {
        "liquidity": liquidity_board(frame, spot),
        "unusual_activity": unusual_activity(getattr(options, "flow_rows", ()), spot),
        "straddle": straddle_context(frame, spot),
        "iv": iv_context(frame, spot, historical_atm_iv=historical_atm_iv),
        "note": "Display-only; existing snapshot only; zero broker/API calls and zero One-Brain weight.",
    }
