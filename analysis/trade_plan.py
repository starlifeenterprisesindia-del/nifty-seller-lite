from __future__ import annotations

from dataclasses import replace
from math import isfinite
from typing import Any

import pandas as pd

from analysis.technical_utils import clamp
from config import CONFIG
from models import (
    FinalDecision,
    IndicatorBundle,
    LevelBundle,
    MarketSession,
    OptionIntelligence,
    OptionLeg,
    ProtectedCandidate,
    SetupPlan,
    TradePlanBundle,
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _future_direction(value: Any) -> str:
    text = str(value or "").upper()
    if text in {"UP", "BULLISH"}:
        return "UP"
    if text in {"DOWN", "BEARISH"}:
        return "DOWN"
    if text in {"RANGE", "SIDEWAYS"}:
        return "RANGE"
    return "WAIT"


def _future_alignment(action: str, direction: str, strength: float) -> float:
    """Future Brain may rank candidates, but it never bypasses safety gates."""
    direction = _future_direction(direction)
    wanted = {
        "PE SELL": "UP", "CE BUY": "UP",
        "CE SELL": "DOWN", "PE BUY": "DOWN",
        "IRON CONDOR": "RANGE",
    }.get(action, "WAIT")
    if direction == "WAIT":
        return 50.0
    confidence = clamp(float(strength or 0.0), 0.0, 100.0)
    if direction == wanted:
        return 55.0 + confidence * 0.45
    if direction == "RANGE" or wanted == "RANGE":
        return 48.0
    return max(5.0, 45.0 - confidence * 0.40)


def _future_strike_alignment(
    action: str,
    direction: str,
    strength: float,
    *,
    strike: float,
    spot: float,
) -> float:
    """Make Future Brain alignment specific to each candidate strike.

    A strong matching forecast may prefer a nearer, more responsive/rewarding
    strike after barrier and delta safety have passed.  A conflicting forecast
    rewards additional distance.  This changes ranking rather than adding the
    same cosmetic constant to every row.
    """
    base = _future_alignment(action, direction, strength)
    future = _future_direction(direction)
    wanted = {
        "PE SELL": "UP", "CE BUY": "UP",
        "CE SELL": "DOWN", "PE BUY": "DOWN",
    }.get(action, "WAIT")
    distance_score = clamp(abs(float(strike) - float(spot)) / 200.0 * 100.0, 0.0, 100.0)
    if future == "WAIT":
        strike_score = 50.0
    elif future == wanted:
        strike_score = 100.0 - distance_score
    elif future == "RANGE":
        strike_score = 60.0
    else:
        strike_score = distance_score
    influence = clamp(float(strength or 0.0), 0.0, 100.0) / 100.0
    return clamp(base * (1.0 - 0.45 * influence) + strike_score * 0.45 * influence, 0.0, 100.0)


def _row_for_leg(frame: pd.DataFrame, leg: OptionLeg) -> pd.Series | None:
    rows = frame[
        frame["side"].astype(str).str.upper().eq(leg.side)
        & pd.to_numeric(frame["strike"], errors="coerce").eq(leg.strike)
    ]
    return None if rows.empty else rows.iloc[0]


def _plan_decay_edge(frame: pd.DataFrame, plan: SetupPlan) -> float | None:
    sold = bought = 0.0
    seen = False
    for leg in plan.short_legs:
        row = _row_for_leg(frame, leg)
        theta = _number(row.get("theta")) if row is not None else None
        if theta is not None:
            sold += abs(theta)
            seen = True
    for leg in plan.hedge_legs:
        row = _row_for_leg(frame, leg)
        theta = _number(row.get("theta")) if row is not None else None
        if theta is not None:
            bought += abs(theta)
            seen = True
    return sold - bought if seen else None


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


def _executable_book(row: pd.Series, *, maximum_spread_pct: float = 10.0) -> bool:
    """Require a real, bounded bid/ask book for an entry leg.

    LTP remains useful for display, but it is not an executable protected-spread
    price and must never make a candidate READY by itself.
    """
    bid = _number(row.get("top_bid_price"))
    ask = _number(row.get("top_ask_price"))
    if bid is None or ask is None or not 0 < bid <= ask:
        return False
    midpoint = (bid + ask) / 2.0
    return midpoint > 0 and (ask - bid) / midpoint * 100.0 <= maximum_spread_pct


def _distance_score(distance_pct: float) -> float:
    target = CONFIG.trade_target_distance_pct
    tolerance = CONFIG.trade_distance_tolerance_pct
    return clamp(100.0 - abs(distance_pct - target) / tolerance * 100.0, 0.0, 100.0)


def _delta_score(
    delta: float | None,
    *,
    target: float | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    target = float(target if target is not None else CONFIG.trade_target_abs_delta)
    minimum = float(minimum if minimum is not None else CONFIG.trade_min_abs_delta)
    maximum = float(maximum if maximum is not None else CONFIG.trade_max_abs_delta)
    if delta is None:
        return 45.0
    absolute = abs(delta)
    if absolute < minimum or absolute > maximum:
        return 15.0
    return clamp(
        100.0
        - abs(absolute - target)
        / max(target, 0.01)
        * 100.0,
        0.0,
        100.0,
    )


def _buy_delta_score(
    delta: float | None,
    *,
    target: float | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    target = float(target if target is not None else CONFIG.buy_target_abs_delta)
    minimum = float(minimum if minimum is not None else CONFIG.buy_min_abs_delta)
    maximum = float(maximum if maximum is not None else CONFIG.buy_max_abs_delta)
    if delta is None:
        return 42.0
    absolute = abs(delta)
    if absolute < minimum or absolute > maximum:
        return 12.0
    return clamp(
        100.0
        - abs(absolute - target)
        / max(target, 0.01)
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
    if "greeks_quality" in rows:
        rows = rows[rows.greeks_quality.isin(["READY", "IV WARNING"])]
    rows["strike"] = pd.to_numeric(rows["strike"], errors="coerce")
    rows["last_price"] = pd.to_numeric(rows["last_price"], errors="coerce")
    rows = rows.dropna(subset=["strike", "last_price"])
    rows = rows[rows["last_price"] >= CONFIG.buy_min_option_premium]
    # Keep ATM and one/two near-ITM/OTM strikes. Liquidity and delta decide the winner.
    max_distance = max(100.0, spot * 0.006)
    rows = rows[rows["strike"].sub(spot).abs() <= max_distance]
    return rows.sort_values("strike").reset_index(drop=True)


def _has_farther_leg(
    frame: pd.DataFrame,
    *,
    side: str,
    strike: float,
    minimum_steps: int,
    maximum_steps: int | None = None,
) -> bool:
    """Check protection before a main leg is scored as the best candidate."""
    step = _strike_step(frame)
    if step is None or step <= 0:
        return False
    rows = frame[frame["side"].astype(str).str.upper().eq(side)].copy()
    rows["strike"] = pd.to_numeric(rows["strike"], errors="coerce")
    rows["last_price"] = pd.to_numeric(rows["last_price"], errors="coerce")
    rows = rows.dropna(subset=["strike", "last_price"])
    rows = rows[rows["last_price"] >= CONFIG.trade_min_hedge_premium]
    minimum_gap = max(1, minimum_steps) * step
    maximum_gap = (maximum_steps * step) if maximum_steps is not None else None
    gap = rows["strike"] - strike if side == "CE" else strike - rows["strike"]
    eligible = gap.ge(minimum_gap)
    if maximum_gap is not None:
        eligible &= gap.le(maximum_gap)
    return bool(eligible.any())


def _select_long_leg(
    frame: pd.DataFrame,
    *,
    side: str,
    spot: float,
    levels: LevelBundle,
    target_delta: float = 0.50,
    min_delta: float = 0.30,
    max_delta: float = 0.72,
    future_direction: str = "WAIT",
    future_strength: float = 0.0,
) -> tuple[OptionLeg | None, float, tuple[str, ...]]:
    rows = _buy_candidate_rows(frame, side, spot)
    if not rows.empty:
        rows = rows[
            rows["strike"].map(
                lambda strike: _has_farther_leg(
                    frame,
                    side=side,
                    strike=float(strike),
                    minimum_steps=1,
                )
            )
        ].reset_index(drop=True)
    if rows.empty:
        return None, 0.0, (f"No usable ATM/near-ITM {side} buy row",)
    if "delta" in rows.columns:
        deltas = pd.to_numeric(rows["delta"], errors="coerce").abs()
        in_band = rows[deltas.between(min_delta, max_delta, inclusive="both")]
        if not in_band.empty:
            rows = in_band.reset_index(drop=True)
    oi_series = rows.get("oi", pd.Series(dtype=float))
    volume_series = rows.get("volume", pd.Series(dtype=float))
    scored: list[tuple[float, pd.Series, float | None, float, str]] = []
    for _, row in rows.iterrows():
        strike = float(row["strike"])
        if not _executable_book(row):
            continue
        ask = _number(row.get("top_ask_price"))
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
        alignment = _future_strike_alignment(
            f"{side} BUY", future_direction, future_strength,
            strike=strike, spot=spot,
        )
        total = (
            liquidity * 0.30
            + _buy_delta_score(
                _number(row.get("delta")),
                target=target_delta,
                minimum=min_delta,
                maximum=max_delta,
            )
            * 0.25
            + distance_score * 0.10
            + level_score * 0.20
            + alignment * 0.15
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
        f"Future Brain alignment {_future_strike_alignment(f'{side} BUY', future_direction, future_strength, strike=leg.strike, spot=spot):.1f}/100 (strike-specific)",
    )


def _buy_plan(
    *,
    name: str,
    side: str,
    frame: pd.DataFrame,
    spot: float,
    levels: LevelBundle,
    target_delta: float = 0.50,
    min_delta: float = 0.30,
    max_delta: float = 0.72,
    hedge_steps: int = 3,
    future_direction: str = "WAIT",
    future_strength: float = 0.0,
) -> SetupPlan:
    leg, quality, reasons = _select_long_leg(
        frame,
        side=side,
        spot=spot,
        levels=levels,
        target_delta=target_delta,
        min_delta=min_delta,
        max_delta=max_delta,
        future_direction=future_direction,
        future_strength=future_strength,
    )
    if leg is None:
        return SetupPlan.unavailable(name, reasons[0])
    hedge = _select_buy_hedge_leg(
        frame, side=side, main=leg, spot=spot, target_steps=hedge_steps
    )
    if hedge is None:
        return SetupPlan.unavailable(
            name, "No liquid farther-OTM short hedge for the buy spread"
        )
    long_price = _buy_price(leg)
    hedge_price = _sell_price(hedge)
    if long_price is None or hedge_price is None:
        return SetupPlan.unavailable(name, "Executable bid/ask is missing; no LTP-only entry")
    debit = long_price - hedge_price
    width = abs(hedge.strike - leg.strike)
    if debit <= 0 or width <= 0 or debit >= width:
        return SetupPlan.unavailable(name, "Debit spread price/width is invalid")
    lower_be = leg.strike - debit if side == "PE" else None
    upper_be = leg.strike + debit if side == "CE" else None
    quality = round(
        clamp(quality * 0.75 + hedge.liquidity_score * 0.25, 0.0, 100.0), 1
    )
    max_profit = width - debit
    payoff_ratio = max_profit / debit
    if payoff_ratio < 0.20:
        return SetupPlan.unavailable(name, "SAFE BUT LOW REWARD — projected spread benefit is too small")
    status = "READY" if quality >= CONFIG.buy_min_plan_quality else "CAUTION"
    return SetupPlan(
        name=name,
        short_legs=(hedge,),
        hedge_legs=(),
        estimated_credit_points=None,
        width_points=round(width, 2),
        max_risk_points=round(debit, 2),
        lower_breakeven=round(lower_be, 2) if lower_be is not None else None,
        upper_breakeven=round(upper_be, 2) if upper_be is not None else None,
        quality_score=quality,
        status=status,
        reasons=reasons
        + (
            f"Farther-OTM hedge {hedge.strike:,.0f} {side}",
            f"Defined max profit {max_profit:.2f} points",
            f"Reward/debit {payoff_ratio:.2f}; meaningful-move filter passed",
        ),
        blocker=(
            "None"
            if status == "READY"
            else "Buy-leg quality is below the ready threshold"
        ),
        long_legs=(leg,),
        estimated_debit_points=round(debit, 2),
    )


def _select_buy_hedge_leg(
    frame: pd.DataFrame,
    *,
    side: str,
    main: OptionLeg,
    spot: float,
    target_steps: int,
) -> OptionLeg | None:
    """Sell one farther-OTM option to turn every directional buy into a debit spread."""

    if frame.empty:
        return None
    rows = frame[frame["side"].astype(str).str.upper().eq(side)].copy()
    rows["strike"] = pd.to_numeric(rows["strike"], errors="coerce")
    rows["last_price"] = pd.to_numeric(rows["last_price"], errors="coerce")
    rows = rows.dropna(subset=["strike", "last_price"])
    rows = rows[rows["last_price"] >= CONFIG.trade_min_hedge_premium]
    if side == "CE":
        rows = rows[rows["strike"] > main.strike]
    else:
        rows = rows[rows["strike"] < main.strike]
    if rows.empty:
        return None
    step = _strike_step(frame)
    if step is None or step <= 0:
        return None
    maximum_gap = max(1, int(CONFIG.trade_max_hedge_steps)) * step
    gap = rows["strike"] - main.strike if side == "CE" else main.strike - rows["strike"]
    rows = rows[gap.le(maximum_gap)]
    if rows.empty:
        return None
    target_gap = max(1, int(target_steps)) * step
    target_strike = main.strike + target_gap if side == "CE" else main.strike - target_gap
    oi_series = rows.get("oi", pd.Series(dtype=float))
    volume_series = rows.get("volume", pd.Series(dtype=float))
    scored: list[tuple[float, pd.Series, float | None, float]] = []
    for _, row in rows.iterrows():
        if not _executable_book(row):
            continue
        bid = _number(row.get("top_bid_price"))
        if bid is None or bid <= 0:
            continue
        spread_pct, spread_score = _spread_metrics(row)
        oi_score = _percentile_score(_number(row.get("oi")), oi_series)
        volume_score = _percentile_score(_number(row.get("volume")), volume_series)
        liquidity = spread_score * 0.50 + oi_score * 0.30 + volume_score * 0.20
        distance = abs(float(row["strike"]) - target_strike)
        distance_score = clamp(100.0 - distance / max(target_gap, step) * 70.0, 10.0, 100.0)
        total = liquidity * 0.70 + distance_score * 0.30
        scored.append((total, row, spread_pct, liquidity))
    if not scored:
        return None
    _, row, spread_pct, liquidity = max(scored, key=lambda item: item[0])
    return _row_to_leg(
        row,
        role="HEDGE SHORT",
        side=side,
        spot=spot,
        liquidity_score=liquidity,
        spread_pct=spread_pct,
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
    if "greeks_quality" in rows:
        rows = rows[rows.greeks_quality.isin(["READY", "IV WARNING"])]
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
    target_delta: float = 0.20,
    min_delta: float = 0.08,
    max_delta: float = 0.38,
    only_strike: float | None = None,
    minimum_hedge_steps: int | None = None,
    future_direction: str = "WAIT",
    future_strength: float = 0.0,
) -> tuple[OptionLeg | None, float, tuple[str, ...]]:
    rows = _candidate_rows(frame, side, spot)
    had_directional_rows = not rows.empty
    if not rows.empty:
        rows = rows[
            rows["strike"].map(
                lambda strike: _has_farther_leg(
                    frame,
                    side=side,
                    strike=float(strike),
                    minimum_steps=minimum_hedge_steps if minimum_hedge_steps is not None else CONFIG.trade_hedge_steps,
                    maximum_steps=CONFIG.trade_max_hedge_steps,
                )
            )
        ].reset_index(drop=True)
    if rows.empty:
        reason = (
            f"No protected OTM {side} pair: farther hedge is missing"
            if had_directional_rows
            else f"No usable OTM {side} row in current option window"
        )
        return None, 0.0, (reason,)
    if "delta" in rows.columns:
        deltas = pd.to_numeric(rows["delta"], errors="coerce").abs()
        in_band = rows[deltas.between(min_delta, max_delta, inclusive="both")]
        if not in_band.empty:
            rows = in_band.reset_index(drop=True)

    # Keep one common liquidity population when comparing individual pairs.
    # Ranking a single row against itself falsely awards every strike full OI/volume rank.
    oi_series = rows.get("oi", pd.Series(dtype=float))
    volume_series = rows.get("volume", pd.Series(dtype=float))
    if only_strike is not None:
        rows = rows[rows["strike"].eq(only_strike)]
    if rows.empty:
        return None, 0.0, ("Selected short is outside the eligible risk band",)
    scores: list[tuple[float, pd.Series, float | None, float, str, str]] = []
    for _, row in rows.iterrows():
        strike = float(row["strike"])
        distance_pct = abs(strike - spot) / max(spot, 1.0) * 100.0
        spread_pct, spread_score = _spread_metrics(row)
        oi_score = _percentile_score(_number(row.get("oi")), oi_series)
        volume_score = _percentile_score(_number(row.get("volume")), volume_series)
        liquidity = spread_score * 0.50 + oi_score * 0.30 + volume_score * 0.20
        level_score, level_reason = _level_score(side, strike, levels)
        wall_score, wall_reason = _wall_score(side, strike, options)
        theta = _number(row.get("theta"))
        theta_values = pd.to_numeric(rows.get("theta", pd.Series(dtype=float)), errors="coerce").abs()
        theta_score = _percentile_score(abs(theta) if theta is not None else None, theta_values)
        alignment = _future_strike_alignment(
            f"{side} SELL", future_direction, future_strength,
            strike=strike, spot=spot,
        )
        total = (
            liquidity * 0.25
            + _delta_score(
                _number(row.get("delta")),
                target=target_delta,
                minimum=min_delta,
                maximum=max_delta,
            )
            * 0.15
            + _distance_score(distance_pct) * 0.10
            + level_score * 0.15
            + wall_score * 0.10
            + theta_score * 0.15
            + alignment * 0.10
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
        f"Future Brain alignment {_future_strike_alignment(f'{side} SELL', future_direction, future_strength, strike=leg.strike, spot=spot):.1f}/100 (strike-specific)",
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
    target_steps: int = 3,
    max_risk_points: float | None = None,
    only_hedge: float | None = None,
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
    if "greeks_quality" in rows:
        rows = rows[rows.greeks_quality.isin(["READY", "IV WARNING"])]
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
    minimum_gap = step * (1 if max_risk_points is not None else CONFIG.trade_hedge_steps)
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
    if only_hedge is not None:
        eligible = eligible[eligible["strike"].eq(only_hedge)]

    short_price = _sell_price(short)
    if short_price is None or short_price <= 0:
        return None

    oi_series = rows.get("oi", pd.Series(dtype=float))
    volume_series = rows.get("volume", pd.Series(dtype=float))
    short_match = rows[rows["strike"].eq(float(short.strike))]
    short_theta = (
        abs(float(short_match.iloc[0]["theta"]))
        if not short_match.empty
        and "theta" in short_match.columns
        and pd.notna(short_match.iloc[0].get("theta"))
        else None
    )
    scored: list[tuple[float, pd.Series, float | None, float]] = []
    target_steps = max(
        CONFIG.trade_hedge_steps,
        min(int(target_steps), CONFIG.trade_max_hedge_steps),
    )
    target_gap = target_steps * step
    for _, row in eligible.iterrows():
        strike = float(row["strike"])
        hedge_price = _number(row.get("top_ask_price"))
        hedge_spread, _ = _spread_metrics(row)
        if hedge_spread is None or hedge_spread > 10:
            continue
        if hedge_price is None or hedge_price <= 0 or hedge_price >= short_price:
            continue
        credit = short_price - hedge_price
        width = abs(strike - short.strike)
        if max_risk_points is not None and width - credit > max_risk_points:
            continue
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
        hedge_theta = _number(row.get("theta"))
        if short_theta is not None and hedge_theta is not None and short_theta > 0:
            # We want the short option's absolute time decay to exceed the hedge's,
            # while the existing distance/liquidity/cost gates keep the hedge useful.
            theta_ratio = abs(hedge_theta) / short_theta
            theta_edge_score = clamp((1.0 - theta_ratio) * 140.0 + 45.0, 15.0, 100.0)
        else:
            theta_edge_score = 55.0
        total = (
            liquidity * 0.35
            + width_score * 0.15
            + efficiency_score * 0.15
            + cost_score * 0.15
            + theta_edge_score * 0.20
        )
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


def _vertical_plan_for_short(
    *,
    name: str,
    side: str,
    frame: pd.DataFrame,
    spot: float,
    levels: LevelBundle,
    options: OptionIntelligence,
    target_delta: float = 0.20,
    min_delta: float = 0.08,
    max_delta: float = 0.38,
    hedge_steps: int = 3,
    only_strike: float | None = None,
    max_risk_points: float | None = None,
    only_hedge: float | None = None,
    future_direction: str = "WAIT",
    future_strength: float = 0.0,
) -> SetupPlan:
    short, quality, reasons = _select_short_leg(
        frame,
        side=side,
        spot=spot,
        levels=levels,
        options=options,
        target_delta=target_delta,
        min_delta=min_delta,
        max_delta=max_delta,
        only_strike=only_strike,
        minimum_hedge_steps=1 if max_risk_points is not None else None,
        future_direction=future_direction,
        future_strength=future_strength,
    )
    if short is None:
        return SetupPlan.unavailable(name, reasons[0])
    hedge = _select_hedge_leg(
        frame,
        side=side,
        short=short,
        spot=spot,
        target_steps=hedge_steps,
        max_risk_points=max_risk_points,
        only_hedge=only_hedge,
    )
    if hedge is None:
        return SetupPlan.unavailable(
            name, "No valid farther-OTM hedge in current option window"
        )

    short_price = _sell_price(short)
    hedge_price = _buy_price(hedge)
    if any(leg.bid is None or leg.ask is None or not 0 < leg.bid <= leg.ask
           or (leg.ask - leg.bid) / ((leg.ask + leg.bid) / 2) > .10 for leg in (short, hedge)):
        return SetupPlan.unavailable(name, "Live bid/ask missing, crossed or wider than 10%; no LTP-only entry")
    if short_price is None or hedge_price is None:
        return SetupPlan.unavailable(name, "Executable bid/ask or LTP is missing")
    credit = short_price - hedge_price
    width = abs(hedge.strike - short.strike)
    if credit < CONFIG.trade_min_credit_points or width <= 0 or credit >= width:
        return SetupPlan.unavailable(name, "Estimated spread credit is too small")

    max_risk = max(0.0, width - credit)
    lower_be = short.strike - credit if side == "PE" else None
    upper_be = short.strike + credit if side == "CE" else None
    liquidity_floor = min(short.liquidity_score, hedge.liquidity_score)
    quality = round(clamp(quality * 0.75 + liquidity_floor * 0.25, 0.0, 100.0), 1)
    short_row = frame[
        frame["side"].astype(str).str.upper().eq(side)
        & pd.to_numeric(frame["strike"], errors="coerce").eq(short.strike)
    ]
    hedge_row = frame[
        frame["side"].astype(str).str.upper().eq(side)
        & pd.to_numeric(frame["strike"], errors="coerce").eq(hedge.strike)
    ]
    short_theta = _number(short_row.iloc[0].get("theta")) if not short_row.empty else None
    hedge_theta = _number(hedge_row.iloc[0].get("theta")) if not hedge_row.empty else None
    for selected_row in (short_row, hedge_row):
        if not selected_row.empty and selected_row.iloc[0].get("greeks_quality") == "IV WARNING":
            reasons = (*reasons, f"{selected_row.iloc[0]['strike']:.0f} {side}: IV WARNING; conditional source Greeks, verify quote/model assumptions")
    if short_theta is not None and hedge_theta is not None:
        decay_edge = abs(short_theta) - abs(hedge_theta)
        theta_reason = (
            f"Theta edge {decay_edge:+.2f}: SELL decay "
            f"{abs(short_theta):.2f} vs hedge {abs(hedge_theta):.2f}"
        )
    else:
        theta_reason = "Theta edge unavailable; liquidity/delta/hedge gates used"
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
        reasons=(*reasons, theta_reason),
        blocker=blocker,
    )


def _vertical_plan(*, name, side, frame, spot, levels, options, target_delta=.28,
                   min_delta=.08, max_delta=.38, hedge_steps=3, max_risk_points=None,
                   future_direction="WAIT", future_strength=0.0):
    """Rank complete protected pairs, not a short premium in isolation."""
    candidates = _candidate_rows(frame, side, spot)
    if candidates.empty:
        return SetupPlan.unavailable(name, "No eligible short/hedge pair")
    # Bounded enumeration keeps refresh latency predictable.
    if "delta" in candidates:
        band = candidates[pd.to_numeric(candidates.delta, errors="coerce").abs().between(min_delta, max_delta)]
        if not band.empty:
            candidates = band
    strikes = sorted(candidates.strike.unique(), key=lambda value: abs(float(value) - spot))[:6]
    step = _strike_step(frame)
    # Hedge candidates need not satisfy the short delta band.
    hedge_strikes = pd.to_numeric(frame.loc[frame.side.astype(str).str.upper().eq(side), "strike"], errors="coerce").dropna().unique()
    pairs = [(float(strike), float(hedge)) for strike in strikes for hedge in hedge_strikes
             if step and step*(1 if max_risk_points is not None else CONFIG.trade_hedge_steps)
             <= (float(hedge)-float(strike))*(1 if side=="CE" else -1) <= step*CONFIG.trade_max_hedge_steps]
    plans = [_vertical_plan_for_short(name=name, side=side, frame=frame, spot=spot,
              levels=levels, options=options, target_delta=target_delta, min_delta=min_delta,
              max_delta=max_delta, hedge_steps=hedge_steps, only_strike=float(strike),
              max_risk_points=max_risk_points, only_hedge=hedge,
              future_direction=future_direction, future_strength=future_strength) for strike,hedge in pairs]
    usable = [p for p in plans if p.available and p.max_risk_points and p.estimated_credit_points]
    if not usable:
        detail = f"; one-lot budget limit {max_risk_points:.2f} points" if max_risk_points is not None else ""
        return SetupPlan.unavailable(name, "No liquid short/hedge pair passes credit/book/risk checks" + detail)
    def value(plan):
        reward_risk = plan.estimated_credit_points / plan.max_risk_points
        decay = _plan_decay_edge(frame, plan)
        decay_score = 55.0 if decay is None else clamp(50.0 + decay * 8.0, 0.0, 100.0)
        reward_score = min(100, reward_risk * 240)
        alignment = _future_strike_alignment(
            name, future_direction, future_strength,
            strike=plan.short_legs[0].strike, spot=spot,
        )
        # Future alignment must be strong enough to alter a close ranking, while
        # executable book, barrier, delta and reward gates remain mandatory.
        return plan.quality_score * .25 + reward_score * .20 + decay_score * .20 + alignment * .35
    reward_eligible = [
        p for p in usable
        if p.estimated_credit_points / p.max_risk_points >= .06
    ]
    status_pool = [p for p in reward_eligible if p.status == "READY"]
    if not status_pool:
        status_pool = [p for p in reward_eligible if p.status == "CAUTION"]
    chosen = max(status_pool or usable, key=value)
    chosen_reward_risk = chosen.estimated_credit_points / chosen.max_risk_points
    if not reward_eligible:
        return replace(
            chosen,
            status="BLOCKED",
            blocker="SAFE BUT LOW REWARD — spread credit/risk is too small",
        )
    comparison = tuple({"short": p.short_legs[0].strike, "hedge": p.hedge_legs[0].strike,
        "credit_points": p.estimated_credit_points, "max_loss_points": p.max_risk_points,
        "credit_risk": round(p.estimated_credit_points/p.max_risk_points,3),
        "spot_buffer_points": round(abs(p.short_legs[0].strike-spot),2),
        "net_theta_edge": None if _plan_decay_edge(frame, p) is None else round(_plan_decay_edge(frame, p), 3),
        "theta_15m_points": None if _plan_decay_edge(frame, p) is None else round(_plan_decay_edge(frame, p) * 15 / 1440, 3),
        "pair_score": round(value(p),1), "quality": p.status,
        "expiry_pnl_at_short": p.estimated_credit_points,
        "expiry_pnl_at_hedge": -p.max_risk_points}
        for p in sorted(usable,key=value,reverse=True)[:3])
    return replace(chosen, pair_comparison=comparison, reasons=(*chosen.reasons,
        f"Compared {len(usable)} executable pairs; quality 25% + reward 20% + decay 20% + Future Brain strike alignment 35%, not win probability",
        f"Pair comparison: net credit/risk {chosen.estimated_credit_points / chosen.max_risk_points:.2f}; expiry payoff, not expected return"))


def _condor_plan(
    ce: SetupPlan,
    pe: SetupPlan,
    *,
    spot: float | None = None,
    levels: LevelBundle | None = None,
    options: OptionIntelligence | None = None,
    indicators: IndicatorBundle | None = None,
    frame: pd.DataFrame | None = None,
    future_direction: str = "WAIT",
    future_strength: float = 0.0,
) -> SetupPlan:
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
    balance_reasons: list[str] = []
    blockers: list[str] = []
    ce_delta = abs(float(ce_short.delta)) if ce_short.delta is not None else None
    pe_delta = abs(float(pe_short.delta)) if pe_short.delta is not None else None
    if ce_delta is not None and pe_delta is not None:
        delta_gap = abs(ce_delta - pe_delta)
        balance_reasons.append(f"Short delta gap {delta_gap:.2f}")
        if delta_gap > 0.15:
            blockers.append("CE/PE short deltas are imbalanced")
            quality -= min(20.0, delta_gap * 80.0)

    if spot is not None and spot > 0:
        up_distance = max(0.0, ce_short.strike - spot)
        down_distance = max(0.0, spot - pe_short.strike)
        distance_ratio = max(up_distance, down_distance) / max(1.0, min(up_distance, down_distance))
        balance_reasons.append(
            f"Risk room UP {up_distance:.0f} / DN {down_distance:.0f} pts"
        )
        future = _future_direction(future_direction)
        if future == "UP":
            skew_score = clamp(60.0 + (up_distance - down_distance) / 4.0, 0.0, 100.0)
            balance_reasons.append(f"Future Brain UP skew {skew_score:.0f}/100: CE room should be wider")
            quality += (skew_score - 50.0) * 0.12
        elif future == "DOWN":
            skew_score = clamp(60.0 + (down_distance - up_distance) / 4.0, 0.0, 100.0)
            balance_reasons.append(f"Future Brain DOWN skew {skew_score:.0f}/100: PE room should be wider")
            quality += (skew_score - 50.0) * 0.12
        else:
            skew_score = clamp(100.0 - abs(up_distance - down_distance) / 2.0, 0.0, 100.0)
            balance_reasons.append(f"Future Brain neutral room balance {skew_score:.0f}/100")
            quality += (skew_score - 50.0) * 0.08
        if distance_ratio > 2.2:
            blockers.append("Upper/lower short-strike room is imbalanced")
            quality -= min(18.0, (distance_ratio - 1.0) * 10.0)

    ce_credit = float(ce.estimated_credit_points or 0.0)
    pe_credit = float(pe.estimated_credit_points or 0.0)
    credit_ratio = max(ce_credit, pe_credit) / max(0.01, min(ce_credit, pe_credit))
    balance_reasons.append(f"Wing credit CE {ce_credit:.2f} / PE {pe_credit:.2f}")
    # Premium equality is not a target: skewed spot room and decay may correctly
    # produce unequal CE/PE credits. Only extreme imbalance remains a safety flag.
    if credit_ratio > 3.5:
        blockers.append("CE/PE wing credits are excessively imbalanced")
        quality -= min(15.0, (credit_ratio - 1.0) * 6.0)

    if indicators is not None:
        rsi3 = indicators.three_minute.rsi14
        rsi15 = indicators.fifteen_minute.rsi14
        if rsi3 is not None and rsi15 is not None:
            balance_reasons.append(f"RSI 3m {rsi3:.1f} / 15m {rsi15:.1f}")
            if rsi3 >= 65 and rsi15 >= 55:
                blockers.append("RSI alignment shows CE-side upside risk")
                quality -= 15.0
            elif rsi3 <= 35 and rsi15 <= 45:
                blockers.append("RSI alignment shows PE-side downside risk")
                quality -= 15.0

    if options is not None and options.market_bias in {"BULLISH", "BEARISH"}:
        if options.confidence >= 75 and "PERSISTENT" in options.persistence:
            blockers.append("Persistent directional option flow blocks Iron Condor")
            quality -= 20.0

    if frame is not None:
        combined_shell = SetupPlan(
            name="IRON CONDOR", short_legs=(pe_short, ce_short),
            hedge_legs=(pe.hedge_legs[0], ce.hedge_legs[0]),
            estimated_credit_points=credit, width_points=max_width,
            max_risk_points=max_risk, lower_breakeven=None, upper_breakeven=None,
            quality_score=quality, status="CAUTION", reasons=(), blocker="None",
        )
        decay = _plan_decay_edge(frame, combined_shell)
        if decay is not None:
            balance_reasons.append(f"Combined net theta edge {decay:+.2f}; decay-first rank")
            quality += clamp(decay * 1.5, -10.0, 12.0)

    quality = round(clamp(quality, 0.0, 100.0), 1)
    status = (
        "READY"
        if quality >= CONFIG.trade_min_plan_quality and not blockers
        else "BLOCKED"
        if blockers
        else "CAUTION"
    )
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
            *balance_reasons,
            *(reason for reason in (*pe.reasons, *ce.reasons) if reason.startswith("Theta edge")),
        ),
        blocker="None" if status == "READY" else "; ".join(blockers) or "One or both wings have weak quality",
    )


def _best_condor_plan(
    *, frame: pd.DataFrame, spot: float, levels: LevelBundle,
    options: OptionIntelligence, indicators: IndicatorBundle | None,
    max_risk_points: float | None, future_direction: str, future_strength: float,
) -> SetupPlan:
    """Jointly rank CE×PE protected wings; equal premium is deliberately irrelevant."""
    wing_specs = ((.18, .08, .26), (.25, .15, .34), (.32, .22, .40))
    ce_wings = [
        _vertical_plan(name="CE SELL", side="CE", frame=frame, spot=spot,
            levels=levels, options=options, target_delta=t, min_delta=lo,
            max_delta=hi, max_risk_points=max_risk_points,
            future_direction=future_direction, future_strength=future_strength)
        for t, lo, hi in wing_specs
    ]
    pe_wings = [
        _vertical_plan(name="PE SELL", side="PE", frame=frame, spot=spot,
            levels=levels, options=options, target_delta=t, min_delta=lo,
            max_delta=hi, max_risk_points=max_risk_points,
            future_direction=future_direction, future_strength=future_strength)
        for t, lo, hi in wing_specs
    ]
    candidates = [
        _condor_plan(ce, pe, spot=spot, levels=levels, options=options,
            indicators=indicators, frame=frame, future_direction=future_direction,
            future_strength=future_strength)
        for ce in ce_wings for pe in pe_wings if ce.available and pe.available
    ]
    usable = [item for item in candidates if item.available and item.max_risk_points]
    if not usable:
        return SetupPlan.unavailable("IRON CONDOR", "No jointly valid CE/PE protected combination")

    def score(plan: SetupPlan) -> float:
        reward = float(plan.estimated_credit_points or 0.0) / max(float(plan.max_risk_points or 0.0), .01)
        decay = _plan_decay_edge(frame, plan)
        decay_score = 55.0 if decay is None else clamp(50.0 + decay * 7.0, 0.0, 100.0)
        return plan.quality_score * .45 + min(100.0, reward * 220.0) * .25 + decay_score * .30

    reward_eligible = [
        item for item in usable
        if (item.estimated_credit_points or 0.0) / max(item.max_risk_points or 0.0, .01) >= .08
    ]
    ready = [item for item in reward_eligible if item.status == "READY"]
    caution = [item for item in reward_eligible if item.status == "CAUTION"]
    chosen = max(ready or caution or usable, key=score)
    if not reward_eligible:
        return replace(chosen, status="BLOCKED", blocker="SAFE BUT LOW REWARD — condor credit/risk is too small")
    return replace(chosen, reasons=(*chosen.reasons,
        f"Jointly compared {len(usable)} CE×PE wing combinations; premium equality was not used"))


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
    if plan.status == "BLOCKED":
        return plan
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


def activate_plan_candidate(
    bundle: TradePlanBundle,
    candidate: str,
    market_session: MarketSession,
) -> TradePlanBundle:
    """Activate the Common Gate candidate without rebuilding any strike math.

    Runtime labels such as ALTERNATIVE/WATCH ONLY are presentation states.  The
    candidate's intrinsic quality is restored here, while hard BLOCKED and
    unavailable plans remain blocked.
    """
    candidate = str(candidate or "WAIT").upper()
    plans = {
        "CE BUY": bundle.ce_buy,
        "PE BUY": bundle.pe_buy,
        "CE SELL": bundle.ce_sell,
        "PE SELL": bundle.pe_sell,
        "IRON CONDOR": bundle.iron_condor,
    }
    plan = plans.get(candidate)
    if plan is not None and plan.available and plan.status != "BLOCKED":
        if not market_session.is_live:
            plan = replace(plan, status="REFERENCE ONLY", blocker="Market is not live")
        else:
            floor = CONFIG.buy_min_plan_quality if plan.is_buy else CONFIG.trade_min_plan_quality
            plan = replace(
                plan,
                status="READY" if plan.quality_score >= floor else "CAUTION",
                blocker="None" if plan.quality_score >= floor else "Candidate quality is below the ready threshold",
            )
        plans[candidate] = plan
    return replace(
        bundle,
        ce_buy=plans["CE BUY"], pe_buy=plans["PE BUY"],
        ce_sell=plans["CE SELL"], pe_sell=plans["PE SELL"],
        iron_condor=plans["IRON CONDOR"], selected_setup=candidate,
    )


def _candidate_action(decision: FinalDecision, selected: str) -> str:
    if selected in {"CE BUY", "PE BUY", "CE SELL", "PE SELL"}:
        return selected
    # A reference candidate must never contradict the same One-Brain direction.
    # When the final action is WAIT we still show the best compatible protected
    # idea, but it remains WATCH ONLY until the execution guard becomes ready.
    direction = str(decision.market_direction or "").upper()
    if direction == "BULLISH":
        evaluations = {"PE SELL": decision.pe_sell, "CE BUY": decision.ce_buy}
    elif direction == "BEARISH":
        evaluations = {"CE SELL": decision.ce_sell, "PE BUY": decision.pe_buy}
    elif direction == "RANGE":
        evaluations = {"IRON CONDOR": decision.iron_condor}
    else:
        evaluations = {
            "CE SELL": decision.ce_sell,
            "PE SELL": decision.pe_sell,
            "CE BUY": decision.ce_buy,
            "PE BUY": decision.pe_buy,
            "IRON CONDOR": decision.iron_condor,
        }
    return max(evaluations, key=lambda name: float(evaluations[name].score))


def _protected_candidate_profiles(
    *,
    action: str,
    frame: pd.DataFrame,
    spot: float,
    levels: LevelBundle,
    options: OptionIntelligence,
    max_risk_points: float | None = None,
    future_direction: str = "WAIT",
    future_strength: float = 0.0,
) -> tuple[ProtectedCandidate, ...]:
    side = "CE" if action.startswith("CE") else "PE"
    is_buy = action.endswith("BUY")
    if is_buy:
        specs = (
            ("LOW RISK", 0.45, 0.30, 0.56, 2, "Kam ₹ risk; OTM/Theta ko check karo"),
            ("BALANCED", 0.58, 0.46, 0.68, 3, "Default balance of response and defined risk"),
            ("HIGH RISK", 0.68, 0.58, 0.82, 4, "Higher debit and faster directional response"),
        )
    else:
        specs = (
            ("LOW RISK", 0.15, 0.07, 0.22, 2, "Farther OTM credit spread; lower credit"),
            ("BALANCED", 0.28, 0.20, 0.34, 3, "Default balance of credit and distance"),
            ("HIGH RISK", 0.38, 0.30, 0.48, 4, "Nearer market short strike; loss moves faster"),
        )

    result: list[ProtectedCandidate] = []
    for rank, (profile, target, minimum, maximum, steps, note) in enumerate(specs, 1):
        if is_buy:
            plan = _buy_plan(
                name=action,
                side=side,
                frame=frame,
                spot=spot,
                levels=levels,
                target_delta=target,
                min_delta=minimum,
                max_delta=maximum,
                hedge_steps=steps,
                future_direction=future_direction,
                future_strength=future_strength,
            )
        else:
            plan = _vertical_plan(
                name=action,
                side=side,
                frame=frame,
                spot=spot,
                levels=levels,
                options=options,
                target_delta=target,
                min_delta=minimum,
                max_delta=maximum,
                hedge_steps=steps,
                max_risk_points=max_risk_points,
                future_direction=future_direction,
                future_strength=future_strength,
            )
        result.append(
            ProtectedCandidate(
                profile=profile,
                plan=plan,
                risk_rank=rank,
                note=note,
            )
        )
    return tuple(result)


def calculate_trade_plan(
    *,
    frame: pd.DataFrame,
    spot: float,
    expiry: str | None,
    levels: LevelBundle,
    options: OptionIntelligence,
    decision: FinalDecision,
    market_session: MarketSession,
    indicators: IndicatorBundle | None = None,
    risk_profile=None,
    future_direction: str = "WAIT",
    future_strength: float = 0.0,
) -> TradePlanBundle:
    """Convert the same One-Brain choice into a concrete option structure.

    The brain compares CE BUY, PE BUY, CE SELL, PE SELL and IRON CONDOR. This
    planner never re-ranks them; it only selects liquid equal-quantity verticals.
    Every directional BUY and SELL therefore has mandatory same-expiry protection.
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

    max_risk_points = None if risk_profile is None else (
        risk_profile.risk_budget_rupees / risk_profile.lot_size
        if risk_profile.lot_size > 0 and risk_profile.max_lots_cap >= 1 else 0.0)
    ce_sell = _vertical_plan(
        name="CE SELL", side="CE", frame=frame, spot=spot, levels=levels, options=options,
        max_risk_points=max_risk_points, future_direction=future_direction,
        future_strength=future_strength,
    )
    pe_sell = _vertical_plan(
        name="PE SELL", side="PE", frame=frame, spot=spot, levels=levels, options=options,
        max_risk_points=max_risk_points, future_direction=future_direction,
        future_strength=future_strength,
    )
    condor = _best_condor_plan(
        frame=frame, spot=spot, levels=levels, options=options,
        indicators=indicators, max_risk_points=max_risk_points,
        future_direction=future_direction, future_strength=future_strength,
    )
    ce_buy = _buy_plan(
        name="CE BUY", side="CE", frame=frame, spot=spot, levels=levels,
        target_delta=0.58, min_delta=0.46, max_delta=0.68, hedge_steps=3,
        future_direction=future_direction, future_strength=future_strength,
    )
    pe_buy = _buy_plan(
        name="PE BUY", side="PE", frame=frame, spot=spot, levels=levels,
        target_delta=0.58, min_delta=0.46, max_delta=0.68, hedge_steps=3,
        future_direction=future_direction, future_strength=future_strength,
    )

    selected = decision.final_action.replace(" WITH HEDGE", "")
    candidate_setup = _candidate_action(decision, selected)
    protected_candidates = _protected_candidate_profiles(
        action=candidate_setup,
        frame=frame,
        spot=spot,
        levels=levels,
        options=options,
        max_risk_points=max_risk_points,
        future_direction=future_direction,
        future_strength=future_strength,
    )
    plans = {
        "CE BUY": ce_buy,
        "PE BUY": pe_buy,
        "CE SELL": ce_sell,
        "PE SELL": pe_sell,
        "IRON CONDOR": condor,
    }
    balanced = next(
        (
            item.plan
            for item in protected_candidates
            if item.profile == "BALANCED" and item.plan.available
        ),
        None,
    )
    if balanced is not None and candidate_setup in plans:
        plans[candidate_setup] = balanced
    # Condor is independently joint-ranked; it need not reuse standalone winners.
    plans["IRON CONDOR"] = condor
    if max_risk_points is not None:
        plans = {name: (SetupPlan.unavailable(name, "One-lot defined risk exceeds configured budget")
                 if plan.available and (plan.max_risk_points is None or plan.max_risk_points > max_risk_points)
                 else plan) for name, plan in plans.items()}
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
        candidate_setup=candidate_setup,
        protected_candidates=protected_candidates,
    )
