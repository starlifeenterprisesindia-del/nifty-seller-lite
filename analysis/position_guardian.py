from __future__ import annotations

from datetime import datetime, time
from typing import Any

import pandas as pd

from analysis.technical_utils import clamp
from config import CONFIG
from models import (
    DisciplineState,
    ExecutionGuard,
    FinalDecision,
    MarketSession,
    PositionGuardian,
    PositionLegMonitor,
    SetupPlan,
    TradePlanBundle,
)


def _selected_plan(bundle: TradePlanBundle) -> SetupPlan | None:
    return {
        "CE SELL": bundle.ce_sell,
        "PE SELL": bundle.pe_sell,
        "IRON CONDOR": bundle.iron_condor,
    }.get(bundle.selected_setup)


def _entry_price(role: str, leg: Any) -> float | None:
    if role == "SHORT":
        return leg.bid if leg.bid is not None and leg.bid > 0 else leg.last_price
    return leg.ask if leg.ask is not None and leg.ask > 0 else leg.last_price


def create_trade_record(
    *,
    captured_at: datetime,
    decision: FinalDecision,
    trade_plan: TradePlanBundle,
    execution_guard: ExecutionGuard,
    lots: int,
    lot_size: int,
    spot: float | None,
) -> dict[str, Any]:
    """Freeze the protected setup actually marked by the user.

    The record is local journal data only. It contains no broker order identifiers and
    cannot place or modify an order.
    """

    plan = _selected_plan(trade_plan)
    if execution_guard.readiness != "ENTRY READY":
        raise ValueError("Only an ENTRY READY setup can be marked as taken")
    if decision.final_action == "WAIT" or plan is None or not plan.available:
        raise ValueError("No protected setup is available to record")
    if lots < 1 or lots > execution_guard.allowed_lots:
        raise ValueError("Selected lots exceed the execution guard allowance")
    if lot_size < 1:
        raise ValueError("Lot size must be positive")

    legs: list[dict[str, Any]] = []
    for role, collection in (("SHORT", plan.short_legs), ("HEDGE", plan.hedge_legs)):
        for leg in collection:
            price = _entry_price(role, leg)
            if price is None or price <= 0:
                raise ValueError("A selected leg is missing an executable entry price")
            legs.append(
                {
                    "role": role,
                    "side": leg.side,
                    "strike": float(leg.strike),
                    "entry_price": float(price),
                }
            )

    return {
        "schema_version": 1,
        "status": "OPEN",
        "opened_at": captured_at.isoformat(),
        "closed_at": "",
        "action": decision.final_action,
        "setup": trade_plan.selected_setup,
        "expiry": trade_plan.expiry or "",
        "entry_spot": float(spot) if spot is not None else None,
        "lots": int(lots),
        "lot_size": int(lot_size),
        # Freeze the planner's exact conservative entry-credit estimate instead of
        # reconstructing it from rounded target fields.
        "entry_credit_points": plan.estimated_credit_points,
        "max_risk_points": plan.max_risk_points,
        "target_capture_points": execution_guard.target_capture_points,
        "target_exit_debit_points": execution_guard.target_exit_debit_points,
        "stop_loss_points": execution_guard.stop_loss_points,
        "stop_exit_debit_points": execution_guard.stop_exit_debit_points,
        "forced_exit_time": execution_guard.forced_exit_time,
        "spot_invalidation_low": execution_guard.spot_invalidation_low,
        "spot_invalidation_high": execution_guard.spot_invalidation_high,
        "legs": legs,
        "exit_debit_points": None,
        "realized_pnl_rupees": None,
    }


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def _find_row(frame: pd.DataFrame, side: str, strike: float) -> pd.Series | None:
    if frame.empty or not {"side", "strike"}.issubset(frame.columns):
        return None
    strike_values = pd.to_numeric(frame["strike"], errors="coerce")
    rows = frame[
        frame["side"].astype(str).str.upper().eq(side.upper())
        & strike_values.sub(float(strike)).abs().le(0.01)
    ]
    return None if rows.empty else rows.iloc[0]


def _close_price(role: str, row: pd.Series) -> float | None:
    if role == "SHORT":
        return _number(row.get("top_ask_price")) or _number(row.get("last_price"))
    return _number(row.get("top_bid_price")) or _number(row.get("last_price"))


def _forced_exit_reached(as_of: datetime, raw: Any) -> bool:
    try:
        parsed = time.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return False
    current = as_of.timetz().replace(tzinfo=None)
    return current >= parsed


def _closed_guardian(
    *, record: dict[str, Any], as_of: datetime, current_spot: float | None
) -> PositionGuardian:
    pnl = _number(record.get("realized_pnl_rupees"))
    debit = _number(record.get("exit_debit_points"))
    outcome = str(record.get("status") or "CLOSED").upper()
    return PositionGuardian(
        as_of=as_of,
        action=str(record.get("action") or ""),
        expiry=str(record.get("expiry") or "") or None,
        opened_at=str(record.get("opened_at") or ""),
        lots=max(0, int(record.get("lots") or 0)),
        lot_size=max(0, int(record.get("lot_size") or 0)),
        entry_spot=_number(record.get("entry_spot")),
        current_spot=current_spot,
        entry_credit_points=_number(record.get("entry_credit_points")),
        current_debit_points=debit,
        unrealized_pnl_points=None,
        unrealized_pnl_rupees=pnl,
        target_exit_debit_points=_number(record.get("target_exit_debit_points")),
        stop_exit_debit_points=_number(record.get("stop_exit_debit_points")),
        target_progress_pct=None,
        forced_exit_time=str(record.get("forced_exit_time") or ""),
        spot_invalidation_low=_number(record.get("spot_invalidation_low")),
        spot_invalidation_high=_number(record.get("spot_invalidation_high")),
        instruction=outcome,
        legs=(),
        reasons=("Trade outcome was manually recorded",),
        blockers=(),
        status="CLOSED",
    )


def calculate_position_guardian(
    *,
    discipline_state: DisciplineState,
    option_chain: pd.DataFrame,
    current_expiry: str | None,
    current_spot: float | None,
    market_session: MarketSession,
    option_chain_live: bool,
    as_of: datetime,
) -> PositionGuardian:
    """Monitor an already-marked protected trade from the current snapshot.

    This module cannot select a strategy, alter the final brain, or place/exit an
    order. It only calculates current protected-combination debit, P&L and deterministic
    target/SL/time/invalidation alerts for the user's manual journal entry.
    """

    record = discipline_state.trade_record
    if not isinstance(record, dict):
        return PositionGuardian.idle(as_of=as_of, current_spot=current_spot)
    if str(record.get("status") or "").upper() != "OPEN":
        return _closed_guardian(record=record, as_of=as_of, current_spot=current_spot)

    action = str(record.get("action") or "")
    expiry = str(record.get("expiry") or "") or None
    lots = max(0, int(record.get("lots") or 0))
    lot_size = max(0, int(record.get("lot_size") or 0))
    entry_credit = _number(record.get("entry_credit_points"))
    target_exit = _number(record.get("target_exit_debit_points"))
    stop_exit = _number(record.get("stop_exit_debit_points"))
    invalidation_low = _number(record.get("spot_invalidation_low"))
    invalidation_high = _number(record.get("spot_invalidation_high"))
    legs_raw = record.get("legs") if isinstance(record.get("legs"), list) else []

    blockers: list[str] = []
    reasons: list[str] = []
    if expiry and current_expiry and expiry != current_expiry:
        blockers.append("Current option-chain expiry does not match the open trade")
    if not legs_raw:
        blockers.append("Open-trade leg record is missing")
    if lots < 1 or lot_size < 1:
        blockers.append("Open-trade lot information is invalid")
    if entry_credit is None:
        blockers.append("Entry credit is missing")

    monitored_legs: list[PositionLegMonitor] = []
    short_cost = 0.0
    hedge_value = 0.0
    for item in legs_raw:
        if not isinstance(item, dict):
            blockers.append("A stored trade leg is invalid")
            continue
        role = str(item.get("role") or "").upper()
        side = str(item.get("side") or "").upper()
        strike = _number(item.get("strike"))
        entry_price = _number(item.get("entry_price"))
        if role not in {"SHORT", "HEDGE"} or side not in {"CE", "PE"}:
            blockers.append("A stored trade leg has an invalid role or side")
            continue
        if strike is None or entry_price is None:
            blockers.append("A stored trade leg is missing strike or entry price")
            continue
        row = _find_row(option_chain, side, strike)
        current_price = _close_price(role, row) if row is not None else None
        if current_price is None:
            blockers.append(f"Current price missing for {strike:,.0f} {side} {role}")
            contribution = None
            status = "MISSING"
        elif role == "SHORT":
            short_cost += current_price
            contribution = entry_price - current_price
            status = "READY"
        else:
            hedge_value += current_price
            contribution = current_price - entry_price
            status = "READY"
        monitored_legs.append(
            PositionLegMonitor(
                role=role,
                side=side,
                strike=strike,
                entry_price=entry_price,
                current_price=current_price,
                pnl_contribution_points=round(contribution, 2)
                if contribution is not None
                else None,
                status=status,
            )
        )

    current_debit = None
    pnl_points = None
    pnl_rupees = None
    progress = None
    if not blockers and entry_credit is not None:
        current_debit = round(max(0.0, short_cost - hedge_value), 2)
        pnl_points = round(entry_credit - current_debit, 2)
        pnl_rupees = round(pnl_points * lot_size * lots, 2)
        target_capture = _number(record.get("target_capture_points"))
        if target_capture is not None and target_capture > 0:
            progress = round(
                clamp(pnl_points / target_capture * 100.0, -200.0, 200.0), 1
            )

    if blockers:
        instruction = "DATA BLOCKED"
        status = "DATA BLOCKED"
    elif not market_session.is_live:
        instruction = "REFERENCE ONLY"
        status = "REFERENCE ONLY"
        reasons.append("Market session is not live")
    elif not option_chain_live:
        instruction = "DATA BLOCKED"
        status = "DATA BLOCKED"
        blockers.append("Option chain is not confirmed live")
    elif _forced_exit_reached(as_of, record.get("forced_exit_time")):
        instruction = "EXIT NOW — TIME"
        status = "EXIT ALERT"
        reasons.append("Compulsory exit time has been reached")
    elif (
        invalidation_low is not None
        and current_spot is not None
        and current_spot <= invalidation_low
    ) or (
        invalidation_high is not None
        and current_spot is not None
        and current_spot >= invalidation_high
    ):
        instruction = "EXIT / REVIEW — SPOT INVALIDATION"
        status = "EXIT ALERT"
        reasons.append("NIFTY crossed the stored spot invalidation")
    elif (
        stop_exit is not None
        and current_debit is not None
        and current_debit >= stop_exit
    ):
        instruction = "SL TRIGGERED"
        status = "EXIT ALERT"
        reasons.append("Protected-combination debit reached the stored SL threshold")
    elif (
        target_exit is not None
        and current_debit is not None
        and current_debit <= target_exit
    ):
        instruction = "TARGET REACHED"
        status = "TARGET ALERT"
        reasons.append("Protected-combination debit reached the stored target")
    elif progress is not None and progress >= CONFIG.position_profit_protect_pct:
        instruction = "PROTECT PROFIT"
        status = "MANAGE"
        reasons.append("Most of the configured target has been captured")
    elif progress is not None and progress <= CONFIG.position_risk_warning_pct:
        instruction = "RISK RISING"
        status = "MANAGE"
        reasons.append("Loss has used most of the configured spread-loss allowance")
    else:
        instruction = "HOLD / MONITOR"
        status = "OPEN"
        reasons.append("Target, SL, time and spot invalidation remain untriggered")

    return PositionGuardian(
        as_of=as_of,
        action=action,
        expiry=expiry,
        opened_at=str(record.get("opened_at") or ""),
        lots=lots,
        lot_size=lot_size,
        entry_spot=_number(record.get("entry_spot")),
        current_spot=current_spot,
        entry_credit_points=entry_credit,
        current_debit_points=current_debit,
        unrealized_pnl_points=pnl_points,
        unrealized_pnl_rupees=pnl_rupees,
        target_exit_debit_points=target_exit,
        stop_exit_debit_points=stop_exit,
        target_progress_pct=progress,
        forced_exit_time=str(record.get("forced_exit_time") or ""),
        spot_invalidation_low=invalidation_low,
        spot_invalidation_high=invalidation_high,
        instruction=instruction,
        legs=tuple(monitored_legs),
        reasons=tuple(dict.fromkeys(reasons))[:4],
        blockers=tuple(dict.fromkeys(blockers))[:6],
        status=status,
    )
