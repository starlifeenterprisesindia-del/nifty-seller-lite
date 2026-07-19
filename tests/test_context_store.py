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
