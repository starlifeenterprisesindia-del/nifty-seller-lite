from analysis.institutional_window import calculate_institutional_window


def _experts():
    def row(bull, bear, rel=.85, fresh=.9):
        return {"available": True, "bullish": bull, "bearish": bear, "reliability": rel, "freshness": fresh}
    return {
        "Structure": row(12, 82),
        "Futures": row(10, 80),
        "Options Flow": row(18, 70),
        "Barriers / Walls": row(15, 75),
        "Heavyweight Breadth": row(20, 68),
        "Momentum Acceleration": row(12, 78),
    }


def _integrity(**changes):
    base = {
        "barrier_attack_score": 78,
        "fake_pressure_score": 18,
        "family_oppositions": 0,
        "quality_score": 76,
        "real_pressure_score": 84,
        "price_response_score": 74,
        "quality_state": "VERIFIED",
        "barrier_state": "UNDER ATTACK",
        "move_attack_state": "ATTACK",
        "supportive_signals": (),
    }
    base.update(changes)
    return base


def _liquidity(**changes):
    base = {
        "path_clearance": 78,
        "hunt_bias": "DOWNSIDE",
        "next_hunt_zone": {"lower": 22480, "upper": 22500},
    }
    base.update(changes)
    return base


def _activity(**changes):
    base = {"status": "READY", "score": 72, "futures_volume_ratio": 1.9}
    base.update(changes)
    return base


def _live_feeds():
    return {
        "quotes": {"use_state": "LIVE"},
        "candles": {"use_state": "LIVE"},
        "option_chain": {"use_state": "LIVE"},
    }


def _calc(**changes):
    args = dict(
        direction="BEARISH",
        bull_pressure=14,
        bear_pressure=78,
        range_pressure=24,
        fast_confirmation_count=4,
        expansion_pressure=74,
        pressure_velocity=12,
        coverage=82,
        conflict="LOW",
        structure_event="BEARISH BREAKOUT",
        breakout_direction="BEARISH",
        breakout_quality=74,
        reversal_direction="MIXED",
        reversal_quality=30,
        experts=_experts(),
        pressure_integrity=_integrity(),
        liquidity=_liquidity(),
        activity=_activity(),
        recorded_feeds=_live_feeds(),
        live_override=True,
    )
    args.update(changes)
    return calculate_institutional_window(**args)


def test_all_six_core_gates_open_without_pattern_requirement():
    result = _calc()
    assert result.gates_ready == 6
    assert result.state in {"OPEN", "STRONG"}
    assert result.alert_eligible is True
    assert result.supportive_signals == ()


def test_forming_when_one_or_two_core_gates_are_missing():
    result = _calc(liquidity=_liquidity(path_clearance=35, hunt_bias="BALANCED", next_hunt_zone=None))
    assert result.gates_ready in {4, 5}
    assert result.state == "FORMING"
    assert result.alert_eligible is False
    assert "Path Clearance" in result.missing_gates


def test_absorption_blocks_pressure_effectiveness_and_open_alert():
    result = _calc(pressure_integrity=_integrity(quality_state="ABSORPTION RISK", fake_pressure_score=82, price_response_score=25))
    assert result.pressure_effectiveness.passed is False
    assert result.state != "OPEN"
    assert result.state != "STRONG"
    assert result.alert_eligible is False


def test_data_safety_blocks_alert_even_if_six_gates_ready():
    feeds = _live_feeds()
    feeds["option_chain"] = {"use_state": "WARMING UP"}
    result = _calc(recorded_feeds=feeds)
    assert result.gates_ready == 6
    assert result.state == "FORMING"
    assert result.alert_eligible is False
    assert result.data_safety_state == "LIMITED"


def test_mixed_direction_never_opens_window():
    result = _calc(direction="MIXED", bull_pressure=48, bear_pressure=47, fast_confirmation_count=0)
    assert result.directional_edge.available is False
    assert result.state != "OPEN"
    assert result.state != "STRONG"


def test_market_intelligence_alert_emits_first_window_open_transition():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from analysis.market_intelligence import _alerts

    window = _calc()
    if window.state == "STRONG":
        # OPEN and STRONG are both eligible first-window transitions.
        pass
    pressure = SimpleNamespace(
        supportive_signals=(), quality_state="VERIFIED", quality_score=76.0,
        move_attack_state="ATTACK", move_risk_state="BUILDING",
        family_confirmations=4, fake_pressure_score=18.0,
        best_progress_points=5.0, barrier_state="UNDER ATTACK",
        barrier_attack_score=78.0,
    )
    liquidity = SimpleNamespace(
        primary_zone=None, zone_role="NEXT HUNT ZONE", hunt_bias="DOWNSIDE",
        hunt_strength="HIGH", upside_hunt_pressure=20.0, downside_hunt_pressure=76.0,
        reach_state="POSSIBLE", sweep_state="NONE", sweep_outcome="UNCLEAR",
        sweep_quality=0.0,
    )
    snapshot = SimpleNamespace(
        market_session=SimpleNamespace(is_live=True),
        nifty_quote={"last_price": 22600.0},
        created_at=datetime.now(timezone.utc),
    )
    alerts = _alerts(
        snapshot=snapshot, direction="BEARISH", expansion=74.0, velocity=12.0,
        system_status="WATCH", alignment="DIRECTION ALIGNED", coverage=82.0,
        conflict="LOW", fake_risk="LOW", liquidity=liquidity,
        move_radar={"state": "BIG MOVE WATCH"}, direction_context="WITH TREND",
        pressure_integrity=pressure, institutional_window=window, previous={},
    )
    kinds = [row.get("kind") for row in alerts]
    assert "INSTITUTIONAL_WINDOW_OPEN" in kinds


def test_historical_backfill_keeps_missing_new_evidence_as_no_vote():
    from analysis.institutional_window import calculate_institutional_window_from_record
    mie = {
        "early_direction": "BEARISH",
        "bull_pressure": 15,
        "bear_pressure": 72,
        "range_pressure": 28,
        "fast_confirmation_count": 3,
        "expansion_pressure": 68,
        "pressure_velocity": 10,
        "evidence_coverage": 78,
        "evidence_conflict": "LOW",
        "structure_event": "BEARISH CONTINUATION",
        "breakout_direction": "BEARISH",
        "breakout_quality": 65,
        "reversal_direction": "MIXED",
        "reversal_quality": 30,
        "experts": list(_experts().values()),
        "liquidity": _liquidity(),
        # historical version did not record pressure_integrity
    }
    # restore expert names for list representation
    names = list(_experts().keys())
    mie["experts"] = [dict({"name": n}, **_experts()[n]) for n in names]
    result = calculate_institutional_window_from_record(
        mie, activity=_activity(), recorded_feeds=_live_feeds(), live_override=True
    )
    assert result.pressure_effectiveness.available is False
    assert result.alert_eligible is False
    assert result.state != "OPEN"
    assert "Pressure Effectiveness" in result.missing_gates
