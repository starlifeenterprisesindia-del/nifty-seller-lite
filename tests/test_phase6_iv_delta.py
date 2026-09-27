from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from analysis.iv_delta_display import compute_iv_delta_payload

IST = ZoneInfo("Asia/Kolkata")


def _rows(shift: float = 0.0):
    base = [
        (25000, "CE", 101, 12.0),
        (25050, "CE", 102, 13.0),
        (25000, "PE", 201, 14.0),
        (25050, "PE", 202, 15.0),
    ]
    return [
        {
            "strike": strike,
            "side": side,
            "security_id": security_id,
            "implied_volatility": iv + shift,
        }
        for strike, side, security_id, iv in base
    ]


def _snap(at, shift):
    return {
        "captured_at": at.isoformat(),
        "expiry": "2026-10-01",
        "rows": _rows(shift),
    }


def test_phase6_iv_delta_uses_persisted_windows_only():
    now = datetime(2026, 9, 28, 11, 0, tzinfo=IST)
    current = _snap(now, 1.0)
    history = [
        _snap(now - timedelta(seconds=60), 0.0),
        _snap(now - timedelta(seconds=180), -1.0),
        _snap(now - timedelta(seconds=300), -2.0),
    ]

    result = compute_iv_delta_payload(current_snapshot=current, history=history)

    assert result["status"] == "READY"
    assert result["preferred_window"] == "3m"
    windows = {row["label"]: row for row in result["windows"]}
    assert windows["1m"]["ce_iv_delta"] == 1.0
    assert windows["1m"]["pe_iv_delta"] == 1.0
    assert windows["3m"]["ce_iv_delta"] == 2.0
    assert windows["5m"]["pe_iv_delta"] == 3.0
    assert result["preferred_strikes"]["CE"][25000.0] == 2.0
    assert result["preferred_strikes"]["PE"][25050.0] == 2.0


def test_phase6_iv_delta_warms_up_without_history():
    now = datetime(2026, 9, 28, 11, 0, tzinfo=IST)
    result = compute_iv_delta_payload(current_snapshot=_snap(now, 0.0), history=[])
    assert result["status"] == "WARMING UP"
    assert result["preferred_window"] is None
    assert all(row["status"] == "WARMING UP" for row in result["windows"])
