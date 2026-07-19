from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from analysis.technical_utils import (
    atr_value,
    clamp,
    completed_candles,
    confirmed_swings,
)
from config import CONFIG, IST_TIMEZONE
from models import IndicatorBundle, LevelBundle, MarketLevel


@dataclass(frozen=True)
class _Candidate:
    price: float
    source: str
    weight: float


@dataclass(frozen=True)
class _MergedZone:
    lower: float
    upper: float
    midpoint: float
    strength: float
    sources: tuple[str, ...]


SOURCE_WEIGHTS: dict[str, float] = {
    "PREVIOUS DAY HIGH": 3.2,
    "PREVIOUS DAY LOW": 3.2,
    "PREVIOUS DAY CLOSE": 1.6,
    "CURRENT DAY HIGH": 2.0,
    "CURRENT DAY LOW": 2.0,
    "OPENING RANGE HIGH": 2.8,
    "OPENING RANGE LOW": 2.8,
    "15M SWING HIGH": 2.8,
    "15M SWING LOW": 2.8,
    "3M SWING HIGH": 1.4,
    "3M SWING LOW": 1.4,
    "15M EMA20": 1.2,
    "15M EMA50": 1.4,
    "3M EMA20": 0.8,
    "3M EMA50": 0.9,
}


def _candidate(price: float | None, source: str) -> _Candidate | None:
    if price is None or pd.isna(price):
        return None
    return _Candidate(float(price), source, SOURCE_WEIGHTS[source])


def _session_values(
    frame: pd.DataFrame,
) -> tuple[object | None, object | None, pd.DataFrame, pd.DataFrame]:
    source = completed_candles(frame)
    if source.empty:
        return None, None, source, source
    source = source.copy()
    source["trade_date"] = source["timestamp"].map(
        lambda value: pd.Timestamp(value).date()
    )
    dates = sorted(source["trade_date"].unique())
    current_date = dates[-1]
    previous_date = dates[-2] if len(dates) >= 2 else None
    current = source[source["trade_date"] == current_date].copy()
    previous = (
        source[source["trade_date"] == previous_date].copy()
        if previous_date is not None
        else source.iloc[0:0].copy()
    )
    return current_date, previous_date, current, previous


def _opening_range(current_3m: pd.DataFrame) -> tuple[float | None, float | None]:
    if current_3m.empty:
        return None, None
    start = pd.Timestamp(
        f"{current_3m.iloc[0]['trade_date']} 09:15:00", tz=IST_TIMEZONE
    )
    end = start + pd.to_timedelta(int(CONFIG.opening_range_minutes), unit="min")
    opening = current_3m[
        (current_3m["timestamp"] >= start) & (current_3m["timestamp"] < end)
    ]
    if opening.empty:
        return None, None
    return float(opening["high"].max()), float(opening["low"].min())


def _merge_candidates(
    candidates: Iterable[_Candidate],
    width: float,
) -> list[_MergedZone]:
    items = sorted(candidates, key=lambda item: item.price)
    clusters: list[list[_Candidate]] = []
    for item in items:
        if not clusters or item.price - clusters[-1][-1].price > width:
            clusters.append([item])
        else:
            clusters[-1].append(item)
    merged: list[_MergedZone] = []
    for cluster in clusters:
        total_weight = sum(item.weight for item in cluster)
        midpoint = sum(item.price * item.weight for item in cluster) / total_weight
        sources = tuple(sorted({item.source for item in cluster}))
        strength = clamp(
            28 + total_weight * 11 + max(0, len(sources) - 1) * 4,
            0,
            100,
        )
        merged.append(
            _MergedZone(
                lower=min(item.price for item in cluster) - width / 2,
                upper=max(item.price for item in cluster) + width / 2,
                midpoint=midpoint,
                strength=strength,
                sources=sources,
            )
        )
    return merged


def _level_status(
    side: str,
    lower: float,
    upper: float,
    current_price: float,
    last_candle: pd.Series | None,
    width: float,
) -> str:
    # Current spot determines where price is now. Completed candles determine whether
    # a break/rejection was confirmed. This prevents a stale completed close from
    # labelling spot inside a zone as already broken.
    if lower <= current_price <= upper:
        return "TESTING / INSIDE ZONE"

    if last_candle is not None:
        close = float(last_candle["close"])
        high = float(last_candle["high"])
        low = float(last_candle["low"])
        if side == "SUPPORT":
            if current_price < lower and close < lower:
                return "BROKEN / ACCEPTED BELOW"
            if current_price < lower:
                return "BELOW ZONE / AWAITING CLOSE"
            if low <= upper and close > upper:
                return "HOLDING / REJECTED LOWER"
        else:
            if current_price > upper and close > upper:
                return "BROKEN / ACCEPTED ABOVE"
            if current_price > upper:
                return "ABOVE ZONE / AWAITING CLOSE"
            if high >= lower and close < lower:
                return "REJECTED"
        if lower <= close <= upper:
            return "TESTING BY COMPLETED CANDLE"

    distance = lower - current_price if side == "RESISTANCE" else current_price - upper
    if distance <= width * 1.5:
        return "APPROACHING"
    return "ACTIVE"


def _build_level(
    raw: _MergedZone,
    side: str,
    current_price: float,
    last_candle: pd.Series | None,
    width: float,
    label: str,
) -> MarketLevel:
    distance = (
        max(0.0, raw.lower - current_price)
        if side == "RESISTANCE"
        else max(0.0, current_price - raw.upper)
    )
    return MarketLevel(
        label=label,
        side=side,
        lower=round(raw.lower, 2),
        upper=round(raw.upper, 2),
        midpoint=round(raw.midpoint, 2),
        strength=round(raw.strength, 1),
        status=_level_status(
            side,
            raw.lower,
            raw.upper,
            current_price,
            last_candle,
            width,
        ),
        distance_points=round(distance, 2),
        sources=raw.sources,
    )


def calculate_levels(
    candles_3m: pd.DataFrame,
    candles_15m: pd.DataFrame,
    indicators: IndicatorBundle,
    current_price: float | None,
) -> LevelBundle:
    three = completed_candles(candles_3m)
    fifteen = completed_candles(candles_15m)
    if three.empty or fifteen.empty:
        return LevelBundle(
            as_of=None,
            current_price=current_price,
            immediate_support=None,
            strong_support=None,
            immediate_resistance=None,
            strong_resistance=None,
            previous_day_high=None,
            previous_day_low=None,
            opening_range_high=None,
            opening_range_low=None,
            upside_room=None,
            downside_room=None,
            current_position="UNAVAILABLE",
            zone_width=None,
            status="CANDLES UNAVAILABLE",
        )

    price = float(
        current_price if current_price is not None else three.iloc[-1]["close"]
    )
    _, _, current_3m, previous_3m = _session_values(three)
    _, _, current_15m, previous_15m = _session_values(fifteen)
    previous = previous_15m if not previous_15m.empty else previous_3m
    previous_day_high = float(previous["high"].max()) if not previous.empty else None
    previous_day_low = float(previous["low"].min()) if not previous.empty else None
    previous_day_close = (
        float(previous.iloc[-1]["close"]) if not previous.empty else None
    )
    current_day_high = float(current_3m["high"].max()) if not current_3m.empty else None
    current_day_low = float(current_3m["low"].min()) if not current_3m.empty else None
    opening_high, opening_low = _opening_range(current_3m)

    atr15 = atr_value(fifteen) or atr_value(three) or 20.0
    width = clamp(
        atr15 * CONFIG.level_zone_atr_fraction,
        CONFIG.minimum_level_zone_points,
        CONFIG.maximum_level_zone_points,
    )
    candidates: list[_Candidate] = []
    for item in (
        _candidate(previous_day_high, "PREVIOUS DAY HIGH"),
        _candidate(previous_day_low, "PREVIOUS DAY LOW"),
        _candidate(previous_day_close, "PREVIOUS DAY CLOSE"),
        _candidate(current_day_high, "CURRENT DAY HIGH"),
        _candidate(current_day_low, "CURRENT DAY LOW"),
        _candidate(opening_high, "OPENING RANGE HIGH"),
        _candidate(opening_low, "OPENING RANGE LOW"),
        _candidate(indicators.fifteen_minute.ema20, "15M EMA20"),
        _candidate(indicators.fifteen_minute.ema50, "15M EMA50"),
        _candidate(indicators.three_minute.ema20, "3M EMA20"),
        _candidate(indicators.three_minute.ema50, "3M EMA50"),
    ):
        if item is not None:
            candidates.append(item)

    highs15, lows15 = confirmed_swings(fifteen)
    highs3, lows3 = confirmed_swings(three)
    candidates.extend(
        _Candidate(item.price, "15M SWING HIGH", SOURCE_WEIGHTS["15M SWING HIGH"])
        for item in highs15[-3:]
    )
    candidates.extend(
        _Candidate(item.price, "15M SWING LOW", SOURCE_WEIGHTS["15M SWING LOW"])
        for item in lows15[-3:]
    )
    candidates.extend(
        _Candidate(item.price, "3M SWING HIGH", SOURCE_WEIGHTS["3M SWING HIGH"])
        for item in highs3[-5:]
    )
    candidates.extend(
        _Candidate(item.price, "3M SWING LOW", SOURCE_WEIGHTS["3M SWING LOW"])
        for item in lows3[-5:]
    )

    merged = _merge_candidates(candidates, width)
    supports_raw = [item for item in merged if item.midpoint <= price]
    resistances_raw = [item for item in merged if item.midpoint > price]
    supports_raw.sort(key=lambda item: price - item.midpoint)
    resistances_raw.sort(key=lambda item: item.midpoint - price)
    last_candle = three.iloc[-1] if not three.empty else None

    supports = [
        _build_level(item, "SUPPORT", price, last_candle, width, f"S{index + 1}")
        for index, item in enumerate(supports_raw)
    ]
    resistances = [
        _build_level(item, "RESISTANCE", price, last_candle, width, f"R{index + 1}")
        for index, item in enumerate(resistances_raw)
    ]
    immediate_support = supports[0] if supports else None
    immediate_resistance = resistances[0] if resistances else None
    strong_support = (
        max(supports[:4], key=lambda item: item.strength) if supports else None
    )
    strong_resistance = (
        max(resistances[:4], key=lambda item: item.strength) if resistances else None
    )
    upside_room = immediate_resistance.distance_points if immediate_resistance else None
    downside_room = immediate_support.distance_points if immediate_support else None

    near_threshold = max(width * 1.5, 5.0)
    if immediate_resistance and immediate_resistance.distance_points <= near_threshold:
        position = "NEAR RESISTANCE"
    elif immediate_support and immediate_support.distance_points <= near_threshold:
        position = "NEAR SUPPORT"
    elif immediate_support and immediate_resistance:
        position = "BETWEEN SUPPORT AND RESISTANCE"
    elif immediate_resistance:
        position = "BELOW RESISTANCE / SUPPORT UNRESOLVED"
    elif immediate_support:
        position = "ABOVE SUPPORT / RESISTANCE UNRESOLVED"
    else:
        position = "LEVELS UNRESOLVED"

    return LevelBundle(
        as_of=pd.Timestamp(three.iloc[-1]["timestamp"]).to_pydatetime(),
        current_price=round(price, 2),
        immediate_support=immediate_support,
        strong_support=strong_support,
        immediate_resistance=immediate_resistance,
        strong_resistance=strong_resistance,
        previous_day_high=round(previous_day_high, 2)
        if previous_day_high is not None
        else None,
        previous_day_low=round(previous_day_low, 2)
        if previous_day_low is not None
        else None,
        opening_range_high=round(opening_high, 2) if opening_high is not None else None,
        opening_range_low=round(opening_low, 2) if opening_low is not None else None,
        upside_room=upside_room,
        downside_room=downside_room,
        current_position=position,
        zone_width=round(width, 2),
        status="READY",
    )
