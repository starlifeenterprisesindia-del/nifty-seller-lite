from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import CONFIG


@dataclass(frozen=True)
class SwingPoint:
    index: int
    timestamp: pd.Timestamp
    price: float
    kind: str


def completed_candles(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    source = frame.copy()
    if "is_complete" in source.columns:
        source = source[source["is_complete"].fillna(False)]
    required = ["timestamp", "open", "high", "low", "close"]
    if any(column not in source.columns for column in required):
        return pd.DataFrame(columns=source.columns)
    for column in ["open", "high", "low", "close", "volume", "open_interest"]:
        if column in source.columns:
            source[column] = pd.to_numeric(source[column], errors="coerce")
    return (
        source.dropna(subset=required)
        .sort_values("timestamp")
        .drop_duplicates(subset=["timestamp"], keep="last")
        .reset_index(drop=True)
    )


def true_range(frame: pd.DataFrame) -> pd.Series:
    source = completed_candles(frame)
    if source.empty:
        return pd.Series(dtype=float)
    previous_close = source["close"].shift(1)
    ranges = pd.concat(
        [
            source["high"] - source["low"],
            (source["high"] - previous_close).abs(),
            (source["low"] - previous_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr_value(frame: pd.DataFrame, period: int | None = None) -> float | None:
    period = period or CONFIG.atr_period
    ranges = true_range(frame).dropna()
    if ranges.empty:
        return None
    window = ranges.tail(period)
    if len(window) < min(5, period):
        return None
    return float(window.mean())


def confirmed_swings(
    frame: pd.DataFrame,
    *,
    left: int | None = None,
    right: int | None = None,
) -> tuple[list[SwingPoint], list[SwingPoint]]:
    source = completed_candles(frame)
    left = CONFIG.swing_left_bars if left is None else left
    right = CONFIG.swing_right_bars if right is None else right
    highs: list[SwingPoint] = []
    lows: list[SwingPoint] = []
    if len(source) < left + right + 1:
        return highs, lows

    high_values = source["high"].tolist()
    low_values = source["low"].tolist()
    for index in range(left, len(source) - right):
        high = float(high_values[index])
        low = float(low_values[index])
        left_highs = high_values[index - left : index]
        right_highs = high_values[index + 1 : index + right + 1]
        left_lows = low_values[index - left : index]
        right_lows = low_values[index + 1 : index + right + 1]

        # Requiring a strict comparison on at least one side prevents flat plateaus
        # from generating several duplicate swing points.
        is_high = (
            high >= max(left_highs)
            and high >= max(right_highs)
            and (high > max(left_highs) or high > max(right_highs))
        )
        is_low = (
            low <= min(left_lows)
            and low <= min(right_lows)
            and (low < min(left_lows) or low < min(right_lows))
        )
        timestamp = pd.Timestamp(source.iloc[index]["timestamp"])
        if is_high:
            highs.append(SwingPoint(index, timestamp, high, "HIGH"))
        if is_low:
            lows.append(SwingPoint(index, timestamp, low, "LOW"))
    return highs, lows


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
