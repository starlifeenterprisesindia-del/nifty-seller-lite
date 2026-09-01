from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from analysis.recent_history import recent_history, IST


def fixture():
    now = datetime(2026, 8, 28, 10, tzinfo=IST)
    snapshot = NS(created_at=now, expiry="2026-09-01", metadata={"version": "TEST"},
                  market_session=NS(is_live=True), decision=NS(market_direction="BEARISH", final_action="WAIT"))
    rows = [{"at": (now-timedelta(minutes=m)).isoformat(), "expiry": snapshot.expiry, "version": "TEST",
             "spot": 24100-m*2, "activity": {"buy_score": 60-m, "sell_score": 20+m},
             "feeds": {k: {"use_state": "LIVE"} for k in ("quotes", "candles", "future_volume", "option_chain")},
             "future_contract": {"security_id": "123", "expiry": "2026-09-29"},
             "barriers": {"nearest_resistance": {"side": "RESISTANCE", "lower": 24100, "upper": 24106}}}
            for m in range(16)]
    return snapshot, {"recent_context": rows, "events": []}


def test_recovery_and_immutable_decision():
    snapshot, report = fixture()
    before = deepcopy(snapshot.__dict__)
    result = recent_history(snapshot, report)
    assert result["extra_weight"] == 0
    assert result["windows"][0]["Nifty change"] == 10
    assert result["windows"][1]["Nifty change"] == 30
    assert "recovery" in result["windows"][0]["Price reaction"]
    assert "(+5)" in result["windows"][0]["Flow"]
    assert snapshot.__dict__ == before


@pytest.mark.parametrize("case", ["old", "future", "expiry", "version", "stale", "naive", "nan"])
def test_bad_rows_not_confirmation(case):
    snapshot, report = fixture()
    for row in report["recent_context"]:
        if case == "old":
            row["at"] = (snapshot.created_at-timedelta(days=1)).isoformat()
        elif case == "future":
            row["at"] = (snapshot.created_at+timedelta(minutes=1)).isoformat()
        elif case in ("expiry", "version"):
            row[case] = "other"
        elif case == "stale":
            row["feeds"]["quotes"]["use_state"] = "STALE"
        elif case == "naive":
            row["at"] = snapshot.created_at.replace(tzinfo=None).isoformat()
        else:
            row["spot"] = float("nan")
    assert recent_history(snapshot, report)["status"] == "PENDING"


def test_gaps_and_duplicates():
    snapshot, report = fixture()
    report["recent_context"] = [r for i, r in enumerate(report["recent_context"]) if i not in (2, 3, 4)]
    assert "gap" in recent_history(snapshot, report)["windows"][0]["Price reaction"]
    report["recent_context"] = [report["recent_context"][0]]*16
    assert recent_history(snapshot, report)["windows"][0]["Flow"] == "PENDING"


def test_contract_and_feed_missing_block_flow_not_price():
    snapshot, report = fixture()
    report["recent_context"][3]["future_contract"]["security_id"] = "rollover"
    result = recent_history(snapshot, report)
    assert result["windows"][0]["Nifty change"] == 10
    assert "unavailable" in result["windows"][0]["Flow"]
    report["recent_context"][3]["future_contract"]["security_id"] = "123"
    report["recent_context"][3]["feeds"]["future_volume"]["use_state"] = "STALE"
    assert "unavailable" in recent_history(snapshot, report)["windows"][0]["Flow"]


def test_selling_without_fall_is_not_confirmed_absorption():
    snapshot, report = fixture()
    for row in report["recent_context"]:
        row["activity"] = {"buy_score": 10, "sell_score": 65}
    assert "absorption confirmed nahi" in recent_history(snapshot, report)["windows"][0]["Price reaction"]


def test_exact_barrier_events_only_and_no_refresh_count():
    snapshot, report = fixture()
    event = {"at": (snapshot.created_at-timedelta(minutes=2)).isoformat(), "kind": "3m REACTION",
             "identity": "RESISTANCE:24100:24106", "expiry": snapshot.expiry, "version": "TEST", "status": "REJECTION — 3m CLOSE"}
    report["events"] = [event]
    a = recent_history(snapshot, report)
    assert "REJECTION" in a["barriers"][0]["Latest recorded reaction"]
    assert recent_history(snapshot, report) == a
    event["identity"] = "RESISTANCE:24101:24107"
    assert "nahi mila" in recent_history(snapshot, report)["barriers"][0]["Latest recorded reaction"]
    event["identity"] = "RESISTANCE:24100:24106"
    event["version"] = "OLD"
    assert "nahi mila" in recent_history(snapshot, report)["barriers"][0]["Latest recorded reaction"]


def test_closed_or_error_has_no_live_claim():
    snapshot, report = fixture()
    report["last_error"] = {"reason": "unavailable"}
    assert recent_history(snapshot, report)["status"] == "UNAVAILABLE"
    snapshot.market_session.is_live = False
    result = recent_history(snapshot, report)
    assert result["status"] == "REFERENCE" and result["windows"] == []


def test_db_exports_required_fields_without_migration(tmp_path):
    from services.day_memory import DayMemory
    from test_day_memory import snapshot as recorded_snapshot
    now = datetime(2026, 8, 28, 10, tzinfo=IST)
    store = DayMemory(tmp_path/"history.db")
    store.record(recorded_snapshot(now))
    report = store.report()
    assert "barriers" in report["recent_context"][0]
    assert "future_contract" in report["recent_context"][0]
    assert all("identity" in event for event in report["events"])
