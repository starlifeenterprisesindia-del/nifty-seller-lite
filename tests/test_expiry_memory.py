import json
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from analysis.history_context import history_context
from services.day_memory import DayMemory, IST
from services.cycle_outcomes import frozen_basket, mark_spread
from test_day_memory import snapshot


def for_expiry(at, expiry="2026-09-01"):
    item = snapshot(at)
    data = item.public_summary()
    data["expiry"] = expiry
    item.public_summary = lambda:data
    return item


def signal(at):
    return {"at":at.isoformat(),"action":"WAIT","candidate":"CE SELL","expiry":"2026-09-01",
            "version":"TEST","spot":24100,"fresh":True,"score":55,
            "legs":[{"role":"SELL","strike":24100,"side":"CE","security_id":"24100CE","top_bid_price":100,"top_ask_price":102},
                    {"role":"HEDGE","strike":24150,"side":"CE","security_id":"24150CE","top_bid_price":50,"top_ask_price":52}]}


def test_cycle_retains_five_sessions_and_archives(tmp_path):
    store=DayMemory(tmp_path/"memory.db")
    for month,day in ((8,26),(8,27),(8,28),(8,31),(9,1)):
        assert store.record(for_expiry(datetime(2026,month,day,10,tzinfo=IST)))
    report=store.report()
    assert report["counts"]["samples"]==5
    assert report["cycle_expiry"]=="2026-09-01"
    assert not report["cycle_summaries"]
    assert store.record(for_expiry(datetime(2026,9,2,10,tzinfo=IST),"2026-09-08"))
    report=store.report()
    assert report["counts"]["samples"]==1
    assert report["cycle_summaries"][0]["sessions"]==5


def test_early_next_expiry_selection_does_not_purge(tmp_path):
    store=DayMemory(tmp_path/"memory.db")
    at=datetime(2026,8,27,10,tzinfo=IST)
    store.record(for_expiry(at))
    store.record(for_expiry(at+timedelta(minutes=1),"2026-09-08"))
    assert store.report()["counts"]["samples"]==2
    assert store.report()["cycle_expiry"]=="2026-09-01"


def test_archive_failure_rolls_back(tmp_path,monkeypatch):
    store=DayMemory(tmp_path/"memory.db")
    store.record(for_expiry(datetime(2026,8,31,10,tzinfo=IST),"2026-08-31"))  # holiday-adjusted date, no Tuesday rule
    def fail(*args):
        raise RuntimeError("test archive failure")
    monkeypatch.setattr("services.cycle_outcomes.cycle_summary",fail)
    with pytest.raises(RuntimeError):
        store.record(for_expiry(datetime(2026,9,1,10,tzinfo=IST)))
    assert store.report()["counts"]["samples"]==1
    assert store.report()["cycle_expiry"]=="2026-08-31"


def test_eight_summaries_and_restart(tmp_path):
    path=tmp_path/"memory.db"
    store=DayMemory(path)
    at=datetime(2026,8,26,10,tzinfo=IST)
    for week in range(11):
        dt=at+timedelta(days=7*week)
        expiry=(dt+timedelta(days=6)).date().isoformat()
        store.record(for_expiry(dt,expiry))
    report=DayMemory(path).report()
    assert len(report["cycle_summaries"])==8
    assert report["counts"]["samples"]==1


def test_full_cycle_archives_are_automatically_bounded(tmp_path, monkeypatch):
    from services import day_memory
    monkeypatch.setattr(day_memory, "CONFIG", replace(
        day_memory.CONFIG,
        day_memory_archive_keep_cycles=2,
        day_memory_archive_max_mb=60,
    ))
    directory = tmp_path / "archives"
    directory.mkdir()
    for index, name in enumerate(("2026-08-18", "2026-08-25", "2026-09-01")):
        path = directory / f"{name}-evidence.jsonl.gz"
        path.write_bytes(b"x" * (index + 1))
        path.touch()
    result = DayMemory(tmp_path / "memory.db").prune_archives()
    assert result["retained"] == 2
    assert not (directory / "2026-08-18-evidence.jsonl.gz").exists()
    assert (directory / "2026-08-25-evidence.jsonl.gz").exists()
    assert (directory / "2026-09-01-evidence.jsonl.gz").exists()


def test_legacy_day_db_migration_preserves_data(tmp_path):
    store=DayMemory(tmp_path/"memory.db")
    at=datetime(2026,8,27,10,tzinfo=IST)
    store.record(for_expiry(at))
    with store.connect() as db:
        db.execute("DELETE FROM meta WHERE key='cycle'")
    store.record(for_expiry(at+timedelta(days=1)))
    assert store.report()["counts"]["samples"]==2


def test_frozen_spread_and_outcome(tmp_path):
    store=DayMemory(tmp_path/"memory.db")
    at=datetime(2026,8,27,10,tzinfo=IST)
    store.record(for_expiry(at))
    store.app_event(at,signal(at))
    for minute in range(1,6):
        item=for_expiry(at+timedelta(minutes=minute))
        f=item.option_chain
        f.loc[(f.strike==24100)&(f.side=="CE"),["top_bid_price","top_ask_price"]]=[80,82] if minute==5 else [100,102]
        f.loc[(f.strike==24150)&(f.side=="CE"),["top_bid_price","top_ask_price"]]=[40,42] if minute==5 else [50,52]
        store.record(item)
    outcome=store.report()["outcomes"][0]
    assert outcome["spread_points"]==6
    assert outcome["observed_max_loss_points"]==4
    assert outcome["spread_path_complete"]
    assert outcome["action"]=="WAIT"  # no fabricated trade/fill
    with store.connect() as db:
        old=json.loads(db.execute("SELECT body FROM signals").fetchone()[0])
    assert old["legs"][0]["strike"]==24100


def test_missing_quotes_and_horizon_no_catchup(tmp_path):
    store=DayMemory(tmp_path/"memory.db")
    at=datetime(2026,8,27,10,tzinfo=IST)
    store.record(for_expiry(at))
    store.app_event(at,signal(at))
    item=for_expiry(at+timedelta(minutes=5))
    item.feed_status["option_chain"].use_state="STALE"
    store.record(item)
    outcome=store.report()["outcomes"][0]
    assert outcome["spread_points"] is None
    assert outcome["coverage"]=="GAPS"
    store.record(for_expiry(at+timedelta(minutes=20)))
    missing=next(x for x in store.report()["outcomes"] if x["horizon_minutes"]==15)
    assert missing["status"]=="UNAVAILABLE"


def test_bad_book_or_wrong_contract_not_profit():
    at=datetime(2026,8,27,10,tzinfo=IST)
    body=signal(at)
    legs,credit=frozen_basket(body)
    frozen={**body,"legs":legs,"entry_credit":credit}
    sample={"expiry":body["expiry"],"version":"TEST","options":body["legs"]}
    assert mark_spread(frozen,sample)==-4
    sample["expiry"]="2026-09-08"
    assert mark_spread(frozen,sample) is None
    body["legs"][0]["top_ask_price"]=90  # crossed book
    assert frozen_basket(body)==([],None)


def test_debit_spread_basket_records_and_marks_both_legs():
    body = {
        "expiry": "2026-09-01", "version": "TEST",
        "legs": [
            {"role": "BUY", "side": "CE", "strike": 24100,
             "security_id": "11", "top_bid_price": 99, "top_ask_price": 101},
            {"role": "SELL", "side": "CE", "strike": 24200,
             "security_id": "12", "top_bid_price": 49, "top_ask_price": 51},
        ],
    }
    legs, debit = frozen_basket(body)
    assert debit == 52
    signal_row = {**body, "legs": legs, "structure_type": "DEBIT", "entry_debit": debit}
    sample = {"expiry": body["expiry"], "version": "TEST", "options": [
        {**body["legs"][0], "top_bid_price": 110, "top_ask_price": 112},
        {**body["legs"][1], "top_bid_price": 45, "top_ask_price": 47},
    ]}
    assert mark_spread(signal_row, sample) == 11


def context_sample(at,**kwargs):
    return {"at":at.isoformat(),"expiry":"2026-09-01","version":"TEST","direction":"BEARISH",**kwargs}


def test_history_is_explanation_only_and_rejects_future_stale():
    at=datetime(2026,8,27,10,tzinfo=IST)
    item=NS(created_at=at,market_session=NS(is_live=True),expiry="2026-09-01",metadata={"version":"TEST"},decision=NS(market_direction="BEARISH",score=80))
    report={"recent_context":[context_sample(at-timedelta(minutes=m),activity={"direction":"BUYING"}) for m in (0,1,2,3)]}
    result=history_context(item,report)
    assert result["status"]=="READY"
    assert result["extra_weight"]==0 and item.decision.score==80
    assert "recovery/pullback" in " ".join(result["lines"])
    for bad in (at+timedelta(minutes=1),at-timedelta(days=1),at-timedelta(minutes=5)):
        assert history_context(item,{"recent_context":[context_sample(bad)]})["status"]=="UNAVAILABLE"
    report["last_error"]={"reason":"gap"}
    assert history_context(item,report)["status"]=="UNAVAILABLE"


def test_roll_does_not_touch_fii_or_journal(tmp_path):
    protected=tmp_path/"fii_and_journal.json"
    protected.write_text("preserve")
    store=DayMemory(tmp_path/"memory.db")
    store.record(for_expiry(datetime(2026,8,31,10,tzinfo=IST),"2026-08-31"))
    store.record(for_expiry(datetime(2026,9,1,10,tzinfo=IST)))
    assert protected.read_text()=="preserve"


def test_five_session_size(tmp_path):
    store=DayMemory(tmp_path/"memory.db")
    for month,day in ((8,26),(8,27),(8,28),(8,31),(9,1)):
        start=datetime(2026,month,day,9,16,tzinfo=IST)
        for minute in range(375):
            item=for_expiry(start+timedelta(minutes=minute))
            for key,value in {"greeks_quality":"READY","delta":0.3,"gamma":0.001,"theta":-8,"vega":10}.items():
                item.option_chain[key]=value
            store.record(item)
    report=store.report()
    assert report["counts"]["samples"]==1875
    assert report["counts"]["candles"]==3750
    print("Five-session synthetic database bytes:",report["bytes"])
    assert report["bytes"]<75*1048576
