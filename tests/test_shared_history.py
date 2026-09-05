from datetime import datetime, timedelta
import json

from services.shared_history import bounded, read_history
from analysis.option_intelligence import _choose_history_sample


NOW = datetime.fromisoformat("2026-08-28T11:07:42+05:30")


def test_boundaries_and_expiry():
    def row(seconds, expiry="2026-09-01"):
        return {"captured_at": (NOW-timedelta(seconds=seconds)).isoformat(), "expiry": expiry}
    valid = row(76)
    assert bounded([valid, valid, row(-1), row(1801), row(80, "other"), {}], NOW, "captured_at", "2026-09-01") == [valid]


def test_overnight_and_naive_rejected():
    now = datetime.fromisoformat("2026-08-28T00:01:00+05:30")
    assert bounded([{"at": "2026-08-27T23:59:00+05:30"}, {"at": "2026-08-28T00:00:00"}], now, "at") == []


def test_delayed_timer_is_bounded():
    rows = [{"captured_at": (NOW-timedelta(seconds=76)).isoformat()}]
    assert _choose_history_sample(rows, NOW, 60)[1] == 76
    rows[0]["captured_at"] = (NOW-timedelta(seconds=91)).isoformat()
    assert _choose_history_sample(rows, NOW, 60) == (None, None)


def test_shared_files_read_only(tmp_path):
    row = {"captured_at": (NOW-timedelta(seconds=80)).isoformat(), "expiry": "2026-09-01"}
    options = json.dumps({"sessions": {"2026-08-28|2026-09-01": [row]}})
    (tmp_path / "options.json").write_text(options)
    (tmp_path / "top9.json").write_text(json.dumps([{"at": (NOW-timedelta(minutes=15)).isoformat(), "prices": {"HDFCBANK": 100}}]))
    result = read_history(tmp_path, NOW, "2026-09-01")
    assert result["options"] == [row]
    assert len(result["top9"]) == 1
    assert (tmp_path / "options.json").read_text() == options
    assert read_history(tmp_path, NOW, "2026-09-08")["options"] == []


def test_missing_files_do_not_fabricate_data(tmp_path):
    assert read_history(tmp_path, NOW, "2026-09-01") == {"options": [], "top9": []}


def test_malformed_rows_ignored():
    assert bounded([None, 12, "bad", {}], NOW, "at") == []


def test_history_endpoint_auth(monkeypatch):
    import pytest
    import live_server
    from fastapi import HTTPException
    monkeypatch.setenv("LIVE_API_KEY", "test-history-key")
    with pytest.raises(HTTPException) as error:
        live_server.market_history({}, "wrong")
    assert error.value.status_code == 401
    monkeypatch.setattr(live_server.DAY_RECORDER, "history_root", None)
    assert live_server.market_history({}, "test-history-key")["data"] == {"options": [], "top9": []}


def test_top9_from_recorded_observations():
    from config import CONFIG
    from analysis.heavyweights import calculate_heavyweight_bundle
    quotes = [{"symbol": item.symbol, "last_price": 101, "ohlc": {"close": 100}} for item in CONFIG.top9]
    prices = {item.symbol: 100 for item in CONFIG.top9}
    history = [{"at": (NOW-timedelta(seconds=seconds)).isoformat(), "prices": prices, "nifty": 24000} for seconds in (970, 250)]
    result = calculate_heavyweight_bundle(quotes, NOW, history=history)
    assert result.recent_15m_move_pct is not None
    assert result.recent_3m_move_pct is not None
    assert result.recent_state != "WARMING UP"


def test_old_or_partial_top9_universe_cannot_drive_recent_signal():
    from config import CONFIG
    from analysis.heavyweights import calculate_heavyweight_bundle
    quotes = [{"symbol": item.symbol, "last_price": 101, "ohlc": {"close": 100}} for item in CONFIG.top9]
    old_prices = {item.symbol: 100 for item in CONFIG.top9 if item.symbol != "KOTAKBANK"}
    old_prices["BAJFINANCE"] = 100
    history = [{"at": (NOW-timedelta(minutes=15)).isoformat(), "prices": old_prices, "nifty": 24000}]
    result = calculate_heavyweight_bundle(quotes, NOW, history=history)
    assert result.recent_15m_move_pct is None
    assert result.recent_state == "WARMING UP"
