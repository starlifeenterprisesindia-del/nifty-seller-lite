from datetime import datetime
from pathlib import Path
from services.activity_state_store import ActivityStateStore


def test_activity_state_atomic_roundtrip(tmp_path: Path):
    store = ActivityStateStore(tmp_path / "activity.json")
    at = datetime.fromisoformat("2026-10-08T10:00:00+05:30")
    rows = store.append(at, direction="SELLING", score=72, state="CONFIRMED", observation_key="x")
    assert len(rows) == 1
    loaded = store.load(at)
    assert loaded[0]["direction"] == "SELLING"
    assert (tmp_path / "activity.json").exists()
    assert (tmp_path / "activity.json.lock").exists()
