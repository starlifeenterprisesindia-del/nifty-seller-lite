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


# ---------------------------------------------------------------------------
# Phase-7: Strategy Repair + Advanced Risk Intelligence
# ---------------------------------------------------------------------------
# This is deliberately advisory/display-only.  It never writes to the journal,
# changes One-Brain scores, moves stops, places an order, or calls the broker.


def _record_position_greeks(record: dict[str, Any] | None, frame: pd.DataFrame | None) -> dict[str, Any]:
    lookup = _chain_lookup(frame)
    totals = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    legs = []
    covered = required = 0
    for raw in (record or {}).get("legs", []) if isinstance((record or {}).get("legs"), list) else []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "").upper()
        side = str(raw.get("side") or "").upper()
        strike = _finite(raw.get("strike"))
        if side not in {"CE", "PE"} or strike is None or role not in {"SHORT", "HEDGE", "LONG"}:
            continue
        sign = -1 if role == "SHORT" else 1
        row = lookup.get((side, float(strike)), {})
        item = {"role": role, "side": side, "strike": float(strike)}
        complete = True
        for greek in totals:
            required += 1
            value = _finite(row.get(greek))
            if value is None:
                complete = False
                item[greek] = None
            else:
                covered += 1
                signed = sign * value
                item[greek] = round(signed, 6)
                totals[greek] += signed
        item["status"] = "READY" if complete else "PARTIAL"
        legs.append(item)
    coverage = 100.0 * covered / required if required else 0.0
    return {
        "status": "READY" if required and covered == required else "PARTIAL" if covered else "UNAVAILABLE",
        "coverage_pct": round(coverage, 1),
        **{key: round(value, 6) for key, value in totals.items()},
        "legs": legs,
    }


def _direction_alignment(action: str, market_direction: str, option_bias: str, big_direction: str) -> dict[str, Any]:
    action = str(action or "").upper()
    market = str(market_direction or "MIXED").upper()
    option = str(option_bias or "MIXED").upper()
    big = str(big_direction or "MIXED").upper()
    score = 0.0
    reasons: list[str] = []

    def directional(expected: str) -> None:
        nonlocal score
        opposite = "BEARISH" if expected == "BULLISH" else "BULLISH"
        if market == expected:
            score += 2.0; reasons.append(f"One Brain direction {market} trade ke favour me")
        elif market == opposite:
            score -= 2.0; reasons.append(f"One Brain direction {market} trade ke against")
        elif market in {"RANGE", "MIXED"}:
            score += 0.25
        if option == expected:
            score += 1.0; reasons.append(f"Option flow {option} supportive")
        elif option == opposite:
            score -= 1.0; reasons.append(f"Option flow {option} opposing")
        expected_big = "BUYING" if expected == "BULLISH" else "SELLING"
        opposite_big = "SELLING" if expected_big == "BUYING" else "BUYING"
        if big == expected_big:
            score += 1.0; reasons.append(f"Big Player {big} supportive")
        elif big == opposite_big:
            score -= 1.0; reasons.append(f"Big Player {big} opposing")

    if action in {"PE SELL", "CE BUY"}:
        directional("BULLISH")
    elif action in {"CE SELL", "PE BUY"}:
        directional("BEARISH")
    elif action == "IRON CONDOR":
        if market in {"RANGE", "MIXED"}:
            score += 2.0; reasons.append(f"One Brain {market} Condor ke favour me")
        elif market in {"BULLISH", "BEARISH"}:
            score -= 1.5; reasons.append(f"Directional market {market} Condor ke against")
        if option in {"RANGE", "MIXED", "NEUTRAL"}:
            score += 1.0
        elif option in {"BULLISH", "BEARISH"}:
            score -= 0.5
        if big == "MIXED":
            score += 0.5
        elif big in {"BUYING", "SELLING"}:
            score -= 0.5
    state = "SUPPORTIVE" if score >= 1.5 else "ADVERSE" if score <= -1.5 else "MIXED"
    return {"score": round(score, 2), "state": state, "reasons": reasons[:5]}


def _barrier_risk_context(snapshot: Any, action: str) -> dict[str, Any]:
    item = getattr(snapshot, "barrier_map", None)
    if item is None:
        return {"status": "UNAVAILABLE", "state": "UNKNOWN", "score": 0.0, "reasons": []}
    action = str(action or "").upper()
    levels = []
    if action in {"CE SELL", "PE BUY"}:
        levels = [getattr(item, "nearest_resistance", None)]
    elif action in {"PE SELL", "CE BUY"}:
        levels = [getattr(item, "nearest_support", None)]
    elif action == "IRON CONDOR":
        levels = [getattr(item, "nearest_resistance", None), getattr(item, "nearest_support", None)]
    levels = [x for x in levels if x is not None]
    if not levels:
        return {"status": "UNAVAILABLE", "state": "UNKNOWN", "score": 0.0, "reasons": []}
    scores, reasons = [], []
    for level in levels:
        distance = abs(_finite(getattr(level, "distance_points", None)) or 9999.0)
        pressure = _finite(getattr(level, "break_pressure", None)) or 0.0
        strength = _finite(getattr(level, "strength", None)) or 0.0
        state = str(getattr(level, "state", "") or "").upper()
        proximity = 100.0 if distance <= 5 else 80.0 if distance <= 15 else 60.0 if distance <= 30 else 35.0 if distance <= 60 else 10.0
        risk = 0.45 * proximity + 0.40 * pressure + 0.15 * max(0.0, 100.0 - strength)
        if "BROKEN" in state:
            risk = max(risk, 95.0)
        elif "TEST" in state:
            risk = max(risk, 65.0)
        scores.append(min(100.0, risk))
        reasons.append(
            f"{getattr(level, 'label', 'Barrier')} {state or 'ACTIVE'} · distance {distance:.0f} pts · break pressure {pressure:.0f}/100"
        )
    score = max(scores) if scores else 0.0
    state = "HIGH" if score >= 70 else "MEDIUM" if score >= 45 else "LOW"
    return {"status": "READY", "state": state, "score": round(score, 1), "reasons": reasons[:3]}


def _current_short_strikes(record: dict[str, Any] | None) -> dict[str, list[float]]:
    out = {"CE": [], "PE": []}
    for raw in (record or {}).get("legs", []) if isinstance((record or {}).get("legs"), list) else []:
        if not isinstance(raw, dict) or str(raw.get("role") or "").upper() != "SHORT":
            continue
        side = str(raw.get("side") or "").upper()
        strike = _finite(raw.get("strike"))
        if side in out and strike is not None:
            out[side].append(float(strike))
    return out


def _candidate_roll_details(action: str, record: dict[str, Any] | None, candidate: SetupPlan | None) -> dict[str, Any]:
    if candidate is None or not candidate.available:
        return {"status": "UNAVAILABLE", "outward": False, "changes": []}
    current = _current_short_strikes(record)
    proposed = {"CE": [], "PE": []}
    for leg in candidate.short_legs:
        side = str(leg.side).upper()
        if side in proposed:
            proposed[side].append(float(leg.strike))
    changes = []
    outward_flags = []
    for side in ("CE", "PE"):
        if not current[side] or not proposed[side]:
            continue
        old = max(current[side]) if side == "CE" else min(current[side])
        new = max(proposed[side]) if side == "CE" else min(proposed[side])
        outward = new > old if side == "CE" else new < old
        outward_flags.append(outward)
        changes.append({"side": side, "current_short": old, "candidate_short": new, "shift_points": round(new-old, 1), "outward": outward})
    action = str(action or "").upper()
    if action == "IRON CONDOR":
        outward = bool(outward_flags) and all(outward_flags)
    else:
        outward = any(outward_flags)
    return {"status": "READY" if changes else "UNAVAILABLE", "outward": outward, "changes": changes}


def _current_hedge_execution_quality(snapshot: Any, record: dict[str, Any] | None) -> dict[str, Any]:
    frame = getattr(snapshot, "option_chain", None)
    spot = _finite((getattr(snapshot, "nifty_quote", {}) or {}).get("last_price"))
    try:
        from analysis.advanced_options_display import liquidity_board
        board = liquidity_board(frame, spot, max_rows=40)
    except Exception:
        board = []
    lookup = {(str(x.get("side") or "").upper(), float(x.get("strike"))): x for x in board if _finite(x.get("strike")) is not None}
    rows = []
    grade_values = {"A+": 5, "A": 4, "B": 3, "C": 2, "D": 1}
    floor = 99
    for raw in (record or {}).get("legs", []) if isinstance((record or {}).get("legs"), list) else []:
        if not isinstance(raw, dict) or str(raw.get("role") or "").upper() != "HEDGE":
            continue
        side = str(raw.get("side") or "").upper(); strike = _finite(raw.get("strike"))
        if strike is None:
            continue
        item = lookup.get((side, float(strike)), {})
        grade = str(item.get("grade") or "UNAVAILABLE")
        if grade in grade_values:
            floor = min(floor, grade_values[grade])
        rows.append({"side": side, "strike": float(strike), "grade": grade, "score": item.get("score"), "spread_pct": item.get("spread_pct"), "oi": item.get("oi"), "volume": item.get("volume")})
    if not rows:
        return {"status": "UNAVAILABLE", "state": "NO HEDGE DATA", "rows": []}
    reverse = {5:"A+",4:"A",3:"B",2:"C",1:"D"}
    floor_grade = reverse.get(floor, "UNAVAILABLE")
    state = "STRONG" if floor >= 4 else "OK" if floor == 3 else "WEAK EXECUTION" if floor <= 2 else "UNAVAILABLE"
    return {"status": "READY", "state": state, "floor_grade": floor_grade, "rows": rows}


def build_strategy_repair_payload(snapshot: Any) -> dict[str, Any]:
    """Build a conservative, advisory repair/risk review from the current snapshot.

    Important: this never recommends adding risk after an existing deterministic exit
    rule has triggered.  Candidate replacement risk assumes the old position is closed
    first; it does not pretend a multi-leg roll can be executed at theoretical fills.
    """
    guardian = getattr(snapshot, "position_guardian", None)
    record = getattr(getattr(snapshot, "discipline_state", None), "trade_record", None)
    if guardian is None or not isinstance(record, dict) or str(record.get("status") or "").upper() != "OPEN":
        return {"status": "IDLE", "instruction": "NO OPEN TRADE", "safety": {"broker_calls": 0, "brain_writes": 0, "auto_orders": False}}

    action = str(getattr(guardian, "action", "") or record.get("action") or "").upper()
    market_live = bool(getattr(getattr(snapshot, "market_session", None), "is_live", False))
    option_intel = getattr(snapshot, "option_intelligence", None)
    big = getattr(snapshot, "big_player_activity", None)
    decision = getattr(snapshot, "decision", None)
    market_direction = str(getattr(decision, "market_direction", "MIXED") or "MIXED").upper()
    option_bias = str(getattr(option_intel, "market_bias", "MIXED") or "MIXED").upper()
    big_direction = str(getattr(big, "direction", "MIXED") or "MIXED").upper()
    alignment = _direction_alignment(action, market_direction, option_bias, big_direction)
    barrier = _barrier_risk_context(snapshot, action)
    hedge = _current_hedge_execution_quality(snapshot, record)
    current_greeks = _record_position_greeks(record, getattr(snapshot, "option_chain", None))

    plans = available_plans(snapshot)
    candidate = plans.get(action)
    candidate_summary = None
    candidate_greeks = None
    roll = _candidate_roll_details(action, record, candidate)
    spot = _finite((getattr(snapshot, "nifty_quote", {}) or {}).get("last_price"))
    risk_profile = getattr(snapshot, "risk_profile", None)
    candidate_lot_size = int(getattr(risk_profile, "lot_size", 1) or 1)
    current_lot_size = max(1, int(getattr(guardian, "lot_size", 0) or record.get("lot_size") or candidate_lot_size))
    lots = max(1, int(getattr(guardian, "lots", 1) or record.get("lots") or 1))
    if candidate is not None and spot is not None:
        candidate_summary = plan_summary(candidate, spot, candidate_lot_size, getattr(snapshot, "option_chain", None))
        candidate_greeks = candidate_summary.get("greeks")

    max_risk_points = _finite(record.get("max_risk_points"))
    pnl_points = _finite(getattr(guardian, "unrealized_pnl_points", None))
    pnl_rupees = _finite(getattr(guardian, "unrealized_pnl_rupees", None))
    remaining_worst_points = None
    if max_risk_points is not None and pnl_points is not None:
        remaining_worst_points = max(0.0, max_risk_points + pnl_points)
    current_risk = {
        "original_max_loss_points": round(max_risk_points, 2) if max_risk_points is not None else None,
        "current_pnl_points": round(pnl_points, 2) if pnl_points is not None else None,
        "current_pnl_rupees": round(pnl_rupees, 2) if pnl_rupees is not None else None,
        "remaining_to_original_worst_points": round(remaining_worst_points, 2) if remaining_worst_points is not None else None,
        "remaining_to_original_worst_rupees": round(remaining_worst_points * current_lot_size * lots, 2) if remaining_worst_points is not None else None,
    }
    replacement_risk = None
    if candidate_summary:
        new_loss = _finite(candidate_summary.get("max_loss_rupees_per_lot"))
        replacement_risk = {
            "candidate": action,
            "candidate_max_loss_rupees": round(new_loss * lots, 2) if new_loss is not None else None,
            "day_pnl_if_old_closed_now": round(pnl_rupees, 2) if pnl_rupees is not None else None,
            "day_worst_if_replaced_after_close": round((pnl_rupees or 0.0) - (new_loss or 0.0) * lots, 2) if new_loss is not None and pnl_rupees is not None else None,
            "assumption": "Old position closed first at current guardian mark; candidate then treated as a fresh protected plan. Slippage/charges excluded.",
        }

    gstatus = str(getattr(guardian, "status", "") or "").upper()
    instruction = str(getattr(guardian, "instruction", "") or "").upper()
    reasons: list[str] = []
    vetoes: list[str] = []
    repair_state = "HOLD / MONITOR"

    if not market_live or gstatus == "REFERENCE ONLY":
        repair_state = "REFERENCE ONLY — NO LIVE REPAIR"
        vetoes.append("Market/session data is not live")
    elif gstatus in {"DATA BLOCKED", "EXIT DUE"}:
        repair_state = "NO REPAIR — DATA BLOCKED"
        vetoes.append("Fresh executable data is not verified")
    elif gstatus == "EXIT ALERT" or "EXIT" in instruction or "SL TRIGGERED" in instruction:
        repair_state = "EXIT / DO NOT REPAIR"
        vetoes.append("Existing deterministic Position Guardian exit rule has triggered")
    elif gstatus == "TARGET ALERT" or instruction == "PROTECT PROFIT":
        repair_state = "PROTECT PROFIT — DO NOT ADD RISK"
        reasons.append("Position Guardian is already at target/profit-protection state")
    else:
        adverse = alignment["state"] == "ADVERSE"
        high_barrier = barrier.get("state") == "HIGH"
        risk_rising = instruction == "RISK RISING" or (pnl_points is not None and pnl_points < 0)
        if adverse and high_barrier:
            repair_state = "REPAIR VETO — EXIT REVIEW"
            vetoes.append("Direction/flow and barrier risk are both adverse")
        elif risk_rising and roll.get("outward") and candidate_summary and float(candidate_summary.get("quality_score") or 0.0) >= 55.0:
            repair_state = "OUTWARD ROLL CANDIDATE — ADVISORY"
            reasons.append("Fresh protected plan shifts threatened short strike(s) farther OTM")
        elif risk_rising and hedge.get("state") == "WEAK EXECUTION":
            repair_state = "HEDGE / LIQUIDITY REVIEW — NO AUTO CHANGE"
            reasons.append("Current hedge exists but execution-liquidity quality is weak")
        elif risk_rising:
            repair_state = "HOLD / EXIT REVIEW — NO CLEAN REPAIR"
            reasons.append("Loss is present but no clean outward replacement passed the conservative gate")
        elif alignment["state"] == "SUPPORTIVE" and barrier.get("state") == "LOW":
            repair_state = "HOLD / MONITOR — REPAIR NOT NEEDED"
        else:
            repair_state = "HOLD / MONITOR — WATCH RISK"

    reasons.extend(alignment.get("reasons") or [])
    reasons.extend(barrier.get("reasons") or [])
    if roll.get("status") == "READY" and not roll.get("outward"):
        vetoes.append("Fresh same-strategy plan does not move threatened short strike(s) outward")
    if candidate_summary and float(candidate_summary.get("quality_score") or 0.0) < 55.0:
        vetoes.append("Fresh replacement plan quality is below conservative repair floor 55/100")

    return {
        "status": "READY",
        "repair_state": repair_state,
        "action": action,
        "guardian_status": gstatus,
        "guardian_instruction": instruction,
        "current": {
            "entry_spot": _finite(getattr(guardian, "entry_spot", None)),
            "current_spot": _finite(getattr(guardian, "current_spot", None)),
            "current_value_points": _finite(getattr(guardian, "current_debit_points", None)),
            "target_progress_pct": _finite(getattr(guardian, "target_progress_pct", None)),
            "lots": lots,
            "lot_size": current_lot_size,
        },
        "alignment": alignment,
        "barrier_risk": barrier,
        "hedge_execution": hedge,
        "current_greeks": current_greeks,
        "candidate_summary": candidate_summary,
        "candidate_greeks": candidate_greeks,
        "roll": roll,
        "current_risk": current_risk,
        "replacement_risk": replacement_risk,
        "reasons": list(dict.fromkeys(reasons))[:8],
        "vetoes": list(dict.fromkeys(vetoes))[:8],
        "safety": {
            "broker_calls": 0,
            "brain_writes": 0,
            "auto_orders": False,
            "auto_stop_changes": False,
            "mode": "DISPLAY ONLY / ON DEMAND",
        },
    }
