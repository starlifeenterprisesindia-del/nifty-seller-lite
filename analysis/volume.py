from __future__ import annotations

import statistics

import pandas as pd

from analysis.technical_utils import clamp, completed_candles
from config import CONFIG
from models import TimeframeVolume, VolumeBundle


def _empty(timeframe: str, status: str) -> TimeframeVolume:
    return TimeframeVolume(
        timeframe=timeframe,
        as_of=None,
        current_volume=None,
        baseline_volume=None,
        relative_volume=None,
        volume_state="UNAVAILABLE",
        volume_trend="UNAVAILABLE",
        price_direction="UNAVAILABLE",
        move_support="UNAVAILABLE",
        baseline_samples=0,
        confidence=0.0,
        status=status,
    )


def _slot_key(timestamp: pd.Timestamp) -> tuple[int, int]:
    return timestamp.hour, timestamp.minute


def _baseline_for_row(source: pd.DataFrame, row_index: int) -> tuple[float | None, int]:
    row = source.iloc[row_index]
    timestamp = pd.Timestamp(row["timestamp"])
    prior = source.iloc[:row_index].copy()
    slot_mask = (
        prior["timestamp"]
        .map(lambda value: _slot_key(pd.Timestamp(value)) == _slot_key(timestamp))
        .astype(bool)
    )
    same_slot = prior.loc[slot_mask]
    date_mask = (
        same_slot["timestamp"]
        .map(lambda value: pd.Timestamp(value).date() < timestamp.date())
        .astype(bool)
    )
    same_slot = same_slot.loc[date_mask]
    samples = (
        same_slot["volume"].dropna().tail(CONFIG.volume_baseline_sessions).tolist()
    )
    if len(samples) >= 2:
        return float(statistics.median(samples)), len(samples)

    fallback = (
        prior["volume"].dropna().tail(CONFIG.volume_recent_fallback_bars).tolist()
    )
    if len(fallback) >= 5:
        return float(statistics.median(fallback)), len(fallback)
    return None, len(samples)


def _relative_for_row(source: pd.DataFrame, row_index: int) -> float | None:
    baseline, _ = _baseline_for_row(source, row_index)
    volume = source.iloc[row_index]["volume"]
    if baseline in (None, 0) or pd.isna(volume):
        return None
    return float(volume) / float(baseline)


def _state(ratio: float) -> str:
    if ratio < CONFIG.volume_low_ratio:
        return "LOW"
    if ratio < CONFIG.volume_high_ratio:
        return "NORMAL"
    if ratio < CONFIG.volume_surge_ratio:
        return "HIGH"
    return "SURGE"


def _price_direction(price_row: pd.Series) -> str:
    change = float(price_row["close"] - price_row["open"])
    spread = max(float(price_row["high"] - price_row["low"]), 0.01)
    if abs(change) / spread < 0.15:
        return "FLAT"
    return "UP" if change > 0 else "DOWN"


def calculate_timeframe_volume(
    volume_frame: pd.DataFrame,
    price_frame: pd.DataFrame,
    timeframe: str,
) -> TimeframeVolume:
    volume_source = completed_candles(volume_frame)
    price_source = completed_candles(price_frame)
    if len(volume_source) < 6 or price_source.empty:
        return _empty(timeframe, "FUTURES VOLUME CANDLES UNAVAILABLE")

    last_timestamp = pd.Timestamp(volume_source.iloc[-1]["timestamp"])
    matching_price = price_source[price_source["timestamp"] == last_timestamp]
    if matching_price.empty:
        matching_price = price_source[price_source["timestamp"] <= last_timestamp].tail(
            1
        )
    if matching_price.empty:
        return _empty(timeframe, "MATCHING PRICE CANDLE UNAVAILABLE")

    baseline, samples = _baseline_for_row(volume_source, len(volume_source) - 1)
    current_volume = float(volume_source.iloc[-1]["volume"])
    if baseline in (None, 0):
        return _empty(timeframe, "VOLUME BASELINE UNAVAILABLE")
    ratio = current_volume / baseline

    recent_ratios = [
        value
        for value in (
            _relative_for_row(volume_source, index)
            for index in range(max(0, len(volume_source) - 3), len(volume_source))
        )
        if value is not None
    ]
    if len(recent_ratios) >= 2 and recent_ratios[-1] > recent_ratios[0] * 1.10:
        trend = "RISING"
    elif len(recent_ratios) >= 2 and recent_ratios[-1] < recent_ratios[0] * 0.90:
        trend = "FALLING"
    else:
        trend = "STABLE"

    direction = _price_direction(matching_price.iloc[-1])
    if ratio >= CONFIG.volume_high_ratio and direction == "UP":
        support = "BULLISH MOVE CONFIRMED"
    elif ratio >= CONFIG.volume_high_ratio and direction == "DOWN":
        support = "BEARISH MOVE CONFIRMED"
    elif ratio < CONFIG.volume_low_ratio and direction in {"UP", "DOWN"}:
        support = "PRICE MOVE ON LOW PARTICIPATION"
    elif direction == "FLAT" and ratio >= CONFIG.volume_high_ratio:
        support = "HIGH ACTIVITY / BREAKOUT BUILD-UP"
    else:
        support = "NORMAL PARTICIPATION"

    confidence = clamp(45 + min(samples, 5) * 7 + (8 if ratio >= 1.2 else 0), 0, 92)
    return TimeframeVolume(
        timeframe=timeframe,
        as_of=last_timestamp.to_pydatetime(),
        current_volume=round(current_volume, 2),
        baseline_volume=round(float(baseline), 2),
        relative_volume=round(ratio, 2),
        volume_state=_state(ratio),
        volume_trend=trend,
        price_direction=direction,
        move_support=support,
        baseline_samples=samples,
        confidence=round(confidence, 1),
        status="READY",
    )


def calculate_volume_bundle(
    future_candles_3m: pd.DataFrame,
    future_candles_15m: pd.DataFrame,
    nifty_candles_3m: pd.DataFrame,
    nifty_candles_15m: pd.DataFrame,
) -> VolumeBundle:
    three = calculate_timeframe_volume(future_candles_3m, nifty_candles_3m, "3 Minute")
    fifteen = calculate_timeframe_volume(
        future_candles_15m, nifty_candles_15m, "15 Minute"
    )
    ready = [item for item in (three, fifteen) if item.status == "READY"]
    if not ready:
        return VolumeBundle(
            source="NIFTY FUTURES",
            three_minute=three,
            fifteen_minute=fifteen,
            overall_view="UNAVAILABLE",
            confidence=0.0,
            status="UNAVAILABLE",
        )

    supports = [item.move_support for item in ready]
    if all("BULLISH" in value for value in supports):
        overall = "BULLISH PARTICIPATION"
    elif all("BEARISH" in value for value in supports):
        overall = "BEARISH PARTICIPATION"
    elif any("LOW PARTICIPATION" in value for value in supports):
        overall = "WEAK / UNCONFIRMED PARTICIPATION"
    elif any("BREAKOUT BUILD-UP" in value for value in supports):
        overall = "ACTIVITY BUILD-UP"
    else:
        overall = "MIXED / NORMAL PARTICIPATION"
    confidence = sum(item.confidence for item in ready) / len(ready)
    return VolumeBundle(
        source="NIFTY FUTURES",
        three_minute=three,
        fifteen_minute=fifteen,
        overall_view=overall,
        confidence=round(confidence, 1),
        status="READY" if len(ready) == 2 else "PARTIAL",
    )
