from __future__ import annotations

from dataclasses import replace
from math import isfinite
from typing import Any

import pandas as pd

from analysis.technical_utils import clamp
from config import CONFIG
from models import (
    FinalDecision,
    LevelBundle,
    MarketSession,
    OptionIntelligence,
    OptionLeg,
    SetupPlan,
    TradePlanBundle,
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _percentile_score(value: float | None, series: pd.Series) -> float:
    if value is None or series.empty:
        return 35.0
    clean = pd.to_numeric(series, errors="coerce").dropna()
    clean = clean[clean >= 0]
    if clean.empty:
        return 35.0
    return float((clean <= value).mean() * 100.0)


def _spread_metrics(row: pd.Series) -> tuple[float | None, float]:
    bid = _number(row.get("top_bid_price"))
    ask = _number(row.get("top_ask_price"))
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None, 25.0
    midpoint = (bid + ask) / 2.0
    if midpoint <= 0:
        return None, 25.0
    spread_pct = (ask - bid) / midpoint * 100.0
    score = clamp(100.0 - max(0.0, spread_pct - 1.0) * 6.0, 0.0, 100.0)
    return round(spread_pct, 2), score


def _distance_score(distance_pct: float) -> float:
    target = CONFIG.trade_target_distance_pct
    tolerance = CONFIG.trade_distance_tolerance_pct
    return clamp(100.0 - abs(distance_pct - target) / tolerance * 100.0, 0.0, 100.0)


def _delta_score(delta: float | None) -> float:
    if delta is None:
        return 45.0
    absolute = abs(delta)
    if absolute < CONFIG.trade_min_abs_delta or absolute > CONFIG.trade_max_abs_delta:
        return 15.0
    return clamp(
        100.0
        - abs(absolute - CONFIG.trade_target_abs_delta)
        / max(CONFIG.trade_target_abs_delta, 0.01)
        * 100.0,
        0.0,
        100.0,
    )


def _buy_delta_score(delta: float | None) -> float:
    if delta is None:
        return 42.0
    absolute = abs(delta)
    if absolute < CONFIG.buy_min_abs_delta or absolute > CONFIG.buy_max_abs_delta:
        return 12.0
    return clamp(
        100.0
        - abs(absolute - CONFIG.buy_target_abs_delta)
        / max(CONFIG.buy_target_abs_delta, 0.01)
        * 100.0,
        0.0,
        100.0,
    )


def _buy_level_score(
    side: str, strike: float, premium: float, spot: float, levels: LevelBundle
) -> tuple[float, str]:
    if levels.status != "READY":
        return 40.0, "Directional room unavailable"
    required_move = max(0.0, premium + abs(strike - spot))
    room = levels.upside_room if side == "CE" else levels.downside_room
    if room is None:
        return 40.0, "Directional room unavailable"
    if room >= max(CONFIG.buy_min_directional_room_points, required_move * 1.15):
        return 92.0, f"Directional room {room:.1f} pts supports breakeven"
    if room >= CONFIG.buy_min_directional_room_points:
        return 65.0, f"Directional room {room:.1f} pts is usable"
    return 15.0, f"Directional room only {room:.1f} pts"


def _buy_candidate_rows(frame: pd.DataFrame, side: str, spot: float) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    rows = frame[frame["side"].astype(str).str.upper().eq(side)].copy()
    rows["strike"] = pd.to_numeric(rows["strike"], errors="coerce")
    rows["last_price"] = pd.to_numeric(rows["last_price"], errors="coerce")
    rows = rows.dropna(subset=["strike", "last_price"])
    rows = rows[rows["last_price"] >= CONFIG.buy_min_option_premium]
    # Keep ATM and one/two near-ITM/OTM strikes. Liquidity and delta decide the winner.
    max_distance = max(100.0, spot * 0.006)
    rows = rows[rows["strike"].sub(spot).abs() <= max_distance]
    return rows.sort_values("strike").reset_index(drop=True)


def _select_long_leg(
    frame: pd.DataFrame,
    *,
    side: str,
    spot: float,
    levels: LevelBundle,
) -> tuple[OptionLeg | None, float, tuple[str, ...]]:
    rows = _buy_candidate_rows(frame, side, spot)
    if rows.empty:
        return None, 0.0, (f"No usable ATM/near-ITM {side} buy row",)
    oi_series = rows.get("oi", pd.Series(dtype=float))
    volume_series = rows.get("volume", pd.Series(dtype=float))
    scored: list[tuple[float, pd.Series, float | None, float, str]] = []
    for _, row in rows.iterrows():
        strike = float(row["strike"])
        ask = _number(row.get("top_ask_price")) or _number(row.get("last_price"))
        if ask is None or ask <= 0:
            continue
        spread_pct, spread_score = _spread_metrics(row)
        oi_score = _percentile_score(_number(row.get("oi")), oi_series)
        volume_score = _percentile_score(_number(row.get("volume")), volume_series)
        liquidity = spread_score * 0.50 + oi_score * 0.25 + volume_score * 0.25
        distance_pct = abs(strike - spot) / max(spot, 1.0) * 100.0
        distance_score = clamp(
            100.0
            - abs(distance_pct - CONFIG.buy_target_distance_pct)
            / max(CONFIG.buy_distance_tolerance_pct, 0.01)
            * 100.0,
            0.0,
            100.0,
        )
        level_score, level_reason = _buy_level_score(side, strike, ask, spot, levels)
        total = (
            liquidity * 0.42
            + _buy_delta_score(_number(row.get("delta"))) * 0.28
            + distance_score * 0.12
            + level_score * 0.18
        )
        scored.append((total, row, spread_pct, liquidity, level_reason))
    if not scored:
        return None, 0.0, (f"No executable {side} buy price",)
    score, row, spread_pct, liquidity, level_reason = max(
        scored, key=lambda item: item[0]
    )
    leg = _row_to_leg(
        row,
        role="LONG",
        side=side,
        spot=spot,
        liquidity_score=liquidity,
        spread_pct=spread_pct,
    )
    return leg, round(clamp(score, 0.0, 100.0), 1), (
        level_reason,
        f"Delta {leg.delta:.2f}" if leg.delta is not None else "Delta unavailable",
        f"Liquidity score {liquidity:.1f}/100",
    )


def _buy_plan(
    *,
    name: str,
    side: str,
    frame: pd.DataFrame,
    spot: float,
    levels: LevelBundle,
) -> SetupPlan:
    leg, quality, reasons = _select_long_leg(
        frame, side=side, spot=spot, levels=levels
    )
    if leg is None:
        return SetupPlan.unavailable(name, reasons[0])
    debit = _buy_price(leg)
    if debit is None or debit <= 0:
        return SetupPlan.unavailable(name, "Executable ask/LTP is missing")
    lower_be = leg.strike - debit if side == "PE" else None
    upper_be = leg.strike + debit if side == "CE" else None
    status = "READY" if quality >= CONFIG.buy_min_plan_quality else "CAUTION"
    return SetupPlan(
        name=name,
        short_legs=(),
        hedge_legs=(),
        estimated_credit_points=None,
        width_points=None,
        max_risk_points=round(debit, 2),
        lower_breakeven=round(lower_be, 2) if lower_be is not None else None,
        upper_breakeven=round(upper_be, 2) if upper_be is not None else None,
        quality_score=quality,
        status=status,
        reasons=reasons,
        blocker=(
            "None"
            if status == "READY"
            else "Buy-leg quality is below the ready threshold"
        ),
        long_legs=(leg,),
        estimated_debit_points=round(debit, 2),
    )


def _level_score(side: str, strike: float, levels: LevelBundle) -> tuple[float, str]:
    if levels.status != "READY":
        return 45.0, "Support/resistance unavailable"
    if side == "CE":
        level = levels.immediate_resistance
        if level is None:
            return 45.0, "Immediate resistance unavailable"
        clearance = strike - level.upper
        if clearance >= CONFIG.trade_level_clearance_points:
            return 100.0, "Short strike is beyond immediate resistance"
        if clearance >= 0:
            return 72.0, "Short strike is just beyond immediate resistance"
        return 20.0, "Short CE is inside immediate resistance"
    level = levels.immediate_support
    if level is None:
        return 45.0, "Immediate support unavailable"
    clearance = level.lower - strike
    if clearance >= CONFIG.trade_level_clearance_points:
        return 100.0, "Short strike is beyond immediate support"
    if clearance >= 0:
        return 72.0, "Short strike is just beyond immediate support"
    return 20.0, "Short PE is inside immediate support"


def _wall_score(
    side: str, strike: float, options: OptionIntelligence
) -> tuple[float, str]:
    wall = options.ce_wall if side == "CE" else options.pe_wall
    if wall.strike is None:
        return 45.0, f"{side} OI wall unavailable"
    gap = abs(strike - wall.strike)
    score = clamp(100.0 - gap / 150.0 * 100.0, 20.0, 100.0)
    return score, f"{side} OI wall {wall.strike:,.0f}"


def _candidate_rows(frame: pd.DataFrame, side: str, spot: float) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    rows = frame[frame["side"].astype(str).str.upper().eq(side)].copy()
    rows["strike"] = pd.to_numeric(rows["strike"], errors="coerce")
    rows["last_price"] = pd.to_numeric(rows["last_price"], errors="coerce")
    rows = rows.dropna(subset=["strike", "last_price"])
    rows = rows[rows["last_price"] >= CONFIG.trade_min_option_premium]
    if side == "CE":
        rows = rows[rows["strike"] > spot]
    else:
        rows = rows[rows["strike"] < spot]
    return rows.sort_values("strike").reset_index(drop=True)


def _row_to_leg(
    row: pd.Series,
    *,
    role: str,
    side: str,
    spot: float,
    liquidity_score: float,
    spread_pct: float | None,
) -> OptionLeg:
    strike = float(row["strike"])
    return OptionLeg(
        role=role,
        side=side,
        strike=strike,
        last_price=_number(row.get("last_price")),
        delta=_number(row.get("delta")),
        oi=_number(row.get("oi")),
        volume=_number(row.get("volume")),
        bid=_number(row.get("top_bid_price")),
        ask=_number(row.get("top_ask_price")),
        spread_pct=spread_pct,
        distance_points=round(abs(strike - spot), 2),
        liquidity_score=round(clamp(liquidity_score, 0.0, 100.0), 1),
        status="READY" if liquidity_score >= 50 else "CAUTION",
    )


def _select_short_leg(
    frame: pd.DataFrame,
    *,
    side: str,
    spot: float,
    levels: LevelBundle,
    options: OptionIntelligence,
) -> tuple[OptionLeg | None, float, tuple[str, ...]]:
    rows = _candidate_rows(frame, side, spot)
    if rows.empty:
        return None, 0.0, (f"No usable OTM {side} row in current option window",)

    scores: list[tuple[float, pd.Series, float | None, float, str, str]] = []
    oi_series = rows.get("oi", pd.Series(dtype=float))
    volume_series = rows.get("volume", pd.Series(dtype=float))
    for _, row in rows.iterrows():
        strike = float(row["strike"])
        distance_pct = abs(strike - spot) / max(spot, 1.0) * 100.0
        spread_pct, spread_score = _spread_metrics(row)
        oi_score = _percentile_score(_number(row.get("oi")), oi_series)
        volume_score = _percentile_score(_number(row.get("volume")), volume_series)
        liquidity = spread_score * 0.50 + oi_score * 0.30 + volume_score * 0.20
        level_score, level_reason = _level_score(side, strike, levels)
        wall_score, wall_reason = _wall_score(side, strike, options)
        total = (
            liquidity * 0.35
            + _delta_score(_number(row.get("delta"))) * 0.25
            + _distance_score(distance_pct) * 0.20
            + level_score * 0.10
            + wall_score * 0.10
        )
        scores.append((total, row, spread_pct, liquidity, level_reason, wall_reason))

    score, row, spread_pct, liquidity, level_reason, wall_reason = max(
        scores, key=lambda item: item[0]
    )
    leg = _row_to_leg(
        row,
        role="SHORT",
        side=side,
        spot=spot,
        liquidity_score=liquidity,
        spread_pct=spread_pct,
    )
    reasons = (
        level_reason,
        wall_reason,
        f"Liquidity score {liquidity:.1f}/100",
    )
    return leg, round(clamp(score, 0.0, 100.0), 1), reasons


def _strike_step(frame: pd.DataFrame) -> float | None:
    strikes = sorted(
        pd.to_numeric(frame.get("strike"), errors="coerce").dropna().unique()
    )
    gaps = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
    return float(pd.Series(gaps).median()) if gaps else None


def _select_hedge_leg(
    frame: pd.DataFrame,
    *,
    side: str,
    short: OptionLeg,
    spot: float,
) -> OptionLeg | None:
    """Choose the best farther-OTM hedge, not merely the first available strike.

    The hedge search is bounded from the configured minimum to maximum strike steps.
    Liquidity is the largest weight, while credit/risk efficiency and distance prevent a
    very cheap but unusable far-away hedge from winning.
    """
    if frame.empty:
        return None
    rows = frame[frame["side"].astype(str).str.upper().eq(side)].copy()
    rows["strike"] = pd.to_numeric(rows["strike"], errors="coerce")
    rows["last_price"] = pd.to_numeric(rows["last_price"], errors="coerce")
    rows = rows.dropna(subset=["strike", "last_price"])
    rows = rows[rows["last_price"] >= CONFIG.trade_min_hedge_premium]
    if side == "CE":
        rows = rows[rows["strike"] > spot]
    else:
        rows = rows[rows["strike"] < spot]
    rows = rows.sort_values("strike").reset_index(drop=True)
    if rows.empty:
        return None
    step = _strike_step(rows)
    if step is None or step <= 0:
        return None
    minimum_gap = step * CONFIG.trade_hedge_steps
    maximum_gap = step * max(CONFIG.trade_hedge_steps, CONFIG.trade_max_hedge_steps)
    if side == "CE":
        eligible = rows[
            (rows["strike"] >= short.strike + minimum_gap)
            & (rows["strike"] <= short.strike + maximum_gap)
        ].copy()
    else:
        eligible = rows[
            (rows["strike"] <= short.strike - minimum_gap)
            & (rows["strike"] >= short.strike - maximum_gap)
        ].copy()
    if eligible.empty:
        return None

    short_price = _sell_price(short)
    if short_price is None or short_price <= 0:
        return None

    oi_series = rows.get("oi", pd.Series(dtype=float))
    volume_series = rows.get("volume", pd.Series(dtype=float))
    scored: list[tuple[float, pd.Series, float | None, float]] = []
    target_steps = max(CONFIG.trade_hedge_steps, min(3, CONFIG.trade_max_hedge_steps))
    target_gap = target_steps * step
    for _, row in eligible.iterrows():
        strike = float(row["strike"])
        hedge_price = _number(row.get("top_ask_price")) or _number(row.get("last_price"))
        if hedge_price is None or hedge_price <= 0 or hedge_price >= short_price:
            continue
        credit = short_price - hedge_price
        width = abs(strike - short.strike)
        if width <= 0 or credit < CONFIG.trade_min_credit_points:
            continue
        spread_pct, spread_score = _spread_metrics(row)
        oi_score = _percentile_score(_number(row.get("oi")), oi_series)
        volume_score = _percentile_score(_number(row.get("volume")), volume_series)
        liquidity = spread_score * 0.50 + oi_score * 0.30 + volume_score * 0.20
        width_score = clamp(100.0 - abs(width - target_gap) / max(target_gap, step) * 55.0, 20.0, 100.0)
        credit_ratio = credit / width
        efficiency_score = clamp(credit_ratio * 500.0, 15.0, 100.0)
        hedge_cost_ratio = hedge_price / short_price
        cost_score = clamp(100.0 - abs(hedge_cost_ratio - 0.35) * 140.0, 20.0, 100.0)
        total = liquidity * 0.45 + width_score * 0.20 + efficiency_score * 0.20 + cost_score * 0.15
        scored.append((total, row, spread_pct, liquidity))

    if not scored:
        return None
    _, row, spread_pct, liquidity = max(scored, key=lambda item: item[0])
    return _row_to_leg(
        row,
        role="HEDGE",
        side=side,
        spot=spot,
        liquidity_score=liquidity,
        spread_pct=spread_pct,
    )


def _sell_price(leg: OptionLeg) -> float | None:
    return leg.bid if leg.bid is not None and leg.bid > 0 else leg.last_price


def _buy_price(leg: OptionLeg) -> float | None:
    return leg.ask if leg.ask is not None and leg.ask > 0 else leg.last_price


def _vertical_plan(
    *,
    name: str,
    side: str,
    frame: pd.DataFrame,
    spot: float,
    levels: LevelBundle,
    options: OptionIntelligence,
) -> SetupPlan:
    short, quality, reasons = _select_short_leg(
        frame, side=side, spot=spot, levels=levels, options=options
    )
    if short is None:
        return SetupPlan.unavailable(name, reasons[0])
    hedge = _select_hedge_leg(frame, side=side, short=short, spot=spot)
    if hedge is None:
        return SetupPlan.unavailable(
            name, "No valid farther-OTM hedge in current option window"
        )

    short_price = _sell_price(short)
    hedge_price = _buy_price(hedge)
    if short_price is None or hedge_price is None:
        return SetupPlan.unavailable(name, "Executable bid/ask or LTP is missing")
    credit = short_price - hedge_price
    width = abs(hedge.strike - short.strike)
    if credit < CONFIG.trade_min_credit_points or width <= 0:
        return SetupPlan.unavailable(name, "Estimated spread credit is too small")

    max_risk = max(0.0, width - credit)
    lower_be = short.strike - credit if side == "PE" else None
    upper_be = short.strike + credit if side == "CE" else None
    liquidity_floor = min(short.liquidity_score, hedge.liquidity_score)
    quality = round(clamp(quality * 0.75 + liquidity_floor * 0.25, 0.0, 100.0), 1)
    status = "READY" if quality >= CONFIG.trade_min_plan_quality else "CAUTION"
    blocker = (
        "None"
        if status == "READY"
        else "Candidate quality is below the ready threshold"
    )
    return SetupPlan(
        name=name,
        short_legs=(short,),
        hedge_legs=(hedge,),
        estimated_credit_points=round(credit, 2),
        width_points=round(width, 2),
        max_risk_points=round(max_risk, 2),
        lower_breakeven=round(lower_be, 2) if lower_be is not None else None,
        upper_breakeven=round(upper_be, 2) if upper_be is not None else None,
        quality_score=quality,
        status=status,
        reasons=reasons,
        blocker=blocker,
    )


def _condor_plan(ce: SetupPlan, pe: SetupPlan) -> SetupPlan:
    if not ce.available or not pe.available:
        return SetupPlan.unavailable(
            "IRON CONDOR",
            "Both protected CE and PE verticals are required",
        )
    ce_short = ce.short_legs[0]
    pe_short = pe.short_legs[0]
    if pe_short.strike >= ce_short.strike:
        return SetupPlan.unavailable("IRON CONDOR", "Short strikes overlap")
    credit = (ce.estimated_credit_points or 0.0) + (pe.estimated_credit_points or 0.0)
    widths = [ce.width_points or 0.0, pe.width_points or 0.0]
    max_width = max(widths)
    max_risk = max(0.0, max_width - credit)
    quality = round(min(ce.quality_score, pe.quality_score), 1)
    status = "READY" if quality >= CONFIG.trade_min_plan_quality else "CAUTION"
    return SetupPlan(
        name="IRON CONDOR",
        short_legs=(pe_short, ce_short),
        hedge_legs=(pe.hedge_legs[0], ce.hedge_legs[0]),
        estimated_credit_points=round(credit, 2),
        width_points=round(max_width, 2),
        max_risk_points=round(max_risk, 2),
        lower_breakeven=round(pe_short.strike - credit, 2),
        upper_breakeven=round(ce_short.strike + credit, 2),
        quality_score=quality,
        status=status,
        reasons=(
            "Protected wings exist on both sides",
            f"Short-strike range {pe_short.strike:,.0f}–{ce_short.strike:,.0f}",
            f"Combined estimated credit {credit:.2f} points",
        ),
        blocker="None" if status == "READY" else "One or both wings have weak quality",
    )


def _apply_runtime_status(
    plan: SetupPlan,
    *,
    selected: bool,
    market_session: MarketSession,
    decision: FinalDecision,
) -> SetupPlan:
    if not plan.available:
        return plan
    if not market_session.is_live:
        return replace(plan, status="REFERENCE ONLY", blocker="Market is not live")
    if decision.final_action == "WAIT":
        return replace(plan, status="WATCH ONLY", blocker=decision.blocker)
    if not selected:
        return replace(
            plan,
            status="ALTERNATIVE",
            blocker="Not selected by the final one-brain decision",
        )
    quality_floor = CONFIG.buy_min_plan_quality if plan.is_buy else CONFIG.trade_min_plan_quality
    if plan.quality_score < quality_floor:
        return replace(
            plan, status="BLOCKED", blocker="Selected candidate quality is too low"
        )
    return replace(plan, status="READY", blocker="None")


def calculate_trade_plan(
    *,
    frame: pd.DataFrame,
    spot: float,
    expiry: str | None,
    levels: LevelBundle,
    options: OptionIntelligence,
    decision: FinalDecision,
    market_session: MarketSession,
) -> TradePlanBundle:
    """Convert the same One-Brain choice into a concrete option structure.

    The brain compares CE BUY, PE BUY, CE SELL, PE SELL and IRON CONDOR. This
    planner never re-ranks them; it only selects a liquid primary leg and mandatory
    protection for seller structures from the same option-chain snapshot.
    """

    if frame.empty or spot <= 0 or not expiry:
        reason = "Option chain or expiry unavailable"
        return TradePlanBundle(
            as_of=options.as_of,
            expiry=expiry,
            spot=spot if spot > 0 else None,
            ce_sell=SetupPlan.unavailable("CE SELL", reason),
            pe_sell=SetupPlan.unavailable("PE SELL", reason),
            iron_condor=SetupPlan.unavailable("IRON CONDOR", reason),
            ce_buy=SetupPlan.unavailable("CE BUY", reason),
            pe_buy=SetupPlan.unavailable("PE BUY", reason),
            selected_setup="WAIT",
            status="UNAVAILABLE",
            blocker=reason,
        )

    ce_sell = _vertical_plan(
        name="CE SELL", side="CE", frame=frame, spot=spot, levels=levels, options=options
    )
    pe_sell = _vertical_plan(
        name="PE SELL", side="PE", frame=frame, spot=spot, levels=levels, options=options
    )
    condor = _condor_plan(ce_sell, pe_sell)
    ce_buy = _buy_plan(name="CE BUY", side="CE", frame=frame, spot=spot, levels=levels)
    pe_buy = _buy_plan(name="PE BUY", side="PE", frame=frame, spot=spot, levels=levels)

    selected = decision.final_action.replace(" WITH HEDGE", "")
    plans = {
        "CE BUY": ce_buy,
        "PE BUY": pe_buy,
        "CE SELL": ce_sell,
        "PE SELL": pe_sell,
        "IRON CONDOR": condor,
    }
    plans = {
        name: _apply_runtime_status(
            plan,
            selected=selected == name,
            market_session=market_session,
            decision=decision,
        )
        for name, plan in plans.items()
    }

    if not market_session.is_live:
        status = "REFERENCE ONLY"
        blocker = "Market is not live"
    elif decision.final_action == "WAIT":
        status = "BLOCKED"
        blocker = decision.blocker
    else:
        selected_plan = plans.get(selected)
        if selected_plan is None or selected_plan.status != "READY":
            status = "BLOCKED"
            blocker = selected_plan.blocker if selected_plan else "Selected plan unavailable"
        else:
            status = "READY"
            blocker = "None"

    return TradePlanBundle(
        as_of=options.as_of,
        expiry=expiry,
        spot=spot,
        ce_sell=plans["CE SELL"],
        pe_sell=plans["PE SELL"],
        iron_condor=plans["IRON CONDOR"],
        ce_buy=plans["CE BUY"],
        pe_buy=plans["PE BUY"],
        selected_setup=selected if selected in plans else "WAIT",
        status=status,
        blocker=blocker,
    )
