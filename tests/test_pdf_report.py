from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import pandas as pd

from services.pdf_report import audit_pdf_filename, build_full_audit_pdf
from services.snapshot_service import SnapshotService


def _snapshot_fixture():
    module_path = Path(__file__).with_name("test_snapshot_service.py")
    spec = importlib.util.spec_from_file_location("snapshot_test_fixture", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    service = SnapshotService(module.StubFutureClient(), module.StubFutureMaster())
    return service.build(datetime(2026, 7, 19, 13, 37, tzinfo=module.IST))


def test_full_audit_pdf_is_valid_and_multi_page():
    snapshot = _snapshot_fixture()
    pdf = build_full_audit_pdf(snapshot)
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 25000
    assert pdf.count(b"/Type /Page") >= 5


def test_pdf_filename_is_snapshot_specific():
    snapshot = _snapshot_fixture()
    name = audit_pdf_filename(snapshot)
    assert name.startswith("nifty_seller_lite_audit_20260719_133700_")
    assert name.endswith(".pdf")


def test_pdf_report_is_read_only_and_not_a_second_brain():
    root = Path(__file__).resolve().parents[1]
    text = (root / "services" / "pdf_report.py").read_text(encoding="utf-8")
    assert "calculate_final_decision(" not in text
    assert "DhanClient(" not in text
    assert "requests." not in text
    assert "OptionStateStore" not in text
    assert "DisciplineStore" not in text


def test_pdf_excludes_raw_json_code_appendix_and_uses_clean_breaks():
    root = Path(__file__).resolve().parents[1]
    text = (root / "services" / "pdf_report.py").read_text(encoding="utf-8")
    assert "Canonical Snapshot JSON Summary" not in text
    assert "json.dumps(" not in text
    assert 'return "\\n".join' in text
    assert "₹" not in text


def test_snapshot_service_filters_forming_candles_and_ages_from_close():
    now = datetime(2026, 7, 20, 10, 22, 24)
    frame = pd.DataFrame(
        {
            "timestamp": [
                datetime(2026, 7, 20, 10, 18),
                datetime(2026, 7, 20, 10, 21),
            ],
            "close": [24222.6, 24224.8],
            "is_complete": [True, False],
        }
    )
    completed = SnapshotService._completed_only(frame)
    assert len(completed) == 1
    assert completed.iloc[-1]["timestamp"] == datetime(2026, 7, 20, 10, 18)
    assert SnapshotService._completed_only(frame.drop(columns=["is_complete"])).empty

    one_minute = pd.DataFrame(
        {
            "timestamp": [datetime(2026, 7, 20, 10, 21)],
            "is_complete": [True],
        }
    )
    age = SnapshotService._latest_candle_age_seconds(
        one_minute, now, interval_minutes=1
    )
    assert age == 24.0


def test_pdf_has_independent_required_feed_status_and_completed_filter():
    root = Path(__file__).resolve().parents[1]
    text = (root / "services" / "pdf_report.py").read_text(encoding="utf-8")
    assert 'required_feeds_value = (' in text
    assert '"PASS / LIVE"' in text
    assert "snapshot.execution_guard.readiness" not in text.split(
        '"Required live feeds"', 1
    )[1].split("],", 1)[0]
    assert "_completed_audit_frame(frame)" in text


def test_app_separates_feed_integrity_from_execution_readiness():
    root = Path(__file__).resolve().parents[1]
    text = (root / "app.py").read_text(encoding="utf-8")
    assert "REQUIRED LIVE FEEDS: PASS" in text
    assert "EXECUTION STATUS: ENTRY READY" in text
    assert "PRE-ENTRY DATA STATUS" not in text
