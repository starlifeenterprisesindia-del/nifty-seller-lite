from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterable
from zipfile import ZIP_DEFLATED, ZipFile

from services.market_intelligence_export import build_market_intelligence_test_pack


def _csv_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    data = [dict(row) for row in rows if isinstance(row, dict)]
    output = io.StringIO()
    if not data:
        output.write("no_rows\n")
        return output.getvalue().encode("utf-8")
    fields: list[str] = []
    seen: set[str] = set()
    for row in data:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(str(key))
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in data:
        clean: dict[str, Any] = {}
        for key in fields:
            value = row.get(key)
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(value, ensure_ascii=True, default=str, separators=(",", ":"))
            clean[key] = value
        writer.writerow(clean)
    return output.getvalue().encode("utf-8")


def build_master_live_test_pack(
    evidence_gzip: bytes,
    *,
    current_snapshot_summary: dict[str, Any] | None = None,
    decision_rows: list[dict[str, Any]] | None = None,
    paper_entries: list[dict[str, Any]] | None = None,
    smart_entry_rows: list[dict[str, Any]] | None = None,
    alert_audit_rows: list[dict[str, Any]] | None = None,
    performance_rows: list[dict[str, Any]] | None = None,
    recording_diagnostics: dict[str, Any] | None = None,
) -> bytes:
    """One-click forensic pack from already-recorded data only.

    No broker/API market call is made here. The caller may fetch the existing Railway
    evidence export on button click, exactly like the MI Test Pack. This pack joins the
    existing MI replay with UI/session validation traces needed for FINAL MASTER TESTING.
    """
    mi_pack = build_market_intelligence_test_pack(
        evidence_gzip, current_snapshot_summary=current_snapshot_summary
    )
    readme = """ONE BRAIN / NIFTY SELLER LITE — MASTER LIVE TEST PACK

Purpose: post-market forensic validation. No order placement and no model tuning.

Included:
- raw_evidence.jsonl.gz: authoritative recorded evidence export.
- market_intelligence_test_pack.zip: existing OB-MIE chronological validation pack.
- decision_journal.csv: WAIT/READY/ENTRY observations restored from local/Railway history.
- paper_validation.csv: One Brain + Market Intelligence research paper samples, setup-cycle IDs, P&L/MFE/MAE/diagnosis when available.
- smart_entry_validation.csv: on-screen Smart Entry Advisor observations captured without extra broker calls.
- alert_delivery_audit.csv: SENT / APP_ONLY / SUPPRESSED reason for STRONG-ONLY alerts in this UI session.
- performance_timeline.csv: pipeline/stage latency observations from this UI session.
- recording_diagnostics.json: recorder/history warm-up/persistence state.
- current_snapshot.json: current public snapshot summary.

Interpretation rules:
- Missing/stale evidence is NO VOTE, never fake neutral evidence.
- Alert suppression does not delete the underlying Market Intelligence event; it remains in recorded evidence.
- Paper samples are correlated research observations, not independent trials or guaranteed outcomes.
- No threshold/weight changes should be inferred from one session.
"""
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("README.txt", readme)
        archive.writestr("raw_evidence.jsonl.gz", evidence_gzip)
        archive.writestr("market_intelligence_test_pack.zip", mi_pack)
        archive.writestr("decision_journal.csv", _csv_bytes(decision_rows or []))
        archive.writestr("paper_validation.csv", _csv_bytes(paper_entries or []))
        archive.writestr("smart_entry_validation.csv", _csv_bytes(smart_entry_rows or []))
        archive.writestr("alert_delivery_audit.csv", _csv_bytes(alert_audit_rows or []))
        archive.writestr("performance_timeline.csv", _csv_bytes(performance_rows or []))
        archive.writestr(
            "recording_diagnostics.json",
            json.dumps(recording_diagnostics or {}, indent=2, ensure_ascii=True, default=str),
        )
        archive.writestr(
            "current_snapshot.json",
            json.dumps(current_snapshot_summary or {}, indent=2, ensure_ascii=True, default=str),
        )
    return output.getvalue()
