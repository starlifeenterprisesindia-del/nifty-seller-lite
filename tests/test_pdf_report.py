from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import pandas as pd

from services.pdf_report import (
    audit_pdf_filename,
    build_full_audit_pdf,
    build_quick_market_pdf,
    build_support_bundle,
    quick_pdf_filename,
    support_bundle_filename,
)
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
    # Support ZIP legitimately serializes JSON; the PDF builder must not embed it.
    pdf_only = text.split("def build_support_bundle(")[0]
    assert "json.dumps(" not in pdf_only
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


def test_app_consolidates_status_in_main_ai_without_duplicate_top_sections():
    root = Path(__file__).resolve().parents[1]
    text = (root / "app.py").read_text(encoding="utf-8")
    assert "render_main_ai_market_view(view_snapshot, previous_view_snapshot)" in text
    assert "render_compact_barrier_map(view_snapshot, previous_view_snapshot)" in text
    assert "render_evidence_matrix(view_snapshot, previous_view_snapshot)" in text
    assert "render_pre_touch_barriers(snapshot)" not in text
    assert "render_best_protected_sells(snapshot)" not in text
    assert "Delete selected date" not in text
    assert 'NSL_SHOW_DEVELOPER_DATA' in text


def test_quick_market_pdf_is_valid_and_compact():
    snapshot = _snapshot_fixture()
    pdf = build_quick_market_pdf(snapshot)
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 5000
    assert pdf.count(b"/Type /Page") >= 2
    assert pdf.count(b"/Type /Page") < 6


def test_quick_pdf_filename_is_snapshot_specific():
    snapshot = _snapshot_fixture()
    name = quick_pdf_filename(snapshot)
    assert name.startswith("nifty_seller_lite_quick_20260719_133700_")
    assert name.endswith(".pdf")


def test_support_bundle_is_single_credential_free_handover_zip():
    from io import BytesIO
    import json
    from zipfile import ZipFile

    snapshot = _snapshot_fixture()
    payload = build_support_bundle(snapshot)
    assert payload.startswith(b"PK")
    with ZipFile(BytesIO(payload)) as archive:
        names = set(archive.namelist())
        assert "complete_diagnostic_report.pdf" in names
        assert "current_snapshot.json" in names
        assert "support_manifest.json" in names
        assert "recording_diagnostics.json" in names
        assert json.loads(archive.read("recording_diagnostics.json"))["available"] is False
        assert "option_chain.csv" in names
        assert "spot_candles_3m.csv" in names
        manifest = json.loads(archive.read("support_manifest.json"))
        assert manifest["contains_credentials"] is False
        combined_names = " ".join(names).lower()
        assert "token" not in combined_names
        assert "secret" not in combined_names

    name = support_bundle_filename(snapshot)
    assert name.startswith("nifty_seller_lite_support_bundle_20260719_133700_")
    assert name.endswith(".zip")
