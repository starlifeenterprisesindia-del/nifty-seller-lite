from __future__ import annotations

import gzip
import io
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from zipfile import ZipFile

from services.master_live_test_pack import build_master_live_test_pack
from services.shadow_journal import ShadowJournalStore, _one_brain_candidate

IST = ZoneInfo("Asia/Kolkata")


def test_one_brain_research_uses_candidate_action_without_changing_live_wait():
    snapshot = SimpleNamespace(
        metadata={
            "simple_brain": {
                "candidate_action": "PE SELL",
                "final_action": "WAIT",
                "direction_strength": 63.0,
                "entry_readiness": 68.0,
                "entry_state": "READY / CONFIRMATION PENDING",
            },
            "common_decision": {"final_action": "WAIT"},
        }
    )
    candidate = _one_brain_candidate(snapshot)
    assert candidate is not None
    assert candidate["setup"] == "PE SELL"
    assert snapshot.metadata["simple_brain"]["final_action"] == "WAIT"


def _cycle_snapshot(at: datetime, *, r_low=22540.0, r_high=22560.0):
    resistance = SimpleNamespace(label="R1", lower=r_low, upper=r_high)
    support = SimpleNamespace(label="S1", lower=22480.0, upper=22500.0)
    return SimpleNamespace(
        created_at=at,
        barrier_map=SimpleNamespace(
            nearest_resistance=resistance,
            nearest_support=support,
        ),
    )


def test_research_cycle_does_not_rearm_on_cooldown_alone(tmp_path):
    store = ShadowJournalStore(tmp_path / "shadow.json")
    candidate = {"direction": "BULLISH", "setup": "PE SELL", "trigger_type": "INSTITUTIONAL WINDOW"}
    snap = _cycle_snapshot(datetime(2026, 10, 9, 13, 58, tzinfo=IST))
    cycle1, sampled1, _ = store.identify_research_cycle("MARKET INTELLIGENCE", snap, candidate)
    assert cycle1 and not sampled1
    store.mark_research_cycle_sampled("MARKET INTELLIGENCE", cycle1)

    later = _cycle_snapshot(datetime(2026, 10, 9, 14, 7, tzinfo=IST))
    cycle2, sampled2, _ = store.identify_research_cycle("MARKET INTELLIGENCE", later, candidate)
    assert cycle2 == cycle1
    assert sampled2

    # Candidate disappears: genuine reset/re-arm.
    store.identify_research_cycle("MARKET INTELLIGENCE", later, None)
    reopened = _cycle_snapshot(datetime(2026, 10, 9, 14, 20, tzinfo=IST))
    cycle3, sampled3, _ = store.identify_research_cycle("MARKET INTELLIGENCE", reopened, candidate)
    assert cycle3 != cycle1
    assert not sampled3


def test_material_barrier_change_creates_new_cycle(tmp_path):
    store = ShadowJournalStore(tmp_path / "shadow.json")
    candidate = {"direction": "BULLISH", "setup": "PE SELL"}
    first = _cycle_snapshot(datetime(2026, 10, 9, 10, 0, tzinfo=IST), r_low=22540, r_high=22560)
    cycle1, _, _ = store.identify_research_cycle("ONE BRAIN", first, candidate)
    store.mark_research_cycle_sampled("ONE BRAIN", cycle1 or "")
    shifted = _cycle_snapshot(datetime(2026, 10, 9, 10, 5, tzinfo=IST), r_low=22600, r_high=22620)
    cycle2, sampled2, _ = store.identify_research_cycle("ONE BRAIN", shifted, candidate)
    assert cycle2 != cycle1
    assert not sampled2


def test_master_live_test_pack_contains_forensic_sections():
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb") as handle:
        handle.write(b'{"format":"nifty-evidence-jsonl","schema":1}\n')
    payload = build_master_live_test_pack(
        raw.getvalue(),
        current_snapshot_summary={"version": "2.75.0"},
        decision_rows=[{"at": "2026-10-09T10:00:00+05:30", "final_action": "WAIT"}],
        paper_entries=[{"trade_id": "SH-1", "setup_cycle_id": "OB-1"}],
        smart_entry_rows=[{"status": "ARMED"}],
        alert_audit_rows=[{"action": "SUPPRESSED", "reason": "STRONG_ONLY"}],
        performance_rows=[{"pipeline_seconds": 4.1}],
        recording_diagnostics={"available": True},
    )
    with ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
    assert {
        "raw_evidence.jsonl.gz",
        "market_intelligence_test_pack.zip",
        "decision_journal.csv",
        "paper_validation.csv",
        "smart_entry_validation.csv",
        "alert_delivery_audit.csv",
        "performance_timeline.csv",
        "recording_diagnostics.json",
        "current_snapshot.json",
    }.issubset(names)
