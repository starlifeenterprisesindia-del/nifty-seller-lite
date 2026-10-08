from analysis.session_calibration import build_calibration_summary, build_latency_summary


def test_calibration_is_descriptive_and_handles_missing_outcomes():
    rows = [
        {"direction": "BULLISH", "move_radar_state": "WATCH", "move_risk_state": "HIGH",
         "pressure_quality_state": "VERIFIED", "move_attack_state": "ATTACK",
         "real_pressure_score": 80, "fake_pressure_score": 20,
         "actual_5m_close_change": 12, "actual_5m_max_up": 20, "actual_5m_max_down": 3,
         "actual_15m_close_change": 18, "actual_15m_max_up": 25, "actual_15m_max_down": 4},
        {"direction": "BEARISH", "move_radar_state": "NORMAL", "move_risk_state": "NORMAL",
         "pressure_quality_state": "UNVERIFIED", "move_attack_state": "NORMAL",
         "actual_5m_close_change": None, "actual_15m_close_change": -7, "actual_15m_max_up": 2, "actual_15m_max_down": 12},
    ]
    result = build_calibration_summary(rows, alerts=[{"kind": "TEST"}])
    assert result["snapshot_rows"] == 2
    assert result["warning_rows"] == 1
    assert result["horizons"]["5m"]["covered_snapshots"] == 1
    assert "accuracy" in result["horizons"]["5m"]["note"].lower()


def test_latency_summary_percentiles():
    rows = [
        {"pipeline_seconds": 2, "build_seconds": 1, "market_intelligence_seconds": .02, "slowest_stage": "quotes"},
        {"pipeline_seconds": 4, "build_seconds": 3, "market_intelligence_seconds": .04, "slowest_stage": "trade_plan"},
    ]
    result = build_latency_summary(rows)
    assert result["samples"] == 2
    assert result["pipeline_p50_seconds"] == 3.0
    assert result["slowest_stage_counts"]["quotes"] == 1
