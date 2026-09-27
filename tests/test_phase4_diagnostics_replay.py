from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from analysis.performance_diagnostics import build_performance_report
from services.replay_audit import audit_samples


def test_performance_report_is_read_only_summary_of_existing_metadata():
    now = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)
    feed = lambda name, state, age: SimpleNamespace(
        name=name, ok=state == "LIVE", use_state=state, age_seconds=age,
        fetched_at=now, source="TEST"
    )
    snapshot = SimpleNamespace(
        created_at=now,
        metadata={"performance": {
            "pipeline_seconds": 2.4,
            "build_seconds": 2.0,
            "finalize_seconds": 0.4,
            "slowest_stage": "option_chain",
            "stages": {"grouped_quotes": 0.5, "option_chain": 0.9},
        }},
        feed_status={
            "quotes": feed("quotes", "LIVE", 1.0),
            "candles": feed("candles", "LIVE", 2.0),
            "option_chain": feed("option_chain", "LIVE", 1.5),
            "future_volume": feed("future_volume", "LIVE", 2.0),
            "vix": feed("vix", "STALE", 20.0),
        },
    )
    before = repr(snapshot.metadata)
    report = build_performance_report(
        snapshot, refresh_interval_seconds=30, snapshot_built_now=False
    )
    assert report["pipeline_seconds"] == 2.4
    assert report["refresh_headroom_seconds"] == 27.6
    assert report["slowest_stage"] == "option_chain"
    assert report["stage_rows"][0]["Stage"] == "option_chain"
    assert report["critical_issue_count"] == 1
    assert report["snapshot_mode"].startswith("REUSED SNAPSHOT")
    assert repr(snapshot.metadata) == before


def test_replay_summary_reports_observed_move_and_big_player_lag():
    start = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    rows = []
    for minute in range(16):
        activity = {}
        if minute >= 3:
            activity = {"direction": "BUYING", "score": 70, "state": "SUSTAINED"}
        rows.append({
            "at": (start + timedelta(minutes=minute)).isoformat(),
            "expiry": "2026-09-29",
            "version": "TEST",
            "spot": 25000 + minute * 3,
            "background_action": "WAIT",
            "direction": "MIXED",
            "feeds": {"quotes": {"use_state": "LIVE"}, "candles": {"use_state": "LIVE"}},
            "activity": activity,
        })
    result = audit_samples(rows, horizon_minutes=15, move_points=30)
    summary = result["episode_summary"]
    assert summary["move_episodes"] == 1
    assert summary["wait_at_start"] == 1
    assert summary["median_move_points"] == 45.0
    assert summary["median_same_direction_bp_lag_minutes"] == 3.0
    assert summary["same_direction_bp_within_3m"] == 1
    episode = result["non_overlapping_episodes"][0]
    assert episode["start_spot"] == 25000.0
    assert episode["end_spot"] == 25045.0
    assert episode["duration_minutes"] == 15.0
