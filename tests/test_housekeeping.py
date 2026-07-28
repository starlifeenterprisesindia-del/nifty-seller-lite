from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from services.housekeeping import run_housekeeping


IST = ZoneInfo("Asia/Kolkata")


def test_housekeeping_prunes_old_raw_state_but_preserves_context_and_discipline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = Path("data")
    data.mkdir()
    now = datetime(2026, 7, 28, 10, 0, tzinfo=IST)
    old = now - timedelta(hours=25)
    recent = now - timedelta(hours=2)

    option_state = {
        "schema_version": 1,
        "sessions": {
            "2026-07-27|2026-07-28": [
                {"captured_at": old.isoformat(), "expiry": "2026-07-28", "rows": []},
                {"captured_at": recent.isoformat(), "expiry": "2026-07-28", "rows": []},
            ]
        },
    }
    (data / "option_state.json").write_text(json.dumps(option_state), encoding="utf-8")
    (data / "news_cache.json").write_text(json.dumps({"fetched_at": old.isoformat()}), encoding="utf-8")
    context_payload = {"schema_version": 1, "entries": [{"date": "2026-07-27"}]}
    (data / "market_context.json").write_text(json.dumps(context_payload), encoding="utf-8")
    (data / "market_context_mirror.json").write_text(json.dumps(context_payload), encoding="utf-8")
    discipline_payload = {"schema_version": 3, "days": {"2026-07-27": {"trade_record": {"status": "OPEN"}}}}
    (data / "discipline_state.json").write_text(json.dumps(discipline_payload), encoding="utf-8")

    result = run_housekeeping(now)
    cleaned = json.loads((data / "option_state.json").read_text(encoding="utf-8"))
    rows = next(iter(cleaned["sessions"].values()))
    assert len(rows) == 1
    assert rows[0]["captured_at"] == recent.isoformat()
    assert not (data / "news_cache.json").exists()
    assert (data / "market_context.json").exists()
    assert (data / "market_context_mirror.json").exists()
    assert (data / "discipline_state.json").exists()
    assert result["option_snapshots_removed"] == 1
