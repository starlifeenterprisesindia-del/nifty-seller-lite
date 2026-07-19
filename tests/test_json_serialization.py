from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from models import _json_safe


def test_json_safe_converts_nested_dates_and_tuples_to_iso_values():
    value = {
        "created": datetime(2026, 7, 20, 10, 15, tzinfo=ZoneInfo("Asia/Kolkata")),
        "as_of": pd.Timestamp("2026-07-20T10:15:00+05:30"),
        "window": time(11, 30),
        "items": (datetime(2026, 7, 20, 10, 18),),
    }
    result = _json_safe(value)
    assert result["created"] == "2026-07-20T10:15:00+05:30"
    assert result["as_of"] == "2026-07-20T10:15:00+05:30"
    assert result["window"] == "11:30"
    assert result["items"] == ["2026-07-20T10:18:00"]
