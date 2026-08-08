from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from math import floor, isfinite
from typing import Any

import pandas as pd

from analysis.spot_premium_calculator import PremiumRangeEstimate
from models import BarrierMap, BarrierMapLevel


@dataclass(frozen=True)
class ExpiryContext:
    expiry_at: datetime | None
    calendar_days: int | None
    minutes_remaining: int | None
    regime: str
    label: str


@dataclass(frozen=True)
class PlannerLevel:
    label: str
    spot: float
    premium: float
    pnl_per_quantity: float
    total_pnl: float
    risk_reward: float | None
    action: str


@dataclass(frozen=True)
class SLTargetPlan:
    direction: str
    stop_spot: float
    stop_buffer_points: float
    stop_level_label: str
    stop_premium: float
    risk_per_quantity: float
    total_risk: float
    targets: tuple[PlannerLevel, ...]
    time_exit_minutes: int
    verdict: str
    warnings: tuple[str, ...]


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def expiry_context(*, captured_at: datetime, expiry: str | None) -> ExpiryContext:
    if not expiry:
        return ExpiryContext(None, None, None, "UNAVAILABLE", "Expiry unavailable")
    try:
        expiry_date = pd.Timestamp(expiry).date()
    except (TypeError, ValueError):
        return ExpiryContext(None, None, None, "UNAVAILABLE", "Expiry invalid")

    expiry_at = datetime.combine(expiry_date, time(15, 30), tzinfo=captured_at.tzinfo)
    remaining = max(0, int((expiry_at - captured_at).total_seconds() // 60))
    days = max(0, (expiry_date - captured_at.date()).days)
    if days == 0:
        regime = "EXPIRY DAY"
        label = f"Expiry today • {remaining} min left"
    elif days == 1:
        regime = "ONE DAY TO EXPIRY"
        label = "1 calendar day to expiry"
    else:
        regime = "NON-EXPIRY"
        label = f"{days} calendar days to expiry"
    return ExpiryContext(expiry_at, days, remaining, regime, label)


def directional_intent(*, side: str, position: str) -> str:
    pair = (str(side).upper(), str(position).upper())
    if pair in {("CE", "BUY"), ("PE", "SELL")}:
        return "BULLISH"
    if pair in {("PE", "BUY"), ("CE", "SELL")}:
        return "BEARISH"
    raise ValueError("CE/PE aur BUY/SELL combination valid nahi hai")


def stop_reference(*, barrier_map: BarrierMap, direction: str) -> BarrierMapLevel:
    level = (
        barrier_map.nearest_support
        if direction == "BULLISH"
        else barrier_map.nearest_resistance
    )
    if level is None:
        raise ValueError("Structural SL ke liye nearest barrier available nahi hai")
    return level


def stop_buffer_points(
    *, atr3: float | None, zone_width: float | None, expiry: ExpiryContext
) -> float:
    atr = max(0.0, _number(atr3) or 0.0)
    width = max(0.0, _number(zone_width) or 0.0)
    atr_fraction = 0.30 if expiry.regime == "EXPIRY DAY" else 0.20
    return round(max(3.0, atr * atr_fraction, width * 0.25), 2)


def stop_spot_price(
    *, level: BarrierMapLevel, direction: str, buffer_points: float
) -> float:
    if direction == "BULLISH":
        return round(max(0.05, float(level.lower) - buffer_points), 2)
    return round(float(level.upper) + buffer_points, 2)


def conservative_premium(
    estimate: PremiumRangeEstimate, *, position: str, is_stop: bool
) -> float:
    """Use the executable-side edge of the estimate, not its optimistic midpoint."""

    position = str(position).upper()
    if is_stop:
        value = estimate.low_price if position == "BUY" else estimate.high_price
    else:
        value = estimate.low_price if position == "BUY" else estimate.high_price
    return round(max(0.0, float(value)), 2)


def position_pnl_points(*, position: str, entry: float, exit_price: float) -> float:
    return (
        float(exit_price) - float(entry)
        if str(position).upper() == "BUY"
        else float(entry) - float(exit_price)
    )


def target_action(risk_reward: float | None) -> str:
    if risk_reward is None:
        return "CHECK"
    if risk_reward >= 2.0:
        return "STRONG TARGET"
    if risk_reward >= 1.5:
        return "ACCEPTABLE"
    if risk_reward >= 1.0:
        return "MARGINAL"
    return "PARTIAL ONLY"


def build_sl_target_plan(
    *,
    side: str,
    position: str,
    entry_premium: float,
    lot_size: int,
    lots: int,
    entry_spot: float,
    current_spot: float,
    barrier_map: BarrierMap,
    atr3: float | None,
    zone_width: float | None,
    expiry: ExpiryContext,
    stop_estimate: PremiumRangeEstimate,
    target_estimates: list[tuple[str, float, PremiumRangeEstimate, int]],
    holding_limit_minutes: int,
) -> SLTargetPlan:
    direction = directional_intent(side=side, position=position)
    reference = stop_reference(barrier_map=barrier_map, direction=direction)
    buffer_points = stop_buffer_points(
        atr3=atr3, zone_width=zone_width, expiry=expiry
    )
    stop_spot = stop_spot_price(
        level=reference, direction=direction, buffer_points=buffer_points
    )
    stop_premium = conservative_premium(
        stop_estimate, position=position, is_stop=True
    )
    stop_pnl = position_pnl_points(
        position=position, entry=entry_premium, exit_price=stop_premium
    )
    risk_per_quantity = max(0.0, -stop_pnl)
    total_risk = risk_per_quantity * int(lot_size) * int(lots)

    warnings: list[str] = []
    if risk_per_quantity <= 0.05:
        warnings.append("Calculated structural stop entry ko loss side par invalidate nahi karta")
    if direction == "BULLISH" and entry_spot < reference.lower:
        warnings.append("Entry NIFTY current bullish support ke neeche recorded hai")
    if direction == "BEARISH" and entry_spot > reference.upper:
        warnings.append("Entry NIFTY current bearish resistance ke upar recorded hai")

    targets: list[PlannerLevel] = []
    eta_highs: list[int] = []
    for label, spot, estimate, eta_high in target_estimates:
        exit_price = conservative_premium(
            estimate, position=position, is_stop=False
        )
        pnl_points = position_pnl_points(
            position=position, entry=entry_premium, exit_price=exit_price
        )
        reward = max(0.0, pnl_points)
        rr = reward / risk_per_quantity if risk_per_quantity > 0.05 else None
        targets.append(
            PlannerLevel(
                label=label,
                spot=round(float(spot), 2),
                premium=exit_price,
                pnl_per_quantity=round(pnl_points, 2),
                total_pnl=round(pnl_points * lot_size * lots, 2),
                risk_reward=round(rr, 2) if rr is not None else None,
                action=target_action(rr),
            )
        )
        if reward > 0:
            eta_highs.append(max(1, int(eta_high)))

    best_rr = max(
        (item.risk_reward or 0.0 for item in targets),
        default=0.0,
    )
    if best_rr >= 2.0:
        verdict = "ROOM ACHHA • T1 PARTIAL, T2 TRAIL"
    elif best_rr >= 1.5:
        verdict = "TRADE ROOM ACCEPTABLE"
    elif best_rr >= 1.0:
        verdict = "MARGINAL • ENTRY/QUANTITY CHECK"
    else:
        verdict = "REWARD CHHOTA • TRADE AVOID/REWORK"

    eta_limit = min(eta_highs) if eta_highs else holding_limit_minutes
    if expiry.regime == "EXPIRY DAY":
        eta_limit = min(eta_limit, 30)
    time_exit = max(3, min(int(holding_limit_minutes), int(eta_limit)))
    if expiry.minutes_remaining is not None:
        time_exit = min(time_exit, max(0, expiry.minutes_remaining))

    if expiry.regime == "EXPIRY DAY":
        warnings.append("Expiry day: Gamma/Theta fast; time-exit strict rakho")
    elif expiry.regime == "ONE DAY TO EXPIRY":
        warnings.append("Overnight hold mein gap aur Theta risk alag ho sakta hai")

    return SLTargetPlan(
        direction=direction,
        stop_spot=stop_spot,
        stop_buffer_points=buffer_points,
        stop_level_label=reference.label,
        stop_premium=stop_premium,
        risk_per_quantity=round(risk_per_quantity, 2),
        total_risk=round(total_risk, 2),
        targets=tuple(targets),
        time_exit_minutes=time_exit,
        verdict=verdict,
        warnings=tuple(warnings),
    )


def lots_for_max_loss(
    *, max_loss_rupees: float, risk_per_quantity: float, lot_size: int
) -> int:
    denominator = float(risk_per_quantity) * int(lot_size)
    if denominator <= 0:
        return 0
    return max(0, floor(float(max_loss_rupees) / denominator))
