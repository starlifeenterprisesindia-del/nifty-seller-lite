from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from math import isfinite
import pandas as pd
from analysis.indicators import _rsi_wilder


@dataclass(frozen=True)
class RsiReversalSetup:
    action: str
    status: str
    zone: str
    confidence: int
    rsi_now: float | None
    rsi_previous: float | None
    trigger_text: str
    barrier_text: str
    big_player_text: str
    structure_text: str
    market_sl_text: str
    money_sl_text: str
    suggested_lots: int
    reasons: tuple[str, ...]
    cautions: tuple[str, ...]


MONEY_STOP_RUPEES = 5_000.0
SELL_PREMIUM_STOP_PCT = 40.0
RSI_TOP = 70.0
RSI_BOTTOM = 30.0
MIN_BARRIER_STRENGTH = 60.0
MAX_BREAK_PRESSURE = 55.0
MIN_BIG_PLAYER_SCORE = 60.0
MIN_BIG_PLAYER_CONFIRMATIONS = 2


def _value_direction(previous: float, current: float) -> str:
    if current > previous:
        return "UP"
    if current < previous:
        return "DOWN"
    return "FLAT"


def _rsi(snapshot: Any | None, timeframe: str = "three_minute") -> float | None:
    if snapshot is None:
        return None
    value = getattr(
        getattr(getattr(snapshot, "indicators", None), timeframe, None),
        "rsi14",
        None,
    )
    return float(value) if value is not None else None


def _completed_rsi_pair(
    snapshot: Any, timeframe: str = "three_minute"
) -> tuple[float | None, float | None]:
    """Use adjacent completed bars, never adjacent UI refreshes."""
    frame_name = "candles_15m" if timeframe == "fifteen_minute" else "candles_3m"
    frame = getattr(snapshot, frame_name, None)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return _rsi(snapshot, timeframe), None
    frame = frame.copy()
    if "is_complete" not in frame or "timestamp" not in frame:
        return _rsi(snapshot, timeframe), None
    frame = (
        frame[frame.is_complete.fillna(False)]
        .sort_values("timestamp")
        .drop_duplicates("timestamp")
    )
    values = _rsi_wilder(pd.to_numeric(frame.close, errors="coerce").dropna()).dropna()
    if len(values) < 2:
        return _rsi(snapshot, timeframe), None
    return float(values.iloc[-1]), float(values.iloc[-2])


def _budget(snapshot: Any) -> float:
    profile = getattr(snapshot, "risk_profile", None)
    value = float(getattr(profile, "risk_budget_rupees", 0) or 0)
    return min(MONEY_STOP_RUPEES, value) if isfinite(value) and value > 0 else 0.0


def _safety_blockers(snapshot: Any) -> list[str]:
    blockers = []
    feeds = getattr(snapshot, "feed_status", {})
    for key in ("quotes", "candles", "future_volume", "option_chain"):
        feed = feeds.get(key) if isinstance(feeds, dict) else None
        if not feed or not feed.ok or feed.use_state != "LIVE":
            blockers.append(f"{key}: fresh live data required")
    now = getattr(snapshot, "created_at", None)
    profile = getattr(snapshot, "risk_profile", None)
    if now is None or profile is None:
        blockers.append("Risk profile / timestamp unavailable")
    else:
        start, end = (
            getattr(profile, "entry_start", None),
            getattr(profile, "entry_end", None),
        )
        if (
            start is None
            or end is None
            or not start <= now.time().replace(tzinfo=None) <= end
        ):
            blockers.append("Entry window closed")
        indicator = getattr(getattr(snapshot, "indicators", None), "three_minute", None)
        stamp = getattr(indicator, "as_of", None)
        if stamp is None or not 180 <= (now - stamp).total_seconds() <= 420:
            blockers.append("Completed 3m RSI stale / unavailable")
    discipline = getattr(snapshot, "discipline_state", None)
    if discipline is None or discipline.day_locked or discipline.trades_taken:
        blockers.append("Trade/day lock active or unavailable")
    return blockers


def _strong_barrier(level: Any | None) -> bool:
    if level is None:
        return False
    raw_strength = getattr(level, "strength", None)
    raw_pressure = getattr(level, "break_pressure", None)
    strength = float(raw_strength) if raw_strength is not None else 0.0
    pressure = float(raw_pressure) if raw_pressure is not None else 100.0
    return (
        strength >= MIN_BARRIER_STRENGTH
        and pressure <= MAX_BREAK_PRESSURE
        and strength - pressure >= 10.0
    )


def _near_barrier(level: Any | None) -> bool:
    if level is None:
        return False
    lower = float(getattr(level, "lower", 0.0) or 0.0)
    upper = float(getattr(level, "upper", lower) or lower)
    raw_distance = getattr(level, "distance_points", None)
    distance = abs(float(raw_distance)) if raw_distance is not None else 9999.0
    return distance <= max(30.0, abs(upper - lower) + 15.0)


def _level_text(level: Any | None, fallback: str) -> str:
    if level is None:
        return f"{fallback} unresolved"
    return (
        f"{float(level.lower):,.0f}–{float(level.upper):,.0f} · "
        f"Strength {float(level.strength):.0f} · "
        f"Break {float(level.break_pressure):.0f}"
    )


def _plan(snapshot: Any, action: str) -> Any | None:
    bundle = getattr(snapshot, "trade_plan", None)
    if bundle is None:
        return None
    return {
        "CE SELL": getattr(bundle, "ce_sell", None),
        "PE SELL": getattr(bundle, "pe_sell", None),
        "IRON CONDOR": getattr(bundle, "iron_condor", None),
    }.get(action)


def _leg_text(legs: tuple[Any, ...] | list[Any]) -> str:
    values = []
    for leg in legs or ():
        strike = getattr(leg, "strike", None)
        option_type = str(getattr(leg, "side", getattr(leg, "option_type", "")))
        if strike is not None:
            values.append(f"{float(strike):,.0f} {option_type}".strip())
    return " + ".join(values) or "—"


def _structure(plan: Any | None) -> str:
    if plan is None or not bool(getattr(plan, "available", False)):
        return "Protected strike abhi unresolved"
    short = _leg_text(getattr(plan, "short_legs", ()))
    hedge = _leg_text(getattr(plan, "hedge_legs", ()))
    return f"SELL {short} · HEDGE {hedge}"


def _suggested_lots(snapshot: Any, plan: Any | None) -> int:
    if plan is None or not bool(getattr(plan, "available", False)):
        return 0
    risk = float(getattr(plan, "max_risk_points", 0.0) or 0.0)
    lot_size = int(getattr(getattr(snapshot, "risk_profile", None), "lot_size", 0) or 0)
    if not isfinite(risk) or risk <= 0 or lot_size <= 0:
        return 0
    estimated_stop_per_lot = risk * lot_size * 1.10
    if estimated_stop_per_lot <= 0:
        return 0
    cap = int(getattr(snapshot.risk_profile, "max_lots_cap", 0) or 0)
    return max(0, min(cap, int(_budget(snapshot) // estimated_stop_per_lot)))


def _market_sl(action: str, resistance: Any | None, support: Any | None) -> str:
    if action == "CE SELL" and resistance is not None:
        return f"NIFTY {float(resistance.upper) + 20:,.0f} ke upar 3m close"
    if action == "PE SELL" and support is not None:
        return f"NIFTY {float(support.lower) - 20:,.0f} ke neeche 3m close"
    if action == "IRON CONDOR" and resistance is not None and support is not None:
        return (
            f"CE side {float(resistance.upper) + 20:,.0f} upar / "
            f"PE side {float(support.lower) - 20:,.0f} neeche 3m close"
        )
    return "Barrier invalidation unavailable"


def evaluate_rsi_reversal_setup(
    snapshot: Any, previous_snapshot: Any | None
) -> RsiReversalSetup:
    """Independent RSI reversal advisory; it never changes the One-Brain decision."""

    # The agreed architecture is 15m permission + 3m trigger.  A completed
    # 15m RSI extreme defines the reversal zone; adjacent completed 3m bars
    # only time the turn.  Legacy/test snapshots without a 15m frame fall back
    # to the old 3m pair instead of fabricating data.
    has_15m = isinstance(getattr(snapshot, "candles_15m", None), pd.DataFrame)
    permission_tf = "fifteen_minute" if has_15m else "three_minute"
    rsi_now, rsi_previous = _completed_rsi_pair(snapshot, permission_tf)
    trigger_now, trigger_previous = _completed_rsi_pair(snapshot, "three_minute")
    barrier_map = getattr(snapshot, "barrier_map", None)
    resistance = getattr(barrier_map, "nearest_resistance", None)
    support = getattr(barrier_map, "nearest_support", None)
    big_player = getattr(snapshot, "big_player_activity", None)

    top_seen = rsi_now is not None and rsi_now >= RSI_TOP
    bottom_seen = rsi_now is not None and rsi_now <= RSI_BOTTOM
    permission_top_turn = (
        rsi_now is not None
        and rsi_previous is not None
        and rsi_previous >= RSI_TOP
        and rsi_now < rsi_previous
    )
    permission_bottom_turn = (
        rsi_now is not None
        and rsi_previous is not None
        and rsi_previous <= RSI_BOTTOM
        and rsi_now > rsi_previous
    )
    zone = (
        "TOP"
        if top_seen or permission_top_turn
        else "BOTTOM"
        if bottom_seen or permission_bottom_turn
        else "NORMAL"
    )
    trigger_down = (
        trigger_now is not None
        and trigger_previous is not None
        and trigger_now < trigger_previous
    )
    trigger_up = (
        trigger_now is not None
        and trigger_previous is not None
        and trigger_now > trigger_previous
    )
    top_turn = permission_top_turn and trigger_down
    bottom_turn = permission_bottom_turn and trigger_up
    trigger_text = (
        f"3m RSI {_value_direction(trigger_previous, trigger_now)} · "
        f"{trigger_previous:.1f} → {trigger_now:.1f}"
        if trigger_now is not None and trigger_previous is not None
        else "3m trigger unavailable"
    )

    resistance_ready = _strong_barrier(resistance) and _near_barrier(resistance)
    support_ready = _strong_barrier(support) and _near_barrier(support)
    both_barriers = _strong_barrier(resistance) and _strong_barrier(support)
    spot = getattr(snapshot, "nifty_quote", {}).get("last_price")
    if spot is None or not isfinite(float(spot)):
        resistance_ready = support_ready = both_barriers = False
    else:
        resistance_ready = resistance_ready and float(spot) <= float(resistance.upper)
        support_ready = support_ready and float(spot) >= float(support.lower)
        both_barriers = both_barriers and float(support.lower) <= float(spot) <= float(resistance.upper)

    bp_direction = str(getattr(big_player, "direction", "MIXED") or "MIXED").upper()
    bp_score = float(getattr(big_player, "score", 0.0) or 0.0)
    bp_confirmations = int(getattr(big_player, "confirmation_count", 0) or 0)
    bp_ready = (
        str(getattr(big_player, "status", "")).upper() == "READY"
        and bp_score >= MIN_BIG_PLAYER_SCORE
        and bp_confirmations >= MIN_BIG_PLAYER_CONFIRMATIONS
    )
    selling_confirmed = bp_ready and bp_direction == "SELLING"
    buying_confirmed = bp_ready and bp_direction == "BUYING"
    mixed_flow = (
        str(getattr(big_player, "status", "")).upper() == "READY"
        and bp_direction == "MIXED"
        and str(getattr(getattr(barrier_map, "trading_range", None), "state", ""))
        == "STRONG RANGE"
    )

    reasons: list[str] = []
    cautions: list[str] = []
    action = "WAIT"
    confidence = 0

    if rsi_now is None:
        cautions.append("3m RSI unavailable")
    elif zone == "TOP":
        reasons.append(f"15m RSI top permission {rsi_now:.1f}")
        confidence += 30
        if permission_top_turn and trigger_down:
            reasons.append("15m top turn + 3m downward trigger")
            confidence += 20
        else:
            cautions.append("RSI abhi top se neeche mudna baaki hai")
        if resistance_ready:
            reasons.append("Upper Barrier + OI majboot aur paas hai")
            confidence += 25
        else:
            cautions.append("Upper Barrier + OI entry-ready nahi")
        if selling_confirmed:
            reasons.append("Big Player selling confirmed")
            confidence += 25
        elif buying_confirmed:
            cautions.append("Big Player buying CE Sell ke opposite hai")
        else:
            cautions.append("Big Player direction abhi confirm nahi")

        if top_turn and resistance_ready and selling_confirmed:
            action = "CE SELL"
        elif top_turn and both_barriers and mixed_flow:
            action = "IRON CONDOR"
            confidence = min(confidence, 69)
    elif zone == "BOTTOM":
        reasons.append(f"15m RSI bottom permission {rsi_now:.1f}")
        confidence += 30
        if permission_bottom_turn and trigger_up:
            reasons.append("15m bottom turn + 3m upward trigger")
            confidence += 20
        else:
            cautions.append("RSI abhi bottom se upar mudna baaki hai")
        if support_ready:
            reasons.append("Lower Barrier + OI majboot aur paas hai")
            confidence += 25
        else:
            cautions.append("Lower Barrier + OI entry-ready nahi")
        if buying_confirmed:
            reasons.append("Big Player buying confirmed")
            confidence += 25
        elif selling_confirmed:
            cautions.append("Big Player selling PE Sell ke opposite hai")
        else:
            cautions.append("Big Player direction abhi confirm nahi")

        if bottom_turn and support_ready and buying_confirmed:
            action = "PE SELL"
        elif bottom_turn and both_barriers and mixed_flow:
            action = "IRON CONDOR"
            confidence = min(confidence, 69)
    else:
        cautions.append("15m completed RSI top 70+ ya bottom 30- permission mein nahi")

    market_live = bool(
        getattr(getattr(snapshot, "market_session", None), "is_live", False)
    )
    plan = _plan(snapshot, action)
    safety = _safety_blockers(snapshot)
    option = getattr(snapshot, "option_intelligence", None)
    if option is None or option.status != "READY":
        safety.append("OI data not ready")
    if action != "WAIT" and safety:
        cautions.extend(safety)
        action, plan = "WAIT", None
    if plan is not None and any(
        getattr(leg, "delta", None) is None or getattr(leg, "status", "") != "READY"
        for leg in (*plan.short_legs, *plan.hedge_legs)
    ):
        cautions.append("Selected legs: verified Greeks/liquidity required")
        action, plan = "WAIT", None
    if action != "WAIT" and (
        plan is None or not bool(getattr(plan, "available", False))
    ):
        cautions.append(f"{action} ka protected strike/hedge available nahi")
        action = "WAIT"
        plan = None
    if not market_live:
        status = "REFERENCE ONLY"
    elif action == "WAIT":
        status = "WAIT"
    else:
        status = "ENTRY READY"

    if not market_live and action != "WAIT":
        cautions.append("Market live nahi hai; fresh entry nahi")

    confidence = max(0, min(100, confidence))
    lots = _suggested_lots(snapshot, plan)
    if action != "WAIT" and lots <= 0:
        cautions.append("₹5,000 SL budget mein 1 lot bhi safely fit nahi")
        action = "WAIT"
        status = "WAIT" if market_live else "REFERENCE ONLY"
        plan = None

    bp_text = (
        f"{bp_direction} {bp_score:.0f}/100 · {bp_confirmations}/"
        f"{max(MIN_BIG_PLAYER_CONFIRMATIONS, int(getattr(big_player, 'confirmation_total', 0) or 0))}"
        if big_player is not None
        else "Unavailable"
    )
    active_level = (
        resistance if zone == "TOP" else support if zone == "BOTTOM" else None
    )
    return RsiReversalSetup(
        action=action,
        status=status,
        zone=zone,
        confidence=confidence,
        rsi_now=rsi_now,
        rsi_previous=rsi_previous,
        trigger_text=trigger_text,
        barrier_text=(
            _level_text(active_level, "Barrier")
            if active_level is not None
            else "RSI permission ke baad relevant barrier evaluate hoga"
        ),
        big_player_text=bp_text,
        structure_text=(
            _structure(plan)
            if zone != "NORMAL"
            else "Entry gate pending — strike abhi select nahi hua"
        ),
        market_sl_text=(
            _market_sl(action, resistance, support)
            if zone != "NORMAL"
            else "Entry gate pending — SL abhi active nahi"
        ),
        money_sl_text=f"Loss alert budget ₹{_budget(snapshot):,.0f} · actual trade record required; auto-exit nahi",
        suggested_lots=lots if action != "WAIT" else 0,
        reasons=tuple(reasons),
        cautions=tuple(cautions),
    )


def create_rsi_trade_record(
    snapshot: Any, lots: int, entry_prices: list[float]
) -> dict:
    """Explicit manual fills only. Never places orders or guesses actual entries."""
    result = evaluate_rsi_reversal_setup(snapshot, None)
    if result.status != "ENTRY READY" or not 1 <= lots <= result.suggested_lots:
        raise ValueError("RSI entry/risk guard does not allow this quantity")
    plan = _plan(snapshot, result.action)
    legs = [("SHORT", x) for x in plan.short_legs] + [
        ("HEDGE", x) for x in plan.hedge_legs
    ]
    if len(entry_prices) != len(legs) or any(
        not isfinite(x) or x <= 0 for x in entry_prices
    ):
        raise ValueError("Enter actual positive fill prices for every leg")
    credit = sum(
        price if role == "SHORT" else -price
        for (role, _), price in zip(legs, entry_prices)
    )
    width = float(plan.width_points or 0)
    qty = lots * snapshot.risk_profile.lot_size
    if not 0 < credit < width or (width - credit) * qty * 1.10 > _budget(snapshot):
        raise ValueError("Actual fills exceed the conservative risk budget")
    barrier = snapshot.barrier_map
    low = (
        barrier.nearest_support.lower - 20
        if result.action in {"PE SELL", "IRON CONDOR"}
        else None
    )
    high = (
        barrier.nearest_resistance.upper + 20
        if result.action in {"CE SELL", "IRON CONDOR"}
        else None
    )
    return {
        "schema_version": 2,
        "status": "OPEN",
        "strategy": "RSI TOP BOTTOM",
        "action": result.action,
        "setup": result.action,
        "opened_at": snapshot.created_at.isoformat(),
        "expiry": snapshot.trade_plan.expiry,
        "entry_spot": snapshot.nifty_quote.get("last_price"),
        "lots": lots,
        "lot_size": snapshot.risk_profile.lot_size,
        "entry_credit_points": credit,
        "max_risk_points": width - credit,
        "money_stop_rupees": _budget(snapshot),
        "stop_exit_debit_points": credit + _budget(snapshot) / qty,
        "target_exit_debit_points": None,
        "target_capture_points": None,
        "forced_exit_time": snapshot.risk_profile.forced_exit.isoformat(),
        "spot_invalidation_low": low,
        "spot_invalidation_high": high,
        "barrier_close_required": True,
        "legs": [
            {"role": role, "side": leg.side, "strike": leg.strike, "entry_price": price}
            for (role, leg), price in zip(legs, entry_prices)
        ],
    }
