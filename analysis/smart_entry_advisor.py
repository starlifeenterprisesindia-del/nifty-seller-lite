"""Smart premium-entry advisory for the Premium Calculator.

Display/research layer only.  It reuses the authoritative MarketSnapshot, the
independent Strike Entry Planner and already-computed Market Intelligence metadata.
It performs no broker/API call and never changes One Brain, strategy selection,
Execution Guard or Position Guardian.

The advisor separates two questions that are easy to confuse in a fast market:

* PRICE QUALITY: is the selected option premium attractive relative to the planned
  spot retest, structural stop and next favorable barrier?
* MOVE URGENCY: is the move becoming strong enough that waiting for the ideal
  premium may cause the setup to escape?

W/M and strong-candle evidence are supportive only; they are never required.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any

import pandas as pd

from analysis.sl_target_planner import directional_intent, expiry_context
from analysis.spot_premium_calculator import calculate_target_premium


@dataclass(frozen=True)
class AdvisorEntry:
    entry_no: int
    premium: float
    lots: int
    condition: str


@dataclass(frozen=True)
class SmartEntryAdvisor:
    status: str
    status_tone: str
    current_executable_premium: float
    preferred_low: float | None
    preferred_high: float | None
    acceptable_low: float | None
    acceptable_high: float | None
    no_chase_level: float | None
    no_chase_relation: str
    reference_entry: float
    price_quality_score: float
    price_quality_state: str
    move_urgency_score: float
    move_urgency_state: str
    spot_zone: tuple[float, float] | None
    invalidation_spot: float | None
    target_spot: float | None
    target_premium: float | None
    stop_premium: float | None
    risk_reward_at_current: float | None
    hedge_strike: float | None
    current_net_credit: float | None
    preferred_net_credit_low: float | None
    preferred_net_credit_high: float | None
    minimum_net_credit: float | None
    institutional_state: str
    liquidity_magnet_bias: str
    liquidity_magnet_score: float | None
    ladder: tuple[AdvisorEntry, ...]
    reasons: tuple[str, ...]
    cautions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ladder"] = [asdict(item) for item in self.ladder]
        return data


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _tick(value: float, tick: float = 0.05) -> float:
    if value <= 0:
        return tick
    return round(round(value / tick) * tick, 2)


def _row(frame: pd.DataFrame, side: str, strike: float) -> pd.Series | None:
    if frame is None or frame.empty or not {"side", "strike"}.issubset(frame.columns):
        return None
    rows = frame[frame["side"].astype(str).str.upper().eq(str(side).upper())].copy()
    strikes = pd.to_numeric(rows["strike"], errors="coerce")
    exact = rows[strikes.sub(float(strike)).abs() < 0.01]
    return None if exact.empty else exact.iloc[0]


def _book_price(row: pd.Series | None, position: str, fallback: float) -> tuple[float, float | None, float | None]:
    if row is None:
        return fallback, None, None
    bid = _num(row.get("top_bid_price"))
    ask = _num(row.get("top_ask_price"))
    if bid is not None and ask is not None and 0 < bid <= ask:
        executable = bid if str(position).upper() == "SELL" else ask
        return float(executable), float(bid), float(ask)
    return fallback, bid, ask


def _choose_favorable_target(snapshot: Any, direction: str, spot: float) -> float | None:
    barrier = getattr(snapshot, "barrier_map", None)
    if barrier is None:
        return None
    attrs = ("nearest_resistance", "next_resistance") if direction == "BULLISH" else ("nearest_support", "next_support")
    for name in attrs:
        item = getattr(barrier, name, None)
        if item is None:
            continue
        midpoint = _num(getattr(item, "midpoint", None))
        if midpoint is None:
            lower = _num(getattr(item, "lower", None))
            upper = _num(getattr(item, "upper", None))
            if lower is not None and upper is not None:
                midpoint = (lower + upper) / 2.0
        if midpoint is None:
            continue
        if direction == "BULLISH" and midpoint > spot + 0.5:
            return midpoint
        if direction == "BEARISH" and midpoint < spot - 0.5:
            return midpoint
    return None


def _estimate_premium(snapshot: Any, *, side: str, position: str, strike: float,
                      current_spot: float, current_premium: float, target_spot: float,
                      lots: int, minutes: int = 3) -> tuple[float, float, float] | None:
    row = _row(snapshot.option_chain, side, strike)
    quality = str(row.get("greeks_quality", "") if row is not None else "").upper()
    if quality not in {"READY", "IV WARNING"}:
        return None
    feed = getattr(snapshot.feed_status.get("option_chain"), "use_state", "UNAVAILABLE")
    context = expiry_context(captured_at=snapshot.created_at, expiry=snapshot.expiry)
    try:
        result = calculate_target_premium(
            option_chain=snapshot.option_chain,
            side=side,
            position=position,
            strike=float(strike),
            current_spot=float(current_spot),
            current_premium=float(current_premium),
            entry_premium=float(current_premium),
            target_spot=float(target_spot),
            target_minutes=max(1, int(minutes)),
            lot_size=int(snapshot.risk_profile.lot_size),
            lots=max(1, int(lots)),
            feed_state=str(feed or "UNAVAILABLE"),
            iv_change_points=0.0,
            minutes_to_expiry=context.minutes_remaining,
        )
    except Exception:
        return None
    return float(result.low_price), float(result.best_price), float(result.high_price)


def _premium_rr_boundary(target: float | None, stop: float | None, rr: float) -> float | None:
    if target is None or stop is None or rr <= 0:
        return None
    # Same algebra for BUY and SELL; inequality direction is handled by the caller.
    return (float(target) + rr * float(stop)) / (1.0 + rr)


def _rr_at_entry(position: str, entry: float, target: float | None, stop: float | None) -> float | None:
    if target is None or stop is None:
        return None
    if str(position).upper() == "SELL":
        reward = entry - target
        risk = stop - entry
    else:
        reward = target - entry
        risk = entry - stop
    if reward <= 0 or risk <= 0:
        return 0.0
    return reward / risk


def _urgency(snapshot: Any, direction: str) -> tuple[float, str, str, str, float | None, list[str]]:
    mie = (getattr(snapshot, "metadata", {}) or {}).get("market_intelligence") or {}
    integrity = mie.get("pressure_integrity") if isinstance(mie.get("pressure_integrity"), dict) else {}
    institutional = mie.get("institutional_window") if isinstance(mie.get("institutional_window"), dict) else {}
    liquidity = mie.get("liquidity") if isinstance(mie.get("liquidity"), dict) else {}
    money = liquidity.get("money_concentration") if isinstance(liquidity.get("money_concentration"), dict) else {}

    expansion = _num(mie.get("expansion_pressure"), 0.0) or 0.0
    quality = _num(integrity.get("quality_score"), 0.0) or 0.0
    attack = _num(integrity.get("barrier_attack_score"), 0.0) or 0.0
    iw_state = str(institutional.get("state") or "CLOSED").upper()
    iw_score = _num(institutional.get("opportunity_score"), 0.0) or 0.0
    iw_component = {"STRONG": 100.0, "OPEN": 88.0, "FORMING": 62.0, "CLOSED": 28.0}.get(iw_state, 35.0)
    iw_component = iw_component * 0.65 + iw_score * 0.35

    money_bias = str(money.get("bias") or "UNCLEAR").upper()
    desired = "UPSIDE" if direction == "BULLISH" else "DOWNSIDE"
    if money_bias == desired:
        magnet_component = 85.0
        magnet_score = _num(money.get("upside_score" if desired == "UPSIDE" else "downside_score"))
    elif money_bias in {"UPSIDE", "DOWNSIDE"}:
        magnet_component = 25.0
        magnet_score = _num(money.get("upside_score" if money_bias == "UPSIDE" else "downside_score"))
    else:
        magnet_component = 50.0
        magnet_score = max(_num(money.get("upside_score"), 0.0) or 0.0, _num(money.get("downside_score"), 0.0) or 0.0)

    score = expansion * 0.25 + quality * 0.20 + attack * 0.20 + iw_component * 0.20 + magnet_component * 0.15
    move_attack = str(integrity.get("move_attack_state") or "NORMAL").upper()
    if move_attack in {"ATTACK", "BREAK / EXPANSION", "EXPANSION"}:
        score += 7.0
    elif move_attack in {"BUILDING", "WATCH"}:
        score += 3.0
    score = _clamp(score)
    state = "HIGH" if score >= 70 else "RISING" if score >= 50 else "LOW"
    reasons = [
        f"Move pressure {expansion:.0f}/100",
        f"Pressure quality {quality:.0f}/100",
        f"Barrier attack {attack:.0f}/100",
        f"Institutional Window {iw_state}",
        f"Liquidity Magnet {money_bias}",
    ]
    return round(score, 1), state, iw_state, money_bias, magnet_score, reasons


def _ladder(*, position: str, current: float, preferred_low: float | None,
            preferred_high: float | None, acceptable_low: float | None,
            acceptable_high: float | None, total_lots: int) -> tuple[AdvisorEntry, ...]:
    total_lots = max(1, int(total_lots))
    entries = min(3, total_lots)
    base, rem = divmod(total_lots, entries)
    if preferred_low is None or preferred_high is None:
        points = [current]
    elif str(position).upper() == "SELL":
        early = max(float(acceptable_low or preferred_low), min(current, preferred_low))
        points = [early, preferred_low, preferred_high]
    else:
        early = min(float(acceptable_high or preferred_high), max(current, preferred_high))
        points = [early, preferred_high, preferred_low]
    labels = (
        "Only if trigger/urgency is active",
        "Main entry around preferred zone",
        "Only if setup remains valid; never automatic averaging",
    )
    output: list[AdvisorEntry] = []
    for idx, price in enumerate(points[:entries]):
        lots = base + (1 if idx < rem else 0)
        output.append(AdvisorEntry(idx + 1, _tick(max(0.05, float(price))), lots, labels[idx]))
    return tuple(output)


def build_smart_entry_advisor(
    snapshot: Any,
    *,
    side: str,
    position: str,
    strike: float,
    lots: int,
    planner: Any,
    hedge_strike: float | None = None,
) -> SmartEntryAdvisor:
    """Build one zero-network advisory view for ``Plan new entry`` mode."""

    side = str(side).upper()
    position = str(position).upper()
    direction = directional_intent(side=side, position=position)
    spot = _num((getattr(snapshot, "nifty_quote", {}) or {}).get("last_price"), 0.0) or 0.0
    row = _row(snapshot.option_chain, side, strike)
    last = _num(row.get("last_price") if row is not None else None, 0.0) or 0.0
    executable, bid, ask = _book_price(row, position, last)
    spread = max(0.05, (ask - bid) if bid is not None and ask is not None else executable * 0.008)

    preferred: tuple[float, float] | None = None
    if getattr(planner, "premium_range", None):
        a, b = planner.premium_range
        preferred = (min(float(a), float(b)), max(float(a), float(b)))
    elif getattr(planner, "zone", None):
        target = sum(planner.zone) / 2.0
        estimate = _estimate_premium(
            snapshot, side=side, position=position, strike=strike,
            current_spot=spot, current_premium=executable, target_spot=target,
            lots=lots, minutes=3,
        )
        if estimate:
            preferred = (estimate[0], estimate[2])
    if preferred is None and executable > 0:
        # Book-only fallback is clearly lower-confidence; it never pretends to know
        # a better retest price when Greeks/scenario evidence is unavailable.
        preferred = (
            max(0.05, executable - spread * (0.6 if position == "BUY" else 0.1)),
            executable + spread * (0.1 if position == "BUY" else 0.6),
        )
    pref_low = _tick(preferred[0]) if preferred else None
    pref_high = _tick(preferred[1]) if preferred else None

    target_spot = _choose_favorable_target(snapshot, direction, spot)
    stop_spot = _num(getattr(planner, "invalidation", None))
    target_est = (
        _estimate_premium(snapshot, side=side, position=position, strike=strike,
                          current_spot=spot, current_premium=executable,
                          target_spot=target_spot, lots=lots, minutes=5)
        if target_spot is not None else None
    )
    stop_est = (
        _estimate_premium(snapshot, side=side, position=position, strike=strike,
                          current_spot=spot, current_premium=executable,
                          target_spot=stop_spot, lots=lots, minutes=3)
        if stop_spot is not None else None
    )
    if position == "SELL":
        target_premium = target_est[2] if target_est else None  # conservative high exit
        stop_premium = stop_est[2] if stop_est else None
    else:
        target_premium = target_est[0] if target_est else None  # conservative low exit
        stop_premium = stop_est[0] if stop_est else None

    acceptable_boundary = _premium_rr_boundary(target_premium, stop_premium, 0.80)
    no_chase_boundary = _premium_rr_boundary(target_premium, stop_premium, 0.50)
    if pref_low is not None and pref_high is not None:
        if position == "SELL":
            acceptable_low = _tick(min(pref_low, acceptable_boundary if acceptable_boundary is not None else pref_low - spread * 1.5))
            acceptable_high = pref_high
            no_chase = _tick(no_chase_boundary if no_chase_boundary is not None else acceptable_low - spread * 1.5)
            no_chase_relation = "BELOW"
        else:
            acceptable_low = pref_low
            acceptable_high = _tick(max(pref_high, acceptable_boundary if acceptable_boundary is not None else pref_high + spread * 1.5))
            no_chase = _tick(no_chase_boundary if no_chase_boundary is not None else acceptable_high + spread * 1.5)
            no_chase_relation = "ABOVE"
    else:
        acceptable_low = acceptable_high = no_chase = None
        no_chase_relation = ""

    if pref_low is None or pref_high is None:
        quality_score = 25.0
    elif pref_low <= executable <= pref_high:
        quality_score = 88.0
    elif position == "SELL" and acceptable_low is not None and executable >= acceptable_low:
        quality_score = 68.0 if executable < pref_low else 82.0
    elif position == "BUY" and acceptable_high is not None and executable <= acceptable_high:
        quality_score = 68.0 if executable > pref_high else 82.0
    else:
        quality_score = 32.0
    quality_state = "GOOD" if quality_score >= 80 else "FAIR" if quality_score >= 55 else "POOR"

    urgency_score, urgency_state, iw_state, money_bias, money_score, urgency_reasons = _urgency(snapshot, direction)
    planner_status = str(getattr(planner, "status", "WAIT") or "WAIT").upper()
    live = bool(getattr(getattr(snapshot, "market_session", None), "is_live", False))
    no_chase_now = bool(
        no_chase is not None and (
            (position == "SELL" and executable < no_chase)
            or (position == "BUY" and executable > no_chase)
        )
    )
    acceptable_now = bool(
        acceptable_low is not None and acceptable_high is not None
        and acceptable_low <= executable <= acceptable_high
    )
    preferred_now = bool(pref_low is not None and pref_high is not None and pref_low <= executable <= pref_high)

    if not live or planner_status == "REFERENCE ONLY":
        status, tone = "REFERENCE ONLY", "REFERENCE"
    elif planner_status == "CANCEL":
        status, tone = "CANCEL — SETUP INVALID", "DANGER"
    elif planner_status in {"NO CHASE", "MISSED / NO CHASE"} or no_chase_now:
        status, tone = "NO CHASE — MOVE/PRICE EXTENDED", "DANGER"
    elif acceptable_now and urgency_score >= 72:
        status, tone = "FAST MOVE — CURRENT PRICE ACCEPTABLE", "SUCCESS"
    elif planner_status.startswith("ENTRY NOW") and acceptable_now:
        status, tone = "ENTRY CONDITIONS MET — PRICE ACCEPTABLE", "SUCCESS"
    elif preferred_now:
        status, tone = "ARMED — WAIT FOR TRIGGER", "WARNING"
    elif acceptable_now:
        status, tone = "WAIT — BETTER PRICE", "WARNING"
    elif quality_state == "POOR":
        status, tone = "WAIT — PRICE QUALITY POOR", "WARNING"
    else:
        status, tone = "WAIT — TRIGGER", "WARNING"

    reasons: list[str] = []
    if pref_low is not None and pref_high is not None:
        if preferred_now:
            reasons.append("Current executable premium is inside the preferred entry zone")
        elif acceptable_now:
            reasons.append("Current premium is acceptable but a better retest price may be available")
        else:
            reasons.append("Current premium is outside the preferred/acceptable entry zone")
    reasons.append(str(getattr(planner, "reason", "Planner trigger pending")))
    reasons.extend(urgency_reasons[:3])

    # Defined-risk spread context for SELL positions.
    current_net_credit = preferred_credit_low = preferred_credit_high = min_credit = None
    if position == "SELL" and hedge_strike is not None:
        hedge_row = _row(snapshot.option_chain, side, hedge_strike)
        hedge_last = _num(hedge_row.get("last_price") if hedge_row is not None else None, 0.0) or 0.0
        hedge_exec, hedge_bid, hedge_ask = _book_price(hedge_row, "BUY", hedge_last)
        if executable > 0 and hedge_exec > 0:
            current_net_credit = _tick(executable - hedge_exec)
        if getattr(planner, "zone", None):
            retest_spot = sum(planner.zone) / 2.0
            hedge_est = _estimate_premium(
                snapshot, side=side, position="BUY", strike=float(hedge_strike),
                current_spot=spot, current_premium=hedge_exec, target_spot=retest_spot,
                lots=lots, minutes=3,
            )
            if hedge_est and pref_low is not None and pref_high is not None:
                # Conservative executable range: short leg can fill across its range;
                # hedge purchase is costed at the high side of its scenario range.
                preferred_credit_low = _tick(max(0.0, pref_low - hedge_est[2]))
                preferred_credit_high = _tick(max(preferred_credit_low, pref_high - hedge_est[1]))
        width = abs(float(hedge_strike) - float(strike))
        reserve_budget = min(5000.0, float(snapshot.risk_profile.risk_budget_rupees)) / 1.10
        budget_floor = width - reserve_budget / max(1, int(snapshot.risk_profile.lot_size) * int(lots))
        quality_floor = (preferred_credit_low * 0.75) if preferred_credit_low is not None else 0.0
        min_credit = _tick(max(0.05, budget_floor, quality_floor)) if width > 0 else None

    reference = executable
    if pref_low is not None and pref_high is not None:
        if position == "SELL":
            reference = (pref_low + pref_high) / 2.0
        else:
            reference = (pref_low + pref_high) / 2.0
    if urgency_score >= 72 and acceptable_now:
        reference = executable

    rr_current = _rr_at_entry(position, executable, target_premium, stop_premium)
    ladder = _ladder(
        position=position, current=executable,
        preferred_low=pref_low, preferred_high=pref_high,
        acceptable_low=acceptable_low, acceptable_high=acceptable_high,
        total_lots=lots,
    )

    return SmartEntryAdvisor(
        status=status,
        status_tone=tone,
        current_executable_premium=_tick(executable),
        preferred_low=pref_low,
        preferred_high=pref_high,
        acceptable_low=acceptable_low,
        acceptable_high=acceptable_high,
        no_chase_level=no_chase,
        no_chase_relation=no_chase_relation,
        reference_entry=_tick(max(0.05, reference)),
        price_quality_score=round(quality_score, 1),
        price_quality_state=quality_state,
        move_urgency_score=round(urgency_score, 1),
        move_urgency_state=urgency_state,
        spot_zone=getattr(planner, "zone", None),
        invalidation_spot=stop_spot,
        target_spot=target_spot,
        target_premium=_tick(target_premium) if target_premium is not None else None,
        stop_premium=_tick(stop_premium) if stop_premium is not None else None,
        risk_reward_at_current=round(rr_current, 2) if rr_current is not None else None,
        hedge_strike=float(hedge_strike) if hedge_strike is not None else None,
        current_net_credit=current_net_credit,
        preferred_net_credit_low=preferred_credit_low,
        preferred_net_credit_high=preferred_credit_high,
        minimum_net_credit=min_credit,
        institutional_state=iw_state,
        liquidity_magnet_bias=money_bias,
        liquidity_magnet_score=round(money_score, 1) if money_score is not None else None,
        ladder=ladder,
        reasons=tuple(dict.fromkeys(str(x) for x in reasons if str(x).strip()))[:6],
        cautions=(
            "Entry zones are scenario estimates, not guaranteed fills or calibrated win probabilities",
            "W/M and strong candles are supportive only; they are never mandatory",
            "No broker/API call is made by this advisor; refresh the authoritative snapshot before any order",
        ),
    )
