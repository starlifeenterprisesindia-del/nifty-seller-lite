from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from analysis.strike_entry import plan_strike_entry
from analysis.heavyweights import calculate_heavyweight_bundle
from analysis.pattern_alerts import aligned_pattern_alert
from analysis.decision import calculate_final_decision, _level_adjustments, _futures_activity_scores
from config import CONFIG
from test_decision import common_kwargs


NOW = datetime(2026, 8, 27, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def planner_inputs():
    timestamps = pd.date_range(end=NOW - timedelta(minutes=3), periods=20, freq="3min")
    frame = pd.DataFrame({"timestamp": timestamps, "open": 102., "high": 104., "low": 101.,
                          "close": 103., "is_complete": True, "volume": 1000.})
    frame.loc[19, ["open", "high", "low", "close"]] = [101.5, 106., 101., 105.]
    chain = pd.DataFrame([
        {"side": "PE", "strike": 100., "top_bid_price": 4., "top_ask_price": 4.2, "last_price": 4.1},
        {"side": "PE", "strike": 95., "top_bid_price": 1.1, "top_ask_price": 1.2, "last_price": 1.15},
        {"side": "CE", "strike": 105., "top_bid_price": 4., "top_ask_price": 4.2, "last_price": 4.1},
    ])
    return dict(candles=frame, barrier_map=NS(nearest_support=NS(lower=100., upper=102.),
                nearest_resistance=NS(lower=120., upper=122.)), option_chain=chain,
                side="PE", position="SELL", strike=100., hedge_strike=95., spot=105., as_of=NOW,
                expiry="2026-09-01", live=True, risk_budget=100., lot_size=1, lots=1)


def test_planner_retest_now_without_ai_dependency():
    result = plan_strike_entry(**planner_inputs())
    assert result.status == "ENTRY NOW — RETEST HOLD"
    assert result.net_credit == pytest.approx(2.8)
    assert result.worst_case_rupees == pytest.approx(2.42)


@pytest.mark.parametrize("change, expected", [({"live": False}, "REFERENCE ONLY"),
    ({"hedge_strike": None}, "WAIT"), ({"spot": 98.}, "CANCEL"),
    ({"risk_budget": 1.}, "WAIT"), ({"spot": 119.}, "NO CHASE"),
    ({"as_of": NOW + timedelta(minutes=10)}, "WAIT")])
def test_planner_safety(change, expected):
    values = planner_inputs()
    values.update(change)
    assert plan_strike_entry(**values).status == expected


def test_planner_crossed_book_blocks():
    values = planner_inputs()
    values["option_chain"].loc[0, "top_ask_price"] = 3.
    assert plan_strike_entry(**values).status == "WAIT"


def test_independent_buy_risk_is_premium_not_hedge_required():
    values = planner_inputs()
    values.update(side="CE", position="BUY", strike=105., hedge_strike=None)
    assert plan_strike_entry(**values).status.startswith("ENTRY NOW")


def test_no_momentum_or_retest_waits_without_invented_estimate():
    values = planner_inputs()
    values["candles"].loc[19, ["open", "low", "high", "close"]] = [104.4, 104.2, 105., 104.6]
    values["spot"] = 104.6
    result = plan_strike_entry(**values)
    assert result.status == "WAIT FOR RETEST"
    assert result.premium_range is None


def test_partial_top9_history_is_warming_and_residual_hidden():
    symbol = CONFIG.top9[0].symbol
    history = [{"at": (NOW - timedelta(minutes=m)).isoformat(), "nifty": 24000.,
                "prices": {symbol: 97. if m == 15 else 98.}} for m in (15, 3)]
    result = calculate_heavyweight_bundle([{"symbol": symbol, "last_price": 99., "ohlc": {"close": 100.}}],
        NOW, {"last_price": 24000., "ohlc": {"close": 24100.}}, history=history)
    assert result.rows[0].recent_state == "WARMING UP"
    assert result.recent_15m_move_pct is None
    assert result.rows[0].change_pct < 0
    assert result.estimated_remaining_move_pct is None
    assert result.recent_contribution_points is None


def test_day_change_is_not_recent_vote():
    from analysis.decision import _heavyweight_scores
    result = calculate_heavyweight_bundle([], NOW)
    assert _heavyweight_scores(result) == (0., 0., 0.)


def test_futures_missing_does_not_create_range():
    assert _futures_activity_scores(None) == (0., 0., 0.)


def test_barrier_penalty_not_added_twice():
    values = common_kwargs()
    level = replace(values["levels"], downside_room=5., current_position="NEAR SUPPORT")
    assert _level_adjustments(level)[0] == -18


def test_fii_and_high_stable_vix_do_not_vote():
    values = common_kwargs()
    first = calculate_final_decision(**values)
    values["institutional"] = replace(values["institutional"], state="FII SELLING", status="MISSING")
    values["vix"] = replace(values["vix"], regime="HIGH", movement="STABLE")
    second = calculate_final_decision(**values)
    assert first.ce_sell.score == second.ce_sell.score
    assert first.pe_sell.score == second.pe_sell.score


def test_pattern_aligned_alert_rejects_weak_opposite_forming():
    signal = NS(stage="CONFIRMED", direction="BULLISH", status="READY", confidence=80,
                strength="STRONG", level_label="S1", family="3M W/M", name="W", detected_at=str(NOW),
                neckline=105., invalidation_level=99.)
    snap = NS(market_session=NS(is_live=True), feed_status={x: NS(use_state="LIVE") for x in ("quotes", "candles", "option_chain")},
              decision=NS(market_direction="BULLISH"), core_evidence=NS(bullish_score=80, bearish_score=10),
              option_intelligence=NS(status="READY", bullish_score=75, bearish_score=10),
              patterns=NS(wm_3m=signal, candle_3m=NS(stage="NONE")), created_at=NOW)
    assert aligned_pattern_alert(snap)
    for stage in ("FORMING", "BREAK DETECTED", "FAILED"):
        signal.stage = stage
        assert aligned_pattern_alert(snap) is None
    signal.stage = "CONFIRMED"
    signal.direction = "BEARISH"
    assert aligned_pattern_alert(snap) is None


def test_paper_threshold_only():
    assert CONFIG.shadow_journal_min_confidence == 50
    assert CONFIG.shadow_journal_min_strategy_score == 55
    assert CONFIG.shadow_journal_min_option_confidence == 55
    assert CONFIG.shadow_journal_min_sell_credit_points == 4
    assert CONFIG.decision_minimum_score == 62


def test_paper_wait_candidate_does_not_bypass_common_gate(tmp_path):
    from test_snapshot_service import StubClient, StubMaster
    import test_execution_guard as fixtures
    from test_position_guardian import bundle
    from services.snapshot_service import SnapshotService
    from services.shadow_journal import process_auto_shadow_journal, ShadowJournalStore
    snap = SnapshotService(StubClient(), StubMaster()).build(NOW)
    decision = replace(fixtures.decision("WAIT"), decision_confidence=55,
                       pe_sell=replace(fixtures.decision().pe_sell, score=55))
    snap = replace(snap, created_at=fixtures.NOW, decision=decision,
        trade_plan=replace(bundle(), selected_setup="WAIT", candidate_setup="PE SELL"),
        risk_profile=fixtures.risk_profile(capital=2_000_000),
        levels=replace(snap.levels, status="READY", upside_room=30.0),
        price_action=fixtures.price_action(), discipline_state=fixtures.discipline(),
        option_intelligence=fixtures.option_intelligence(confidence=55),
        market_session=fixtures.MarketSession("LIVE", "LIVE", True, "fresh"),
        feed_status=fixtures.feeds())
    store = ShadowJournalStore(tmp_path / "paper.json")
    entries = process_auto_shadow_journal(snap, store, enabled=True)
    assert entries == []
    assert "Common Gate" in store.last_blocker
    assert snap.trade_plan.selected_setup == snap.decision.final_action == "WAIT"
    assert process_auto_shadow_journal(snap, store, enabled=True) == []


def test_live_guard_not_lowered_to_paper_50():
    import test_execution_guard as fixtures
    d = replace(fixtures.decision(), decision_confidence=50,
                pe_sell=replace(fixtures.decision().pe_sell, score=50))
    guard = fixtures.run(decision=d)
    assert guard.readiness == "BLOCKED"
    assert any("75" in reason for reason in guard.blockers)


def test_old_news_has_no_score_or_confidence_penalty():
    values = common_kwargs()
    baseline = calculate_final_decision(**values)
    # A lightweight same-shape context suffices; no external news request.
    values["news"] = NS(status="OLD", risk_level="HIGH")
    old = calculate_final_decision(**values)
    assert old.ce_sell.score == baseline.ce_sell.score
    assert old.pe_sell.score == baseline.pe_sell.score
    assert old.decision_confidence == baseline.decision_confidence


def test_unreadable_journal_is_not_overwritten(tmp_path):
    from services.shadow_journal import ShadowJournalStore
    path = tmp_path / "broken.json"
    path.write_text("not-json")
    store = ShadowJournalStore(path)
    assert store.load() == []
    assert store.local_read_failed
    with pytest.raises(ValueError):
        store.save([])
    assert path.read_text() == "not-json"


def test_pattern_telegram_dedup_and_stale_no_real_delivery():
    from services.telegram_alerts import LiveAlertEngine
    messages = []
    engine = LiveAlertEngine(NS(configured=True), sender=messages.append, async_delivery=False)
    payload = dict(captured_at=NOW.isoformat(), direction="BULLISH",
                   pattern_ids=["W:confirmed-at-10"], message="test only")
    assert engine.observe_pattern(payload, now_ts=NOW.timestamp())
    assert not engine.observe_pattern(payload, now_ts=NOW.timestamp() + 1)
    payload["pattern_ids"] = ["new"]
    assert not engine.observe_pattern(payload, now_ts=NOW.timestamp() + 121)
    assert len(messages) == 1


def test_expired_contract_and_invalid_quantity_block_planner():
    values = planner_inputs()
    values["expiry"] = "2026-08-26"
    assert plan_strike_entry(**values).status == "WAIT"
    values = planner_inputs()
    values["lots"] = 0
    assert plan_strike_entry(**values).status == "WAIT"


def test_calculator_and_alert_panel_render_offline():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string("""
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tests"))
from datetime import datetime
from test_snapshot_service import StubClient, StubMaster, IST
from services.snapshot_service import SnapshotService
from ui.premium_calculator import render_spot_premium_calculator
from ui.pattern_alerts import render_pattern_alerts
s = SnapshotService(StubClient(), StubMaster()).build(datetime(2026,7,19,13,37,tzinfo=IST))
render_spot_premium_calculator(s)
render_pattern_alerts(s)
""").run(timeout=30)
    assert not app.exception
