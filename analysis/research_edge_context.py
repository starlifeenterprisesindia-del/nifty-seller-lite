"""Zero-weight research context for One Brain.

This module deliberately reuses the already-built MarketSnapshot and compact local IV
history.  It performs no broker/network/API calls and does not mutate One-Brain scores,
strategy selection, execution guards or risk controls.

The three contexts are intentionally separate:

* VIX expected-move context answers: how much statistical movement/room is left?
* Trend + mean-reversion context answers: pullback completion or genuine reversal risk?
* DTE-matched IV percentile answers: is option premium relatively rich/cheap for selling?

All outputs are advisory evidence labels, not calibrated win probabilities.
"""
from __future__ import annotations

from datetime import date, datetime
import math
from statistics import median
from typing import Any, Iterable, Mapping

import pandas as pd


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _completed(frame: pd.DataFrame | None, *, tail: int = 200) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    source = frame.copy()
    if "is_complete" in source.columns:
        source = source[source["is_complete"].fillna(False).astype(bool)]
    if "timestamp" in source.columns:
        source = source.sort_values("timestamp").drop_duplicates("timestamp")
    return source.tail(max(1, int(tail))).reset_index(drop=True)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            return None


def _dte(captured_date: date, expiry: Any) -> int | None:
    expiry_date = _parse_date(expiry)
    if expiry_date is None:
        return None
    return max(0, (expiry_date - captured_date).days)


def dte_bucket(days: int | None) -> str:
    """Stable calendar-DTE buckets for comparing like-with-like IV observations."""
    if days is None:
        return "UNKNOWN"
    if days <= 1:
        return "0-1D"
    if days <= 3:
        return "2-3D"
    if days <= 7:
        return "4-7D"
    return "8+D"


def _atm_iv(frame: pd.DataFrame | None, spot: float | None) -> tuple[float | None, float | None]:
    if frame is None or frame.empty or spot is None:
        return None, None
    required = {"strike", "side", "implied_volatility"}
    if not required.issubset(frame.columns):
        return None, None
    source = frame.copy()
    source["strike"] = pd.to_numeric(source["strike"], errors="coerce")
    source["implied_volatility"] = pd.to_numeric(source["implied_volatility"], errors="coerce")
    source["side"] = source["side"].astype(str).str.upper()
    source = source.dropna(subset=["strike"])
    strikes = sorted(source["strike"].unique().tolist())
    if not strikes:
        return None, None
    atm = min(strikes, key=lambda value: abs(float(value) - float(spot)))
    pair = source[source["strike"].eq(atm) & source["side"].isin(["CE", "PE"])]
    ivs = [
        float(value)
        for value in pair["implied_volatility"].tolist()
        if _finite(value) is not None and float(value) > 0
    ]
    if not ivs:
        return float(atm), None
    return float(atm), float(median(ivs))


def _session_anchor(snapshot: Any) -> tuple[float | None, str]:
    """Prefer today's first completed 1m open; fall back to previous close."""
    frame = _completed(getattr(snapshot, "candles_1m", None), tail=500)
    created_at = getattr(snapshot, "created_at", None)
    session_date = _parse_date(created_at)
    if not frame.empty and "open" in frame.columns:
        source = frame
        if session_date is not None and "timestamp" in source.columns:
            stamps = pd.to_datetime(source["timestamp"], errors="coerce")
            mask = stamps.dt.date.eq(session_date)
            current_day = source.loc[mask]
            if not current_day.empty:
                source = current_day
        value = _finite(source.iloc[0].get("open")) if not source.empty else None
        if value is not None and value > 0:
            return value, "SESSION OPEN"
    quote = getattr(snapshot, "nifty_quote", {}) or {}
    previous_close = _finite((quote.get("ohlc") or {}).get("close")) if isinstance(quote, Mapping) else None
    if previous_close is not None and previous_close > 0:
        return previous_close, "PREVIOUS CLOSE"
    return None, "UNAVAILABLE"


def build_vix_expected_move_context(snapshot: Any) -> dict[str, Any]:
    """Convert the existing Barrier-map VIX move into a usable anchored range context.

    The Barrier Map already computes the standard 1-sigma-style daily move:
        spot * IndiaVIX / 100 / sqrt(252)
    and scales the future variability by sqrt(remaining_session_fraction).

    This helper does *not* recompute a second volatility model.  It simply anchors the
    existing daily move to the session open/previous close so current range utilisation
    and barrier realism can be interpreted.
    """
    barrier = getattr(snapshot, "barrier_map", None)
    spot = _finite(getattr(barrier, "current_price", None))
    if spot is None:
        quote = getattr(snapshot, "nifty_quote", {}) or {}
        spot = _finite(quote.get("last_price")) if isinstance(quote, Mapping) else None
    daily = _finite(getattr(barrier, "vix_expected_daily_move_points", None))
    remaining = _finite(getattr(barrier, "vix_expected_remaining_move_points", None))
    anchor, anchor_source = _session_anchor(snapshot)
    if spot is None or spot <= 0 or daily is None or daily <= 0 or anchor is None or anchor <= 0:
        return {
            "status": "UNAVAILABLE",
            "state": "NO VOTE",
            "decision_weight": 0,
            "note": "Fresh spot, session anchor and India-VIX expected move are required.",
        }

    upper = anchor + daily
    lower = anchor - daily
    move = spot - anchor
    utilization = abs(move) / daily * 100.0
    if utilization >= 100.0:
        state = "OUTSIDE 1σ ENVELOPE"
    elif utilization >= 80.0:
        state = "NEAR 1σ EDGE"
    else:
        state = "INSIDE EXPECTED ENVELOPE"

    upside_room = upper - spot
    downside_room = spot - lower

    def barrier_fit(level: Any | None, *, side: str) -> dict[str, Any]:
        if level is None:
            return {"state": "NO BARRIER", "distance_points": None, "room_ratio": None}
        distance = _finite(getattr(level, "distance_points", None))
        if distance is None:
            if side == "UPSIDE":
                distance = max(0.0, _finite(getattr(level, "lower", None)) - spot) if _finite(getattr(level, "lower", None)) is not None else None
            else:
                distance = max(0.0, spot - _finite(getattr(level, "upper", None))) if _finite(getattr(level, "upper", None)) is not None else None
        if distance is None:
            return {"state": "UNAVAILABLE", "distance_points": None, "room_ratio": None}
        expected_room = remaining if remaining is not None and remaining > 0 else daily
        ratio = distance / expected_room if expected_room and expected_room > 0 else None
        envelope_room = max(0.0, upside_room if side == "UPSIDE" else downside_room)
        if envelope_room <= 0 and distance > 0:
            fit = "BEYOND DAILY ENVELOPE"
        elif distance > envelope_room + 1e-9:
            fit = "BEYOND DAILY ENVELOPE"
        elif ratio is not None and ratio > 1.0:
            fit = "BEYOND REMAINING EXPECTED MOVE"
        elif ratio is not None and ratio >= 0.75:
            fit = "NEAR EXPECTED LIMIT"
        else:
            fit = "WITHIN EXPECTED ROOM"
        return {
            "state": fit,
            "distance_points": round(distance, 1),
            "room_ratio": round(ratio, 3) if ratio is not None else None,
        }

    upside_fit = barrier_fit(getattr(barrier, "nearest_resistance", None), side="UPSIDE")
    downside_fit = barrier_fit(getattr(barrier, "nearest_support", None), side="DOWNSIDE")
    if state == "OUTSIDE 1σ ENVELOPE":
        caution = "EXTENDED MOVE — STRONGER BREAK/CONTINUATION EVIDENCE NEEDED"
    elif state == "NEAR 1σ EDGE":
        caution = "RANGE EDGE WATCH — FAKE BREAK / EXHAUSTION RISK HIGHER"
    else:
        caution = "NORMAL STATISTICAL ROOM"

    return {
        "status": "READY",
        "state": state,
        "decision_weight": 0,
        "anchor": round(anchor, 2),
        "anchor_source": anchor_source,
        "spot": round(spot, 2),
        "daily_move_points": round(daily, 1),
        "remaining_move_points": round(remaining, 1) if remaining is not None else None,
        "lower_1sigma": round(lower, 2),
        "upper_1sigma": round(upper, 2),
        "session_move_points": round(move, 1),
        "utilization_pct": round(utilization, 1),
        "upside_room_to_envelope": round(upside_room, 1),
        "downside_room_to_envelope": round(downside_room, 1),
        "upside_barrier_fit": upside_fit,
        "downside_barrier_fit": downside_fit,
        "caution": caution,
        "note": "1σ-style VIX envelope is statistical context, not a guaranteed high/low or direction forecast.",
    }


def _rsi_wilder(values: pd.Series, period: int = 2) -> float | None:
    series = pd.to_numeric(values, errors="coerce").dropna()
    if len(series) < period + 2:
        return None
    delta = series.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    avg_gain = gains.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    gain = _finite(avg_gain.iloc[-1])
    loss = _finite(avg_loss.iloc[-1])
    if gain is None or loss is None:
        return None
    if loss <= 1e-12:
        return 100.0 if gain > 0 else 50.0
    rs = gain / loss
    return 100.0 - 100.0 / (1.0 + rs)


def _pattern_support(snapshot: Any, direction: str) -> bool:
    patterns = getattr(snapshot, "patterns", None)
    if patterns is None:
        return False
    for name in ("candle_3m", "candle_5m", "candle_15m", "wm_3m"):
        item = getattr(patterns, name, None)
        if item is None:
            continue
        item_direction = str(getattr(item, "direction", "NEUTRAL") or "NEUTRAL").upper()
        confidence = _finite(getattr(item, "confidence", None)) or 0.0
        if item_direction == direction and confidence >= 55.0:
            return True
    return False


def build_mean_reversion_context(snapshot: Any, market_intelligence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Intraday adaptation of trend-filtered short-term mean reversion.

    This is deliberately *not* presented as the original Connors daily RSI(2) system.
    It uses the research principle (trade an extreme pullback only with the dominant
    trend) and combines it with One Brain's existing 15m EMA/ATR, 3m evidence, barriers
    and Pressure Integrity.  It remains a zero-weight pullback-vs-reversal diagnostic.
    """
    frame = _completed(getattr(snapshot, "candles_15m", None), tail=120)
    indicators = getattr(snapshot, "indicators", None)
    ind = getattr(indicators, "fifteen_minute", None) if indicators is not None else None
    pa = getattr(getattr(snapshot, "price_action", None), "fifteen_minute", None)
    if frame.empty or len(frame) < 8 or ind is None or str(getattr(ind, "status", "")).upper() != "READY":
        return {
            "status": "WARMING UP",
            "state": "NO VOTE",
            "decision_weight": 0,
            "note": "Completed 15m trend/RSI evidence is not ready.",
        }

    close = _finite(frame.iloc[-1].get("close"))
    ema20 = _finite(getattr(ind, "ema20", None))
    ema50 = _finite(getattr(ind, "ema50", None))
    atr = _finite(getattr(pa, "atr14", None)) if pa is not None else None
    rsi2 = _rsi_wilder(frame["close"], 2) if "close" in frame.columns else None
    if close is None or ema20 is None or ema50 is None or atr is None or atr <= 0 or rsi2 is None:
        return {
            "status": "WARMING UP",
            "state": "NO VOTE",
            "decision_weight": 0,
            "note": "EMA20/EMA50/ATR14/RSI(2) inputs are incomplete.",
        }

    ema_gap = ema20 - ema50
    gap_floor = max(0.5, atr * 0.05)
    trend = "BULLISH" if ema_gap > gap_floor else "BEARISH" if ema_gap < -gap_floor else "MIXED"
    stretch_atr = (close - ema20) / atr
    if trend == "BULLISH":
        extreme = rsi2 <= 10.0
        very_extreme = rsi2 <= 5.0
        continuation_direction = "BULLISH"
        relevant_level = getattr(getattr(snapshot, "barrier_map", None), "nearest_support", None)
    elif trend == "BEARISH":
        extreme = rsi2 >= 90.0
        very_extreme = rsi2 >= 95.0
        continuation_direction = "BEARISH"
        relevant_level = getattr(getattr(snapshot, "barrier_map", None), "nearest_resistance", None)
    else:
        extreme = very_extreme = False
        continuation_direction = "MIXED"
        relevant_level = None

    distance = _finite(getattr(relevant_level, "distance_points", None)) if relevant_level is not None else None
    near_barrier = distance is not None and distance <= max(12.0, atr * 0.75)

    pa3 = getattr(getattr(snapshot, "price_action", None), "three_minute", None)
    bull3 = _finite(getattr(pa3, "bullish_score", None)) or 0.0
    bear3 = _finite(getattr(pa3, "bearish_score", None)) or 0.0
    if continuation_direction == "BULLISH":
        three_confirm = bull3 >= 55.0 and bull3 - bear3 >= 8.0
    elif continuation_direction == "BEARISH":
        three_confirm = bear3 >= 55.0 and bear3 - bull3 >= 8.0
    else:
        three_confirm = False
    pattern_confirm = _pattern_support(snapshot, continuation_direction)

    mie = market_intelligence if isinstance(market_intelligence, Mapping) else {}
    integrity = mie.get("pressure_integrity") if isinstance(mie.get("pressure_integrity"), Mapping) else {}
    pressure_direction = str(integrity.get("direction") or "MIXED").upper()
    quality_state = str(integrity.get("quality_state") or "UNVERIFIED").upper()
    attack_state = str(integrity.get("move_attack_state") or "NORMAL").upper()
    barrier_state = str(integrity.get("barrier_state") or "").upper()
    opposing = (
        (trend == "BULLISH" and pressure_direction == "BEARISH")
        or (trend == "BEARISH" and pressure_direction == "BULLISH")
    )
    opposing_verified = opposing and quality_state in {"VERIFIED", "REALIZED", "FLIP CONFIRMED"}
    opposing_attack = opposing and attack_state in {"ATTACK", "BREAK / EXPANSION", "BREAK/EXPANSION", "FLIP CONFIRMED"}

    if trend == "MIXED":
        state = "NO TREND FILTER"
        verdict = "NO VOTE"
    elif opposing_verified and opposing_attack:
        state = "GENUINE REVERSAL RISK"
        verdict = "DO NOT ASSUME MEAN REVERSION"
    elif extreme and (three_confirm or pattern_confirm) and near_barrier and not opposing_verified:
        state = "PULLBACK COMPLETION CONFIRMED"
        verdict = "SUPPORTS TREND CONTINUATION"
    elif extreme and (near_barrier or three_confirm or pattern_confirm) and not opposing_verified:
        state = "PULLBACK COMPLETION WATCH"
        verdict = "WAIT FOR CONFIRMATION"
    elif extreme and not opposing_verified:
        state = "MEAN-REVERSION EXTREME"
        verdict = "EXTREME ONLY — LOCATION/CONFIRMATION MISSING"
    else:
        state = "NO EXTREME"
        verdict = "NO VOTE"

    strength = 0.0
    if extreme:
        strength += 45.0
    if very_extreme:
        strength += 10.0
    if near_barrier:
        strength += 15.0
    if three_confirm:
        strength += 15.0
    if pattern_confirm:
        strength += 8.0
    if not opposing_verified and trend in {"BULLISH", "BEARISH"}:
        strength += 7.0
    if opposing_verified:
        strength = min(strength, 35.0)

    return {
        "status": "READY",
        "state": state,
        "decision_weight": 0,
        "verdict": verdict,
        "trend": trend,
        "rsi2_15m": round(rsi2, 1),
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "atr14_15m": round(atr, 2),
        "stretch_atr": round(stretch_atr, 2),
        "extreme": bool(extreme),
        "very_extreme": bool(very_extreme),
        "near_trend_barrier": bool(near_barrier),
        "barrier_distance_points": round(distance, 1) if distance is not None else None,
        "three_minute_confirmation": bool(three_confirm),
        "pattern_confirmation": bool(pattern_confirm),
        "pressure_direction": pressure_direction,
        "pressure_quality_state": quality_state,
        "pressure_attack_state": attack_state,
        "pressure_barrier_state": barrier_state,
        "context_strength": round(min(100.0, strength), 1),
        "note": "Intraday trend-filtered mean-reversion diagnostic; not a copied daily RSI(2) win-rate claim.",
    }


def _history_dte_bucket(row: Mapping[str, Any]) -> str:
    row_date = _parse_date(row.get("date") or row.get("updated_at"))
    days = _dte(row_date, row.get("expiry")) if row_date is not None else None
    return dte_bucket(days)


def dte_matched_iv_history(
    snapshot: Any,
    iv_history: Iterable[Mapping[str, Any]] | None,
) -> tuple[list[float], list[dict[str, Any]], str, int | None]:
    """Return prior real ATM-IV observations from the same calendar-DTE bucket.

    The current trading date is intentionally excluded so an in-progress session can
    never influence its own IV Rank/Percentile.  No interpolation or synthetic rows
    are created.
    """
    current_date = _parse_date(getattr(snapshot, "created_at", None))
    days = _dte(current_date, getattr(snapshot, "expiry", None)) if current_date is not None else None
    bucket = dte_bucket(days)
    if current_date is None or bucket == "UNKNOWN":
        return [], [], bucket, days

    values: list[float] = []
    rows: list[dict[str, Any]] = []
    for raw in iv_history or ():
        if not isinstance(raw, Mapping):
            continue
        row_date = _parse_date(raw.get("date") or raw.get("updated_at"))
        if row_date is None or row_date >= current_date:
            continue
        if _history_dte_bucket(raw) != bucket:
            continue
        value = _finite(raw.get("last"))
        if value is None or value <= 0:
            continue
        values.append(float(value))
        rows.append(dict(raw))
    return values, rows, bucket, days


def build_iv_percentile_context(
    snapshot: Any,
    iv_history: Iterable[Mapping[str, Any]] | None,
    *,
    minimum_bucket_sessions: int = 20,
) -> dict[str, Any]:
    """DTE-matched ATM-IV percentile from already-persisted real session summaries."""
    quote = getattr(snapshot, "nifty_quote", {}) or {}
    spot = _finite(quote.get("last_price")) if isinstance(quote, Mapping) else None
    atm, current_iv = _atm_iv(getattr(snapshot, "option_chain", None), spot)
    created_at = getattr(snapshot, "created_at", None)
    current_date = _parse_date(created_at)
    values, _matched_rows, bucket, days = dte_matched_iv_history(snapshot, iv_history)
    if current_iv is None or current_iv <= 0 or current_date is None or bucket == "UNKNOWN":
        return {
            "status": "UNAVAILABLE",
            "state": "NO VOTE",
            "decision_weight": 0,
            "dte_bucket": bucket,
            "note": "Current ATM IV/expiry context unavailable.",
        }

    minimum = max(5, int(minimum_bucket_sessions))
    if len(values) < minimum:
        return {
            "status": "WARMING UP",
            "state": "NO VOTE",
            "decision_weight": 0,
            "atm": round(atm, 2) if atm is not None else None,
            "atm_iv": round(current_iv, 2),
            "days_to_expiry": days,
            "dte_bucket": bucket,
            "history_sessions": len(values),
            "minimum_sessions": minimum,
            "seller_context": "NO VOTE",
            "note": f"Need >= {minimum} prior real sessions in the same DTE bucket; no synthetic history used.",
        }

    low = min(values)
    high = max(values)
    iv_rank = ((current_iv - low) / (high - low) * 100.0) if high > low else None
    percentile = 100.0 * sum(value <= current_iv for value in values) / len(values)
    percentile = max(0.0, min(100.0, percentile))
    if percentile >= 75.0:
        state = "RICH IV"
        seller_context = "SELL PREMIUM FAVOURABLE CONTEXT"
    elif percentile >= 60.0:
        state = "ELEVATED IV"
        seller_context = "SELL PREMIUM SUPPORTIVE"
    elif percentile < 30.0:
        state = "CHEAP IV"
        seller_context = "SELL PREMIUM WEAK VALUE"
    else:
        state = "NORMAL IV"
        seller_context = "NEUTRAL PREMIUM CONTEXT"

    return {
        "status": "READY",
        "state": state,
        "decision_weight": 0,
        "atm": round(atm, 2) if atm is not None else None,
        "atm_iv": round(current_iv, 2),
        "days_to_expiry": days,
        "dte_bucket": bucket,
        "history_sessions": len(values),
        "iv_rank": round(max(0.0, min(100.0, iv_rank)), 1) if iv_rank is not None else None,
        "iv_percentile": round(percentile, 1),
        "history_low": round(low, 2),
        "history_high": round(high, 2),
        "seller_context": seller_context,
        "note": "ATM IV is compared only with prior real sessions in the same calendar-DTE bucket; not an entry signal by itself.",
    }


def build_research_edge_context(
    snapshot: Any,
    *,
    market_intelligence: Mapping[str, Any] | None = None,
    iv_history: Iterable[Mapping[str, Any]] | None = None,
    minimum_iv_bucket_sessions: int = 20,
) -> dict[str, Any]:
    """Build all three zero-weight contexts in one bounded CPU-only call."""
    expected_move = build_vix_expected_move_context(snapshot)
    mean_reversion = build_mean_reversion_context(snapshot, market_intelligence)
    iv_percentile = build_iv_percentile_context(
        snapshot,
        iv_history,
        minimum_bucket_sessions=minimum_iv_bucket_sessions,
    )
    return {
        "engine": "ONE_BRAIN_EDGE_CONTEXT_V1",
        "mode": "INTEGRATED ADVISORY / ZERO DECISION WEIGHT",
        "decision_weight": 0,
        "expected_move": expected_move,
        "mean_reversion": mean_reversion,
        "iv_percentile": iv_percentile,
        "golden_rule": "Existing snapshot/local history only; no broker/API call; no score/threshold/strategy mutation.",
    }
