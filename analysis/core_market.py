from __future__ import annotations

from analysis.technical_utils import clamp
from models import (
    CoreMarketEvidence,
    IndicatorBundle,
    LevelBundle,
    MarketSession,
    PriceActionBundle,
    VolumeBundle,
)


def _indicator_scores(
    indicators: IndicatorBundle,
) -> tuple[float, float, float, list[str]]:
    bullish = 0.0
    bearish = 0.0
    range_score = 0.0
    reasons: list[str] = []
    for item, weight in (
        (indicators.fifteen_minute, 0.60),
        (indicators.three_minute, 0.40),
    ):
        if item.status != "READY":
            range_score += 45 * weight
            continue
        if "BULLISH" in item.ema_state:
            bullish += 34 * weight
        elif "BEARISH" in item.ema_state:
            bearish += 34 * weight
        else:
            range_score += 25 * weight
        if "BULLISH" in item.macd_state:
            bullish += 32 * weight
        elif "BEARISH" in item.macd_state:
            bearish += 32 * weight
        else:
            range_score += 24 * weight
        if item.rsi14 is not None:
            if item.rsi14 >= 55:
                bullish += 24 * weight
            elif item.rsi14 <= 45:
                bearish += 24 * weight
            else:
                range_score += 26 * weight
            if item.rsi14 >= 70 or item.rsi14 <= 30:
                range_score += 8 * weight
    if indicators.fifteen_minute.status == "READY":
        reasons.append(
            f"15m indicators: {indicators.fifteen_minute.ema_state}, {indicators.fifteen_minute.macd_state}"
        )
    if indicators.three_minute.status == "READY":
        reasons.append(
            f"3m indicators: {indicators.three_minute.ema_state}, {indicators.three_minute.macd_state}"
        )
    return (
        clamp(bullish, 0, 100),
        clamp(bearish, 0, 100),
        clamp(range_score, 0, 100),
        reasons,
    )


def _volume_scores(volume: VolumeBundle) -> tuple[float, float, float, list[str]]:
    if volume.status == "UNAVAILABLE":
        return 35.0, 35.0, 55.0, ["NIFTY futures volume unavailable"]
    view = volume.overall_view
    if "BULLISH" in view:
        return 82.0, 18.0, 25.0, [view]
    if "BEARISH" in view:
        return 18.0, 82.0, 25.0, [view]
    if "WEAK" in view:
        return 35.0, 35.0, 72.0, [view]
    if "BUILD-UP" in view:
        return 45.0, 45.0, 68.0, [view]
    return 42.0, 42.0, 58.0, [view]


def calculate_core_market_evidence(
    price_action: PriceActionBundle,
    indicators: IndicatorBundle,
    levels: LevelBundle,
    volume: VolumeBundle,
    market_session: MarketSession,
    *,
    future_volume_live: bool = True,
) -> CoreMarketEvidence:
    pa3 = price_action.three_minute
    pa15 = price_action.fifteen_minute
    pa_ready = pa3.status == "READY" and pa15.status == "READY"
    pa_bull = (
        (pa15.bullish_score * 0.65 + pa3.bullish_score * 0.35) if pa_ready else 40.0
    )
    pa_bear = (
        (pa15.bearish_score * 0.65 + pa3.bearish_score * 0.35) if pa_ready else 40.0
    )
    pa_range = (pa15.range_score * 0.65 + pa3.range_score * 0.35) if pa_ready else 60.0

    ind_bull, ind_bear, ind_range, ind_reasons = _indicator_scores(indicators)
    if market_session.is_live and not future_volume_live:
        vol_bull, vol_bear, vol_range, vol_reasons = (
            0.0,
            0.0,
            100.0,
            ["NIFTY futures volume is not confirmed live"],
        )
    else:
        vol_bull, vol_bear, vol_range, vol_reasons = _volume_scores(volume)

    # Core engine weights are internal evidence weights, not final strategy weights.
    bullish = pa_bull * 0.45 + ind_bull * 0.35 + vol_bull * 0.20
    bearish = pa_bear * 0.45 + ind_bear * 0.35 + vol_bear * 0.20
    range_score = pa_range * 0.45 + ind_range * 0.35 + vol_range * 0.20

    reasons: list[str] = [price_action.combined_state]
    reasons.extend(ind_reasons[:1])
    reasons.extend(vol_reasons[:1])
    blockers: list[str] = []

    if levels.status == "READY":
        if levels.current_position == "NEAR RESISTANCE":
            bullish -= 8
            range_score += 8
            blockers.append("Immediate resistance is close")
        if levels.current_position == "NEAR SUPPORT":
            bearish -= 8
            range_score += 8
            blockers.append("Immediate support is close")
        if levels.upside_room is not None and levels.upside_room < 10:
            bullish -= 5
        if levels.downside_room is not None and levels.downside_room < 10:
            bearish -= 5
    else:
        blockers.append("Support/resistance evidence unavailable")

    if market_session.is_live and not future_volume_live:
        blockers.append("NIFTY futures volume is not confirmed live")

    if price_action.relationship in {"TIMEFRAMES MIXED", "TREND / RANGE CONFLICT"}:
        range_score += 10
        blockers.append("3m and 15m price action conflict")
    if not market_session.is_live:
        blockers.append("Reference-only market session")

    bullish = clamp(bullish, 0, 100)
    bearish = clamp(bearish, 0, 100)
    range_score = clamp(range_score, 0, 100)
    ordered = sorted(
        [("BULLISH", bullish), ("BEARISH", bearish), ("RANGE / MIXED", range_score)],
        key=lambda item: item[1],
        reverse=True,
    )
    leader, leader_score = ordered[0]
    margin = leader_score - ordered[1][1]
    if margin < 8:
        state = "MIXED / NO CLEAR CORE EDGE"
    else:
        state = leader

    component_confidences = [price_action.confidence]
    if volume.confidence:
        component_confidences.append(volume.confidence)
    indicator_ready_count = sum(
        item.status == "READY"
        for item in (indicators.three_minute, indicators.fifteen_minute)
    )
    component_confidences.append(55 + indicator_ready_count * 15)
    confidence = sum(component_confidences) / len(component_confidences)
    if blockers:
        confidence -= min(18, len(blockers) * 4)
    if not market_session.is_live:
        status = "REFERENCE ONLY"
        confidence = min(confidence, 65)
    elif not pa_ready or levels.status != "READY":
        status = "PARTIAL"
    else:
        status = "READY"

    move_stage = price_action.fifteen_minute.move_stage
    if price_action.three_minute.move_stage.startswith("EXHAUSTION"):
        move_stage = "SHORT-TERM EXHAUSTION RISK"

    return CoreMarketEvidence(
        bullish_score=round(bullish, 1),
        bearish_score=round(bearish, 1),
        range_score=round(range_score, 1),
        confidence=round(clamp(confidence, 0, 95), 1),
        market_state=state,
        move_stage=move_stage,
        status=status,
        reasons=tuple(dict.fromkeys(reasons))[:3],
        blockers=tuple(dict.fromkeys(blockers))[:3],
    )
