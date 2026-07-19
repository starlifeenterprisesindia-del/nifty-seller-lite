from datetime import date

from analysis.market_context import calculate_market_context


def test_missing_context_stays_missing_not_zero():
    institutional, event = calculate_market_context([], date(2026, 7, 20))
    assert institutional.status == "MISSING"
    assert institutional.latest_fii_net is None
    assert institutional.latest_dii_net is None
    assert event.level == "NONE"
    assert event.status == "NOT PROVIDED"


def test_context_builds_rolling_sums_and_verified_event():
    entries = []
    for day in range(1, 7):
        entries.append(
            {
                "date": f"2026-07-{day:02d}",
                "fii_cash_net": 100.0 * day,
                "dii_cash_net": 50.0 * day,
                "fii_index_futures_net": 25.0 * day,
                "event_risk": "NONE",
                "event_note": "",
                "verified": False,
            }
        )
    entries[-1].update(
        {"event_risk": "MEDIUM", "event_note": "RBI policy", "verified": True}
    )
    institutional, event = calculate_market_context(entries, date(2026, 7, 6))
    assert institutional.observations == 6
    assert institutional.fii_5d_net == 2000.0
    assert institutional.dii_5d_net == 1000.0
    assert institutional.fii_index_futures_5d_net == 500.0
    assert institutional.latest_fii_index_futures_net == 150.0
    assert institutional.status == "READY"
    assert event.level == "MEDIUM"
    assert event.verified is True
