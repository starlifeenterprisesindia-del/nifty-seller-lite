"""Independent advisory entry timing: no One-Brain/decision/flow access."""
from dataclasses import dataclass
from datetime import timedelta
from math import isfinite

from analysis.sl_target_planner import directional_intent, expiry_context
from analysis.spot_premium_calculator import calculate_target_premium
from analysis.technical_utils import completed_candles, atr_value


@dataclass(frozen=True)
class StrikeEntry:
    status: str
    reason: str
    zone: tuple[float, float] | None = None
    invalidation: float | None = None
    premium_range: tuple[float, float] | None = None
    net_credit: float | None = None
    worst_case_rupees: float | None = None
    valid_until: str = ""


def _book(row):
    try:
        bid, ask = float(row["top_bid_price"]), float(row["top_ask_price"])
        if all(isfinite(x) for x in (bid, ask)) and 0 < bid <= ask and (ask - bid) / ((ask + bid) / 2) <= .10:
            return bid, ask
    except (TypeError, ValueError, KeyError):
        pass
    return None


def plan_strike_entry(*, candles, barrier_map, option_chain, side, position, strike,
                      spot, as_of, expiry, live, risk_budget, lot_size, lots=1,
                      hedge_strike=None, frozen_zone=None):
    """Prices are indicative, never a guaranteed optimal price or fill.

    A frozen zone prevents an invalidated level moving with the market. Range
    estimates at a retest are conditional on that future spot/time/IV scenario.
    """
    if not live:
        return StrikeEntry("REFERENCE ONLY", "Fresh live quote, option chain and candles required")
    if side not in {"CE", "PE"} or position not in {"BUY", "SELL"}:
        return StrikeEntry("WAIT", "Invalid option side/position")
    if lot_size < 1 or lots < 1 or not isfinite(risk_budget) or risk_budget <= 0:
        return StrikeEntry("WAIT", "Invalid quantity/risk budget")
    if expiry_context(captured_at=as_of, expiry=expiry).minutes_remaining <= 0:
        return StrikeEntry("WAIT", "Contract expired or expiry unavailable")
    direction = directional_intent(side=side, position=position)
    sign = 1 if direction == "BULLISH" else -1
    source = completed_candles(candles)
    if len(source) < 6:
        return StrikeEntry("WAIT", "Completed 3m candles warming up")
    last, prev = source.iloc[-1], source.iloc[-2]
    close_at = last["timestamp"].to_pydatetime() + timedelta(minutes=3)
    if not 0 <= (as_of - close_at).total_seconds() <= 240:
        return StrikeEntry("WAIT", "3m candle is stale")
    atr = atr_value(source)
    if atr is None or not isfinite(atr) or atr <= 0:
        return StrikeEntry("WAIT", "Candle volatility estimate unavailable")
    level = barrier_map.nearest_support if sign == 1 else barrier_map.nearest_resistance
    if frozen_zone is None and level is None:
        return StrikeEntry("WAIT", "Entry barrier unavailable")
    zone = frozen_zone or (float(level.lower), float(level.upper))
    buffer = max(2.0, atr * .15)
    stop = zone[0] - buffer if sign == 1 else zone[1] + buffer
    if (spot - stop) * sign <= 0 or (float(last.close) - stop) * sign <= 0:
        return StrikeEntry("CANCEL", "Entry barrier invalidated; re-plan explicitly", zone, stop)
    rows = option_chain[(option_chain.side == side) & (option_chain.strike == strike)]
    if rows.empty:
        return StrikeEntry("WAIT", "Selected contract unavailable", zone, stop)
    row = rows.iloc[0]
    book = _book(row)
    if book is None:
        return StrikeEntry("WAIT", "Executable bid/ask unavailable or too wide", zone, stop)
    premium = book[0] if position == "SELL" else book[1]
    credit = None
    risk = premium * lot_size * lots
    if position == "SELL":
        if hedge_strike is None or (hedge_strike - strike) * (1 if side == "CE" else -1) <= 0:
            return StrikeEntry("WAIT", "Select a farther protective same-expiry hedge", zone, stop)
        hedges = option_chain[(option_chain.side == side) & (option_chain.strike == hedge_strike)]
        hedge_book = _book(hedges.iloc[0]) if not hedges.empty else None
        if hedge_book is None:
            return StrikeEntry("WAIT", "Hedge bid/ask unavailable or too wide", zone, stop)
        credit = premium - hedge_book[1]
        width = abs(hedge_strike - strike)
        if not 0 < credit < width:
            return StrikeEntry("WAIT", "Invalid/non-positive net spread credit", zone, stop)
        risk = (width - credit) * lot_size * lots
    risk *= 1.10  # reserve for costs; not an exact charges calculation
    if risk > risk_budget:
        return StrikeEntry("WAIT", "Defined maximum-risk budget exceeded; reduce quantity/change pair", zone, stop, book, credit, risk)
    adverse_distance = (spot - stop) * sign
    next_level = barrier_map.nearest_resistance if sign == 1 else barrier_map.nearest_support
    room = ((next_level.lower - spot) if sign == 1 else (spot - next_level.upper)) if next_level else None
    if room is None:
        return StrikeEntry("WAIT", "Next barrier/available space unresolved", zone, stop, book, credit, risk)
    if adverse_distance > atr * 2.5 or room < max(atr * .5, adverse_distance * .5):
        return StrikeEntry("NO CHASE", "Extended entry or insufficient space before next barrier", zone, stop, book, credit, risk)
    hold = (float(last.low) <= zone[1] + buffer and float(last.close) > zone[1] and float(last.close) > float(last.open)) if sign == 1 else (float(last.high) >= zone[0] - buffer and float(last.close) < zone[0] and float(last.close) < float(last.open))
    # Completed close beyond previous extreme, with directional previous candle.
    momentum = ((float(last.close) > float(prev.high) + buffer and float(prev.close) > float(prev.open)) if sign == 1 else (float(last.close) < float(prev.low) - buffer and float(prev.close) < float(prev.open)))
    momentum = momentum and abs(float(last.close) - float(last.open)) >= atr * .5
    not_reversed = (spot - float(last.close)) * sign >= -buffer
    status = "ENTRY NOW — RETEST HOLD" if hold and not_reversed else "ENTRY NOW — MOMENTUM" if momentum and not_reversed else "WAIT FOR RETEST"
    reason = "Completed 3m trigger + barrier + room + risk checks passed" if status.startswith("ENTRY NOW") else "Retest/3m trigger pending; target premium is conditional, not promised"
    estimate_range = book
    if status == "WAIT FOR RETEST":
        estimate_range = None
        if row.get("greeks_quality", "UNAVAILABLE") == "READY":
            context = expiry_context(captured_at=as_of, expiry=expiry)
            estimate = calculate_target_premium(option_chain=option_chain, side=side, position=position,
                strike=strike, current_spot=spot, current_premium=premium, entry_premium=premium,
                target_spot=sum(zone) / 2, target_minutes=3, lot_size=lot_size, lots=lots,
                feed_state="LIVE", minutes_to_expiry=context.minutes_remaining)
            estimate_range = (estimate.low_price, estimate.high_price)
    return StrikeEntry(status, reason, zone, stop, estimate_range, credit, risk,
                       (as_of + timedelta(seconds=30)).isoformat())
