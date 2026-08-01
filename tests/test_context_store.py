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


def test_context_store_mirror_recovers_primary_regression(tmp_path):
    store = MarketContextStore(tmp_path / "context.json")
    store.upsert(
        session_date=date(2026, 7, 16),
        fii_cash_net=-100,
        dii_cash_net=200,
        event_risk="NONE",
    )
    store.upsert(
        session_date=date(2026, 7, 17),
        fii_cash_net=-50,
        dii_cash_net=250,
        event_risk="NONE",
    )
    # Simulate a stale/corrupt primary copy while the mirror remains authoritative.
    store.path.write_text('{"schema_version":1,"entries":[]}', encoding="utf-8")
    rows = store.load()
    assert [row["date"] for row in rows] == ["2026-07-16", "2026-07-17"]
    # load() self-heals the primary from the monotonic merged view.
    assert "2026-07-17" in store.path.read_text(encoding="utf-8")


def test_context_store_accepts_fii_futures_contracts_and_long_short_percentages(tmp_path):
    store = MarketContextStore(tmp_path / "context.json")
    rows = store.upsert(
        session_date=date(2026, 7, 27),
        fii_cash_net=-1688.2,
        dii_cash_net=2329.1,
        fii_index_futures_contracts=266925,
        fii_futures_long_pct=8.78,
        fii_futures_short_pct=91.22,
        event_risk="NONE",
    )
    row = rows[0]
    assert row["fii_index_futures_contracts"] == 266925.0
    assert row["fii_futures_long_pct"] == 8.78
    assert row["fii_futures_short_pct"] == 91.22


def test_context_store_rejects_bad_futures_percentage_pair(tmp_path):
    store = MarketContextStore(tmp_path / "context.json")
    with pytest.raises(ValueError, match="about 100"):
        store.upsert(
            session_date=date(2026, 7, 27),
            fii_cash_net=-1688.2,
            dii_cash_net=2329.1,
            fii_index_futures_contracts=266925,
            fii_futures_long_pct=8.78,
            fii_futures_short_pct=80.0,
            event_risk="NONE",
        )


class FakeCloudJournal:
    enabled = True
    location = "owner/private-data:fii.json"

    def __init__(self, entries=None, fail=False):
        self.entries = list(entries or [])
        self.fail = fail
        self.sha = "sha-1" if self.entries else None
        self.write_calls = 0

    def read(self):
        from services.github_journal import GitHubJournalError, GitHubJournalSnapshot

        if self.fail:
            raise GitHubJournalError("offline")
        return GitHubJournalSnapshot(
            data={"schema_version": 1, "entries": list(self.entries)},
            sha=self.sha,
            exists=bool(self.sha),
        )

    def write(self, data, *, sha):
        from services.github_journal import GitHubJournalError

        if self.fail:
            raise GitHubJournalError("offline")
        self.write_calls += 1
        self.entries = list(data["entries"])
        self.sha = f"sha-{self.write_calls + 1}"
        return self.sha


def test_context_store_cloud_sync_survives_new_local_filesystem(tmp_path):
    cloud = FakeCloudJournal()
    first = MarketContextStore(
        tmp_path / "first" / "context.json",
        cloud_backend=cloud,
        cloud_pull_ttl_seconds=0,
    )
    first.upsert(
        session_date=date(2026, 8, 1),
        fii_cash_net=-123.4,
        dii_cash_net=456.7,
        event_risk="NONE",
    )
    assert cloud.write_calls >= 1

    second = MarketContextStore(
        tmp_path / "fresh-deployment" / "context.json",
        cloud_backend=cloud,
        cloud_pull_ttl_seconds=0,
    )
    rows = second.load()
    assert rows[0]["date"] == "2026-08-01"
    assert rows[0]["fii_cash_net"] == -123.4
    assert second.sync_status().label == "CLOUD SYNC OK"


def test_context_store_cloud_failure_keeps_local_data(tmp_path):
    cloud = FakeCloudJournal(fail=True)
    store = MarketContextStore(
        tmp_path / "context.json",
        cloud_backend=cloud,
        cloud_pull_ttl_seconds=0,
    )
    rows = store.upsert(
        session_date=date(2026, 8, 1),
        fii_cash_net=-100,
        dii_cash_net=200,
        event_risk="NONE",
    )
    assert rows[0]["fii_cash_net"] == -100.0
    assert store.path.exists()
    assert store.sync_status().label == "CLOUD FAILED · LOCAL SAFE"


def test_blank_update_does_not_erase_existing_fii_dii_values(tmp_path):
    store = MarketContextStore(tmp_path / "context.json")
    store.upsert(
        session_date=date(2026, 8, 1),
        fii_cash_net=-100,
        dii_cash_net=200,
        fii_index_futures_contracts=266925,
        fii_futures_long_pct=8.78,
        fii_futures_short_pct=91.22,
        event_risk="NONE",
    )
    rows = store.upsert(
        session_date=date(2026, 8, 1),
        fii_cash_net=None,
        dii_cash_net=None,
        fii_index_futures_contracts=None,
        fii_futures_long_pct=None,
        fii_futures_short_pct=None,
        event_risk="LOW",
        verified=True,
    )
    row = rows[0]
    assert row["fii_cash_net"] == -100.0
    assert row["dii_cash_net"] == 200.0
    assert row["fii_index_futures_contracts"] == 266925.0
    assert row["fii_futures_long_pct"] == 8.78
