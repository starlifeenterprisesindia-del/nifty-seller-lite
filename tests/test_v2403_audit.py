from types import SimpleNamespace as NS
from datetime import time

import pytest

from analysis.barrier_map import _range_context
from config import CONFIG


def test_requested_entry_and_exit_defaults():
    assert CONFIG.risk_default_entry_end == time(15, 0)
    assert CONFIG.risk_default_forced_exit == time(15, 15)


def test_entry_window_closes_after_three_pm():
    from datetime import datetime
    from analysis.execution_guard import _entry_window

    profile = NS(entry_start=time(10, 15), entry_end=CONFIG.risk_default_entry_end)
    assert _entry_window(profile, datetime(2026, 8, 28, 15, 0))[1] is True
    assert _entry_window(profile, datetime(2026, 8, 28, 15, 0, 1))[1] is False


def test_overlapping_zones_do_not_claim_a_clear_range():
    support = NS(lower=24086, upper=24110, midpoint=24098)
    resistance = NS(lower=24105, upper=24124, midpoint=24114.5)
    result = _range_context(
        spot=24107.3, support=support, resistance=resistance,
        next_support=None, next_resistance=None, options=None, core=None, speed=None,
    )
    assert result.state == "OVERLAPPING ZONES"
    assert result.lower is None and result.upper is None
    assert result.position_pct is None
    assert support.upper == 24110 and resistance.lower == 24105


def test_actual_strategy_ledger_reconciles():
    from test_snapshot_service import StubClient, StubMaster, IST
    from datetime import datetime
    from services.snapshot_service import SnapshotService

    snapshot = SnapshotService(StubClient(), StubMaster()).build(
        datetime(2026, 7, 19, 13, 37, tzinfo=IST)
    )
    audit = snapshot.decision.score_audit
    assert set(audit) == {"CE SELL", "PE SELL", "CE BUY", "PE BUY", "IRON CONDOR"}
    for row in audit.values():
        assert row["Base total"] + row["Net adjustments / caps / rounding"] == pytest.approx(row["Final fit"])
