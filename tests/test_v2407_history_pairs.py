from datetime import datetime, timedelta, date
import gzip
import json
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from analysis.history_features import IST, oi_history, futures_vwap, institutional_trends
from services.day_memory import DayMemory
from services.paper_monitor import PaperMonitor
from test_day_memory import snapshot
from test_trade_plan import option_frame, option_intelligence, levels, decision, NOW
from analysis.trade_plan import calculate_trade_plan
from models import MarketSession


def oi_sample(at, oi=100, price=20, volume=100, expiry="2026-09-01", security=7):
    return {"captured_at": at.isoformat(), "expiry": expiry, "spot": 24100+volume/100,
            "rows": [{"security_id": security, "strike":24100, "side":"PE", "oi":oi,
                      "volume":volume, "last_price":price}]}


def test_oi_same_contract_history_and_no_extra_vote():
    now = datetime(2026,8,28,10,tzinfo=IST)
    history = [oi_sample(now-timedelta(minutes=i)) for i in range(16,0,-1)]
    result = oi_history(history, oi_sample(now,200,18,200))
    assert result["extra_vote"] == 0
    assert [w["status"] for w in result["windows"]] == ["READY"]*3
    row = result["windows"][0]["rows"][0]
    assert row["oi_change"] == 100 and row["inference"] == "SHORT BUILD-UP"
    assert result["windows"][0]["inferred_pressure"] == "BULLISH"
    assert not oi_history(history, oi_sample(now,security=8))["windows"][0]["rows"]
    assert not oi_history(history, oi_sample(now,expiry="2026-09-08"))["windows"][0]["rows"]


def test_oi_gaps_counter_resets_and_closed_session():
    now = datetime(2026,8,28,10,tzinfo=IST)
    history = [oi_sample(now-timedelta(minutes=5))]
    assert oi_history(history,oi_sample(now))["windows"][1]["status"] == "DATA GAP"
    history += [oi_sample(now-timedelta(minutes=i)) for i in (1,2,3,4)]
    assert not oi_history(history,oi_sample(now,volume=1))["windows"][0]["rows"]
    assert oi_history(history,oi_sample(now),live=False)["status"] == "REFERENCE"


def test_vwap_session_reset_completed_bars_and_partial_label():
    now = datetime(2026,8,28,9,18,tzinfo=IST)
    bars = pd.DataFrame([{"timestamp":now-timedelta(minutes=i), "high":p,"low":p,"close":p,
                          "volume":v,"is_complete":True} for i,p,v in ((3,100,10),(2,110,20),(1,120,30),(0,900,900))])
    prior = dict(bars.iloc[0]); prior["timestamp"] = now-timedelta(days=1); prior["close"]=900
    result = futures_vwap(pd.concat([pd.DataFrame([prior]),bars]), now)
    assert result["status"] == "READY" and result["value"] == 113.33
    assert result["bars"] == 3
    assert futures_vwap(bars.iloc[1:],now)["status"] == "PARTIAL SESSION"
    assert futures_vwap(bars,now+timedelta(minutes=20))["status"] == "STALE"


def test_fii_daily_units_missing_and_no_future_lookahead():
    entries = [{"date": f"2026-08-{d:02}", "fii_cash_net":d, "dii_cash_net":-d} for d in (25,26,27,28)]
    data = institutional_trends(entries,date(2026,8,28))
    assert data["as_of"] == "2026-08-27"
    assert data["rows"][0]["3_sum"] == 78
    assert data["rows"][0]["5_sum"] is None
    assert data["rows"][2]["unit"] == "contracts"
    assert data["rows"][2]["3_sum"] is None


def test_export_coverage_heartbeat_archive_and_rollback(tmp_path,monkeypatch):
    store = DayMemory(tmp_path/"memory.sqlite3")
    now = datetime(2026,8,28,10,tzinfo=IST)
    store.record(snapshot(now)); store.record(snapshot(now+timedelta(minutes=2)))
    for minutes in (0,1):
        at=now+timedelta(minutes=minutes)
        assert store.app_event(at,{"at":at.isoformat(),"action":"WAIT"})
    report=store.report()["recording_coverage"]
    assert report["missing_slots"] == 1
    assert report["last_app_heartbeat_at"] != report["last_app_ai_at"]
    exported=[json.loads(line) for line in gzip.decompress(store.export_bytes()).decode().splitlines()]
    assert sum(r.get("table")=="samples" for r in exported)==2
    with store.connect() as db:
        store._roll(db,"2026-09-02","2026-09-08")
    assert store.report()["counts"]["samples"]==0
    archive=tmp_path/"archives/2026-09-01-evidence.jsonl.gz"
    assert archive.exists() and "samples" in gzip.decompress(archive.read_bytes()).decode()
    monkeypatch.setattr(store,"_archive_cycle",lambda *args: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError), store.connect() as db:
        store._roll(db,"2026-09-09","2026-09-15")
    assert store.report()["cycle_expiry"] == "2026-09-08"


def test_paper_registration_never_reopens_closed_position(tmp_path):
    monitor=PaperMonitor(tmp_path/"paper.json")
    opened={"trade_id":"SH-1","status":"OPEN"}
    monitor.register([opened])
    closed={**opened,"status":"CLOSED","closed_at":"2026-08-28T15:15:00+05:30"}
    monitor.register([closed])
    assert monitor.register([opened])[0]["status"]=="CLOSED"


def test_complete_pair_comparison_payoffs():
    # Fixture's live MarketSession fields are sourced from existing regression helper.
    from test_trade_plan import live_session
    plan=calculate_trade_plan(frame=option_frame(),spot=24350,expiry="2026-07-28",
         levels=levels(),options=option_intelligence(),decision=decision("CE SELL"),market_session=live_session())
    rows=plan.ce_sell.pair_comparison
    assert rows
    assert len({(r["short"],r["hedge"]) for r in rows})==len(rows)
    for row in rows:
        assert row["max_loss_points"] == pytest.approx(abs(row["hedge"]-row["short"])-row["credit_points"])
        assert row["expiry_pnl_at_hedge"] == -row["max_loss_points"]


def test_deadline_with_missing_quote_does_not_fake_fill(tmp_path):
    from test_position_guardian import trade_record, calculate, chain, NOW as ENTRY
    record=trade_record()
    result=calculate(record,chain(20,8),as_of=ENTRY.replace(hour=15),option_chain_live=False)
    assert result.status == "EXIT DUE" and result.unrealized_pnl_rupees is None
    monitor=PaperMonitor(tmp_path/"paper.json")
    record.update(trade_id="SH-TIME",session_date=ENTRY.date().isoformat())
    monitor.register([record])
    item=NS(created_at=ENTRY.replace(hour=15),option_chain=chain(20,8),expiry="2026-07-21",
            levels=NS(current_price=24350),market_session=MarketSession("LIVE","LIVE",True,"fresh"),
            feed_status={"option_chain":NS(use_state="STALE")})
    monitor.observe(item)
    assert monitor.register([])[0]["status"]=="OPEN"
    assert monitor.register([])[0]["guardian_status"]=="EXIT DUE"
    item.feed_status["option_chain"].use_state="LIVE"
    monitor.observe(item)
    assert monitor.register([])[0]["status"]=="CLOSED"
    assert monitor.register([])[0]["closed_at"]==item.created_at.isoformat()


def test_exit_never_uses_ltp_or_ambiguous_duplicate():
    from test_position_guardian import trade_record, calculate, chain
    frame=chain(20,8)
    frame=frame.drop(columns=["top_bid_price","top_ask_price"])
    assert calculate(trade_record(),frame).unrealized_pnl_rupees is None
    frame=chain(20,8)
    assert calculate(trade_record(),pd.concat([frame,frame])).unrealized_pnl_rupees is None


def test_replay_labels_future_move_not_signal_and_rejects_gaps():
    from services.replay_audit import audit_samples
    now=datetime(2026,8,28,10,tzinfo=IST)
    rows=[{"at":(now+timedelta(minutes=i)).isoformat(),"expiry":"2026-09-01","version":"TEST",
           "spot":24100+i*3,"background_action":"WAIT","direction":"MIXED",
           "feeds":{k:{"use_state":"LIVE"} for k in ("quotes","candles")}} for i in range(16)]
    result=audit_samples(rows)
    assert result["non_overlapping_episodes"][0]["label"]=="MOVE WHILE WAIT"
    assert rows[0]["background_action"]=="WAIT"
    assert not audit_samples(rows[:3]+rows[10:])["non_overlapping_episodes"]


def test_pair_comparison_and_history_panels_render_offline():
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_string('''
import sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/"tests"))
from datetime import datetime
import streamlit as st
from test_snapshot_service import StubFutureClient, StubFutureMaster, IST
from services.snapshot_service import SnapshotService
from ui.components import render_protected_candidates
from ui.day_memory import render_day_memory
s=SnapshotService(StubFutureClient(),StubFutureMaster()).build(datetime(2026,7,19,13,37,tzinfo=IST))
st.session_state.day_memory_report={"counts":{},"recording_coverage":{}}
render_protected_candidates(s)
render_day_memory(s,"https://example.invalid","test-only")
''').run(timeout=30)
    assert not app.exception


def test_individual_short_liquidity_uses_common_population():
    from analysis.trade_plan import _select_short_leg
    frame=option_frame()
    frame.loc[frame.strike.eq(24500),["oi","volume"]]=1
    leg,_,_=_select_short_leg(frame,side="CE",spot=24350,levels=levels(),
                             options=option_intelligence(),only_strike=24500)
    assert leg is not None and leg.liquidity_score < 90
