from datetime import timedelta
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from analysis.big_player import _future_setup
from analysis.option_chain import validate_greeks
from analysis.option_intelligence import _choose_history_sample
from analysis.rsi_reversal_setup import (
    evaluate_rsi_reversal_setup,
    create_rsi_trade_record,
)
from test_rsi_reversal_setup import snapshot


def test_same_candle_stays_same_across_refreshes():
    current = snapshot(68, "SELLING")
    first = evaluate_rsi_reversal_setup(current, snapshot(74, "SELLING"))
    second = evaluate_rsi_reversal_setup(current, current)
    assert first.action == second.action == "CE SELL"
    assert first.rsi_previous == second.rsi_previous


@pytest.mark.parametrize("status", ["PARTIAL", "UNAVAILABLE", "REFERENCE ONLY"])
def test_incomplete_flow_never_becomes_condor(status):
    current = snapshot(68, "MIXED", score=35)
    current.big_player_activity.status = status
    assert evaluate_rsi_reversal_setup(current, None).action == "WAIT"


def test_stale_feed_blocks_independent_entry():
    current = snapshot(68, "SELLING")
    current.feed_status["quotes"].use_state = "STALE"
    assert evaluate_rsi_reversal_setup(current, None).action == "WAIT"


def test_missing_oi_blocks_entry():
    current = snapshot(68, "SELLING")
    current.option_intelligence.status = "UNAVAILABLE"
    assert evaluate_rsi_reversal_setup(current, None).action == "WAIT"


def test_risk_budget_and_cap_respected():
    current = snapshot(68, "SELLING")
    assert evaluate_rsi_reversal_setup(current, None).suggested_lots == 1
    current.risk_profile.risk_budget_rupees = 1000
    assert evaluate_rsi_reversal_setup(current, None).action == "WAIT"
    current.risk_profile.risk_budget_rupees = 5000
    current.risk_profile.max_lots_cap = 0
    assert evaluate_rsi_reversal_setup(current, None).action == "WAIT"


def test_incomplete_forming_rsi_candle_ignored():
    current = snapshot(68, "SELLING")
    before = evaluate_rsi_reversal_setup(current, None)
    extra = current.candles_3m.tail(1).copy()
    extra.timestamp += pd.Timedelta(minutes=3)
    extra.close = 10000
    extra.is_complete = False
    current.candles_3m = pd.concat([current.candles_3m, extra])
    after = evaluate_rsi_reversal_setup(current, None)
    assert before.rsi_now == after.rsi_now
    assert after.action == "CE SELL"


def test_futures_latest_window_not_largest_old_move():
    frame = pd.DataFrame(
        {
            "close": [200 - i * 10 for i in range(13)] + [85, 90, 95],
            "open_interest": [100000 + i * 1000 for i in range(16)],
            "is_complete": True,
        }
    )
    assert _future_setup(frame)[2] == "BUY"


def test_28_second_observation_not_called_one_minute():
    now = snapshot(68, "SELLING").created_at
    history = [{"captured_at": (now - timedelta(seconds=28)).isoformat()}]
    assert _choose_history_sample(history, now, 60) == (None, None)


def test_greeks_mismatch_preserves_quotes_oi_and_source_values():
    frame = pd.DataFrame(
        [
            dict(
                strike=24200,
                side="CE",
                delta=0.52693,
                gamma=0.00128,
                theta=-15.0641,
                vega=11.644,
                implied_volatility=10.6504,
                oi=100,
                last_price=134,
            ),
            dict(
                strike=24200,
                side="PE",
                delta=-0.46142,
                gamma=0.00193,
                theta=-4.56778,
                vega=11.616,
                implied_volatility=7.0303,
                oi=200,
                last_price=72.85,
            ),
        ]
    )
    result = validate_greeks(frame)
    assert result.greeks_quality.eq("IV WARNING").all()
    assert list(result.delta) == list(frame.delta)
    assert list(result.oi) == [100, 200]
    assert list(result.last_price) == [134, 72.85]
    assert list(result.source_delta) == list(frame.delta)


def test_actual_fills_required_and_money_limit_frozen():
    current = snapshot(68, "SELLING")
    record = create_rsi_trade_record(current, 1, [80.0, 30.0])
    assert record["money_stop_rupees"] == 5000
    assert record["entry_credit_points"] == 50
    assert record["barrier_close_required"]
    with pytest.raises(ValueError):
        create_rsi_trade_record(current, 2, [80.0, 30.0])
    with pytest.raises(ValueError):
        create_rsi_trade_record(current, 1, [0.0, 30.0])


def test_total_spread_loss_triggers_manual_exit_alert():
    from analysis.position_guardian import calculate_position_guardian

    current = snapshot(68, "SELLING")
    record = create_rsi_trade_record(current, 1, [80.0, 30.0])
    # Lower alert threshold deliberately to exercise trigger within theoretical risk.
    record["money_stop_rupees"] = 1000
    rows = pd.DataFrame(
        [
            dict(
                side="CE",
                strike=24450,
                top_ask_price=110.0,
                top_bid_price=109.0,
                last_price=110.0,
            ),
            dict(
                side="CE",
                strike=24550,
                top_ask_price=41.0,
                top_bid_price=40.0,
                last_price=40.0,
            ),
        ]
    )
    result = calculate_position_guardian(
        discipline_state=NS(trade_record=record),
        option_chain=rows,
        current_expiry="2026-09-01",
        current_spot=24425.0,
        market_session=current.market_session,
        option_chain_live=True,
        as_of=current.created_at,
        completed_spot_close=24425.0,
    )
    assert result.unrealized_pnl_rupees == -1300
    assert result.instruction == "SL TRIGGERED — TOTAL POSITION LOSS"


def test_barrier_alert_uses_completed_close_not_wick():
    from analysis.position_guardian import calculate_position_guardian

    current = snapshot(68, "SELLING")
    record = create_rsi_trade_record(current, 1, [80.0, 30.0])
    rows = pd.DataFrame(
        [
            dict(
                side="CE",
                strike=24450,
                top_ask_price=80.0,
                top_bid_price=79.0,
                last_price=80.0,
            ),
            dict(
                side="CE",
                strike=24550,
                top_ask_price=31.0,
                top_bid_price=30.0,
                last_price=30.0,
            ),
        ]
    )
    kwargs = dict(
        discipline_state=NS(trade_record=record),
        option_chain=rows,
        current_expiry="2026-09-01",
        current_spot=24460.0,
        market_session=current.market_session,
        option_chain_live=True,
        as_of=current.created_at,
    )
    assert (
        "INVALIDATION"
        not in calculate_position_guardian(
            **kwargs, completed_spot_close=24425.0
        ).instruction
    )
    assert (
        "INVALIDATION"
        in calculate_position_guardian(
            **kwargs, completed_spot_close=24460.0
        ).instruction
    )


def test_main_and_rsi_cards_render_without_live_credentials():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tests"))
from datetime import datetime
from test_snapshot_service import StubClient, StubMaster, IST
from services.snapshot_service import SnapshotService
from ui.components import render_main_ai_market_view
from ui.rsi_reversal_setup import render_rsi_reversal_setup
s = SnapshotService(StubClient(), StubMaster()).build(datetime(2026,7,19,13,37,tzinfo=IST))
render_main_ai_market_view(s, None)
render_rsi_reversal_setup(s, None)
''').run(timeout=20)
    assert not app.exception


def test_rsi_actual_fill_form_renders():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tests"))
from test_rsi_reversal_setup import snapshot
from ui.rsi_reversal_setup import render_rsi_reversal_setup
s = snapshot(68, "SELLING")
render_rsi_reversal_setup(s, None, record_trade=lambda **kwargs: None)
''').run(timeout=20)
    assert not app.exception
    assert len(app.number_input) == 3


def test_confirmed_opposite_flow_vetoes_instant_main_entry():
    from test_decision import common_kwargs
    from analysis.decision import calculate_final_decision
    kwargs = common_kwargs()
    kwargs["big_player"] = NS(direction="SELLING", state="VERY STRONG", status="READY",
        score=89., confirmation_count=2, confirmation_total=2, futures_setup="SHORT BUILD-UP",
        activity_type="SHORT BUILD-UP", price_response="FOLLOW-THROUGH")
    result = calculate_final_decision(**kwargs)
    assert result.instant_action == result.final_action == "WAIT"
    assert any("opposes" in reason for reason in result.wait_need.reasons)
