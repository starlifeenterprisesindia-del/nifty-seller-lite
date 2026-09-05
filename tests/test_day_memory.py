from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from threading import RLock
import gzip

import pandas as pd
import pytest

from services.day_memory import DayMemory, IST, recording_time, candle_reaction
from services.day_recorder import DayRecorder, GatewayReader


def snapshot(at, *, spot=24100, live=True):
    feeds = {k: NS(use_state="LIVE" if live else "STALE") for k in ("quotes", "candles", "option_chain")}
    bars = pd.DataFrame([{"timestamp": at - timedelta(minutes=1), "open": spot, "high": spot+2,
                          "low": spot-2, "close": spot, "volume": 10, "open_interest": 20, "is_complete": True}])
    chain = pd.DataFrame([{"strike": k, "side": side, "security_id": str(k)+side,
                          "last_price": 100, "oi": 100000, "volume": 1000,
                          "implied_volatility": 12, "top_bid_price": 99, "top_ask_price": 101}
                         for k in range(23600, 24601, 50) for side in ("CE", "PE")])
    level = {"lower": 24098, "upper": 24104, "side": "RESISTANCE"}
    data = {"created_at": at.isoformat(), "nifty_last_price": spot, "expiry": "2026-09-01",
            "market_session": {"is_live": live}, "feeds": {k: {"use_state": v.use_state} for k,v in feeds.items()},
            "indicators": {"3m": {"rsi14": 50}, "15m": {"rsi14": 40}},
            "barrier_map": {"nearest_resistance": level}, "big_player_activity": {"direction": "BUYING", "state": "NORMAL", "confirmation_count": 0},
            "decision": {"market_direction": "BEARISH", "final_action": "WAIT"}}
    return NS(created_at=at, feed_status=feeds, public_summary=lambda: data, option_chain=chain,
              nifty_future_quote={}, vix_quote={}, heavyweight_quotes=[{"symbol": str(i), "last_price": 100} for i in range(9)],
              metadata={"version": "TEST"}, candles_1m=bars, future_candles_1m=bars,
              candles_3m=bars, market_session=NS(is_live=False))


def test_restart_dedupe_and_no_repeat_testing(tmp_path):
    path = tmp_path / "day.sqlite3"
    at = datetime(2026, 8, 27, 10, tzinfo=IST)
    store = DayMemory(path)
    assert store.record(snapshot(at))
    assert not store.record(snapshot(at))
    restarted = DayMemory(path)
    assert restarted.record(snapshot(at+timedelta(minutes=1)))
    events = [x for x in restarted.report()["events"] if x["kind"] == "BARRIER"]
    assert len(events) == 1
    assert restarted.report()["counts"]["samples"] == 2


def test_roll_only_on_fresh_session(tmp_path):
    store = DayMemory(tmp_path / "day.sqlite3")
    at = datetime(2026, 8, 27, 10, tzinfo=IST)
    store.record(snapshot(at))
    assert not store.record(snapshot(at+timedelta(days=1), live=False))
    assert store.report()["day"] == "2026-08-27"
    store.record(snapshot(at+timedelta(days=1)))
    assert store.report()["counts"]["samples"] == 2  # same expiry: preserve previous session
    assert store.report()["day"] == "2026-08-28"
    with pytest.raises(ValueError):
        store.record(snapshot(at))


def test_gap_and_missing_options(tmp_path):
    store = DayMemory(tmp_path / "day.sqlite3")
    at = datetime(2026, 8, 27, 10, tzinfo=IST)
    store.record(snapshot(at))
    item = snapshot(at+timedelta(minutes=5))
    item.feed_status["option_chain"].use_state = "STALE"
    store.record(item)
    assert any(x.get("status") == "GAP" for x in store.report()["events"])
    import json
    with store.connect() as db:
        data = json.loads(db.execute("SELECT body FROM samples ORDER BY at DESC LIMIT 1").fetchone()[0])
    assert data["options"] == []


def test_actual_app_event_separate_and_stale_rejected(tmp_path):
    store = DayMemory(tmp_path / "day.sqlite3")
    at = datetime(2026, 8, 27, 10, tzinfo=IST)
    store.record(snapshot(at))
    event = {"at": at.isoformat(), "action": "WAIT", "reason": "BLOCKED"}
    assert store.app_event(at, event)
    assert store.app_event(at, event)
    assert not store.app_event(at+timedelta(minutes=4), event)
    assert len([x for x in store.report()["events"] if x["kind"] == "APP AI"]) == 1


def test_streaming_export_writes_complete_gzip_file(tmp_path):
    store = DayMemory(tmp_path / "day.sqlite3")
    at = datetime(2026, 8, 27, 10, tzinfo=IST)
    store.record(snapshot(at))
    target = store.export_file(tmp_path / "full-evidence.jsonl.gz")
    with gzip.open(target, "rt", encoding="utf-8") as handle:
        text = handle.read()
    assert '"format":"nifty-evidence-jsonl"' in text
    assert '"table":"samples"' in text


def test_old_zone_is_tracked_after_nearest_changes(tmp_path):
    store = DayMemory(tmp_path / "day.sqlite3")
    at = datetime(2026, 8, 27, 10, tzinfo=IST)
    store.record(snapshot(at))
    item = snapshot(at+timedelta(minutes=2), spot=24110)
    data = item.public_summary()
    data["barrier_map"] = {}
    item.public_summary = lambda: data
    store.record(item)
    assert any(x.get("status") == "BREAK — 3m CLOSE" for x in store.report()["events"])


def test_recorder_requires_volume(monkeypatch):
    monkeypatch.setenv("DAY_MEMORY_ENABLED", "1")
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    recorder = DayRecorder(lambda: None)
    recorder.start()
    assert recorder.store is None
    assert "BLOCKED" in recorder.status


def test_gateway_rejects_failure_fallback():
    gateway = NS(last_error="429", market_quote=lambda _: {}, _lock=RLock())
    with pytest.raises(RuntimeError):
        GatewayReader(gateway).market_quote({})


def test_retest_requires_previous_break():
    level = {"lower": 100, "upper": 105, "side": "RESISTANCE"}
    bar = {"low": 103, "high": 108, "close": 107}
    state, broken = candle_reaction(level, bar)
    assert state == "BREAK — 3m CLOSE"
    assert broken
    level["broken"] = True
    assert candle_reaction(level, bar)[0] == "RETEST HOLD — 3m CLOSE"
    assert candle_reaction(level, {"low": 95, "high": 106, "close": 99}) == ("BREAK FAILED — 3m CLOSE", False)


def test_support_retest_and_rejection():
    level = {"lower": 100, "upper": 105, "side": "SUPPORT"}
    assert candle_reaction(level, {"low": 102, "high": 108, "close": 107})[0] == "REJECTION — 3m CLOSE"
    level["broken"] = True
    assert candle_reaction(level, {"low": 95, "high": 102, "close": 98})[0] == "RETEST HOLD — 3m CLOSE"


def test_endpoint_requires_key(monkeypatch):
    import asyncio
    import live_server
    monkeypatch.setenv("LIVE_API_KEY", "test-only")
    monkeypatch.setattr(live_server, "DAY_RECORDER", NS(store=None, report=lambda: {"events": []}))
    async def request(headers):
        sent = []
        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}
        async def send(message):
            sent.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "method": "POST",
                 "path": "/day-memory", "raw_path": b"/day-memory", "query_string": b"",
                 "headers": [(b"content-type", b"application/json")] + headers,
                 "scheme": "http", "server": ("test", 80), "client": ("test", 1), "root_path": "", "http_version": "1.1"}
        await live_server.app(scope, receive, send)
        return next(x["status"] for x in sent if x["type"] == "http.response.start")
    assert asyncio.run(request([])) == 401
    assert asyncio.run(request([(b"x-live-key", b"test-only")])) == 200
    monkeypatch.delenv("LIVE_API_KEY")
    assert asyncio.run(request([])) == 503


def test_compact_real_snapshot_schema(tmp_path, monkeypatch):
    from test_snapshot_service import StubClient, StubMaster
    from services.snapshot_service import SnapshotService
    from services.day_memory import compact, encode
    monkeypatch.chdir(tmp_path)
    item = SnapshotService(StubClient(), StubMaster()).build(now=datetime(2026,7,18,12,tzinfo=IST))
    data = compact(item)
    assert data["options"] == []  # closed-session data cannot masquerade as live options
    assert "access-token" not in encode(data)


def test_only_one_worker_on_volume(tmp_path, monkeypatch):
    monkeypatch.setenv("DAY_MEMORY_ENABLED", "1")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    monkeypatch.setattr(DayRecorder, "_run", lambda self, root: self.stop_event.wait(5))
    first, second = DayRecorder(lambda: None), DayRecorder(lambda: None)
    try:
        first.start()
        second.start()
        assert first.store is not None
        assert "already running" in second.status
    finally:
        first.stop()
        second.stop()


@pytest.mark.parametrize("day,hour,minute,expected", [(27,9,14,False),(27,9,15,True),(27,15,31,True),(27,15,32,False),(29,10,0,False)])
def test_recording_window(day,hour,minute,expected):
    assert recording_time(datetime(2026,8,day,hour,minute,tzinfo=IST)) == expected


def test_one_day_size(tmp_path):
    store = DayMemory(tmp_path / "day.sqlite3")
    at = datetime(2026,8,27,9,16,tzinfo=IST)
    for minute in range(375):
        store.record(snapshot(at+timedelta(minutes=minute)))
    report = store.report()
    assert report["counts"]["samples"] == 375
    assert report["counts"]["candles"] == 750
    assert report["bytes"] < 15 * 1048576
    print("Synthetic day SQLite bytes:", report["bytes"])
