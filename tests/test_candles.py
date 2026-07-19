from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from analysis.candles import (
    aggregate_candles,
    candles_from_dhan,
    mark_completed_candles,
)


IST = ZoneInfo("Asia/Kolkata")


def test_three_minute_candles_anchor_at_0915():
    timestamps = pd.date_range("2026-07-16 09:15:00", periods=6, freq="1min", tz=IST)
    source = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100, 101, 102, 103, 104, 105],
            "high": [101, 102, 103, 104, 105, 106],
            "low": [99, 100, 101, 102, 103, 104],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
            "volume": [10, 20, 30, 40, 50, 60],
            "open_interest": [None] * 6,
        }
    )
    result = aggregate_candles(source, 3)
    assert list(result["timestamp"].dt.strftime("%H:%M")) == ["09:15", "09:18"]
    assert result.iloc[0]["open"] == 100
    assert result.iloc[0]["close"] == 102.5
    assert result.iloc[0]["volume"] == 60


def test_dhan_payload_parsing_and_completion():
    payload = {
        "open": [100, 101],
        "high": [102, 103],
        "low": [99, 100],
        "close": [101, 102],
        "volume": [1000, 1200],
        "timestamp": [1784173500, 1784173560],
        "open_interest": [0, 0],
    }
    frame = candles_from_dhan(payload)
    assert len(frame) == 2
    marked = mark_completed_candles(
        frame,
        1,
        datetime(2026, 7, 16, 12, 0, tzinfo=IST),
    )
    assert marked["is_complete"].dtype == bool
