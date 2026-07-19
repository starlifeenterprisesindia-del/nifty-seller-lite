from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from config import IST_TIMEZONE


REQUIRED_ARRAYS = ("open", "high", "low", "close", "volume", "timestamp")


def candles_from_dhan(payload: dict[str, Any]) -> pd.DataFrame:
    lengths = [len(payload.get(name, [])) for name in REQUIRED_ARRAYS]
    if not lengths or min(lengths, default=0) == 0:
        return pd.DataFrame(
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "open_interest",
            ]
        )
    size = min(lengths)
    frame = pd.DataFrame(
        {name: payload.get(name, [])[:size] for name in REQUIRED_ARRAYS}
    )
    oi = payload.get("open_interest", [])
    frame["open_interest"] = list(oi[:size]) + [None] * max(0, size - len(oi))
    frame["timestamp"] = pd.to_datetime(
        frame["timestamp"], unit="s", utc=True
    ).dt.tz_convert(IST_TIMEZONE)
    for col in ("open", "high", "low", "close", "volume", "open_interest"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(
        subset=["timestamp", "open", "high", "low", "close"]
    ).sort_values("timestamp")
    return frame.drop_duplicates(subset=["timestamp"], keep="last").reset_index(
        drop=True
    )


def aggregate_candles(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    source = frame.copy().set_index("timestamp").sort_index()
    output: list[pd.DataFrame] = []
    for trade_date, day in source.groupby(source.index.date):
        origin = pd.Timestamp(f"{trade_date} 09:15:00", tz=IST_TIMEZONE)
        resampled = day.resample(
            f"{minutes}min",
            origin=origin,
            label="left",
            closed="left",
        ).agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "open_interest": "last",
            }
        )
        output.append(resampled.dropna(subset=["open", "high", "low", "close"]))
    if not output:
        return pd.DataFrame(columns=frame.columns)
    return (
        pd.concat(output).reset_index().sort_values("timestamp").reset_index(drop=True)
    )


def mark_completed_candles(
    frame: pd.DataFrame,
    interval_minutes: int,
    now: datetime,
) -> pd.DataFrame:
    result = frame.copy()
    if result.empty:
        result["is_complete"] = pd.Series(dtype=bool)
        return result
    current = pd.Timestamp(now)
    if current.tzinfo is None:
        current = current.tz_localize(IST_TIMEZONE)
    else:
        current = current.tz_convert(IST_TIMEZONE)
    result["is_complete"] = (
        result["timestamp"] + pd.to_timedelta(int(interval_minutes), unit="min")
        <= current
    )
    return result
