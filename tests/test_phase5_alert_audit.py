from analysis.alert_audit import filter_alert_history, summarize_alert_history


def test_alert_audit_summary_is_read_only_and_computes_latency():
    rows = [
        {"status": "SENT", "latency_seconds": 1.0, "direction": "BULLISH", "conflict": False},
        {"status": "SENT", "latency_seconds": 2.0, "direction": "BEARISH", "conflict": True},
        {"status": "FAILED", "latency_seconds": 4.0, "direction": "BEARISH", "conflict": False},
    ]
    original = [dict(row) for row in rows]
    summary = summarize_alert_history(rows)
    assert summary["total"] == 3
    assert summary["sent"] == 2
    assert summary["failed"] == 1
    assert summary["delivery_rate_pct"] == 66.7
    assert summary["median_latency_seconds"] == 2.0
    assert summary["slow_count"] == 1
    assert rows == original


def test_alert_audit_filters_do_not_mutate_source():
    rows = [
        {"status": "SENT", "direction": "BULLISH"},
        {"status": "FAILED", "direction": "BEARISH"},
    ]
    result = filter_alert_history(rows, status="SENT", direction="BULLISH")
    assert result == [{"status": "SENT", "direction": "BULLISH"}]
    assert len(rows) == 2
