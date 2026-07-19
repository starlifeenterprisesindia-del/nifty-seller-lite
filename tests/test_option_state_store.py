from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from services.option_state_store import OptionStateStore


IST = ZoneInfo("Asia/Kolkata")


def frame(oi: float = 1000.0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "strike": 24350.0,
                "side": "CE",
                "last_price": 100.0,
                "oi": oi,
                "previous_oi": 900.0,
                "volume": 500.0,
                "previous_volume": 100.0,
                "previous_close_price": 110.0,
                "day_oi_change": oi - 900.0,
                "day_price_change": -10.0,
                "implied_volatility": 12.0,
                "is_atm": True,
            }
        ]
    )


def test_store_dedupes_quick_identical_snapshot(tmp_path):
    store = OptionStateStore(tmp_path / "state.json")
    start = datetime(2026, 7, 20, 10, 0, tzinfo=IST)
    first = store.make_snapshot(
        captured_at=start, expiry="2026-07-21", spot=24340, frame=frame()
    )
    history, appended = store.append(first)
    assert appended
    assert len(history) == 1

    duplicate = store.make_snapshot(
        captured_at=start + timedelta(seconds=10),
        expiry="2026-07-21",
        spot=24340,
        frame=frame(),
    )
    history, appended = store.append(duplicate)
    assert not appended
    assert len(history) == 1


def test_store_keeps_only_current_date_sessions(tmp_path):
    store = OptionStateStore(tmp_path / "state.json")
    day1 = datetime(2026, 7, 20, 10, 0, tzinfo=IST)
    day2 = datetime(2026, 7, 21, 10, 0, tzinfo=IST)
    store.append(
        store.make_snapshot(
            captured_at=day1, expiry="2026-07-21", spot=24340, frame=frame()
        )
    )
    store.append(
        store.make_snapshot(
            captured_at=day2, expiry="2026-07-28", spot=24400, frame=frame(1200)
        )
    )
    assert store.load_session(captured_at=day1, expiry="2026-07-21") == []
    assert len(store.load_session(captured_at=day2, expiry="2026-07-28")) == 1


def test_store_clear_removes_active_file(tmp_path):
    store = OptionStateStore(tmp_path / "state.json")
    now = datetime(2026, 7, 20, 10, 0, tzinfo=IST)
    store.append(
        store.make_snapshot(
            captured_at=now, expiry="2026-07-21", spot=24340, frame=frame()
        )
    )
    assert store.path.exists()
    store.clear()
    assert not store.path.exists()
