from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from analysis.option_intelligence import calculate_option_intelligence
from services.option_state_store import OptionStateStore


IST = ZoneInfo("Asia/Kolkata")


def chain(shift: int = 0) -> pd.DataFrame:
    rows = [
        {
            "strike": 24300.0,
            "side": "CE",
            "is_atm": False,
            "last_price": 145.0 - shift,
            "oi": 900.0 + shift * 5,
            "previous_oi": 800.0,
            "volume": 700.0 + shift * 10,
            "previous_volume": 100.0,
            "previous_close_price": 150.0,
            "day_oi_change": 100.0 + shift * 5,
            "day_price_change": -5.0 - shift,
            "implied_volatility": 12.0,
        },
        {
            "strike": 24300.0,
            "side": "PE",
            "is_atm": False,
            "last_price": 65.0 - shift,
            "oi": 1100.0 + shift * 8,
            "previous_oi": 900.0,
            "volume": 800.0 + shift * 12,
            "previous_volume": 120.0,
            "previous_close_price": 70.0,
            "day_oi_change": 200.0 + shift * 8,
            "day_price_change": -5.0 - shift,
            "implied_volatility": 12.5,
        },
        {
            "strike": 24350.0,
            "side": "CE",
            "is_atm": True,
            "last_price": 100.0 - shift,
            "oi": 1500.0 + shift * 20,
            "previous_oi": 1200.0,
            "volume": 1200.0 + shift * 20,
            "previous_volume": 200.0,
            "previous_close_price": 110.0,
            "day_oi_change": 300.0 + shift * 20,
            "day_price_change": -10.0 - shift,
            "implied_volatility": 13.0,
        },
        {
            "strike": 24350.0,
            "side": "PE",
            "is_atm": True,
            "last_price": 90.0 - shift,
            "oi": 1800.0 + shift * 30,
            "previous_oi": 1400.0,
            "volume": 1500.0 + shift * 25,
            "previous_volume": 250.0,
            "previous_close_price": 100.0,
            "day_oi_change": 400.0 + shift * 30,
            "day_price_change": -10.0 - shift,
            "implied_volatility": 13.5,
        },
        {
            "strike": 24400.0,
            "side": "CE",
            "is_atm": False,
            "last_price": 70.0 - shift,
            "oi": 1300.0 + shift * 12,
            "previous_oi": 1000.0,
            "volume": 1000.0 + shift * 15,
            "previous_volume": 180.0,
            "previous_close_price": 80.0,
            "day_oi_change": 300.0 + shift * 12,
            "day_price_change": -10.0 - shift,
            "implied_volatility": 14.0,
        },
        {
            "strike": 24400.0,
            "side": "PE",
            "is_atm": False,
            "last_price": 125.0 - shift,
            "oi": 1000.0 + shift * 10,
            "previous_oi": 850.0,
            "volume": 900.0 + shift * 10,
            "previous_volume": 140.0,
            "previous_close_price": 130.0,
            "day_oi_change": 150.0 + shift * 10,
            "day_price_change": -5.0 - shift,
            "implied_volatility": 14.5,
        },
    ]
    return pd.DataFrame(rows)


def test_first_snapshot_uses_day_change_and_is_warming_up(tmp_path):
    now = datetime(2026, 7, 20, 10, 0, tzinfo=IST)
    store = OptionStateStore(tmp_path / "state.json")
    current = chain()
    state = store.make_snapshot(
        captured_at=now, expiry="2026-07-21", spot=24340, frame=current
    )
    result = calculate_option_intelligence(
        current_frame=current,
        spot=24340,
        expiry="2026-07-21",
        captured_at=now,
        history=[],
        current_snapshot=state,
        is_live=True,
    )
    assert result.basis == "DAY CHANGE — FIRST SNAPSHOT"
    assert result.status == "WARMING UP"
    assert (
        round(result.bullish_score + result.bearish_score + result.range_score, 1)
        == 100.0
    )
    assert all(window.status == "INSUFFICIENT CONTINUITY" for window in result.windows)


def test_intraday_flow_windows_and_classification(tmp_path):
    now = datetime(2026, 7, 20, 10, 5, tzinfo=IST)
    store = OptionStateStore(tmp_path / "state.json")
    history = []
    for seconds, shift in ((300, 0), (180, 1), (60, 2)):
        frame = chain(shift)
        history.append(
            store.make_snapshot(
                captured_at=now - timedelta(seconds=seconds),
                expiry="2026-07-21",
                spot=24340,
                frame=frame,
            )
        )
    current = chain(4)
    current_state = store.make_snapshot(
        captured_at=now, expiry="2026-07-21", spot=24340, frame=current
    )
    result = calculate_option_intelligence(
        current_frame=current,
        spot=24340,
        expiry="2026-07-21",
        captured_at=now,
        history=history,
        current_snapshot=current_state,
        is_live=True,
    )
    assert result.basis == "INTRADAY SNAPSHOT DELTA"
    assert result.status == "READY"
    assert all(window.status == "READY" for window in result.windows)
    assert len(result.flow_rows) == 6
    atm_ce = next(
        row
        for row in result.flow_rows
        if row["strike"] == 24350.0 and row["side"] == "CE"
    )
    assert atm_ce["classification"] == "SHORT BUILDUP"
    assert atm_ce["directional_bias"] == "BEARISH"
    assert result.ce_wall.strike == 24350.0
    assert result.pe_wall.strike == 24350.0
    assert result.pcr.near_atm_oi_pcr is not None
