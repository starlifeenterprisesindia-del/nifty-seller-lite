from dataclasses import replace

from analysis.decision import _entry_alignment_blocker
from config import CONFIG
from test_decision import common_kwargs
from test_execution_guard import price_action


def test_bullish_15m_permission_and_3m_trigger_allow_bullish_candidate():
    blocker = _entry_alignment_blocker(
        setup="PE SELL",
        price_action=price_action(),
        levels=common_kwargs()["levels"],
        volume=None,
        patterns=None,
    )
    assert blocker is None


def test_bullish_timeframes_block_bearish_candidate():
    blocker = _entry_alignment_blocker(
        setup="CE SELL",
        price_action=price_action(),
        levels=common_kwargs()["levels"],
        volume=None,
        patterns=None,
    )
    assert blocker is not None
    assert "15m permission" in blocker


def test_good_direction_still_blocks_when_barrier_space_is_too_small():
    levels = replace(common_kwargs()["levels"], upside_room=5.0)
    blocker = _entry_alignment_blocker(
        setup="PE SELL",
        price_action=price_action(),
        levels=levels,
        volume=None,
        patterns=None,
    )
    assert blocker is not None
    assert "Barrier space" in blocker


def test_entry_ready_threshold_is_stricter_than_candidate_threshold():
    assert CONFIG.execution_minimum_unified_score == 75
    assert CONFIG.decision_minimum_score < CONFIG.execution_minimum_unified_score
