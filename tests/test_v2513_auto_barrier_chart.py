from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from ui.live_barrier_chart import build_live_barrier_chart_payload


def _level(label, side, lower, upper, strength, pressure):
    return SimpleNamespace(
        label=label,
        side=side,
        lower=lower,
        upper=upper,
        midpoint=(lower + upper) / 2,
        strength=strength,
        break_pressure=pressure,
        state="TESTING",
    )


def test_chart_reuses_snapshot_candles_and_barriers_only():
    times = pd.date_range("2026-09-29 09:15", periods=40, freq="min", tz="Asia/Kolkata")
    frame = pd.DataFrame({
        "timestamp": times,
        "open": range(100, 140),
        "high": range(101, 141),
        "low": range(99, 139),
        "close": range(100, 140),
    })
    barriers = SimpleNamespace(
        current_price=22684.0,
        nearest_resistance=_level("R1", "RESISTANCE", 22700, 22710, 80, 43),
        next_resistance=_level("R2", "RESISTANCE", 22745, 22755, 82, 25),
        nearest_support=_level("S1", "SUPPORT", 22655, 22666, 75, 32),
        next_support=_level("S2", "SUPPORT", 22620, 22633, 88, 22),
    )
    snapshot = SimpleNamespace(
        snapshot_id="SNAP-test",
        created_at=datetime(2026, 9, 29, 15, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        barrier_map=barriers,
        nifty_quote={"last_price": 22684.0},
        candles_1m=frame,
        candles_3m=frame.iloc[::3].copy(),
        candles_15m=frame.iloc[::15].copy(),
    )
    payload = build_live_barrier_chart_payload(snapshot)
    assert payload["spot"] == 22684.0
    assert payload["defaultTf"] == "15m"
    assert [item["label"] for item in payload["barriers"]] == ["R1", "R2", "S1", "S2"]
    assert payload["candles"]["1m"]
    assert payload["candles"]["3m"]
    assert payload["candles"]["15m"]
