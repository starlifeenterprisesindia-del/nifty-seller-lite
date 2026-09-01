from copy import deepcopy
from datetime import datetime
import json

from analysis.cycle_prices import cycle_prices, selected_rows, contract_key
from ui.timeframe_outlook import _pick, build_timeframe_rows
from services.day_memory import DayMemory, IST


def sample(time="09:30:12", side="CE", expiry="2026-09-01", price=100):
    return {"at": f"2026-08-28T{time}+05:30", "expiry": expiry, "spot": 24100,
            "feeds": {k: {"use_state": "LIVE"} for k in ("quotes", "option_chain")},
            "options": [{"strike": 24200, "side": side, "security_id": 7 if side == "CE" else 8,
                         "last_price": price, "oi": 1000, "implied_volatility": 12}]}


def test_exact_slots_no_nearest_or_expiry_mix():
    data = cycle_prices([sample(), sample("15:28:00"), sample("15:30:00", expiry="2026-09-08")], "2026-09-01")
    assert len(data["rows"]) == 2
    assert data["rows"][0]["observed_at"].endswith("09:30:12+05:30")
    assert data["rows"][1]["spot"] is None
    assert data["rows"][1]["options"] == {}


def test_two_sides_selection_and_missing_not_zero():
    first = sample()
    first["options"] += sample(side="PE", price=70)["options"]
    last = deepcopy(first)
    last["at"] = "2026-08-28T15:30:05+05:30"
    last["options"][0]["last_price"] = 80
    last["options"][1]["last_price"] = 65
    data = cycle_prices([first, last], "2026-09-01")
    rows = selected_rows(data, "24200|CE|7", "24200|PE|8")
    assert rows[0]["CE LTP"] == 100 and rows[1]["PE LTP"] == 65
    assert selected_rows(data, "wrong", "wrong")[0]["CE LTP"] is None


def test_duplicates_stale_nonfinite_and_ids():
    first = sample()
    first["options"] *= 2
    assert not cycle_prices([first], "2026-09-01")["contracts"]
    first = sample(price=float("nan"))
    assert not cycle_prices([first], "2026-09-01")["contracts"]
    first = sample()
    first["feeds"]["option_chain"]["use_state"] = "STALE"
    assert not cycle_prices([first], "2026-09-01")["contracts"]
    assert contract_key({"strike":24200,"side":"CE","security_id":7.0}) == "24200|CE|7"


def test_report_restart_uses_saved_cycle(tmp_path):
    from test_day_memory import snapshot
    path = tmp_path / "history.sqlite3"
    store = DayMemory(path)
    store.record(snapshot(datetime(2026, 8, 28, 9, 30, 12, tzinfo=IST)))
    report = DayMemory(path).report()
    assert report["cycle_prices"]["rows"][0]["spot"] == 24100
    assert report["cycle_prices"]["rows"][1]["spot"] is None
    assert report["counts"]["samples"] == 1


def test_pick_missing_tied_not_false_bullish():
    assert _pick(0,0,0)[0] == "INSUFFICIENT DATA"
    assert _pick(50,50,0)[0] == "MIXED"
    assert _pick(float("nan"), None, float("inf"))[0] == "INSUFFICIENT DATA"


def test_initial_contracts_retained_after_spot_moves(tmp_path):
    from test_day_memory import snapshot
    store = DayMemory(tmp_path / "history.sqlite3")
    store.record(snapshot(datetime(2026,8,28,9,30,tzinfo=IST),spot=24100))
    store.record(snapshot(datetime(2026,8,28,10,30,tzinfo=IST),spot=25000))
    with store.connect() as db:
        saved = json.loads(db.execute("SELECT body FROM samples ORDER BY at DESC LIMIT 1").fetchone()[0])
    assert any(r["strike"] == 23600 for r in saved["options"])


def test_horizons_do_not_change_decision_or_accept_cached_impulse():
    from test_snapshot_service import StubFutureClient, StubFutureMaster
    from services.snapshot_service import SnapshotService
    from types import SimpleNamespace as NS
    snapshot = SnapshotService(StubFutureClient(),StubFutureMaster()).build(datetime(2026,7,19,13,37,tzinfo=IST))
    decision = deepcopy(snapshot.decision)
    before = build_timeframe_rows(snapshot)
    after = build_timeframe_rows(snapshot,NS(direction="BULLISH", score=100))
    assert before == after
    assert [r["Time"] for r in before] == ["5 min","15 min","30 min","1 hour","1 day"]
    assert before[-1]["Evidence /100"] is None
    assert snapshot.decision == decision


def test_cycle_ui_renders_and_one_day_has_no_probability():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
import sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/"tests"))
from test_v2408_cycle_outlook import sample
from analysis.cycle_prices import cycle_prices
from ui.day_memory import render_cycle_prices
render_cycle_prices(cycle_prices([sample(),sample("15:30:10",side="PE")],"2026-09-01"))
''').run(timeout=30)
    assert not app.exception
    assert len(app.selectbox) == 2
