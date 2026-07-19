from __future__ import annotations

from analysis.technical_utils import clamp
from config import CONFIG
from models import (
    CoreMarketEvidence,
    EventRiskContext,
    FinalDecision,
    HeavyweightBundle,
    InstitutionalContext,
    LevelBundle,
    MarketSession,
    OptionIntelligence,
    StrategyEvaluation,
    VixContext,
)


def _heavyweight_scores(bundle: HeavyweightBundle) -> tuple[float, float, float]:
    state = bundle.state.upper()
    if "BROAD BULLISH" in state:
        return 88.0, 12.0, 20.0
    if "NARROW BULLISH" in state:
        return 68.0, 25.0, 42.0
    if "BROAD BEARISH" in state:
        return 12.0, 88.0, 20.0
    if "NARROW BEARISH" in state:
        return 25.0, 68.0, 42.0
    if bundle.status == "UNAVAILABLE":
        return 45.0, 45.0, 58.0
    return 42.0, 42.0, 70.0


def _institutional_scores(
    context: InstitutionalContext,
) -> tuple[float, float, float, str | None]:
    state = context.state.upper()
    if context.status == "MISSING":
        return 50.0, 50.0, 55.0, "FII/DII background is missing"
    if "SUPPORT" in state or "FII BUYING" in state:
        return 68.0, 32.0, 42.0, None
    if "PRESSURE" in state or "FII SELLING" in state:
        return 32.0, 68.0, 42.0, None
    return 48.0, 48.0, 60.0, None


def _seller_environment_score(vix: VixContext) -> float:
    if vix.status != "READY":
        return 42.0
    if vix.movement == "RISING FAST" or vix.regime == "HIGH":
        return 30.0
    if vix.regime == "ELEVATED":
        return 72.0 if vix.movement != "RISING FAST" else 38.0
    if vix.regime == "NORMAL":
        return 68.0
    if vix.regime == "LOW":
        return 48.0
    return 55.0


def _level_adjustments(levels: LevelBundle) -> tuple[float, float, float, list[str]]:
    ce_adjust = pe_adjust = condor_adjust = 0.0
    cautions: list[str] = []
    if levels.status != "READY":
        return -8.0, -8.0, -12.0, ["Support/resistance evidence unavailable"]

    if levels.downside_room is not None:
        if levels.downside_room < 10:
            ce_adjust -= 18
            cautions.append("CE sell has limited downside room before support")
        elif levels.downside_room >= 25:
            ce_adjust += 7
    if levels.upside_room is not None:
        if levels.upside_room < 10:
            pe_adjust -= 18
            cautions.append("PE sell has limited upside room before resistance")
        elif levels.upside_room >= 25:
            pe_adjust += 7

    if levels.current_position == "NEAR SUPPORT":
        ce_adjust -= 10
    elif levels.current_position == "NEAR RESISTANCE":
        pe_adjust -= 10

    rooms = [
        value
        for value in (levels.upside_room, levels.downside_room)
        if value is not None
    ]
    if len(rooms) == 2 and min(rooms) >= 18:
        condor_adjust += 8
    elif len(rooms) < 2 or min(rooms) < 10:
        condor_adjust -= 16
        cautions.append("Iron Condor does not have balanced room on both sides")
    return ce_adjust, pe_adjust, condor_adjust, cautions


def _event_adjustment(event: EventRiskContext) -> tuple[float, str | None]:
    if event.level == "HIGH":
        return 55.0, "Verified high-impact event risk"
    if event.level == "MEDIUM":
        return 22.0, "Verified medium-impact event risk"
    if event.level == "LOW":
        return 5.0, None
    return 0.0, None


def _status(score: float, cautions: list[str]) -> str:
    if score >= 75 and not cautions:
        return "STRONG"
    if score >= CONFIG.decision_minimum_score:
        return "READY / WATCH"
    if score >= 48:
        return "WEAK"
    return "AVOID"


def _top_reasons(*groups: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    items: list[str] = []
    for group in groups:
        for item in group:
            clean = str(item).strip()
            if clean and clean not in items:
                items.append(clean)
    return tuple(items[:3])


def calculate_final_decision(
    *,
    core: CoreMarketEvidence,
    options: OptionIntelligence,
    heavyweights: HeavyweightBundle,
    vix: VixContext,
    levels: LevelBundle,
    institutional: InstitutionalContext,
    event_risk: EventRiskContext,
    market_session: MarketSession,
    quote_live: bool,
    candles_live: bool,
    option_chain_live: bool,
) -> FinalDecision:
    """One canonical read-only strategy brain.

    Strategy scores are independent suitability percentages. WAIT is an uncertainty/risk
    need percentage and is deliberately not normalized against the three strategies.
    """

    heavy_bull, heavy_bear, heavy_range = _heavyweight_scores(heavyweights)
    inst_bull, inst_bear, inst_range, inst_caution = _institutional_scores(
        institutional
    )
    seller_score = _seller_environment_score(vix)

    # Frozen architecture weights: core 35%, options 35%, Top-7 15%,
    # VIX/session/institutional/event background 15%.
    ce = (
        core.bearish_score * 0.35
        + options.bearish_score * 0.35
        + heavy_bear * 0.15
        + (seller_score * 0.60 + inst_bear * 0.40) * 0.15
    )
    pe = (
        core.bullish_score * 0.35
        + options.bullish_score * 0.35
        + heavy_bull * 0.15
        + (seller_score * 0.60 + inst_bull * 0.40) * 0.15
    )
    condor = (
        core.range_score * 0.35
        + options.range_score * 0.35
        + heavy_range * 0.15
        + (seller_score * 0.65 + inst_range * 0.35) * 0.15
    )

    ce_adjust, pe_adjust, condor_adjust, level_cautions = _level_adjustments(levels)
    ce += ce_adjust
    pe += pe_adjust
    condor += condor_adjust

    if core.move_stage in {"MATURE", "EXHAUSTION", "SHORT-TERM EXHAUSTION RISK"}:
        ce -= 6
        pe -= 6
    if options.persistence in {"WARMING UP", "UNAVAILABLE"}:
        ce -= 5
        pe -= 5
        condor -= 5

    event_wait, event_blocker = _event_adjustment(event_risk)
    if event_risk.level == "HIGH":
        ce -= 12
        pe -= 12
        condor -= 20
    elif event_risk.level == "MEDIUM":
        ce -= 5
        pe -= 5
        condor -= 10

    ce = round(clamp(ce, 0, 100), 1)
    pe = round(clamp(pe, 0, 100), 1)
    condor = round(clamp(condor, 0, 100), 1)

    wait = 10.0
    blockers: list[str] = []
    if not market_session.is_live:
        wait = 100.0
        blockers.append("Market session is reference-only")
    else:
        if not quote_live:
            wait += 30
            blockers.append("NIFTY quote is not confirmed live")
        if not candles_live:
            wait += 25
            blockers.append("Completed candle feed is not confirmed live")
        if not option_chain_live or options.status == "UNAVAILABLE":
            wait += 35
            blockers.append("Option chain is unavailable")
        if options.confidence < CONFIG.decision_min_option_confidence:
            wait += 18
            blockers.append("Option-flow continuity is still warming up")
        if core.confidence < CONFIG.decision_min_core_confidence:
            wait += 12
            blockers.append("Core market confidence is low")
        ready_windows = sum(item.status == "READY" for item in options.windows)
        if ready_windows < 2:
            wait += 12
            blockers.append("Fewer than two option movement windows are ready")
        if options.persistence == "WARMING UP":
            wait += 10
        direction_gap = abs(pe - ce)
        if direction_gap < CONFIG.decision_minimum_margin and condor < 62:
            wait += 12
            blockers.append("Directional edge is not separated")
        if vix.status != "READY":
            wait += 6
            blockers.append("India VIX data is unavailable")
        elif vix.movement == "RISING FAST" or vix.regime == "HIGH":
            wait += 14
            blockers.append("VIX risk is elevated")
        if inst_caution:
            wait += 4
        wait += event_wait
        if event_blocker:
            blockers.append(event_blocker)

    wait = round(clamp(wait, 0, 100), 1)

    ce_cautions = list(level_cautions)
    pe_cautions = list(level_cautions)
    condor_cautions = list(level_cautions)
    if options.confidence < CONFIG.decision_min_option_confidence:
        warning = "Option-flow continuity is not mature"
        ce_cautions.append(warning)
        pe_cautions.append(warning)
        condor_cautions.append(warning)
    if vix.status != "READY":
        vix_warning = "India VIX data is unavailable"
        ce_cautions.append(vix_warning)
        pe_cautions.append(vix_warning)
        condor_cautions.append(vix_warning)
    if event_blocker:
        ce_cautions.append(event_blocker)
        pe_cautions.append(event_blocker)
        condor_cautions.append(event_blocker)

    ce_eval = StrategyEvaluation(
        name="CE SELL",
        score=ce,
        status=_status(ce, ce_cautions),
        reasons=_top_reasons(
            (f"Core bearish evidence {core.bearish_score:.1f}/100",),
            (f"Bearish option flow {options.bearish_score:.1f}%",),
            (f"Top-7 state: {heavyweights.state}",),
        ),
        cautions=tuple(dict.fromkeys(ce_cautions))[:3],
    )
    pe_eval = StrategyEvaluation(
        name="PE SELL",
        score=pe,
        status=_status(pe, pe_cautions),
        reasons=_top_reasons(
            (f"Core bullish evidence {core.bullish_score:.1f}/100",),
            (f"Bullish option flow {options.bullish_score:.1f}%",),
            (f"Top-7 state: {heavyweights.state}",),
        ),
        cautions=tuple(dict.fromkeys(pe_cautions))[:3],
    )
    condor_eval = StrategyEvaluation(
        name="IRON CONDOR",
        score=condor,
        status=_status(condor, condor_cautions),
        reasons=_top_reasons(
            (f"Core range/mixed evidence {core.range_score:.1f}/100",),
            (f"Option decay/mixed evidence {options.range_score:.1f}%",),
            (f"VIX environment: {vix.seller_environment}",),
        ),
        cautions=tuple(dict.fromkeys(condor_cautions))[:3],
    )
    wait_eval = StrategyEvaluation(
        name="WAIT NEED",
        score=wait,
        status=(
            "MANDATORY"
            if wait >= CONFIG.decision_wait_block_threshold
            else "CAUTION"
            if wait >= 35
            else "LOW"
        ),
        reasons=tuple(dict.fromkeys(blockers))[:3],
        cautions=(),
    )

    candidates = sorted(
        (("CE SELL", ce), ("PE SELL", pe), ("IRON CONDOR", condor)),
        key=lambda item: item[1],
        reverse=True,
    )
    leader, leader_score = candidates[0]
    runner_up = candidates[1][1]
    if wait >= CONFIG.decision_wait_block_threshold:
        final_action = "WAIT"
    elif (
        leader_score < CONFIG.decision_minimum_score
        or leader_score - runner_up < CONFIG.decision_minimum_margin
    ):
        final_action = "WAIT"
        blockers.append("No strategy meets score and separation thresholds")
        wait = max(wait, 50.0)
        wait_eval = StrategyEvaluation(
            name="WAIT NEED",
            score=round(wait, 1),
            status="CAUTION",
            reasons=tuple(dict.fromkeys(blockers))[:3],
            cautions=(),
        )
    else:
        final_action = f"{leader} WITH HEDGE"

    confidence_inputs = [core.confidence, options.confidence, heavyweights.confidence]
    if institutional.confidence > 0:
        confidence_inputs.append(institutional.confidence)
    confidence = sum(confidence_inputs) / len(confidence_inputs)
    confidence *= max(0.35, 1.0 - wait_eval.score / 160.0)
    confidence = round(clamp(confidence, 0, 95), 1)

    evaluation_map = {
        "CE SELL": ce_eval,
        "PE SELL": pe_eval,
        "IRON CONDOR": condor_eval,
    }
    chosen = evaluation_map.get(leader)
    final_reasons = (
        chosen.reasons if final_action != "WAIT" and chosen else wait_eval.reasons
    )
    blocker = (tuple(dict.fromkeys(blockers)) or ("None",))[0]
    execution_status = "READY" if final_action != "WAIT" else "BLOCKED"
    status = "REFERENCE ONLY" if not market_session.is_live else "READY"

    return FinalDecision(
        ce_sell=ce_eval,
        pe_sell=pe_eval,
        iron_condor=condor_eval,
        wait_need=wait_eval,
        final_action=final_action,
        execution_status=execution_status,
        decision_confidence=confidence,
        hedge_required=True,
        reasons=final_reasons[:3],
        blocker=blocker,
        status=status,
    )
