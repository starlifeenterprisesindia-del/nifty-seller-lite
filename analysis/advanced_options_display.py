"""Display-only advanced options analytics for Phase-10.

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
from datetime import datetime, time as dt_time
from statistics import median
from typing import Any

import pandas as pd

from config import CONFIG


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
    """Rank near-ATM contracts with a display-only execution-quality proxy.

    Phase-10 score = spread quality 45% + relative OI 25% + relative volume 20%
    + ATM proximity 10%.  The result is an execution context only; it does not
    alter TradePlan scoring or One-Brain decisions.
    """
    data = _clean_frame(frame)
    spot_value = _finite(spot)
    if data.empty or spot_value is None or not {"strike", "side"}.issubset(data.columns):
        return []
    data = data[data["side"].isin(["CE", "PE"])].copy()
    data["distance"] = (data["strike"] - spot_value).abs()
    data = data.sort_values(["distance", "strike", "side"]).head(max(24, max_rows * 3))
    oi_pop = [max(0.0, _finite(v) or 0.0) for v in data.get("oi", pd.Series(dtype=float)).tolist()]
    vol_pop = [max(0.0, _finite(v) or 0.0) for v in data.get("volume", pd.Series(dtype=float)).tolist()]
    max_distance = max([float(v) for v in data["distance"].dropna().tolist()] or [1.0])
    lot_size = max(1, int(getattr(CONFIG, "risk_default_lot_size", 65) or 65))
    rows: list[dict[str, Any]] = []
    for raw in data.to_dict("records"):
        bid, ask, ltp = (_finite(raw.get(k)) for k in ("top_bid_price", "top_ask_price", "last_price"))
        midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None and bid > 0 and ask >= bid else None
        spread_points = (ask - bid) if bid is not None and ask is not None and ask >= bid else None
        spread_pct = ((ask - bid) / midpoint * 100.0) if midpoint and midpoint > 0 else None
        oi = max(0.0, _finite(raw.get("oi")) or 0.0)
        volume = max(0.0, _finite(raw.get("volume")) or 0.0)
        distance = float(raw.get("distance") or 0.0)
        proximity = max(0.0, 100.0 - 100.0 * distance / max(max_distance, 1.0))
        oi_rank = _pct_rank(oi, oi_pop)
        vol_rank = _pct_rank(volume, vol_pop)
        score = (
            0.45 * _spread_score(spread_pct)
            + 0.25 * oi_rank
            + 0.20 * vol_rank
            + 0.10 * proximity
        )
        executable = bool(
            midpoint is not None
            and spread_pct is not None
            and spread_pct <= 5.0
            and oi > 0
            and volume > 0
        )
        if score >= 75 and executable:
            state = "ENTRY FRIENDLY"
        elif score >= 55 and midpoint is not None:
            state = "CAUTION"
        else:
            state = "AVOID / THIN"
        # Half-spread is only a friction proxy, not a guaranteed fill/slippage estimate.
        half_spread_rupees_per_lot = (spread_points / 2.0 * lot_size) if spread_points is not None else None
        rows.append({
            "strike": round(float(raw["strike"]), 2),
            "side": str(raw["side"]),
            "ltp": round(ltp, 2) if ltp is not None else None,
            "bid": round(bid, 2) if bid is not None else None,
            "ask": round(ask, 2) if ask is not None else None,
            "mid": round(midpoint, 2) if midpoint is not None else None,
            "spread_points": round(spread_points, 2) if spread_points is not None else None,
            "spread_pct": round(spread_pct, 2) if spread_pct is not None else None,
            "oi": int(round(oi)),
            "volume": int(round(volume)),
            "oi_rank": round(oi_rank, 1),
            "volume_rank": round(vol_rank, 1),
            "distance": round(distance, 1),
            "score": round(score, 1),
            "grade": _grade(score),
            "state": state,
            "executable": executable,
            "half_spread_rupees_per_lot": round(half_spread_rupees_per_lot, 2) if half_spread_rupees_per_lot is not None else None,
        })
    return sorted(rows, key=lambda row: (-row["score"], row["distance"]))[:max_rows]


def liquidity_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact market-quality summary from the already-built liquidity rows."""
    if not rows:
        return {"status": "UNAVAILABLE"}
    spreads = [float(r["spread_pct"]) for r in rows if r.get("spread_pct") is not None]
    scores = [float(r.get("score") or 0.0) for r in rows]
    friendly = [r for r in rows if r.get("state") == "ENTRY FRIENDLY"]
    best_ce = next((r for r in rows if r.get("side") == "CE"), None)
    best_pe = next((r for r in rows if r.get("side") == "PE"), None)
    median_spread = float(median(spreads)) if spreads else None
    average_score = sum(scores) / len(scores) if scores else 0.0
    if len(friendly) >= 4 and average_score >= 70:
        market_state = "DEEP / HEALTHY"
    elif len(friendly) >= 2 and average_score >= 55:
        market_state = "USABLE"
    else:
        market_state = "THIN / CAUTION"
    return {
        "status": "READY",
        "market_state": market_state,
        "friendly_contracts": len(friendly),
        "contracts_checked": len(rows),
        "median_spread_pct": round(median_spread, 2) if median_spread is not None else None,
        "average_score": round(average_score, 1),
        "best_ce": best_ce,
        "best_pe": best_pe,
    }


def _window_alignment(options: Any, bias: str) -> tuple[int, int, str]:
    windows = list(getattr(options, "windows", ()) or ()) if options is not None else []
    ready = [w for w in windows if str(getattr(w, "status", "")).upper() == "READY"]
    if not ready or bias not in {"BULLISH", "BEARISH"}:
        return 0, len(ready), "UNCONFIRMED"
    aligned = sum(str(getattr(w, "bias", "")).upper() == bias for w in ready)
    if aligned >= 2:
        state = "MULTI-WINDOW CONFIRMED"
    elif aligned == 1:
        state = "PARTIAL CONFIRM"
    else:
        state = "NOT CONFIRMED"
    return aligned, len(ready), state


def unusual_activity(
    flow_rows: Any,
    spot: float | None,
    *,
    frame: pd.DataFrame | None = None,
    options: Any | None = None,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """Find relative intraday anomalies from already-computed matched flow rows.

    Phase-10 adds current-IV richness and multi-window confirmation without any
    new broker call. Scores remain relative anomaly proxies, not institutional
    identity or calibrated trade probabilities.
    """
    rows = list(flow_rows or [])
    spot_value = _finite(spot) or 0.0
    chain = _clean_frame(frame)
    iv_map: dict[tuple[float, str], float] = {}
    iv_pop: list[float] = []
    if not chain.empty and {"strike", "side", "implied_volatility"}.issubset(chain.columns):
        for r in chain.to_dict("records"):
            strike = _finite(r.get("strike"))
            iv = _finite(r.get("implied_volatility"))
            side = str(r.get("side") or "").upper()
            if strike is not None and iv is not None and iv > 0 and side in {"CE", "PE"}:
                iv_map[(strike, side)] = iv
                iv_pop.append(iv)
    cleaned: list[dict[str, Any]] = []
    for raw in rows:
        if str(raw.get("integrity_status", "")).upper() != "READY":
            continue
        strike = _finite(raw.get("strike"))
        side = str(raw.get("side") or "").upper()
        if strike is None or side not in {"CE", "PE"}:
            continue
        cleaned.append({
            **raw,
            "strike_num": strike,
            "side_norm": side,
            "oi_abs": abs(_finite(raw.get("oi_delta")) or 0.0),
            "vol_abs": abs(_finite(raw.get("volume_delta")) or 0.0),
            "premium_abs": abs(_finite(raw.get("price_delta")) or 0.0),
            "flow_abs": abs(_finite(raw.get("flow_strength")) or 0.0),
            "iv": iv_map.get((strike, side)),
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
        oi_rank = _pct_rank(raw["oi_abs"], oi_pop)
        vol_rank = _pct_rank(raw["vol_abs"], vol_pop)
        prem_rank = _pct_rank(raw["premium_abs"], prem_pop)
        flow_rank = _pct_rank(raw["flow_abs"], flow_pop)
        iv_rank = _pct_rank(raw["iv"], iv_pop) if raw.get("iv") is not None and iv_pop else 0.0
        bias = str(raw.get("directional_bias") or "NEUTRAL").upper()
        aligned, ready_windows, confirmation = _window_alignment(options, bias)
        window_score = (100.0 * aligned / ready_windows) if ready_windows else 0.0
        score = (
            0.28 * oi_rank
            + 0.24 * vol_rank
            + 0.18 * prem_rank
            + 0.10 * flow_rank
            + 0.08 * iv_rank
            + 0.07 * proximity
            + 0.05 * window_score
        )
        if raw["oi_abs"] <= 0 and raw["vol_abs"] <= 0:
            continue
        top_factors = sorted(
            [("OI", oi_rank), ("VOL", vol_rank), ("PREM", prem_rank), ("IV", iv_rank), ("FLOW", flow_rank)],
            key=lambda x: x[1], reverse=True,
        )[:2]
        reason = " + ".join(name for name, val in top_factors if val >= 50) or "relative flow anomaly"
        result.append({
            "strike": round(raw["strike_num"], 2),
            "side": raw["side_norm"],
            "classification": str(raw.get("classification") or "ACTIVITY"),
            "bias": bias,
            "oi_delta": round(_finite(raw.get("oi_delta")) or 0.0),
            "volume_delta": round(_finite(raw.get("volume_delta")) or 0.0),
            "premium_delta": round(_finite(raw.get("price_delta")) or 0.0, 2),
            "iv": round(raw["iv"], 2) if raw.get("iv") is not None else None,
            "iv_rank_chain": round(iv_rank, 1) if raw.get("iv") is not None else None,
            "window_confirm": f"{aligned}/{ready_windows}" if ready_windows else "0/0",
            "confirmation": confirmation,
            "reason": reason,
            "score": round(score, 1),
            "tag": _tag(score),
        })
    return sorted(result, key=lambda row: row["score"], reverse=True)[:top_n]


def activity_clusters(rows: list[dict[str, Any]], frame: pd.DataFrame | None = None) -> list[dict[str, Any]]:
    """Group nearby same-bias anomalies into display-only strike clusters."""
    if not rows:
        return []
    data = _clean_frame(frame)
    step = _strike_step(data) if not data.empty else None
    threshold = max(50.0, float(step or 50.0) * 1.05)
    ordered = sorted(rows, key=lambda r: (str(r.get("bias")), float(r.get("strike") or 0.0)))
    clusters: list[list[dict[str, Any]]] = []
    for row in ordered:
        if not clusters:
            clusters.append([row])
            continue
        prev = clusters[-1][-1]
        same_bias = str(prev.get("bias")) == str(row.get("bias")) and str(row.get("bias")) in {"BULLISH", "BEARISH"}
        near = abs(float(row.get("strike") or 0.0) - float(prev.get("strike") or 0.0)) <= threshold
        if same_bias and near:
            clusters[-1].append(row)
        else:
            clusters.append([row])
    out: list[dict[str, Any]] = []
    for group in clusters:
        if len(group) < 2:
            continue
        scores = [float(r.get("score") or 0.0) for r in group]
        strikes = [float(r.get("strike") or 0.0) for r in group]
        sides = sorted(set(str(r.get("side") or "") for r in group))
        out.append({
            "bias": str(group[0].get("bias") or "NEUTRAL"),
            "strike_from": round(min(strikes), 2),
            "strike_to": round(max(strikes), 2),
            "contracts": len(group),
            "sides": "/".join(sides),
            "avg_score": round(sum(scores) / len(scores), 1),
            "peak_score": round(max(scores), 1),
            "state": "CLUSTERED ACTIVITY",
        })
    return sorted(out, key=lambda r: (r["peak_score"], r["contracts"]), reverse=True)[:6]

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




def _strike_step(data: pd.DataFrame) -> float | None:
    if data.empty or "strike" not in data:
        return None
    strikes = sorted({float(v) for v in data["strike"].dropna().tolist()})
    diffs = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
    return float(median(diffs)) if diffs else None


def _pair_for_strike(data: pd.DataFrame, strike: float) -> dict[str, dict[str, Any]]:
    pair = data[data["strike"].eq(strike) & data["side"].isin(["CE", "PE"])]
    return {str(row.get("side")): row for row in pair.to_dict("records")}


def _pair_premium(data: pd.DataFrame, strike: float) -> tuple[float | None, float | None, float | None]:
    by_side = _pair_for_strike(data, strike)
    ce = _finite((by_side.get("CE") or {}).get("last_price"))
    pe = _finite((by_side.get("PE") or {}).get("last_price"))
    if ce is None or pe is None or ce < 0 or pe < 0:
        return ce, pe, None
    return ce, pe, ce + pe


def _expiry_fraction_years(snapshot: Any) -> tuple[float | None, float | None]:
    """Return calendar days and time-to-expiry in years without any network lookup."""
    raw_expiry = getattr(snapshot, "expiry", None)
    created_at = getattr(snapshot, "created_at", None)
    if raw_expiry is None or created_at is None:
        return None, None
    try:
        expiry_date = raw_expiry if hasattr(raw_expiry, "year") else datetime.fromisoformat(str(raw_expiry)).date()
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        expiry_close = datetime.combine(expiry_date, dt_time(15, 30), tzinfo=getattr(created_at, "tzinfo", None))
        seconds = max(0.0, (expiry_close - created_at).total_seconds())
        return max(0.0, seconds / 86400.0), max(seconds / (365.0 * 86400.0), 1.0 / (365.0 * 24.0 * 60.0))
    except Exception:
        return None, None


def expected_move_context(snapshot: Any, straddle: dict[str, Any], iv: dict[str, Any]) -> dict[str, Any]:
    """Premium- and IV-based expected-move context; display-only, not a forecast probability."""
    spot = _finite((getattr(snapshot, "nifty_quote", {}) or {}).get("last_price"))
    if spot is None or spot <= 0:
        return {"status": "UNAVAILABLE"}
    premium = _finite(straddle.get("combined_premium"))
    atm_iv = _finite(iv.get("atm_iv"))
    days, years = _expiry_fraction_years(snapshot)
    iv_move = spot * (atm_iv / 100.0) * math.sqrt(years) if atm_iv is not None and years is not None else None
    premium_move = premium if premium is not None and premium >= 0 else None
    if premium_move is not None and iv_move is not None and iv_move > 0:
        ratio = premium_move / iv_move
        state = "PREMIUM RICH" if ratio >= 1.15 else "PREMIUM CHEAP" if ratio <= 0.85 else "BALANCED"
    else:
        ratio, state = None, "REFERENCE ONLY"
    return {
        "status": "READY" if premium_move is not None or iv_move is not None else "UNAVAILABLE",
        "spot": round(spot, 2),
        "days_to_expiry": round(days, 3) if days is not None else None,
        "premium_move_points": round(premium_move, 2) if premium_move is not None else None,
        "premium_move_pct": round(premium_move / spot * 100.0, 2) if premium_move is not None else None,
        "premium_lower": round(spot - premium_move, 2) if premium_move is not None else None,
        "premium_upper": round(spot + premium_move, 2) if premium_move is not None else None,
        "iv_1sigma_points": round(iv_move, 2) if iv_move is not None else None,
        "iv_1sigma_pct": round(iv_move / spot * 100.0, 2) if iv_move is not None else None,
        "iv_lower": round(spot - iv_move, 2) if iv_move is not None else None,
        "iv_upper": round(spot + iv_move, 2) if iv_move is not None else None,
        "premium_vs_iv_ratio": round(ratio, 2) if ratio is not None else None,
        "state": state,
        "note": "Premium move = ATM CE+PE cost proxy; IV move = simple 1σ IV-time estimate. Neither is a guaranteed trading range.",
    }


def strangle_context(frame: pd.DataFrame | None, spot: float | None) -> dict[str, Any]:
    """Build symmetric 1-step and 2-step OTM strangle context from the fetched chain."""
    data = _clean_frame(frame)
    spot_value = _finite(spot)
    atm = nearest_atm_strike(data, spot_value)
    step = _strike_step(data)
    if data.empty or spot_value is None or atm is None or step is None or step <= 0:
        return {"status": "UNAVAILABLE"}
    strikes = sorted({float(v) for v in data["strike"].dropna().tolist()})

    def nearest(target: float) -> float | None:
        return min(strikes, key=lambda x: abs(x - target)) if strikes else None

    rows: list[dict[str, Any]] = []
    for steps in (1, 2):
        ce_strike = nearest(atm + step * steps)
        pe_strike = nearest(atm - step * steps)
        if ce_strike is None or pe_strike is None or ce_strike <= atm or pe_strike >= atm:
            continue
        ce_row = (data[data["strike"].eq(ce_strike) & data["side"].eq("CE")].to_dict("records") or [{}])[0]
        pe_row = (data[data["strike"].eq(pe_strike) & data["side"].eq("PE")].to_dict("records") or [{}])[0]
        ce_p = _finite(ce_row.get("last_price")); pe_p = _finite(pe_row.get("last_price"))
        if ce_p is None or pe_p is None or ce_p < 0 or pe_p < 0:
            continue
        premium = ce_p + pe_p
        ce_oi = max(0.0, _finite(ce_row.get("oi")) or 0.0); pe_oi = max(0.0, _finite(pe_row.get("oi")) or 0.0)
        ce_vol = max(0.0, _finite(ce_row.get("volume")) or 0.0); pe_vol = max(0.0, _finite(pe_row.get("volume")) or 0.0)
        rows.append({
            "steps": steps,
            "label": f"{steps}-step OTM",
            "pe_strike": round(pe_strike, 2),
            "ce_strike": round(ce_strike, 2),
            "pe_premium": round(pe_p, 2),
            "ce_premium": round(ce_p, 2),
            "combined_premium": round(premium, 2),
            "lower_breakeven_proxy": round(pe_strike - premium, 2),
            "upper_breakeven_proxy": round(ce_strike + premium, 2),
            "wing_width": round(ce_strike - pe_strike, 2),
            "combined_oi": int(round(ce_oi + pe_oi)),
            "combined_volume": int(round(ce_vol + pe_vol)),
        })
    return {"status": "READY" if rows else "UNAVAILABLE", "atm": round(atm, 2), "strike_step": round(step, 2), "rows": rows}


def _snapshot_frame(snapshot: dict[str, Any]) -> pd.DataFrame:
    rows = snapshot.get("rows") or [] if isinstance(snapshot, dict) else []
    if not isinstance(rows, list) or not rows:
        return pd.DataFrame()
    return _clean_frame(pd.DataFrame([row for row in rows if isinstance(row, dict)]))


def _parse_stamp(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def straddle_decay_series(
    history: list[dict[str, Any]] | None,
    current_spot: float | None,
    *,
    max_points: int = 90,
) -> dict[str, Any]:
    """Same-strike multi-straddle and decay analytics from persisted same-day snapshots."""
    snaps = [s for s in (history or []) if isinstance(s, dict)]
    spot = _finite(current_spot)
    if not snaps or spot is None:
        return {"status": "UNAVAILABLE", "series": [], "multi": []}
    latest_frame = _snapshot_frame(snaps[-1])
    atm = nearest_atm_strike(latest_frame, spot)
    step = _strike_step(latest_frame)
    if latest_frame.empty or atm is None:
        return {"status": "UNAVAILABLE", "series": [], "multi": []}
    anchors = [atm]
    if step:
        for target in (atm - step, atm + step):
            strikes = sorted({float(v) for v in latest_frame["strike"].dropna().tolist()})
            if strikes:
                strike = min(strikes, key=lambda x: abs(x - target))
                if strike not in anchors:
                    anchors.append(strike)
    anchors = sorted(anchors)
    points: list[dict[str, Any]] = []
    for snap in snaps:
        stamp = _parse_stamp(snap.get("captured_at"))
        frame = _snapshot_frame(snap)
        if stamp is None or frame.empty:
            continue
        row: dict[str, Any] = {"at": stamp.isoformat(), "spot": _finite(snap.get("spot"))}
        valid_any = False
        for strike in anchors:
            ce, pe, prem = _pair_premium(frame, strike)
            key = f"straddle_{int(round(strike))}"
            row[key] = round(prem, 2) if prem is not None else None
            if strike == atm:
                row["ce"] = round(ce, 2) if ce is not None else None
                row["pe"] = round(pe, 2) if pe is not None else None
                row["atm_premium"] = round(prem, 2) if prem is not None else None
            valid_any = valid_any or prem is not None
        if valid_any:
            points.append(row)
    if not points:
        return {"status": "UNAVAILABLE", "series": [], "multi": []}
    if len(points) > max_points:
        stride = max(1, len(points) // max_points)
        sampled = points[::stride]
        if sampled[-1] != points[-1]:
            sampled.append(points[-1])
        points = sampled[-max_points:]
    valid_atm = [p for p in points if _finite(p.get("atm_premium")) is not None]
    decay: dict[str, Any] = {}
    if len(valid_atm) >= 2:
        first, last = valid_atm[0], valid_atm[-1]
        first_p = _finite(first.get("atm_premium")); last_p = _finite(last.get("atm_premium"))
        first_t, last_t = _parse_stamp(first.get("at")), _parse_stamp(last.get("at"))
        minutes = max(0.0, (last_t - first_t).total_seconds() / 60.0) if first_t and last_t else 0.0
        change = (last_p - first_p) if first_p is not None and last_p is not None else None
        decay.update({
            "start_premium": round(first_p, 2) if first_p is not None else None,
            "current_premium": round(last_p, 2) if last_p is not None else None,
            "net_change": round(change, 2) if change is not None else None,
            "decay_points": round(-change, 2) if change is not None else None,
            "decay_pct": round((-change / first_p) * 100.0, 2) if change is not None and first_p and first_p > 0 else None,
            "elapsed_minutes": round(minutes, 1),
            "decay_per_hour": round((-change) / minutes * 60.0, 2) if change is not None and minutes >= 5 else None,
        })
        for horizon in (5, 15, 30):
            cutoff = last_t.timestamp() - horizon * 60 if last_t else None
            prior = None
            if cutoff is not None:
                candidates = [p for p in valid_atm if (_parse_stamp(p.get("at")) or last_t).timestamp() <= cutoff]
                prior = candidates[-1] if candidates else None
            pp = _finite((prior or {}).get("atm_premium"))
            if pp is not None and last_p is not None:
                decay[f"change_{horizon}m"] = round(last_p - pp, 2)
    return {
        "status": "READY",
        "anchor_atm": round(atm, 2),
        "strike_step": round(step, 2) if step else None,
        "series": points,
        "multi": [{"strike": round(s, 2), "key": f"straddle_{int(round(s))}"} for s in anchors],
        "decay": decay,
        "note": "Decay uses the same fixed current-ATM strike through saved snapshots; this avoids rolling-ATM jump distortion.",
    }


def build_phase9_payload(
    snapshot: Any,
    historical_atm_iv: list[float] | None = None,
    intraday_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Phase-9 Straddle/Strangle + Expected Move + Decay Intelligence."""
    base = build_phase8_payload(
        snapshot,
        historical_atm_iv=historical_atm_iv,
        intraday_history=intraday_history,
    )
    spot = _finite((getattr(snapshot, "nifty_quote", {}) or {}).get("last_price"))
    frame = getattr(snapshot, "option_chain", None)
    base.update({
        "strangles": strangle_context(frame, spot),
        "expected_move": expected_move_context(snapshot, base.get("straddle", {}), base.get("iv", {})),
        "straddle_decay": straddle_decay_series(intraday_history, spot),
        "phase9_note": (
            "Straddle/strangle, expected-move and decay analytics use only the already-fetched current-expiry chain "
            "plus same-day persisted option-state. No extra expiry fetch, no broker call, no One-Brain weight change."
        ),
    })
    return base


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
    liquidity = liquidity_board(frame, spot)
    unusual = unusual_activity(
        getattr(options, "flow_rows", ()),
        spot,
        frame=frame,
        options=options,
    )
    return {
        "liquidity": liquidity,
        "liquidity_summary": liquidity_summary(liquidity),
        "unusual_activity": unusual,
        "activity_clusters": activity_clusters(unusual, frame),
        "straddle": straddle_context(frame, spot),
        "iv": iv_context(frame, spot, historical_atm_iv=historical_atm_iv),
        "note": "Display-only; existing snapshot only; zero broker/API calls and zero One-Brain weight.",
        "phase10_note": (
            "Advanced unusual-activity confirmation and liquidity/execution-quality context use only the already-fetched "
            "option chain + OptionIntelligence windows. Scores are relative diagnostics, not order-flow identity or fill guarantees."
        ),
    }
