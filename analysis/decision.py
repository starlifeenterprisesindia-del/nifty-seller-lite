from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from analysis.technical_utils import clamp
from config import CONFIG
from models import (
    CoreMarketEvidence,
    EventRiskContext,
    FinalDecision,
    HeavyweightBundle,
    InstitutionalContext,
    LevelBundle,
    MarketOutlook,
    MarketSession,
    OptionIntelligence,
    PriceActionBundle,
    StrategyEvaluation,
    VixContext,
    VolumeBundle,
)


_DIRECTION_FROM_ACTION = {
    "PE SELL": "BULLISH",
    "PE SELL WITH HEDGE": "BULLISH",
    "CE SELL": "BEARISH",
    "CE SELL WITH HEDGE": "BEARISH",
    "IRON CONDOR": "RANGE",
    "IRON CONDOR WITH HEDGE": "RANGE",
}


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


def _level_adjustments(
    levels: LevelBundle,
) -> tuple[
    float,
    float,
    float,
    list[str],
    list[str],
    list[str],
]:
    ce_adjust = pe_adjust = condor_adjust = 0.0
    ce_cautions: list[str] = []
    pe_cautions: list[str] = []
    condor_cautions: list[str] = []
    if levels.status != "READY":
        common = ["Support/resistance evidence unavailable"]
        return -8.0, -8.0, -12.0, common.copy(), common.copy(), common.copy()

    if levels.downside_room is not None:
        if levels.downside_room < 10:
            ce_adjust -= 18
            ce_cautions.append("CE sell has limited downside room before support")
        elif levels.downside_room >= 25:
            ce_adjust += 7
    if levels.upside_room is not None:
        if levels.upside_room < 10:
            pe_adjust -= 18
            pe_cautions.append("PE sell has limited upside room before resistance")
        elif levels.upside_room >= 25:
            pe_adjust += 7

    if levels.current_position == "NEAR SUPPORT":
        ce_adjust -= 10
        ce_cautions.append("Current price is near support")
    elif levels.current_position == "NEAR RESISTANCE":
        pe_adjust -= 10
        pe_cautions.append("Current price is near resistance")

    rooms = [
        value
        for value in (levels.upside_room, levels.downside_room)
        if value is not None
    ]
    if len(rooms) == 2 and min(rooms) >= 18:
        condor_adjust += 8
    elif len(rooms) < 2 or min(rooms) < 10:
        condor_adjust -= 16
        condor_cautions.append("Iron Condor does not have balanced room on both sides")
    return (
        ce_adjust,
        pe_adjust,
        condor_adjust,
        ce_cautions,
        pe_cautions,
        condor_cautions,
    )


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


def _direction_from_action(action: str) -> str | None:
    return _DIRECTION_FROM_ACTION.get(str(action or "").upper())


def _direction_from_scores(
    ce: float, pe: float, condor: float
) -> tuple[str, float, float]:
    ranked = sorted(
        (("BEARISH", ce), ("BULLISH", pe), ("RANGE", condor)),
        key=lambda item: item[1],
        reverse=True,
    )
    return ranked[0][0], ranked[0][1], ranked[1][1]


def _normalized_triplet(
    bullish: float, bearish: float, range_score: float
) -> tuple[float, float, float]:
    values = [max(0.0, bullish), max(0.0, bearish), max(0.0, range_score)]
    total = sum(values)
    if total <= 0:
        return 33.4, 33.3, 33.3
    raw = [value * 100.0 / total for value in values]
    rounded = [round(value, 1) for value in raw]
    rounded[-1] = round(100.0 - rounded[0] - rounded[1], 1)
    return rounded[0], rounded[1], rounded[2]


def _valid_history(
    signal_history: Iterable[dict[str, Any]],
    as_of: datetime | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in signal_history:
        if not isinstance(raw, dict):
            continue
        direction = str(raw.get("market_direction") or "").upper()
        if direction not in {"BULLISH", "BEARISH", "RANGE"}:
            direction = _direction_from_action(str(raw.get("action") or "")) or ""
        if direction not in {"BULLISH", "BEARISH", "RANGE"}:
            continue
        captured: datetime | None = None
        try:
            captured = datetime.fromisoformat(str(raw.get("captured_at")))
        except Exception:
            captured = None
        if as_of is not None and captured is not None:
            if captured.tzinfo is None and as_of.tzinfo is not None:
                captured = captured.replace(tzinfo=as_of.tzinfo)
            age = (as_of - captured).total_seconds()
            if age < 0 or age > CONFIG.decision_memory_max_age_seconds:
                continue
            if captured.date() != as_of.date():
                continue
        clean = dict(raw)
        clean["market_direction"] = direction
        clean["_captured_at"] = captured
        clean["_sort_timestamp"] = captured.timestamp() if captured is not None else 0.0
        rows.append(clean)
    rows.sort(key=lambda item: float(item.get("_sort_timestamp") or 0.0))
    return rows[-CONFIG.decision_memory_lookback :]


def _history_distribution(
    rows: list[dict[str, Any]],
) -> tuple[float, float, float] | None:
    if not rows:
        return None
    bull = bear = range_score = weight_total = 0.0
    for index, row in enumerate(rows, start=1):
        weight = float(index)
        try:
            pe = float(row.get("pe_score"))
            ce = float(row.get("ce_score"))
            condor = float(row.get("condor_score"))
            b, d, r = _normalized_triplet(pe, ce, condor)
        except (TypeError, ValueError):
            direction = row["market_direction"]
            b, d, r = (
                (100.0, 0.0, 0.0)
                if direction == "BULLISH"
                else (0.0, 100.0, 0.0)
                if direction == "BEARISH"
                else (0.0, 0.0, 100.0)
            )
        bull += b * weight
        bear += d * weight
        range_score += r * weight
        weight_total += weight
    return _normalized_triplet(
        bull / weight_total,
        bear / weight_total,
        range_score / weight_total,
    )


def _directional_label(value: str) -> str | None:
    upper = str(value or "").upper()
    if "BULL" in upper:
        return "BULLISH"
    if "BEAR" in upper:
        return "BEARISH"
    if "RANGE" in upper or "MIXED" in upper or "FLAT" in upper:
        return "RANGE"
    return None


def _fake_move_risk(
    *,
    direction: str,
    core: CoreMarketEvidence,
    options: OptionIntelligence,
    heavyweights: HeavyweightBundle,
    vix: VixContext,
    levels: LevelBundle,
    event_risk: EventRiskContext,
    market_session: MarketSession,
    price_action: PriceActionBundle | None,
    volume: VolumeBundle | None,
    history: list[dict[str, Any]],
    score_gap: float,
) -> tuple[float, tuple[str, ...]]:
    if not market_session.is_live:
        return 100.0, ("Market session is reference-only",)

    risk = 5.0
    reasons: list[str] = []

    if price_action is not None:
        relationship = price_action.relationship.upper()
        if "MIXED" in relationship or "CONFLICT" in relationship:
            risk += 20
            reasons.append("3m and 15m price action conflict")
        pa_direction = _directional_label(price_action.combined_state)
        if pa_direction and pa_direction not in {direction, "RANGE"}:
            risk += 15
            reasons.append("Price-action direction opposes the current score leader")

    core_direction = _directional_label(core.market_state)
    option_direction = _directional_label(options.market_bias)
    if core_direction and option_direction and core_direction != option_direction:
        risk += 22
        reasons.append("Core market and option flow disagree")
    if core_direction and core_direction not in {direction, "RANGE"}:
        risk += 10
    if option_direction and option_direction not in {direction, "RANGE"}:
        risk += 14

    heavy_direction = _directional_label(heavyweights.state)
    if heavy_direction == "RANGE":
        risk += 12
        reasons.append("Top-7 heavyweights are mixed or flat")
    elif heavy_direction and heavy_direction != direction and direction != "RANGE":
        risk += 18
        reasons.append("Top-7 heavyweights do not confirm the move")

    if volume is None or volume.status != "READY":
        risk += 10
        reasons.append("Futures-volume confirmation is unavailable")
    else:
        volume_view = volume.overall_view.upper()
        if direction == "BULLISH" and "BEARISH" in volume_view:
            risk += 18
            reasons.append("Futures volume opposes the bullish move")
        elif direction == "BEARISH" and "BULLISH" in volume_view:
            risk += 18
            reasons.append("Futures volume opposes the bearish move")
        elif (
            "LOW" in volume_view
            or "WEAK" in volume_view
            or "NOT CONFIRMED" in volume_view
        ):
            risk += 12
            reasons.append("Movement has weak volume participation")

    if direction == "BULLISH" and levels.current_position == "NEAR RESISTANCE":
        risk += 13
        reasons.append("Bullish move is testing nearby resistance")
    elif direction == "BEARISH" and levels.current_position == "NEAR SUPPORT":
        risk += 13
        reasons.append("Bearish move is testing nearby support")

    ready_windows = sum(window.status == "READY" for window in options.windows)
    if ready_windows < 2:
        risk += 15
        reasons.append("Option movement windows are not mature")
    if options.persistence in {"WARMING UP", "UNAVAILABLE"}:
        risk += 12
        reasons.append("Option-flow persistence is warming up")
    if options.confidence < CONFIG.decision_min_option_confidence:
        risk += 10

    if score_gap < CONFIG.decision_minimum_margin:
        risk += 12
        reasons.append("Directional score separation is small")

    if history and history[-1]["market_direction"] != direction:
        risk += 20
        reasons.append("Latest direction flipped against recent memory")

    if vix.status != "READY":
        risk += 8
        reasons.append("India VIX confirmation is unavailable")
    elif vix.movement == "RISING FAST" or vix.regime == "HIGH":
        risk += 12
        reasons.append("VIX risk is elevated")

    if event_risk.level == "HIGH":
        risk += 30
        reasons.append("High-impact event risk can create false movement")
    elif event_risk.level == "MEDIUM":
        risk += 15
        reasons.append("Event risk can disturb short-term movement")

    return round(clamp(risk, 0, 100), 1), tuple(dict.fromkeys(reasons))[:3]


def _memory_confirmation(
    *,
    direction: str,
    history: list[dict[str, Any]],
    score_gap: float,
    fake_move_risk: float,
    core: CoreMarketEvidence,
    options: OptionIntelligence,
    market_session: MarketSession,
) -> tuple[str, str, bool]:
    directions = [row["market_direction"] for row in history]
    recent = (directions + [direction])[-CONFIG.decision_memory_lookback :]
    memory_count = recent.count(direction)
    memory_text = f"{memory_count}/{len(recent)} {direction}"
    if len(recent) < CONFIG.decision_confirmation_snapshots:
        memory_text += " — WARMING UP"

    if not market_session.is_live:
        return "REFERENCE ONLY", memory_text, False

    consecutive = 1
    for previous in reversed(directions):
        if previous != direction:
            break
        consecutive += 1

    previous_confirmed: str | None = None
    for row in reversed(history):
        state = str(row.get("signal_state") or "").upper()
        row_direction = row["market_direction"]
        if "CONFIRMED" in state:
            previous_confirmed = row_direction
            break
    if (
        previous_confirmed is None
        and len(directions) >= 2
        and directions[-1] == directions[-2]
    ):
        previous_confirmed = directions[-1]

    core_direction = _directional_label(core.market_state)
    option_direction = _directional_label(options.market_bias)
    emergency = (
        direction in {"BULLISH", "BEARISH"}
        and score_gap >= CONFIG.decision_emergency_flip_margin
        and core_direction == direction
        and option_direction == direction
        and core.confidence >= 70
        and options.confidence >= 70
        and fake_move_risk < CONFIG.fake_move_medium_threshold
    )

    prior_direction = directions[-1] if directions else previous_confirmed
    if prior_direction and prior_direction != direction:
        if emergency:
            return f"{direction} CONFIRMED — RAPID REVERSAL", memory_text, True
        if (
            consecutive >= CONFIG.decision_flip_confirmations
            and score_gap >= CONFIG.decision_flip_margin
            and fake_move_risk < CONFIG.fake_move_high_threshold
        ):
            return f"{direction} CONFIRMED", memory_text, True
        return "TRANSITION / WAIT", memory_text, False

    if (
        consecutive >= CONFIG.decision_confirmation_snapshots
        and score_gap >= CONFIG.decision_minimum_margin
        and fake_move_risk < CONFIG.fake_move_high_threshold
    ):
        return f"{direction} CONFIRMED", memory_text, True

    return f"{direction} DEVELOPING", memory_text, False


def _invalidation_text(
    *,
    direction: str,
    current_price: float | None,
    price_action: PriceActionBundle | None,
    levels: LevelBundle,
) -> tuple[float | None, float | None, str]:
    support = levels.immediate_support.midpoint if levels.immediate_support else None
    resistance = (
        levels.immediate_resistance.midpoint if levels.immediate_resistance else None
    )
    pa_level = (
        price_action.three_minute.invalidation_level
        if price_action is not None
        else None
    )

    if direction == "BULLISH":
        candidates = [
            value
            for value in (pa_level, support, levels.opening_range_low)
            if value is not None and (current_price is None or value < current_price)
        ]
        low = max(candidates) if candidates else None
        return low, None, f"Below {low:,.2f}" if low is not None else "Unavailable"
    if direction == "BEARISH":
        candidates = [
            value
            for value in (pa_level, resistance, levels.opening_range_high)
            if value is not None and (current_price is None or value > current_price)
        ]
        high = min(candidates) if candidates else None
        return None, high, f"Above {high:,.2f}" if high is not None else "Unavailable"

    low = support or levels.opening_range_low
    high = resistance or levels.opening_range_high
    if low is not None and high is not None:
        return low, high, f"Outside {low:,.2f}–{high:,.2f}"
    return low, high, "Range boundary unavailable"


def _build_outlook(
    *,
    ce: float,
    pe: float,
    condor: float,
    direction: str,
    fake_move_risk: float,
    fake_reasons: tuple[str, ...],
    history: list[dict[str, Any]],
    signal_state: str,
    memory_text: str,
    market_session: MarketSession,
    current_price: float | None,
    price_action: PriceActionBundle | None,
    levels: LevelBundle,
) -> MarketOutlook:
    current_bull, current_bear, current_range = _normalized_triplet(pe, ce, condor)
    historical = _history_distribution(history)
    if historical is None:
        bull, bear, range_score = current_bull, current_bear, current_range
    else:
        hb, hbear, hrange = historical
        current_weight = CONFIG.outlook_current_weight
        memory_weight = 1.0 - current_weight
        bull = current_bull * current_weight + hb * memory_weight
        bear = current_bear * current_weight + hbear * memory_weight
        range_score = current_range * current_weight + hrange * memory_weight

    # High fake-move risk shifts directional conviction toward a transition/range path.
    shift = min(18.0, fake_move_risk * 0.18)
    directional_total = bull + bear
    if directional_total > 0:
        bull -= shift * bull / directional_total
        bear -= shift * bear / directional_total
        range_score += shift
    bull, bear, range_score = _normalized_triplet(bull, bear, range_score)

    invalidation_low, invalidation_high, invalidation_text = _invalidation_text(
        direction=direction,
        current_price=current_price,
        price_action=price_action,
        levels=levels,
    )
    fake_state = (
        "HIGH"
        if fake_move_risk >= CONFIG.fake_move_high_threshold
        else "MEDIUM"
        if fake_move_risk >= CONFIG.fake_move_medium_threshold
        else "LOW"
    )
    status = "REFERENCE ONLY" if not market_session.is_live else "READY"
    return MarketOutlook(
        bullish_path_pct=bull,
        range_path_pct=range_score,
        bearish_path_pct=bear,
        fake_move_risk=fake_move_risk,
        fake_move_state=fake_state,
        signal_state=signal_state,
        signal_memory=memory_text,
        invalidation_low=invalidation_low,
        invalidation_high=invalidation_high,
        invalidation_text=invalidation_text,
        reasons=fake_reasons,
        status=status,
    )


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
    price_action: PriceActionBundle | None = None,
    volume: VolumeBundle | None = None,
    signal_history: tuple[dict[str, Any], ...] = (),
    as_of: datetime | None = None,
    current_price: float | None = None,
) -> FinalDecision:
    """One canonical read-only strategy brain with bounded market memory.

    The same function calculates current strategy suitability, anti-flip confirmation,
    fake-move risk and the conditional 5–15 minute outlook. No downstream module may
    choose or override a strategy.
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

    (
        ce_adjust,
        pe_adjust,
        condor_adjust,
        ce_level_cautions,
        pe_level_cautions,
        condor_level_cautions,
    ) = _level_adjustments(levels)
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

    ce_cautions = list(ce_level_cautions)
    pe_cautions = list(pe_level_cautions)
    condor_cautions = list(condor_level_cautions)
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

    candidates = sorted(
        (("CE SELL", ce), ("PE SELL", pe), ("IRON CONDOR", condor)),
        key=lambda item: item[1],
        reverse=True,
    )
    leader, leader_score = candidates[0]
    runner_up = candidates[1][1]
    direction, _, _ = _direction_from_scores(ce, pe, condor)
    score_gap = max(0.0, leader_score - runner_up)

    if wait >= CONFIG.decision_wait_block_threshold:
        instant_action = "WAIT"
    elif (
        leader_score < CONFIG.decision_minimum_score
        or score_gap < CONFIG.decision_minimum_margin
    ):
        instant_action = "WAIT"
        blockers.append("No strategy meets score and separation thresholds")
        wait = max(wait, 50.0)
    else:
        instant_action = f"{leader} WITH HEDGE"

    history = _valid_history(signal_history, as_of)
    fake_move_risk, fake_reasons = _fake_move_risk(
        direction=direction,
        core=core,
        options=options,
        heavyweights=heavyweights,
        vix=vix,
        levels=levels,
        event_risk=event_risk,
        market_session=market_session,
        price_action=price_action,
        volume=volume,
        history=history,
        score_gap=score_gap,
    )
    signal_state, memory_text, confirmed = _memory_confirmation(
        direction=direction,
        history=history,
        score_gap=score_gap,
        fake_move_risk=fake_move_risk,
        core=core,
        options=options,
        market_session=market_session,
    )

    final_action = instant_action
    if instant_action != "WAIT":
        if signal_state == "TRANSITION / WAIT":
            final_action = "WAIT"
            wait = max(wait, CONFIG.decision_stability_wait_floor)
            blockers.append(
                "Opposite movement is not persistent; anti-flip filter is holding WAIT"
            )
        elif fake_move_risk >= CONFIG.fake_move_high_threshold:
            final_action = "WAIT"
            wait = max(wait, CONFIG.decision_stability_wait_floor)
            blockers.append("Fake-move risk is high")

    wait = round(clamp(wait, 0, 100), 1)
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

    confidence_inputs = [core.confidence, options.confidence, heavyweights.confidence]
    if institutional.confidence > 0:
        confidence_inputs.append(institutional.confidence)
    confidence = sum(confidence_inputs) / len(confidence_inputs)
    confidence *= max(0.35, 1.0 - wait_eval.score / 160.0)
    confidence *= max(0.45, 1.0 - fake_move_risk / 180.0)
    confidence = round(clamp(confidence, 0, 95), 1)

    outlook = _build_outlook(
        ce=ce,
        pe=pe,
        condor=condor,
        direction=direction,
        fake_move_risk=fake_move_risk,
        fake_reasons=fake_reasons,
        history=history,
        signal_state=signal_state,
        memory_text=memory_text,
        market_session=market_session,
        current_price=current_price,
        price_action=price_action,
        levels=levels,
    )

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
        instant_action=instant_action,
        final_action=final_action,
        signal_state=signal_state,
        market_direction=direction,
        execution_status=execution_status,
        decision_confidence=confidence,
        hedge_required=True,
        reasons=final_reasons[:3],
        blocker=blocker,
        outlook=outlook,
        status=status,
    )
