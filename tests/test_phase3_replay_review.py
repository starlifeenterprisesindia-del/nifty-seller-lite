import json
from pathlib import Path

from analysis.replay_review import build_replay_bundle
from services.day_memory import DayMemory, encode


def _sample(at, spot, r1=(22700, 22710), ce=22700, pe=22600, bp=("BUYING", 65)):
    return {
        "at": at,
        "spot": spot,
        "version": "TEST",
        "direction": "MIXED",
        "background_action": "WAIT",
        "barriers": {
            "nearest_resistance": {"lower": r1[0], "upper": r1[1], "strength": 80, "break_pressure": 40, "state": "TESTING"},
            "next_resistance": {"lower": 22750, "upper": 22760, "strength": 82, "break_pressure": 25, "state": "AHEAD"},
            "nearest_support": {"lower": 22650, "upper": 22660, "strength": 75, "break_pressure": 30, "state": "HOLDING"},
            "next_support": {"lower": 22600, "upper": 22610, "strength": 85, "break_pressure": 20, "state": "AHEAD"},
        },
        "activity": {"direction": bp[0], "score": bp[1], "state": "SUSTAINED", "activity_type": "ABSORPTION"},
        "evidence": {
            "option_intelligence": {
                "market_bias": "BULLISH",
                "confidence": 84,
                "persistence": "MIXED",
                "ce_wall": {"strike": ce, "oi": 1000, "previous_strike": ce, "migration_points": 0, "cluster_center": 22750, "cluster_oi": 2000, "status": "READY"},
                "pe_wall": {"strike": pe, "oi": 900, "previous_strike": pe, "migration_points": 0, "cluster_center": 22600, "cluster_oi": 1800, "status": "READY"},
                "pcr": {"near_atm_oi_pcr": 1.02, "day_addition_pcr": 0.9, "intraday_addition_pcr": 0.8, "volume_pcr": 1.1, "state": "CE OI DOMINANT"},
            }
        },
    }


def test_build_replay_bundle_is_read_only_and_aligns_decisions():
    samples = [
        _sample("2026-10-02T10:00:00+05:30", 22700),
        _sample("2026-10-02T10:01:00+05:30", 22708, r1=(22710, 22720), ce=22750, bp=("SELLING", 72)),
    ]
    decisions = [{
        "at": "2026-10-02T10:01:22+05:30", "session_date": "2026-10-02", "regime": "TREND",
        "direction": "BEARISH", "entry_readiness": 77, "entry_state": "READY", "final_action": "CE SELL",
        "trigger": "retest", "outcome_5m_points": -18, "outcome_5m_label": "DOWN",
    }]
    candles = [
        {"at": "2026-10-02T10:00:00+05:30", "open": 22698, "high": 22703, "low": 22696, "close": 22700, "volume": 10},
        {"at": "2026-10-02T10:01:00+05:30", "open": 22700, "high": 22710, "low": 22699, "close": 22708, "volume": 11},
    ]
    before = json.dumps(samples, sort_keys=True)
    result = build_replay_bundle(samples, decisions, [], candles)
    assert json.dumps(samples, sort_keys=True) == before
    assert result["timeline"][1]["final_action"] == "CE SELL"
    assert result["timeline"][1]["ce_wall"]["strike"] == 22750.0
    assert result["statistics"]["barrier_state_changes"] == 1
    assert result["statistics"]["ce_wall_changes"] == 1
    assert result["statistics"]["big_player_60plus_samples"] == 2
    assert result["statistics"]["outcomes"]["5m"]["covered"] == 1
    assert result["safety"] == {"broker_calls": 0, "brain_writes": 0, "threshold_tuning": False, "mode": "READ ONLY / ON DEMAND"}


def test_day_memory_replay_bundle_projects_only_replay_fields(tmp_path: Path):
    store = DayMemory(tmp_path / "memory.sqlite3")
    day = "2026-10-02"
    with store.connect() as db:
        db.execute("INSERT OR REPLACE INTO meta VALUES ('day',?)", (day,))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('cycle',?)", ("2026-10-06",))
        sample = _sample(f"{day}T10:00:00+05:30", 22700)
        sample["options"] = [{"strike": 22700, "blob": "x" * 1000}]
        db.execute("INSERT INTO samples(at,body) VALUES (?,?)", (f"{day}T10:00:00+05:30", encode(sample)))
        decision = {"at": f"{day}T10:00:20+05:30", "session_date": day, "final_action": "WAIT", "direction": "MIXED", "entry_readiness": 44}
        db.execute("INSERT INTO app_decisions(minute,at,body) VALUES (?,?,?)", (f"{day}T10:00:00+05:30", decision["at"], encode(decision)))
        db.execute("INSERT INTO candles(instrument,at,body) VALUES (?,?,?)", ("NIFTY", f"{day}T10:00:00+05:30", encode({"open": 22698, "high": 22703, "low": 22696, "close": 22700, "volume": 10})))
        db.execute("INSERT INTO events(at,kind,identity,body) VALUES (?,?,?,?)", (f"{day}T10:00:30+05:30", "BARRIER", "R", encode({"status": "TESTING", "side": "RESISTANCE", "zone": "22700-22710"})))
    result = store.replay_bundle()
    assert result["session_date"] == day
    assert len(result["timeline"]) == 1
    assert "options" not in result["timeline"][0]
    assert result["timeline"][0]["final_action"] == "WAIT"
    assert result["candles_1m"][0]["close"] == 22700.0
    assert result["events"][0]["kind"] == "BARRIER"
