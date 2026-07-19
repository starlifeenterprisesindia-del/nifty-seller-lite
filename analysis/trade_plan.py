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
    rows = _candidate_rows(frame, side, spot)
    if rows.empty:
        return None
    step = _strike_step(rows)
    if step is None or step <= 0:
        return None
    minimum_gap = step * CONFIG.trade_hedge_steps
    if side == "CE":
        eligible = rows[rows["strike"] >= short.strike + minimum_gap]
        eligible = eligible.sort_values("strike")
    else:
        eligible = rows[rows["strike"] <= short.strike - minimum_gap]
        eligible = eligible.sort_values("strike", ascending=False)
    if eligible.empty:
        return None
    row = eligible.iloc[0]
    spread_pct, spread_score = _spread_metrics(row)
    oi_score = _percentile_score(
        _number(row.get("oi")), rows.get("oi", pd.Series(dtype=float))
    )
    volume_score = _percentile_score(
        _number(row.get("volume")), rows.get("volume", pd.Series(dtype=float))
    )
    liquidity = spread_score * 0.50 + oi_score * 0.30 + volume_score * 0.20
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
    if plan.quality_score < CONFIG.trade_min_plan_quality:
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
    """Select protected strike candidates after, never instead of, the final brain.

    This module cannot change CE/PE/Condor/WAIT scores or the final action. It only
    converts the already-decided setup into a read-only, hedged candidate plan using
    the same option-chain snapshot.
    """

    if frame.empty or spot <= 0 or not expiry:
        unavailable = SetupPlan.unavailable(
            "CE SELL", "Option chain or expiry unavailable"
        )
        return TradePlanBundle(
            as_of=options.as_of,
            expiry=expiry,
            spot=spot if spot > 0 else None,
            ce_sell=unavailable,
            pe_sell=SetupPlan.unavailable(
                "PE SELL", "Option chain or expiry unavailable"
            ),
            iron_condor=SetupPlan.unavailable(
                "IRON CONDOR", "Option chain or expiry unavailable"
            ),
            selected_setup="WAIT",
            status="UNAVAILABLE",
            blocker="Option chain or expiry unavailable",
        )

    ce = _vertical_plan(
        name="CE SELL",
        side="CE",
        frame=frame,
        spot=spot,
        levels=levels,
        options=options,
    )
    pe = _vertical_plan(
        name="PE SELL",
        side="PE",
        frame=frame,
        spot=spot,
        levels=levels,
        options=options,
    )
    condor = _condor_plan(ce, pe)

    selected = decision.final_action.replace(" WITH HEDGE", "")
    ce = _apply_runtime_status(
        ce,
        selected=selected == "CE SELL",
        market_session=market_session,
        decision=decision,
    )
    pe = _apply_runtime_status(
        pe,
        selected=selected == "PE SELL",
        market_session=market_session,
        decision=decision,
    )
    condor = _apply_runtime_status(
        condor,
        selected=selected == "IRON CONDOR",
        market_session=market_session,
        decision=decision,
    )

    if not market_session.is_live:
        status = "REFERENCE ONLY"
        blocker = "Market is not live"
    elif decision.final_action == "WAIT":
        status = "BLOCKED"
        blocker = decision.blocker
    else:
        selected_plan = {
            "CE SELL": ce,
            "PE SELL": pe,
            "IRON CONDOR": condor,
        }.get(selected)
        if selected_plan is None or selected_plan.status != "READY":
            status = "BLOCKED"
            blocker = (
                selected_plan.blocker if selected_plan else "Selected plan unavailable"
            )
        else:
            status = "READY"
            blocker = "None"

    return TradePlanBundle(
        as_of=options.as_of,
        expiry=expiry,
        spot=spot,
        ce_sell=ce,
        pe_sell=pe,
        iron_condor=condor,
        selected_setup=selected
        if selected in {"CE SELL", "PE SELL", "IRON CONDOR"}
        else "WAIT",
        status=status,
        blocker=blocker,
    )
