from datetime import datetime

from analysis.future_brain import calculate_future_brain, feature_signature
from services.snapshot_service import SnapshotService
from test_snapshot_service import StubClient, StubMaster


NOW = datetime.fromisoformat("2026-07-20T11:30:00+05:30")


def snapshot():
    return SnapshotService(StubClient(), StubMaster()).build(NOW)


def test_future_brain_paths_are_normalised_and_separate_from_current_brain():
    item = snapshot()
    result = calculate_future_brain(item)
    assert round(result.up_5m + result.down_5m + result.range_5m, 1) == 100.0
    assert round(result.up_15m + result.down_15m + result.range_15m, 1) == 100.0
    assert result.current_direction in {"UP", "DOWN", "RANGE"}
    assert result.next_direction in {"UP", "DOWN", "RANGE"}
    assert result.model_label == "FORECAST SCORE"
    assert result.historical_status == "INSUFFICIENT DATA"


def test_matching_history_is_bounded_calibration_not_an_override():
    item = snapshot()
    key = feature_signature(item)
    rows = [
        {"feature_key": key, "horizon_minutes": 15, "status": "OBSERVED", "spot_change": 12}
        for _ in range(30)
    ]
    result = calculate_future_brain(item, outcomes=rows)
    assert result.historical_matches == 30
    assert result.historical_status == "EARLY ESTIMATE"
    assert 0 <= result.up_15m <= 100


def test_feature_signature_contains_only_compact_regime_fields():
    key = feature_signature(snapshot())
    assert len(key.split("|")) == 6
    assert len(key) < 100
