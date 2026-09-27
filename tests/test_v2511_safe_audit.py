from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from analysis.presentation_safety import (
    future_brain_display_label,
    simple_core_block_coverage,
)
from services.news_service import MarketNewsService
from services.snapshot_service import SnapshotService

IST = ZoneInfo("Asia/Kolkata")


def test_core_block_availability_is_distinct_from_entry_coverage():
    simple = {
        "evidence_coverage": 60.0,  # entry-readiness denominator: 45 + 15
        "blocks": {
            "trend": {"weight": 40, "available": True},
            "options": {"weight": 25, "available": False},
            "participation": {"weight": 20, "available": False},
            "barrier_entry": {"weight": 15, "state": "UNDER ATTACK"},
        },
    }
    assert simple_core_block_coverage(simple) == 55.0
    assert simple["evidence_coverage"] == 60.0


def test_future_brain_small_history_is_explicitly_experimental():
    title, note = future_brain_display_label({
        "historical_matches": 28,
        "historical_status": "INSUFFICIENT DATA",
        "model_label": "FORECAST SCORE",
    })
    assert "EXPERIMENTAL" in title
    assert "28" in note
    assert "not calibrated win probabilities" in note


def test_news_skips_network_on_weekend(monkeypatch, tmp_path):
    service = MarketNewsService(tmp_path / "news.json")

    def fail(*args, **kwargs):
        raise AssertionError("network must not be called outside regular live window")

    monkeypatch.setattr("services.news_service.requests.get", fail)
    context = service.fetch(datetime(2026, 9, 27, 12, 0, tzinfo=IST))  # Sunday
    assert context.status == "UNAVAILABLE"
    assert context.risk_level == "NONE"
    assert "fetch skipped" in context.summary.lower()


def test_existing_flatline_guard_detects_repeated_bars():
    import pandas as pd

    now = datetime(2026, 9, 28, 11, 0, tzinfo=IST)
    rows = []
    for minute in range(4):
        rows.append({
            "timestamp": datetime(2026, 9, 28, 10, 55 + minute, tzinfo=IST),
            "open": 23000.0,
            "high": 23000.0,
            "low": 23000.0,
            "close": 23000.0,
            "volume": 1000.0,
        })
    frame = pd.DataFrame(rows)
    assert SnapshotService._spot_flatline_run(frame, now) == 4
