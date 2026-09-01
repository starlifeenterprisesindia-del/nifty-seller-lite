from types import SimpleNamespace as NS

from analysis.presentation_safety import (
    candidate_invalidation_text,
    display_main_blocker,
    market_rukh_display,
    normalized_news_display,
    prepare_snapshot_for_presentation,
    safe_brain_hinglish_line,
)


def fake_snapshot():
    decision = NS(
        final_action="WAIT",
        market_direction="BULLISH",
        signal_state="BEARISH DEVELOPING",
        blocker="Older market news exists; low decision weight",
        reasons=(
            "Older market news exists; low decision weight",
            "No strategy meets score and separation thresholds",
        ),
        ce_sell=NS(reasons=(), cautions=("Older market news exists; low decision weight",)),
        pe_sell=NS(reasons=(), cautions=()),
        iron_condor=NS(reasons=(), cautions=()),
        wait_need=NS(reasons=(), cautions=()),
    )
    return NS(
        decision=decision,
        core_evidence=NS(
            market_state="MIXED / NO CLEAR CORE EDGE",
            bullish_score=51.1,
            bearish_score=20.6,
            range_score=45.3,
            confidence=68.1,
        ),
        option_intelligence=NS(bias="BEARISH"),
        heavyweight_intelligence=NS(state="BROAD BULLISH"),
        barrier_map=NS(
            nearest_resistance=NS(lower=24276.0, upper=24282.0),
            nearest_support=NS(lower=24258.0, upper=24264.0),
        ),
        news_context=NS(
            status="OLD",
            bias="NEUTRAL",
            risk_level="HIGH",
            newest_age_minutes=143.0,
            summary="OLD context; risk HIGH",
            headlines=(NS(impact="HIGH"),),
        ),
        feed_status={"news": NS(use_state="UNAVAILABLE", message="risk HIGH")},
        execution_guard=NS(
            blockers=(
                "Older market news exists; low decision weight",
                "Flow confidence 74.0% is below 75%",
            )
        ),
        trade_plan=NS(
            blocker="Older market news exists; low decision weight",
            ce_sell=NS(blocker="Older market news exists; low decision weight", reasons=()),
            pe_sell=NS(blocker="None", reasons=()),
            iron_condor=NS(blocker="None", reasons=()),
        ),
    )


def test_mixed_core_is_not_presented_as_unconditional_up_or_down():
    label, score, note = market_rukh_display(fake_snapshot())
    assert label == "MIXED"  # core card must not borrow combined AI direction
    assert score >= 0
    assert "Core MIXED" in note


def test_brain_line_labels_levels_by_actual_side():
    line = safe_brain_hinglish_line(fake_snapshot())
    assert "R1 24,276–24,282" in line
    assert "S1 24,258–24,264" in line
    assert "R1 24,258–24,264" not in line
    assert "WAIT" in line


def test_old_news_is_not_used_as_main_blocker():
    blocker = display_main_blocker(fake_snapshot())
    assert blocker == "No strategy meets score and separation thresholds"


def test_old_news_risk_is_normalized_to_low_weight():
    display = normalized_news_display(fake_snapshot().news_context)
    assert display.status == "OLD / LOW WEIGHT"
    assert display.risk == "LOW WEIGHT"
    assert display.bias == "NEUTRAL"


def test_pe_sell_reference_has_support_break_invalidation():
    text = candidate_invalidation_text(fake_snapshot(), "PE SELL")
    assert text is not None
    assert "support ke neeche" in text
    assert "below 24,258" in text


def test_presentation_copy_does_not_mutate_authoritative_snapshot():
    original = fake_snapshot()
    view = prepare_snapshot_for_presentation(original)
    assert original.decision.blocker.startswith("Older market news")
    assert view.decision.blocker == "No strategy meets score and separation thresholds"
    assert original.news_context.risk_level == "HIGH"
    assert view.news_context.risk_level == "LOW WEIGHT"
    assert view.news_context.headlines[0].impact == "HIGH (OLD / NO LIVE WEIGHT)"
    assert view.feed_status["news"].use_state == "OLD / LOW WEIGHT"
    assert view.decision.market_direction == original.decision.market_direction
