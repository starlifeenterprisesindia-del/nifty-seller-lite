from analysis.replay_review import build_replay_bundle


def test_replay_keeps_recorded_market_intelligence_without_recalculation():
    samples = [{
        "at": "2026-10-08T10:00:00+05:30",
        "spot": 22600,
        "version": "test",
        "direction": "DOWN",
        "background_action": "WAIT",
        "barriers": {},
        "activity": {},
        "evidence": {"option_intelligence": {}},
        "snapshot_integrity": {"state": "GOOD"},
        "market_intelligence": {
            "market_state": "TREND",
            "direction": "BEARISH",
            "expansion_pressure": 72,
            "pressure_velocity": 12,
            "one_brain_alignment": "DIRECTION ALIGNED",
            "system_status": "WATCH",
            "move_radar": {"state": "WATCH"},
            "pressure_integrity": {
                "quality_state": "VERIFIED",
                "quality_score": 78,
                "move_attack_state": "ATTACK",
                "move_risk_state": "HIGH",
            },
            "liquidity": {"hunt_bias": "DOWNSIDE", "next_hunt_zone": {"lower": 22550, "upper": 22570}},
        },
    }]
    bundle = build_replay_bundle(samples, [], [], [])
    row = bundle["timeline"][0]
    assert row["mi_direction"] == "BEARISH"
    assert row["mi_quality_state"] == "VERIFIED"
    assert row["snapshot_sync_state"] == "GOOD"
    assert bundle["statistics"]["move_radar_non_normal_samples"] == 1
