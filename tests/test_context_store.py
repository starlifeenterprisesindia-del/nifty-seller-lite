from datetime import date

import pytest

from services.context_store import MarketContextStore


def test_context_store_upserts_same_date_and_bounds(tmp_path):
    store = MarketContextStore(tmp_path / "context.json")
    store.upsert(
        session_date=date(2026, 7, 20),
        fii_cash_net=-100,
        dii_cash_net=200,
        event_risk="LOW",
        event_note="first",
        verified=True,
    )
    rows = store.upsert(
        session_date=date(2026, 7, 20),
        fii_cash_net=-50,
        dii_cash_net=250,
        event_risk="NONE",
        event_note="updated",
        verified=False,
    )
    assert len(rows) == 1
    assert rows[0]["fii_cash_net"] == -50.0
    assert rows[0]["event_note"] == "updated"


def test_context_store_rejects_unverified_high_risk(tmp_path):
    store = MarketContextStore(tmp_path / "context.json")
    with pytest.raises(ValueError, match="must be marked verified"):
        store.upsert(
            session_date=date(2026, 7, 20),
            fii_cash_net=None,
            dii_cash_net=None,
            event_risk="HIGH",
            event_note="rumour",
            verified=False,
        )


def test_context_store_keeps_dates_separate_and_loads_selected_date(tmp_path):
    store = MarketContextStore(tmp_path / "context.json")
    store.upsert(
        session_date=date(2026, 7, 17),
        fii_cash_net=-376.4,
        dii_cash_net=1017.9,
        fii_index_futures_net=5393.0,
        event_risk="NONE",
    )
    store.upsert(
        session_date=date(2026, 7, 16),
        fii_cash_net=-4205.6,
        dii_cash_net=2986.4,
        fii_index_futures_net=1994.3,
        event_risk="NONE",
    )
    row_17 = store.get(date(2026, 7, 17))
    row_16 = store.get(date(2026, 7, 16))
    assert row_17 is not None and row_17["fii_cash_net"] == -376.4
    assert row_16 is not None and row_16["fii_cash_net"] == -4205.6


def test_context_store_keeps_only_latest_fifteen_dates(tmp_path):
    store = MarketContextStore(tmp_path / "context.json")
    for day in range(1, 18):
        store.upsert(
            session_date=date(2026, 7, day),
            fii_cash_net=float(day),
            dii_cash_net=None,
            event_risk="NONE",
        )
    rows = store.load()
    assert len(rows) == 15
    assert rows[0]["date"] == "2026-07-03"
    assert rows[-1]["date"] == "2026-07-17"


def test_context_store_backup_round_trip(tmp_path):
    first = MarketContextStore(tmp_path / "first.json")
    first.upsert(
        session_date=date(2026, 7, 17),
        fii_cash_net=-376.4,
        dii_cash_net=1017.9,
        fii_index_futures_net=5393.0,
        event_risk="LOW",
        verified=True,
    )
    second = MarketContextStore(tmp_path / "second.json")
    rows = second.import_bytes(first.export_bytes())
    assert len(rows) == 1
    assert rows[0]["fii_index_futures_net"] == 5393.0


def test_context_store_rejects_likely_contract_quantity_in_crore_field(tmp_path):
    store = MarketContextStore(tmp_path / "context.json")
    with pytest.raises(ValueError, match="looks too large"):
        store.upsert(
            session_date=date(2026, 7, 17),
            fii_cash_net=-376.4,
            dii_cash_net=1017.9,
            fii_index_futures_net=-216528,
            event_risk="NONE",
        )
