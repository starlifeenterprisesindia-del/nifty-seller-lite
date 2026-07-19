from __future__ import annotations

import math
from datetime import datetime
from typing import Iterable

from config import CONFIG
from models import (
    DisciplineState,
    ExecutionGuard,
    FeedStatus,
    FinalDecision,
    MarketSession,
    PriceActionBundle,
    RiskProfile,
    SetupPlan,
    TradePlanBundle,
)


def _selected_plan(bundle: TradePlanBundle) -> SetupPlan | None:
    return {
        "CE SELL": bundle.ce_sell,
        "PE SELL": bundle.pe_sell,
        "IRON CONDOR": bundle.iron_condor,
    }.get(bundle.selected_setup)


def _fresh_live(feed_status: dict[str, FeedStatus], key: str) -> bool:
    item = feed_status.get(key)
    return bool(item and item.ok and item.use_state == "LIVE")


def _confirmation_count(
    history: Iterable[dict[str, object]], action: str, as_of: datetime
) -> int:
    if action == "WAIT":
        return 0
    samples = list(history)
    count = 0
    next_time = as_of
    for sample in reversed(samples):
        if str(sample.get("action") or "").upper() != action:
            break
        if str(sample.get("execution_status") or "").upper() not in {
            "READY",
            "REFERENCE ONLY",
        }:
            break
        try:
            captured_at = datetime.fromisoformat(str(sample.get("captured_at")))
        except Exception:
            break
        gap = max(0.0, (next_time - captured_at).total_seconds())
        if gap > CONFIG.discipline_signal_max_gap_seconds:
            break
        count += 1
        next_time = captured_at
    return count


def _entry_window(profile: RiskProfile, as_of: datetime) -> tuple[str, bool, bool]:
    current = as_of.timetz().replace(tzinfo=None)
    if current < profile.entry_start:
        return (
            f"BEFORE WINDOW ({profile.entry_start.strftime('%H:%M')}–{profile.entry_end.strftime('%H:%M')})",
            False,
            False,
        )
    if current <= profile.entry_end:
        return (
            f"OPEN ({profile.entry_start.strftime('%H:%M')}–{profile.entry_end.strftime('%H:%M')})",
            True,
            False,
        )
    return (
        f"CLOSED AFTER {profile.entry_end.strftime('%H:%M')}",
        False,
        True,
    )


def _spot_invalidations(
    setup: str,
    plan: SetupPlan | None,
    price_action: PriceActionBundle,
) -> tuple[float | None, float | None]:
    three = price_action.three_minute
    fifteen = price_action.fifteen_minute
    if setup == "PE SELL":
        values = [
            item
            for item in (three.invalidation_level, fifteen.invalidation_level)
            if item is not None
        ]
        return (max(values) if values else None, None)
    if setup == "CE SELL":
        values = [
            item
            for item in (three.invalidation_level, fifteen.invalidation_level)
            if item is not None
        ]
        return (None, min(values) if values else None)
    if setup == "IRON CONDOR" and plan is not None:
        return plan.lower_breakeven, plan.upper_breakeven
    return None, None


def _risk_math(
    plan: SetupPlan | None, profile: RiskProfile
) -> tuple[
    float | None,
    int,
    int,
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
]:
    if (
        plan is None
        or not plan.available
        or plan.max_risk_points is None
        or plan.estimated_credit_points is None
        or profile.lot_size <= 0
    ):
        return (None, 0, 0, None, None, None, None, None, None)

    risk_per_lot = max(0.0, float(plan.max_risk_points)) * profile.lot_size
    max_by_budget = (
        math.floor(profile.risk_budget_rupees / risk_per_lot) if risk_per_lot > 0 else 0
    )
    allowed_lots = max(0, min(max_by_budget, max(0, profile.max_lots_cap)))

    credit = max(0.0, float(plan.estimated_credit_points))
    target_capture = credit * max(0.0, profile.target_capture_pct) / 100.0
    target_exit_debit = max(0.0, credit - target_capture)
    configured_stop = credit * max(0.0, profile.stop_loss_pct) / 100.0
    stop_loss_points = min(configured_stop, max(0.0, float(plan.max_risk_points)))
    stop_exit_debit = credit + stop_loss_points
    target_rupees = target_capture * profile.lot_size * allowed_lots
    stop_rupees = stop_loss_points * profile.lot_size * allowed_lots
    return (
        round(risk_per_lot, 2),
        max_by_budget,
        allowed_lots,
        round(target_capture, 2),
        round(target_exit_debit, 2),
        round(target_rupees, 2),
        round(stop_loss_points, 2),
        round(stop_exit_debit, 2),
        round(stop_rupees, 2),
    )


def calculate_execution_guard(
    *,
    decision: FinalDecision,
    trade_plan: TradePlanBundle,
    market_session: MarketSession,
    price_action: PriceActionBundle,
    risk_profile: RiskProfile,
    discipline_state: DisciplineState,
    feed_status: dict[str, FeedStatus],
    as_of: datetime,
) -> ExecutionGuard:
    """Convert the chosen setup into a read-only entry/risk gate.

    This module cannot select CE/PE/Condor, change strategy scores or place orders.
    It only applies freshness, persistence, timing, risk-budget and one-trade rules
    to the already-selected final action and protected plan.
    """

    plan = _selected_plan(trade_plan)
    setup = trade_plan.selected_setup
    confirmations = _confirmation_count(
        discipline_state.signal_history, decision.final_action, as_of
    )
    if confirmations >= CONFIG.execution_required_confirmations:
        signal_state = f"CONFIRMED ×{confirmations}"
    elif confirmations > 0:
        signal_state = (
            f"WARMING UP {confirmations}/{CONFIG.execution_required_confirmations}"
        )
    else:
        signal_state = "NOT CONFIRMED"

    entry_window, within_window, after_window = _entry_window(risk_profile, as_of)
    (
        risk_per_lot,
        max_lots_by_budget,
        allowed_lots,
        target_capture_points,
        target_exit_debit_points,
        target_profit_rupees,
        stop_loss_points,
        stop_exit_debit_points,
        stop_loss_rupees,
    ) = _risk_math(plan, risk_profile)
    invalidation_low, invalidation_high = _spot_invalidations(setup, plan, price_action)

    blockers: list[str] = []
    reasons: list[str] = []

    if not market_session.is_live:
        readiness = "REFERENCE ONLY"
        blockers.append("Market session is reference-only")
    else:
        if decision.final_action == "WAIT":
            blockers.append(f"Final one-brain action is WAIT: {decision.blocker}")
        if setup == "WAIT" or plan is None:
            blockers.append("No protected setup is selected")
        elif plan.status != "READY":
            blockers.append(f"Protected setup is not READY: {plan.blocker}")
        if not _fresh_live(feed_status, "quotes"):
            blockers.append("NIFTY quote is not confirmed live")
        if not _fresh_live(feed_status, "candles"):
            blockers.append("Completed candles are not confirmed live")
        if not _fresh_live(feed_status, "option_chain"):
            blockers.append("Option chain is not confirmed live")
        if confirmations < CONFIG.execution_required_confirmations:
            blockers.append("Final action needs consecutive fresh confirmations")
        if discipline_state.trades_taken >= 1 or discipline_state.day_locked:
            outcome = discipline_state.last_outcome or "ONE TRADE USED"
            blockers.append(f"One-trade day is locked: {outcome}")
        if after_window:
            blockers.append("New-entry window has closed")
        elif not within_window:
            reasons.append("Wait for the configured entry window")
        if risk_per_lot is None:
            blockers.append("Protected-plan risk could not be calculated")
        elif allowed_lots < 1:
            blockers.append("Risk budget does not permit even one protected lot")

        hard_block = bool(blockers)
        if hard_block:
            readiness = "BLOCKED"
        elif not within_window:
            readiness = "WATCH"
        else:
            readiness = "ENTRY READY"

    if plan is not None and plan.available:
        reasons.extend(
            [
                f"Protected {setup} plan quality {plan.quality_score:.1f}/100",
                f"Risk budget ₹{risk_profile.risk_budget_rupees:,.0f}",
                f"One-trade cap allows {allowed_lots} lot(s)",
            ]
        )
    if confirmations:
        reasons.append(signal_state)

    # Keep UI compact and deterministic.
    unique_reasons = tuple(dict.fromkeys(item for item in reasons if item))[:4]
    unique_blockers = tuple(dict.fromkeys(item for item in blockers if item))[:6]

    return ExecutionGuard(
        as_of=as_of,
        selected_setup=setup,
        readiness=readiness,
        signal_state=signal_state,
        confirmations=confirmations,
        required_confirmations=CONFIG.execution_required_confirmations,
        entry_window=entry_window,
        risk_budget_rupees=risk_profile.risk_budget_rupees,
        risk_per_lot_rupees=risk_per_lot,
        allowed_lots=allowed_lots,
        max_lots_by_budget=max_lots_by_budget,
        max_lots_cap=risk_profile.max_lots_cap,
        target_capture_points=target_capture_points,
        target_exit_debit_points=target_exit_debit_points,
        target_profit_rupees=target_profit_rupees,
        stop_loss_points=stop_loss_points,
        stop_exit_debit_points=stop_exit_debit_points,
        stop_loss_rupees=stop_loss_rupees,
        forced_exit_time=risk_profile.forced_exit.strftime("%H:%M"),
        spot_invalidation_low=invalidation_low,
        spot_invalidation_high=invalidation_high,
        trade_taken_today=discipline_state.trades_taken >= 1,
        day_locked=discipline_state.day_locked,
        reasons=unique_reasons,
        blockers=unique_blockers,
        status=readiness,
    )
