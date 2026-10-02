"""On-demand Strategy Lab analytics for Phase-4.

Golden Rule:
- No broker/API calls.
- No One-Brain writes, score changes, or threshold tuning.
- No disk/history reads.
- Runs only when the Strategy Lab UI is opened.

The lab reuses the already-built protected SetupPlan objects and the current validated
option-chain snapshot.  Expiry payoff is exact for the protected legs at the quoted
entry assumptions.  Pre-expiry What-If is a local Greek approximation and is clearly
labelled as such; it is not a price forecast.
"""
from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any

import pandas as pd

from models import OptionLeg, SetupPlan


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mark_price(row: pd.Series | dict[str, Any] | None) -> float | None:
    if row is None:
        return None
    get = row.get
    bid = _finite(get("top_bid_price"))
    ask = _finite(get("top_ask_price"))
    ltp = _finite(get("last_price"))
    if bid is not None and ask is not None and 0 < bid <= ask:
        return (bid + ask) / 2.0
    return ltp if ltp is not None and ltp >= 0 else None


def _entry_price(leg: OptionLeg, sign: int) -> float | None:
    """Executable-style entry assumption: buy at ask, sell at bid, LTP fallback."""
    if sign > 0:
        return _finite(leg.ask) or _finite(leg.last_price)
    return _finite(leg.bid) or _finite(leg.last_price)


def _position_legs(plan: SetupPlan) -> list[dict[str, Any]]:
    """Return normalized long(+1)/short(-1) legs for any supported SetupPlan."""
    rows: list[dict[str, Any]] = []
    for leg in plan.long_legs:
        rows.append({"leg": leg, "sign": 1, "position": "LONG"})
    for leg in plan.short_legs:
        rows.append({"leg": leg, "sign": -1, "position": "SHORT"})
    # Credit-spread / condor hedge legs are purchased protection.
    for leg in plan.hedge_legs:
        rows.append({"leg": leg, "sign": 1, "position": "LONG HEDGE"})
    return rows


def _intrinsic(side: str, strike: float, spot: float) -> float:
    side = str(side).upper()
    if side == "CE":
        return max(0.0, spot - strike)
    if side == "PE":
        return max(0.0, strike - spot)
    return 0.0


def _entry_cashflow_points(plan: SetupPlan) -> float | None:
    rows = _position_legs(plan)
    if not rows:
        return None
    total = 0.0
    for item in rows:
        price = _entry_price(item["leg"], item["sign"])
        if price is None:
            # Plan-level values were already validated by TradePlan; use them when
            # a leg quote field is absent in an old/reference snapshot.
            if plan.estimated_credit_points is not None:
                return float(plan.estimated_credit_points)
            if plan.estimated_debit_points is not None:
                return -float(plan.estimated_debit_points)
            return None
        total += -item["sign"] * price
    return total


def expiry_pnl_points(plan: SetupPlan, expiry_spot: float) -> float | None:
    cashflow = _entry_cashflow_points(plan)
    if cashflow is None:
        return None
    pnl = cashflow
    for item in _position_legs(plan):
        leg: OptionLeg = item["leg"]
        pnl += item["sign"] * _intrinsic(leg.side, float(leg.strike), float(expiry_spot))
    return float(pnl)


def _payoff_bounds(plan: SetupPlan, spot: float) -> tuple[float, float]:
    strikes = [float(item["leg"].strike) for item in _position_legs(plan)]
    if not strikes:
        span = max(300.0, spot * 0.025)
        return max(0.0, spot - span), spot + span
    width = max(strikes) - min(strikes)
    span = max(250.0, width * 2.5, spot * 0.02)
    return max(0.0, min(min(strikes), spot) - span), max(max(strikes), spot) + span


def payoff_curve(plan: SetupPlan, spot: float, *, points: int = 161) -> list[dict[str, float]]:
    if not plan.available or points < 3:
        return []
    low, high = _payoff_bounds(plan, spot)
    step = (high - low) / (points - 1)
    result: list[dict[str, float]] = []
    for idx in range(points):
        expiry_spot = low + idx * step
        pnl = expiry_pnl_points(plan, expiry_spot)
        if pnl is not None:
            result.append({"spot": round(expiry_spot, 2), "pnl_points": round(pnl, 3)})
    return result


def _breakevens_from_curve(curve: list[dict[str, float]]) -> list[float]:
    roots: list[float] = []
    for left, right in zip(curve, curve[1:]):
        x1, y1 = left["spot"], left["pnl_points"]
        x2, y2 = right["spot"], right["pnl_points"]
        if y1 == 0:
            roots.append(float(x1))
        if y1 * y2 < 0 and y2 != y1:
            root = x1 + (0.0 - y1) * (x2 - x1) / (y2 - y1)
            roots.append(float(root))
    # Deduplicate interpolation noise.
    clean: list[float] = []
    for value in roots:
        if not clean or abs(value - clean[-1]) > 1.0:
            clean.append(round(value, 2))
    return clean[:2]


def _theoretical_limits(plan: SetupPlan) -> tuple[float | None, float | None]:
    if not plan.available:
        return None, None
    if plan.estimated_credit_points is not None:
        max_profit = max(0.0, float(plan.estimated_credit_points))
        max_loss = max(0.0, float(plan.max_risk_points or 0.0))
        return max_profit, max_loss
    if plan.estimated_debit_points is not None:
        debit = max(0.0, float(plan.estimated_debit_points))
        width = max(0.0, float(plan.width_points or 0.0))
        return max(0.0, width - debit), debit
    return None, None


def _chain_lookup(frame: pd.DataFrame | None) -> dict[tuple[str, float], dict[str, Any]]:
    if frame is None or frame.empty:
        return {}
    data = frame.copy()
    if "side" not in data or "strike" not in data:
        return {}
    data["side"] = data["side"].astype(str).str.upper()
    data["strike"] = pd.to_numeric(data["strike"], errors="coerce")
    lookup: dict[tuple[str, float], dict[str, Any]] = {}
    for raw in data.dropna(subset=["strike"]).to_dict("records"):
        lookup[(str(raw.get("side") or "").upper(), float(raw["strike"]))] = raw
    return lookup


def combined_greeks(plan: SetupPlan, frame: pd.DataFrame | None) -> dict[str, Any]:
    lookup = _chain_lookup(frame)
    totals = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    coverage = 0
    required = 0
    leg_rows: list[dict[str, Any]] = []
    for item in _position_legs(plan):
        leg: OptionLeg = item["leg"]
        sign = int(item["sign"])
        row = lookup.get((str(leg.side).upper(), float(leg.strike)), {})
        values: dict[str, float | None] = {}
        leg_complete = True
        for greek in totals:
            value = _finite(row.get(greek))
            required += 1
            if value is None:
                leg_complete = False
                values[greek] = None
            else:
                coverage += 1
                values[greek] = sign * value
                totals[greek] += sign * value
        leg_rows.append({
            "position": item["position"],
            "side": str(leg.side).upper(),
            "strike": float(leg.strike),
            **values,
            "status": "READY" if leg_complete else "PARTIAL",
        })
    coverage_pct = 100.0 * coverage / required if required else 0.0
    return {
        "status": "READY" if required and coverage == required else "PARTIAL" if coverage else "UNAVAILABLE",
        "coverage_pct": round(coverage_pct, 1),
        **{name: round(value, 6) for name, value in totals.items()},
        "legs": leg_rows,
    }


def _plan_liquidity_floor(plan: SetupPlan) -> float | None:
    scores = [_finite(item["leg"].liquidity_score) for item in _position_legs(plan)]
    clean = [value for value in scores if value is not None]
    return round(min(clean), 1) if clean else None


def plan_summary(plan: SetupPlan, spot: float, lot_size: int, frame: pd.DataFrame | None) -> dict[str, Any]:
    curve = payoff_curve(plan, spot)
    max_profit, max_loss = _theoretical_limits(plan)
    greeks = combined_greeks(plan, frame)
    breakevens = [value for value in (plan.lower_breakeven, plan.upper_breakeven) if value is not None]
    if not breakevens and curve:
        breakevens = _breakevens_from_curve(curve)
    entry_cashflow = _entry_cashflow_points(plan)
    return {
        "name": plan.name,
        "status": plan.status,
        "quality_score": round(float(plan.quality_score), 1),
        "entry_type": "CREDIT" if (entry_cashflow or 0.0) >= 0 else "DEBIT",
        "entry_points": round(abs(entry_cashflow), 2) if entry_cashflow is not None else None,
        "max_profit_points": round(max_profit, 2) if max_profit is not None else None,
        "max_loss_points": round(max_loss, 2) if max_loss is not None else None,
        "max_profit_rupees_per_lot": round(max_profit * lot_size, 2) if max_profit is not None else None,
        "max_loss_rupees_per_lot": round(max_loss * lot_size, 2) if max_loss is not None else None,
        "breakevens": [round(float(value), 2) for value in breakevens],
        "width_points": _finite(plan.width_points),
        "liquidity_floor": _plan_liquidity_floor(plan),
        "greeks": greeks,
        "curve": curve,
        "legs": [
            {
                "position": item["position"],
                "side": str(item["leg"].side).upper(),
                "strike": float(item["leg"].strike),
                "entry_price": _entry_price(item["leg"], item["sign"]),
                "liquidity": _finite(item["leg"].liquidity_score),
            }
            for item in _position_legs(plan)
        ],
        "blocker": plan.blocker,
    }


def what_if_scenario(
    plan: SetupPlan,
    frame: pd.DataFrame | None,
    *,
    spot: float,
    spot_change_points: float = 0.0,
    iv_change_points: float = 0.0,
    minutes_forward: float = 0.0,
    lot_size: int = 1,
) -> dict[str, Any]:
    """Approximate mark-to-market change using current Greeks.

    dOption ~= delta*dS + .5*gamma*dS^2 + vega*dIV + theta*days.
    The approximation is local; large moves/IV shocks require repricing and are
    intentionally labelled caution rather than presented as a forecast.
    """
    lookup = _chain_lookup(frame)
    d_s = float(spot_change_points)
    d_iv = float(iv_change_points)
    days = max(0.0, float(minutes_forward)) / 1440.0
    new_spot = float(spot) + d_s
    total_change = 0.0
    covered = 0
    required = 0
    legs: list[dict[str, Any]] = []
    for item in _position_legs(plan):
        leg: OptionLeg = item["leg"]
        sign = int(item["sign"])
        row = lookup.get((str(leg.side).upper(), float(leg.strike)), {})
        mark = _mark_price(row) or _finite(leg.last_price)
        delta = _finite(row.get("delta"))
        gamma = _finite(row.get("gamma"))
        theta = _finite(row.get("theta"))
        vega = _finite(row.get("vega"))
        required += 4
        for value in (delta, gamma, theta, vega):
            if value is not None:
                covered += 1
        if mark is None or delta is None:
            legs.append({"position": item["position"], "side": leg.side, "strike": leg.strike, "status": "UNAVAILABLE"})
            continue
        option_change = delta * d_s
        if gamma is not None:
            option_change += 0.5 * gamma * d_s * d_s
        if vega is not None:
            option_change += vega * d_iv
        if theta is not None:
            option_change += theta * days
        projected = max(_intrinsic(leg.side, float(leg.strike), new_spot), mark + option_change, 0.0)
        leg_pnl = sign * (projected - mark)
        total_change += leg_pnl
        legs.append({
            "position": item["position"], "side": str(leg.side).upper(), "strike": float(leg.strike),
            "mark": round(mark, 3), "projected_mark": round(projected, 3),
            "pnl_change_points": round(leg_pnl, 3), "status": "READY",
        })
    coverage_pct = 100.0 * covered / required if required else 0.0
    move_scale = abs(d_s) / max(1.0, float(spot)) * 100.0
    caution = move_scale >= 1.0 or abs(d_iv) >= 5.0 or minutes_forward >= 1440
    return {
        "status": "READY" if legs and all(row.get("status") == "READY" for row in legs) else "PARTIAL",
        "coverage_pct": round(coverage_pct, 1),
        "new_spot": round(new_spot, 2),
        "pnl_change_points": round(total_change, 2),
        "pnl_change_rupees_per_lot": round(total_change * max(1, int(lot_size)), 2),
        "legs": legs,
        "caution": caution,
        "note": "Local Greek approximation only; not a forecast or guaranteed executable P&L.",
    }


def available_plans(snapshot: Any) -> dict[str, SetupPlan]:
    bundle = getattr(snapshot, "trade_plan", None)
    if bundle is None:
        return {}
    candidates = {
        "CE SELL": getattr(bundle, "ce_sell", None),
        "PE SELL": getattr(bundle, "pe_sell", None),
        "IRON CONDOR": getattr(bundle, "iron_condor", None),
        "CE BUY": getattr(bundle, "ce_buy", None),
        "PE BUY": getattr(bundle, "pe_buy", None),
    }
    return {name: plan for name, plan in candidates.items() if isinstance(plan, SetupPlan) and plan.available}


def build_strategy_lab_payload(snapshot: Any) -> dict[str, Any]:
    spot = _finite((getattr(snapshot, "nifty_quote", {}) or {}).get("last_price"))
    risk = getattr(snapshot, "risk_profile", None)
    lot_size = int(getattr(risk, "lot_size", 1) or 1)
    frame = getattr(snapshot, "option_chain", None)
    plans = available_plans(snapshot)
    if spot is None or not plans:
        return {
            "status": "UNAVAILABLE", "spot": spot, "lot_size": lot_size,
            "plans": {}, "preferred": None,
            "safety": {"broker_calls": 0, "brain_writes": 0, "threshold_tuning": False, "mode": "DISPLAY ONLY / ON DEMAND"},
        }
    summaries = {name: plan_summary(plan, spot, lot_size, frame) for name, plan in plans.items()}
    bundle = getattr(snapshot, "trade_plan", None)
    selected = str(getattr(bundle, "selected_setup", "") or "").upper()
    candidate = str(getattr(bundle, "candidate_setup", "") or "").upper()
    if selected in summaries:
        preferred = selected
    elif candidate in summaries:
        preferred = candidate
    else:
        preferred = max(summaries, key=lambda name: summaries[name]["quality_score"])
    return {
        "status": "READY",
        "spot": round(spot, 2),
        "lot_size": lot_size,
        "plans": summaries,
        "preferred": preferred,
        "snapshot_id": getattr(snapshot, "snapshot_id", None),
        "safety": {"broker_calls": 0, "brain_writes": 0, "threshold_tuning": False, "mode": "DISPLAY ONLY / ON DEMAND"},
    }
