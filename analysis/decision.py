from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from analysis.technical_utils import clamp
from analysis.activity_gate import activity_gate, confirmed_activity
from config import CONFIG
from models import (
    BigPlayerActivity,
    CoreMarketEvidence,
    EventRiskContext,
    FinalDecision,
    HeavyweightBundle,
    InstitutionalContext,
    LevelBundle,
    MarketOutlook,
    MarketSession,
    NewsContext,
    OptionIntelligence,
    PatternEvidenceBundle,
    PriceActionBundle,
    StrategyEvaluation,
    VixContext,
    VolumeBundle,
)


_DIRECTION_FROM_ACTION = {
    "CE BUY": "BULLISH",
    "PE SELL": "BULLISH",
    "PE SELL WITH HEDGE": "BULLISH",
    "PE BUY": "BEARISH",
    "CE SELL": "BEARISH",
    "CE SELL WITH HEDGE": "BEARISH",
    "IRON CONDOR": "RANGE",
    "IRON CONDOR WITH HEDGE": "RANGE",
}


def _heavyweight_scores(bundle: HeavyweightBundle) -> tuple[float, float, float]:
    if bundle.status not in {"READY", "CAUTION"}:
        return 0.0, 0.0, 0.0
    move = getattr(bundle, "recent_15m_move_pct", None)
    if move is None:
        return 0.0, 0.0, 0.0
    coverage = min(1.0, bundle.recent_coverage_pct / max(bundle.covered_weight_pct, 0.01))
    strength = min(95.0, abs(move) * 250 + 40)
    if abs(move) <= 0.03:
        return 10 * coverage, 10 * coverage, 70 * coverage
    return (strength * coverage, 5 * coverage, 0.0) if move > 0 else (5 * coverage, strength * coverage, 0.0)


def _institutional_scores(
    context: InstitutionalContext,
) -> tuple[float, float, float, str | None]:
    state = context.state.upper()
    if context.status == "MISSING":
        return 50.0, 50.0, 55.0, "FII/DII background is missing"

    # Cash stays primary; futures Long/Short positioning is secondary confirmation.
    if "SUPPORT" in state or "FII BUYING" in state:
        bull, bear, neutral = 68.0, 32.0, 42.0
    elif "PRESSURE" in state or "FII SELLING" in state:
        bull, bear, neutral = 32.0, 68.0, 42.0
    else:
        bull, bear, neutral = 48.0, 48.0, 60.0

    long_pct = context.latest_fii_futures_long_pct
    short_pct = context.latest_fii_futures_short_pct
    if long_pct is not None or short_pct is not None:
        long_value = float(long_pct if long_pct is not None else 100.0 - float(short_pct or 0.0))
        short_value = float(short_pct if short_pct is not None else 100.0 - float(long_pct or 0.0))
        # Max 15-point secondary adjustment even in extreme positioning.
        adjustment = min(15.0, abs(long_value - short_value) * 0.18)
        if long_value > short_value:
            bull += adjustment
            bear -= adjustment
        elif short_value > long_value:
            bear += adjustment
            bull -= adjustment
        neutral = max(30.0, neutral - adjustment * 0.5)
    elif "FUTURES LONG" in state:
        bull += 8.0
        bear -= 8.0
    elif "FUTURES SHORT" in state:
        bear += 8.0
        bull -= 8.0

    return clamp(bull, 5.0, 95.0), clamp(bear, 5.0, 95.0), clamp(neutral, 20.0, 80.0), None


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




def _buyer_environment_score(vix: VixContext) -> float:
    """Score whether option premium conditions are reasonable for directional buying."""
    if vix.status != "READY":
        return 42.0
    if vix.movement == "RISING FAST" or vix.regime == "HIGH":
        return 32.0
    if vix.regime == "ELEVATED":
        return 48.0
    if vix.regime == "NORMAL":
        return 72.0
    if vix.regime == "LOW":
        return 82.0
    return 55.0


def _futures_activity_scores(
    activity: BigPlayerActivity | None,
) -> tuple[float, float, float]:
    """Return the *raw futures setup* vote without reusing composite BP score.

    BigPlayer score also contains options and Top-9.  Using that composite here
    would double-count those modules, so only its futures price/OI classification
    is allowed into the canonical base score.
    """
    setup = str(getattr(activity, "futures_setup", "") or "").upper()
    if activity is None or activity.status != "READY" or getattr(activity, "futures_oi_change_pct", None) is None:
        return 0.0, 0.0, 0.0
    move = abs(float(activity.futures_price_change_points or 0.0))
    strength = min(95.0, 25 + move / max(activity.required_move_points, 1) * 20 + abs(activity.futures_oi_change_pct) * 60)
    if setup in {"SHORT COVERING", "LONG UNWINDING"}:
        strength *= 0.75
    if setup in {"LONG BUILD-UP", "SHORT COVERING"}:
        return strength, 0.0, 0.0
    if setup in {"SHORT BUILD-UP", "LONG UNWINDING"}:
        return 0.0, strength, 0.0
    return 0.0, 0.0, 0.0


def _volume_scores(volume: VolumeBundle | None) -> tuple[float, float, float]:
    """Directional participation only; OI and price structure are not reused."""

    if volume is None or volume.status not in {"READY", "PARTIAL"}:
        return 0.0, 0.0, 0.0
    bull = bear = neutral = 0.0
    ready = 0
    for item in (volume.three_minute, volume.fifteen_minute):
        if item.status != "READY":
            continue
        ready += 1
        text = f"{item.move_support} {item.price_direction}".upper()
        strength = clamp(float(item.confidence), 0.0, 100.0)
        if "BULLISH" in text and "BEARISH" not in text:
            bull += strength
        elif "BEARISH" in text and "BULLISH" not in text:
            bear += strength
        else:
            neutral += strength
    if not ready:
        return 0.0, 0.0, 0.0
    return bull / ready, bear / ready, neutral / ready


def _pattern_scores(
    patterns: PatternEvidenceBundle | None,
) -> tuple[float, float, float]:
    """Completed special candle/W-M confirmation as one bounded module."""

    if patterns is None or patterns.status != "READY":
        return 0.0, 0.0, 0.0
    bull = bear = 0.0
    for item in (
        patterns.wm_3m,
        patterns.candle_3m,
        getattr(patterns, "candle_5m", None),
        getattr(patterns, "candle_15m", None),
    ):
        if (
            item is None
            or item.status != "READY"
            or item.direction not in {"BULLISH", "BEARISH"}
            or item.confidence < CONFIG.pattern_min_brain_confidence
        ):
            continue
        stage = str(item.stage or "").upper()
        factor = 1.0 if stage == "CONFIRMED" else 0.35 if stage in {"DETECTED", "FORMING", "BREAK DETECTED"} else 0.0
        points = float(item.confidence) * factor
        if item.direction == "BULLISH":
            bull += points
        else:
            bear += points
    bull = clamp(bull, 0.0, 100.0)
    bear = clamp(bear, 0.0, 100.0)
    neutral = min(bull, bear) if bull and bear else 0.0
    if bull and bear:
        bull *= 0.60
        bear *= 0.60
    return bull, bear, neutral


def _barrier_scores(
    levels: LevelBundle,
    price_action: PriceActionBundle | None,
) -> tuple[float, float, float]:
    """Convert directional room into a volatility-aware 0–100 component."""

    if levels.status != "READY":
        return 0.0, 0.0, 0.0
    atr = None
    if price_action is not None:
        atr = price_action.three_minute.atr14
    unit = max(18.0, float(atr or 0.0))

    def score(room: float | None) -> float:
        if room is None or room <= 0:
            return 0.0
        return clamp(float(room) / (1.5 * unit) * 100.0, 0.0, 100.0)

    bull = score(levels.upside_room)
    bear = score(levels.downside_room)
    range_score = min(bull, bear)
    return bull, bear, range_score


def _combined_activity_scores(
    futures: tuple[float, float, float],
    heavy: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Combine raw futures and Top-9 once, without composite Big Player reuse."""

    available = [item for item in (futures, heavy) if any(value > 0 for value in item)]
    if not available:
        return 0.0, 0.0, 0.0
    return tuple(sum(item[index] for item in available) / len(available) for index in range(3))


def _timeframe_state(item: Any) -> str:
    if item is None or str(getattr(item, "status", "")).upper() != "READY":
        return "UNAVAILABLE"
    bull = float(getattr(item, "bullish_score", 0.0) or 0.0)
    bear = float(getattr(item, "bearish_score", 0.0) or 0.0)
    range_score = float(getattr(item, "range_score", 0.0) or 0.0)
    if bull >= bear + 8 and bull >= range_score:
        return "BULLISH"
    if bear >= bull + 8 and bear >= range_score:
        return "BEARISH"
    return "MIXED"


def _entry_alignment_blocker(
    *,
    setup: str,
    price_action: PriceActionBundle | None,
    levels: LevelBundle,
    volume: VolumeBundle | None,
    patterns: PatternEvidenceBundle | None,
) -> str | None:
    """Hard permission gates: 15m direction, 3m trigger and usable room."""

    desired = _DIRECTION_FROM_ACTION.get(setup)
    if desired not in {"BULLISH", "BEARISH"}:
        return None
    if price_action is None:
        return "15m/3m alignment unavailable"
    fifteen = _timeframe_state(price_action.fifteen_minute)
    three = _timeframe_state(price_action.three_minute)
    if fifteen != desired:
        return f"15m permission is {fifteen}; {desired} setup blocked"
    if three != desired:
        return f"3m trigger is {three}; {desired} entry not confirmed"

    atr = max(18.0, float(price_action.three_minute.atr14 or 0.0))
    room = levels.upside_room if desired == "BULLISH" else levels.downside_room
    if levels.status != "READY" or room is None or float(room) < atr:
        side = "resistance" if desired == "BULLISH" else "support"
        shown = "unavailable" if room is None else f"{float(room):.1f} pts"
        return f"Barrier space to {side} is {shown}; minimum {atr:.1f} pts required"

    if volume is not None and volume.status in {"READY", "PARTIAL"}:
        view = str(volume.overall_view or "").upper()
        if desired == "BULLISH" and "BEARISH" in view:
            return "Futures volume confirms the opposite bearish move"
        if desired == "BEARISH" and "BULLISH" in view:
            return "Futures volume confirms the opposite bullish move"

    if patterns is not None and patterns.status == "READY":
        confirmed_opposite = [
            item
            for item in (
                patterns.wm_3m,
                patterns.candle_3m,
                getattr(patterns, "candle_5m", None),
                getattr(patterns, "candle_15m", None),
            )
            if item is not None
            and str(item.stage).upper() == "CONFIRMED"
            and item.confidence >= CONFIG.pattern_min_brain_confidence
            and item.direction in {"BULLISH", "BEARISH"}
            and item.direction != desired
        ]
        if confirmed_opposite:
            strongest = max(confirmed_opposite, key=lambda item: item.confidence)
            return f"Confirmed special candle/pattern is {strongest.direction}: {strongest.name}"
    return None


def _buy_level_adjustments(levels: LevelBundle) -> tuple[float, float, list[str], list[str]]:
    ce_adjust = pe_adjust = 0.0
    ce_cautions: list[str] = []
    pe_cautions: list[str] = []
    if levels.status != "READY":
        return -10.0, -10.0, ["Upside room unavailable"], ["Downside room unavailable"]

    if levels.upside_room is None:
        ce_adjust -= 8.0
        ce_cautions.append("Upside room unavailable")
    elif levels.upside_room < CONFIG.buy_min_directional_room_points:
        ce_adjust -= 22.0
        ce_cautions.append("CE buy has too little room before resistance")

    if levels.downside_room is None:
        pe_adjust -= 8.0
        pe_cautions.append("Downside room unavailable")
    elif levels.downside_room < CONFIG.buy_min_directional_room_points:
        pe_adjust -= 22.0
        pe_cautions.append("PE buy has too little room before support")

    if levels.current_position == "NEAR RESISTANCE":
        ce_adjust -= 10.0
        ce_cautions.append("Current price is near resistance")
    elif levels.current_position == "NEAR SUPPORT":
        pe_adjust -= 10.0
        pe_cautions.append("Current price is near support")
    return ce_adjust, pe_adjust, ce_cautions, pe_cautions


def _directional_momentum_adjustments(
    *,
    core: CoreMarketEvidence,
    price_action: PriceActionBundle | None,
    volume: VolumeBundle | None,
) -> tuple[float, float, list[str], list[str]]:
    """Apply timing and contradiction gates without re-voting core evidence.

    Price action and futures volume are already inside ``core``.  This helper must
    therefore not add a second positive vote for the same bullish/bearish reading.
    It only adds a small timing bonus and bounded penalties when the raw modules
    are mixed, opposite or unavailable.
    """

    ce_adjust = pe_adjust = 0.0
    ce_cautions: list[str] = []
    pe_cautions: list[str] = []

    stage = str(core.move_stage or "").upper()
    timing_inputs_ready = (
        price_action is not None
        and volume is not None
        and volume.status == "READY"
    )
    if (
        timing_inputs_ready
        and stage in {"DEVELOPING", "EARLY", "BUILDING", "BREAKOUT"}
    ):
        # Timing is not already a directional component of the core score.
        ce_adjust += 11.0
        pe_adjust += 11.0
    elif stage in {"MATURE", "EXHAUSTION", "SHORT-TERM EXHAUSTION RISK"}:
        ce_adjust -= 12.0
        pe_adjust -= 12.0
        ce_cautions.append("Directional move may be mature")
        pe_cautions.append("Directional move may be mature")

    if price_action is not None:
        pa = f"{price_action.combined_state} {price_action.relationship}".upper()
        if "BULLISH" in pa and "BEARISH" not in pa:
            # Bullish price action is already counted in core; only penalise PE BUY.
            pe_adjust -= 10.0
            pe_cautions.append("Price action is not bearish-aligned")
        elif "BEARISH" in pa and "BULLISH" not in pa:
            # Bearish price action is already counted in core; only penalise CE BUY.
            ce_adjust -= 10.0
            ce_cautions.append("Price action is not bullish-aligned")
        elif "MIXED" in pa or "RANGE" in pa or "CONFLICT" in pa:
            ce_adjust -= 8.0
            pe_adjust -= 8.0
            ce_cautions.append("Timeframes are mixed/range")
            pe_cautions.append("Timeframes are mixed/range")
    else:
        ce_adjust -= 10.0
        pe_adjust -= 10.0
        ce_cautions.append("Price-action alignment unavailable")
        pe_cautions.append("Price-action alignment unavailable")

    if volume is not None and volume.status == "READY":
        view = str(volume.overall_view or "").upper()
        if "BULLISH" in view:
            # Positive volume is already in core; only block the opposite buy.
            pe_adjust -= 6.0
            pe_cautions.append("Futures volume is not bearish-aligned")
        elif "BEARISH" in view:
            ce_adjust -= 6.0
            ce_cautions.append("Futures volume is not bullish-aligned")
        elif "WEAK" in view or "LOW" in view or "MIXED" in view:
            ce_adjust -= 5.0
            pe_adjust -= 5.0
            ce_cautions.append("Volume confirmation is weak")
            pe_cautions.append("Volume confirmation is weak")
    else:
        ce_adjust -= 8.0
        pe_adjust -= 8.0
        ce_cautions.append("Volume confirmation unavailable")
        pe_cautions.append("Volume confirmation unavailable")

    return ce_adjust, pe_adjust, ce_cautions, pe_cautions

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
            ce_cautions.append("Support paas hai; bounce se CE Sell ko risk")
    if levels.upside_room is not None:
        if levels.upside_room < 10:
            pe_adjust -= 18
            pe_cautions.append("Resistance paas hai; rejection se PE Sell ko risk")

    if levels.current_position == "NEAR SUPPORT":
        ce_adjust = min(ce_adjust, -10)
        ce_cautions.append("Price support ke paas hai; CE Sell me bounce risk")
    elif levels.current_position == "NEAR RESISTANCE":
        pe_adjust = min(pe_adjust, -10)
        pe_cautions.append("Price resistance ke paas hai; PE Sell me rejection risk")

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


def _news_adjustment(news: NewsContext | None) -> tuple[float, str | None]:
    if news is None:
        return 0.0, None
    if news.status == "READY":
        if news.risk_level == "HIGH":
            return 18.0, "Fresh/recent high-impact market news is active"
        if news.risk_level == "MEDIUM":
            return 8.0, "Fresh/recent medium-impact market news is active"
        return 0.0, None
    return 0.0, None


def _pattern_adjustments(
    patterns: PatternEvidenceBundle | None,
) -> tuple[float, float, float, float, bool, tuple[str, ...]]:
    """Return bounded pattern evidence for the canonical brain.

    W/M uses completed 3-minute structure and the main candle uses completed
    5-minute candles (falling back to legacy 3-minute data). Their combined impact
    is capped and reduced when they conflict. They never
    emit an action or bypass the normal score, WAIT and fake-move gates.
    """

    if patterns is None or patterns.status != "READY":
        return 0.0, 0.0, 0.0, 0.0, False, ()

    effects: list[tuple[str, float, str]] = []
    wm = patterns.wm_3m
    if (
        wm.status == "READY"
        and wm.direction in {"BULLISH", "BEARISH"}
        and wm.confidence >= CONFIG.pattern_min_brain_confidence
    ):
        stage_factor = 1.0 if wm.stage == "CONFIRMED" else 0.35
        points = CONFIG.pattern_wm_max_adjustment * (wm.confidence / 100.0) * stage_factor
        effects.append((wm.direction, points, f"3m {wm.name} {wm.stage.lower()}"))

    candle = getattr(patterns, "candle_5m", None) or patterns.candle_3m
    if (
        candle.status == "READY"
        and candle.direction in {"BULLISH", "BEARISH"}
        and candle.confidence >= CONFIG.pattern_min_brain_confidence
    ):
        points = CONFIG.pattern_candle_max_adjustment * (candle.confidence / 100.0)
        effects.append((candle.direction, points, f"5m {candle.name}"))

    neutral_wait = 0.0
    if (
        candle.status == "READY"
        and candle.direction == "NEUTRAL"
        and candle.name == "DOJI"
        and candle.confidence >= CONFIG.pattern_min_brain_confidence
    ):
        neutral_wait = 3.0

    if not effects:
        return 0.0, 0.0, 0.0, neutral_wait, False, ()

    directions = {item[0] for item in effects}
    conflict = len(directions) > 1
    if conflict:
        effects = [(direction, points * 0.60, label) for direction, points, label in effects]

    bull = sum(points for direction, points, _ in effects if direction == "BULLISH")
    bear = sum(points for direction, points, _ in effects if direction == "BEARISH")
    total = bull + bear
    if total > CONFIG.pattern_combined_max_adjustment and total > 0:
        scale = CONFIG.pattern_combined_max_adjustment / total
        bull *= scale
        bear *= scale

    condor = min(2.0, (bull + bear) * 0.20) if conflict else 0.0
    wait = neutral_wait + (4.0 if conflict else 0.0)
    notes = tuple(label for _, _, label in effects)
    return round(bear, 2), round(bull, 2), round(condor, 2), wait, conflict, notes


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
    news: NewsContext | None,
    market_session: MarketSession,
    price_action: PriceActionBundle | None,
    volume: VolumeBundle | None,
    patterns: PatternEvidenceBundle | None,
    history: list[dict[str, Any]],
    score_gap: float,
) -> tuple[float, tuple[str, ...]]:
    reference_only = not market_session.is_live
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

    # Top-9 already votes once; barrier proximity already adjusts entry location.
    # Low volume is participation context, not a second direction/range penalty.

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
    elif vix.movement == "RISING FAST":
        risk += 12
        reasons.append("VIX risk is elevated")

    if event_risk.level == "HIGH":
        risk += 30
        reasons.append("High-impact event risk can create false movement")
    elif event_risk.level == "MEDIUM":
        risk += 15
        reasons.append("Event risk can disturb short-term movement")

    if news is not None and news.status == "READY":
        if news.risk_level == "HIGH":
            risk += 20
            reasons.append("Fresh/recent high-impact news can create a fast false move")
        elif news.risk_level == "MEDIUM":
            risk += 8
            reasons.append("Fresh/recent market news adds short-term movement risk")

    if reference_only:
        reasons.append("Live session not confirmed; fake-move score is reference-only")
    return round(clamp(risk, 0, 100), 1), tuple(dict.fromkeys(reasons))[:3]


def _memory_confirmation(
    *,
    direction: str,
    history: list[dict[str, Any]],
    score_gap: float,
    fake_move_risk: float,
    core: CoreMarketEvidence,
    options: OptionIntelligence,
    volume: VolumeBundle | None,
    big_player: BigPlayerActivity | None,
    leader_score: float,
    market_session: MarketSession,
    as_of: datetime | None = None,
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
    contiguous_times: list[datetime] = []
    for previous in reversed(directions):
        if previous != direction:
            break
        consecutive += 1
    for row in reversed(history):
        if row["market_direction"] != direction:
            break
        captured = row.get("_captured_at")
        if isinstance(captured, datetime):
            contiguous_times.append(captured)
    stable_seconds = 0.0
    if as_of is not None and contiguous_times:
        oldest = min(contiguous_times)
        stable_seconds = max(0.0, (as_of - oldest).total_seconds())
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
    activity_direction = {
        "BUYING": "BULLISH",
        "SELLING": "BEARISH",
        "MIXED": "RANGE",
    }.get(str(getattr(big_player, "direction", "")).upper(), "")
    volume_text = str(getattr(volume, "overall_view", "")).upper()
    strong_alignment = (
        direction in {"BULLISH", "BEARISH"}
        and leader_score >= CONFIG.decision_strong_min_score
        and core_direction == direction
        and option_direction == direction
        and core.confidence >= 80
        and options.confidence >= 80
        and volume is not None
        and volume.status == "READY"
        and direction in volume_text
        and big_player is not None
        and big_player.status == "READY"
        and big_player.score >= 75
        and big_player.confirmation_count >= 2
        and confirmed_activity(big_player)
        and activity_direction == direction
        and fake_move_risk < CONFIG.fake_move_medium_threshold
    )

    prior_direction = directions[-1] if directions else previous_confirmed
    if prior_direction and prior_direction != direction:
        required_seconds = CONFIG.decision_reversal_confirmation_seconds
        time_confirmed = stable_seconds >= required_seconds
        memory_text += f" · reversal {stable_seconds:.0f}/{required_seconds}s"
        if (
            consecutive >= CONFIG.decision_flip_confirmations
            and time_confirmed
            and score_gap >= CONFIG.decision_flip_margin
            and fake_move_risk < CONFIG.fake_move_high_threshold
        ):
            return f"{direction} CONFIRMED", memory_text, True
        return "TRANSITION / WAIT", memory_text, False

    if direction == "RANGE":
        required_snapshots = CONFIG.decision_confirmation_snapshots
        required_seconds = CONFIG.decision_condor_confirmation_seconds
        confirmation_label = "condor"
    elif strong_alignment:
        required_snapshots = 2
        required_seconds = CONFIG.decision_strong_confirmation_seconds
        confirmation_label = "strong"
    else:
        required_snapshots = CONFIG.decision_confirmation_snapshots
        required_seconds = CONFIG.decision_normal_confirmation_seconds
        confirmation_label = "normal"
    time_confirmed = stable_seconds >= required_seconds
    memory_text += f" · {confirmation_label} {stable_seconds:.0f}/{required_seconds}s"
    if (
        consecutive >= required_snapshots
        and time_confirmed
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
    if max(ce, pe, condor) <= 0:
        current_bull, current_bear, current_range = 0.0, 0.0, 100.0
    else:
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
    # Scenario weights are conditional possibilities, not certainties. Keep a small
    # non-zero path for reversal and range outcomes so the UI never shows impossible
    # 0%/100% claims from one snapshot.
    floor_pct = max(0.0, min(20.0, float(CONFIG.outlook_min_path_pct)))
    bull, bear, range_score = _normalized_triplet(
        max(floor_pct, bull), max(floor_pct, bear), max(floor_pct, range_score)
    )

    invalidation_low, invalidation_high, invalidation_text = _invalidation_text(
        direction=direction,
        current_price=current_price,
        price_action=price_action,
        levels=levels,
    )
    fake_state = (
        "REFERENCE"
        if not market_session.is_live
        else "HIGH"
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
    news: NewsContext | None = None,
    price_action: PriceActionBundle | None = None,
    volume: VolumeBundle | None = None,
    patterns: PatternEvidenceBundle | None = None,
    big_player: BigPlayerActivity | None = None,
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

    futures_bull, futures_bear, futures_range = _futures_activity_scores(big_player)
    volume_bull, volume_bear, volume_range = _volume_scores(volume)
    pattern_bull, pattern_bear, pattern_range = _pattern_scores(patterns)
    barrier_bull, barrier_bear, barrier_range = _barrier_scores(levels, price_action)
    activity_bull, activity_bear, activity_range = _combined_activity_scores(
        (futures_bull, futures_bear, futures_range),
        (heavy_bull, heavy_bear, heavy_range),
    )

    unified_inputs_ready = price_action is not None and volume is not None and patterns is not None

    def unified_base(side: int) -> float:
        values = (
            (core.bullish_score, core.bearish_score, core.range_score),
            (options.bullish_score, options.bearish_score, options.range_score),
            (volume_bull, volume_bear, volume_range),
            (activity_bull, activity_bear, activity_range),
            (barrier_bull, barrier_bear, barrier_range),
            (pattern_bull, pattern_bear, pattern_range),
        )
        weights = (0.40, 0.15, 0.10, 0.10, 0.15, 0.10)
        return sum(group[side] * weight for group, weight in zip(values, weights))

    if unified_inputs_ready:
        base_bull, base_bear, base_range = (
            unified_base(0), unified_base(1), unified_base(2)
        )
    else:
        # Backward-compatible safe fallback for old reports/tests that do not
        # contain the newer raw modules. Production snapshots always use the
        # unified path above.
        base_bull = core.bullish_score * 0.45 + options.bullish_score * 0.35 + futures_bull * 0.10 + heavy_bull * 0.10
        base_bear = core.bearish_score * 0.45 + options.bearish_score * 0.35 + futures_bear * 0.10 + heavy_bear * 0.10
        base_range = core.range_score * 0.45 + options.range_score * 0.35 + futures_range * 0.10 + heavy_range * 0.10
    pe = ce_buy = base_bull
    ce = pe_buy = base_bear
    condor = base_range
    # RANGE needs observed range structure, not merely conflicting/missing votes.
    range_eligible = bool(
        price_action is not None
        and getattr(price_action.fifteen_minute, "status", "") == "READY"
        and "RANGE" in getattr(price_action.fifteen_minute, "structure", "")
        and core.range_score > max(core.bullish_score, core.bearish_score)
        and levels.status == "READY"
        and levels.immediate_support is not None and levels.immediate_resistance is not None
        and levels.immediate_support.status != "BROKEN" and levels.immediate_resistance.status != "BROKEN"
        and min(levels.upside_room or 0, levels.downside_room or 0) >= 18
    )
    evidence_direction = (
        "RANGE" if range_eligible and base_range > max(base_bull, base_bear)
        else "BULLISH" if base_bull - base_bear >= 8
        else "BEARISH" if base_bear - base_bull >= 8 else "MIXED"
    )

    (
        _ce_adjust,
        _pe_adjust,
        _condor_adjust,
        ce_level_cautions,
        pe_level_cautions,
        condor_level_cautions,
    ) = _level_adjustments(levels)
    # In the unified path barrier room already owns 15%; do not add it twice.
    if not unified_inputs_ready:
        ce += _ce_adjust
        pe += _pe_adjust
        condor += _condor_adjust

    (
        ce_buy_level_adjust,
        pe_buy_level_adjust,
        ce_buy_level_cautions,
        pe_buy_level_cautions,
    ) = _buy_level_adjustments(levels)
    if not unified_inputs_ready:
        ce_buy += ce_buy_level_adjust
        pe_buy += pe_buy_level_adjust

    (
        ce_buy_momentum_adjust,
        pe_buy_momentum_adjust,
        ce_buy_momentum_cautions,
        pe_buy_momentum_cautions,
    ) = _directional_momentum_adjustments(
        core=core, price_action=price_action, volume=volume
    )
    if not unified_inputs_ready:
        ce_buy += ce_buy_momentum_adjust
        pe_buy += pe_buy_momentum_adjust

    # Iron Condor is a range structure, not a generic fallback. A clearly
    # directional core or option-flow read must reduce its fit before ranking.
    core_directional = max(core.bullish_score, core.bearish_score)
    option_directional = max(options.bullish_score, options.bearish_score)
    if core_directional >= core.range_score + 12:
        condor -= 8
        condor_level_cautions = tuple(condor_level_cautions) + (
            "Directional core evidence weakens Iron Condor",
        )
    if option_directional >= options.range_score + 12:
        condor -= 8
        condor_level_cautions = tuple(condor_level_cautions) + (
            "Directional option flow weakens Iron Condor",
        )
    core_side = "BULL" if core.bullish_score > core.bearish_score + 8 else "BEAR" if core.bearish_score > core.bullish_score + 8 else "MIXED"
    option_side = "BULL" if options.bullish_score > options.bearish_score + 8 else "BEAR" if options.bearish_score > options.bullish_score + 8 else "MIXED"
    if core_side == option_side and core_side in {"BULL", "BEAR"}:
        condor -= 6
        condor_level_cautions = tuple(condor_level_cautions) + (
            "Core and option flow agree directionally",
        )

    # Patterns own one explicit 10% component in the unified path.  Legacy
    # snapshots retain the old bounded adjustment for report compatibility.
    pattern_ce, pattern_pe, pattern_condor, pattern_wait, pattern_conflict, pattern_notes = _pattern_adjustments(patterns)
    if not unified_inputs_ready:
        ce += pattern_ce
        pe += pattern_pe
        condor += pattern_condor
        ce_buy += pattern_pe
        pe_buy += pattern_ce

    # Composite Big Player is confirmation-only.  Its raw futures classification
    # is already represented once above; no composite +points are allowed.
    big_player_note: str | None = None
    if (
        big_player is not None
        and big_player.status == "READY"
        and big_player.confirmation_count >= 2
        and big_player.score >= 60
    ):
        big_player_note = (
            f"Big Player {big_player.direction} {big_player.score:.0f}/100 "
            f"confirmed {big_player.confirmation_count}/{big_player.confirmation_total}"
        )

    if options.persistence in {"WARMING UP", "UNAVAILABLE"}:
        ce -= 5
        pe -= 5
        condor -= 5
        ce_buy -= 7
        pe_buy -= 7

    event_wait, event_blocker = _event_adjustment(event_risk)
    news_wait, news_blocker = _news_adjustment(news)
    if event_risk.level == "HIGH":
        ce -= 12
        pe -= 12
        condor -= 20
        ce_buy -= 18
        pe_buy -= 18
    elif event_risk.level == "MEDIUM":
        ce -= 5
        pe -= 5
        condor -= 10
        ce_buy -= 8
        pe_buy -= 8
    if news is not None and news.status == "READY":
        if news.risk_level == "HIGH":
            ce -= 5
            pe -= 5
            condor -= 10
            ce_buy -= 10
            pe_buy -= 10
        elif news.risk_level == "MEDIUM":
            condor -= 4
            ce_buy -= 4
            pe_buy -= 4

    ce = round(clamp(ce, 0, 100), 1)
    pe = round(clamp(pe, 0, 100), 1)
    condor = round(clamp(condor, 0, 100), 1)
    ce_buy = round(clamp(ce_buy, 0, 100), 1)
    pe_buy = round(clamp(pe_buy, 0, 100), 1)

    option_data_available = (
        options.status != "UNAVAILABLE" and options.market_bias != "UNAVAILABLE"
    )
    if not option_data_available:
        # Missing option data is not neutral/decay evidence. No seller setup can be
        # scored from absent CE/PE premiums, OI and volume.
        ce = pe = condor = ce_buy = pe_buy = 0.0

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
        bullish_edge = max(pe, ce_buy)
        bearish_edge = max(ce, pe_buy)
        direction_gap = abs(bullish_edge - bearish_edge)
        if direction_gap < CONFIG.decision_minimum_margin and condor < 62:
            wait += 12
            blockers.append("Directional edge is not separated")
        if vix.status != "READY":
            wait += 6
            blockers.append("India VIX data is unavailable")
        elif vix.movement == "RISING FAST":
            wait += 14
            blockers.append("VIX risk is elevated")
        wait += pattern_wait
        if pattern_conflict:
            blockers.append("3-minute W/M and candle evidence conflict")
        wait += event_wait
        wait += news_wait
        if event_blocker:
            blockers.append(event_blocker)
        if news_blocker:
            blockers.append(news_blocker)

    wait = round(clamp(wait, 0, 100), 1)

    ce_cautions = list(ce_level_cautions)
    pe_cautions = list(pe_level_cautions)
    condor_cautions = list(condor_level_cautions)
    ce_buy_cautions = list(ce_buy_level_cautions) + list(ce_buy_momentum_cautions)
    pe_buy_cautions = list(pe_buy_level_cautions) + list(pe_buy_momentum_cautions)
    if big_player_note and big_player is not None:
        if big_player.direction == "BUYING":
            ce_cautions.append(big_player_note)
            pe_buy_cautions.append(big_player_note)
        elif big_player.direction == "SELLING":
            pe_cautions.append(big_player_note)
            ce_buy_cautions.append(big_player_note)
    if options.confidence < CONFIG.decision_min_option_confidence:
        warning = "Option-flow continuity is not mature"
        ce_cautions.append(warning)
        pe_cautions.append(warning)
        condor_cautions.append(warning)
        ce_buy_cautions.append(warning)
        pe_buy_cautions.append(warning)
    if vix.status != "READY":
        vix_warning = "India VIX data is unavailable"
        ce_cautions.append(vix_warning)
        pe_cautions.append(vix_warning)
        condor_cautions.append(vix_warning)
        ce_buy_cautions.append(vix_warning)
        pe_buy_cautions.append(vix_warning)
    if event_blocker:
        ce_cautions.append(event_blocker)
        pe_cautions.append(event_blocker)
        condor_cautions.append(event_blocker)
        ce_buy_cautions.append(event_blocker)
        pe_buy_cautions.append(event_blocker)
    if news_blocker:
        ce_cautions.append(news_blocker)
        pe_cautions.append(news_blocker)
        condor_cautions.append(news_blocker)
        ce_buy_cautions.append(news_blocker)
        pe_buy_cautions.append(news_blocker)
    if pattern_conflict:
        pattern_warning = "3-minute W/M and candle evidence conflict"
        ce_cautions.append(pattern_warning)
        pe_cautions.append(pattern_warning)
        condor_cautions.append(pattern_warning)
        ce_buy_cautions.append(pattern_warning)
        pe_buy_cautions.append(pattern_warning)

    unavailable_reason = ("Option chain unavailable — strategy not scored",)
    ce_eval = StrategyEvaluation(
        name="CE SELL",
        score=ce,
        status="UNAVAILABLE" if not option_data_available else _status(ce, ce_cautions),
        reasons=(
            unavailable_reason
            if not option_data_available
            else _top_reasons(
                (f"Core bearish evidence {core.bearish_score:.1f}/100",),
                (f"Bearish option flow {options.bearish_score:.1f}%",),
                (f"Top-7 state: {heavyweights.state}",),
            )
        ),
        cautions=tuple(dict.fromkeys(ce_cautions))[:3],
    )
    pe_eval = StrategyEvaluation(
        name="PE SELL",
        score=pe,
        status="UNAVAILABLE" if not option_data_available else _status(pe, pe_cautions),
        reasons=(
            unavailable_reason
            if not option_data_available
            else _top_reasons(
                (f"Core bullish evidence {core.bullish_score:.1f}/100",),
                (f"Bullish option flow {options.bullish_score:.1f}%",),
                (f"Top-7 state: {heavyweights.state}",),
            )
        ),
        cautions=tuple(dict.fromkeys(pe_cautions))[:3],
    )
    condor_eval = StrategyEvaluation(
        name="IRON CONDOR",
        score=condor,
        status="UNAVAILABLE"
        if not option_data_available
        else _status(condor, condor_cautions),
        reasons=(
            unavailable_reason
            if not option_data_available
            else _top_reasons(
                (f"Core range/mixed evidence {core.range_score:.1f}/100",),
                (f"Option decay/mixed evidence {options.range_score:.1f}%",),
                (f"VIX environment: {vix.seller_environment}",),
            )
        ),
        cautions=tuple(dict.fromkeys(condor_cautions))[:3],
    )
    ce_buy_eval = StrategyEvaluation(
        name="CE BUY",
        score=ce_buy,
        status="UNAVAILABLE"
        if not option_data_available
        else _status(ce_buy, ce_buy_cautions),
        reasons=(
            unavailable_reason
            if not option_data_available
            else _top_reasons(
                (f"Bullish momentum evidence {core.bullish_score:.1f}/100",),
                (f"Bullish option flow {options.bullish_score:.1f}%",),
                (f"Price action: {price_action.combined_state}" if price_action else "Price action unavailable",),
            )
        ),
        cautions=tuple(dict.fromkeys(ce_buy_cautions))[:3],
    )
    pe_buy_eval = StrategyEvaluation(
        name="PE BUY",
        score=pe_buy,
        status="UNAVAILABLE"
        if not option_data_available
        else _status(pe_buy, pe_buy_cautions),
        reasons=(
            unavailable_reason
            if not option_data_available
            else _top_reasons(
                (f"Bearish momentum evidence {core.bearish_score:.1f}/100",),
                (f"Bearish option flow {options.bearish_score:.1f}%",),
                (f"Price action: {price_action.combined_state}" if price_action else "Price action unavailable",),
            )
        ),
        cautions=tuple(dict.fromkeys(pe_buy_cautions))[:3],
    )

    candidates = sorted(
        (
            ("CE SELL", ce),
            ("PE SELL", pe),
            ("IRON CONDOR", condor if range_eligible else 0.0),
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    leader, leader_score = candidates[0]
    runner_up = candidates[1][1]
    bullish_score = max(pe, ce_buy)
    bearish_score = max(ce, pe_buy)
    direction = evidence_direction if option_data_available else "UNAVAILABLE"
    score_gap = max(0.0, leader_score - runner_up)

    alignment_blocker = (
        _entry_alignment_blocker(
            setup=leader,
            price_action=price_action,
            levels=levels,
            volume=volume,
            patterns=patterns,
        )
        if unified_inputs_ready
        else None
    )
    if alignment_blocker:
        wait = max(wait, CONFIG.decision_wait_block_threshold)
        blockers.append(alignment_blocker)

    # Same policy as the execution guard; never a duplicate direction vote.
    activity_blocked, activity_note = activity_gate(leader, big_player)
    if market_session.is_live and activity_blocked:
        wait = max(wait + 35.0, CONFIG.decision_wait_block_threshold)
        blockers.append(activity_note)
    wait = round(clamp(wait, 0, 100), 1)

    if wait >= CONFIG.decision_wait_block_threshold or alignment_blocker:
        instant_action = "WAIT"
    elif (
        leader_score < CONFIG.decision_minimum_score
        or score_gap < CONFIG.decision_minimum_margin
    ):
        instant_action = "WAIT"
        blockers.append("No strategy meets score and separation thresholds")
        wait = max(wait, 50.0)
    elif leader in {"CE SELL", "PE SELL", "IRON CONDOR"}:
        instant_action = f"{leader} WITH HEDGE"
    else:
        instant_action = leader

    history = _valid_history(signal_history, as_of)
    fake_move_risk, fake_reasons = _fake_move_risk(
        direction=direction,
        core=core,
        options=options,
        heavyweights=heavyweights,
        vix=vix,
        levels=levels,
        event_risk=event_risk,
        news=news,
        market_session=market_session,
        price_action=price_action,
        volume=volume,
        patterns=patterns,
        history=history,
        score_gap=score_gap,
    )
    signal_state, memory_text, _confirmed = _memory_confirmation(
        direction=direction,
        history=history,
        score_gap=score_gap,
        fake_move_risk=fake_move_risk,
        core=core,
        options=options,
        volume=volume,
        big_player=big_player,
        leader_score=leader_score,
        market_session=market_session,
        as_of=as_of,
    )
    if not option_data_available:
        signal_state = "DATA UNAVAILABLE / WAIT"
        memory_text = "Option chain unavailable"

    final_action = instant_action
    if direction not in {"BULLISH", "BEARISH", "RANGE"}:
        final_action = "WAIT"
        blockers.append("Direction mixed; no automatic range fallback")
    elif _direction_from_action(instant_action) not in {None, direction}:
        final_action = "WAIT"
        blockers.append("Entry candidate does not match independent direction evidence")
    # Strategy identity has its own persistence, independent of market direction.
    if final_action != "WAIT" and history:
        recent_actions = [str(row.get("candidate_action", row.get("action", ""))) for row in history[-4:]]
        different = [a for a in recent_actions if a not in {"", "WAIT", final_action}]
        if different and recent_actions[-2:] != [final_action, final_action]:
            final_action = "WAIT"
            blockers.append("Strategy switch pending: candidate must persist; no automatic conversion")
    if instant_action != "WAIT":
        if signal_state == "TRANSITION / WAIT":
            final_action = "WAIT"
            wait = max(wait, CONFIG.decision_stability_wait_floor)
            blockers.append(
                "Opposite movement is not persistent; anti-flip filter is holding WAIT"
            )
        elif "DEVELOPING" in signal_state or "WARMING UP" in signal_state:
            final_action = "WAIT"
            wait = max(wait, CONFIG.decision_stability_wait_floor)
            blockers.append(
                "Adaptive confirmation is warming up: strong 30s, normal 60s, condor 120s"
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

    if unified_inputs_ready:
        coverage = core.confidence * .40 + options.confidence * .15
        coverage += (volume.confidence if volume is not None else 0.0) * .10
        activity_ready = any(value > 0 for value in (activity_bull, activity_bear, activity_range))
        coverage += 10.0 if activity_ready else 0.0
        coverage += 15.0 if levels.status == "READY" else 0.0
        coverage += 10.0 if patterns is not None and patterns.status == "READY" else 0.0
    else:
        coverage = core.confidence * .45 + options.confidence * .35
        coverage += 10 if big_player is not None and big_player.status == "READY" else 0
        coverage += min(10., heavyweights.recent_coverage_pct / max(heavyweights.covered_weight_pct, .01) * 10)
    # One risk discount, not two penalties for the same evidence.
    confidence = coverage * max(.35, 1 - max(wait_eval.score, fake_move_risk) / 180)
    confidence = round(clamp(confidence, 0, 95), 1)

    outlook = _build_outlook(
        ce=bearish_score,
        pe=bullish_score,
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
        "CE BUY": ce_buy_eval,
        "PE BUY": pe_buy_eval,
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

    score_audit = {}
    for name, evaluation in evaluation_map.items():
        side = 0 if name in {"PE SELL", "CE BUY"} else 1 if name in {"CE SELL", "PE BUY"} else 2
        if unified_inputs_ready:
            contributions = {
                "15m permission + 3m trigger + indicators 40%": (core.bullish_score, core.bearish_score, core.range_score)[side] * .40,
                "OI / Options flow 15%": (options.bullish_score, options.bearish_score, options.range_score)[side] * .15,
                "Futures volume 10%": (volume_bull, volume_bear, volume_range)[side] * .10,
                "Buying/Selling activity (Futures + Top-9) 10%": (activity_bull, activity_bear, activity_range)[side] * .10,
                "Barrier space 15%": (barrier_bull, barrier_bear, barrier_range)[side] * .15,
                "Special candle / W-M 10%": (pattern_bull, pattern_bear, pattern_range)[side] * .10,
            }
        else:
            contributions = {
                "Legacy Core 45%": (core.bullish_score, core.bearish_score, core.range_score)[side] * .45,
                "Legacy OI / Options 35%": (options.bullish_score, options.bearish_score, options.range_score)[side] * .35,
                "Legacy Raw Futures 10%": (futures_bull, futures_bear, futures_range)[side] * .10,
                "Legacy Top-9 10%": (heavy_bull, heavy_bear, heavy_range)[side] * .10,
            }
        # Capture values from the canonical inputs and final evaluation. Never
        # multiply evidence quality again in the presentation layer.
        subtotal = sum(contributions.values())
        contributions["Base total"] = subtotal
        contributions["Net adjustments / caps / rounding"] = evaluation.score - subtotal
        contributions["Final fit"] = evaluation.score
        score_audit[name] = contributions

    return FinalDecision(
        ce_sell=ce_eval,
        pe_sell=pe_eval,
        iron_condor=condor_eval,
        wait_need=wait_eval,
        instant_action=instant_action,
        final_action=final_action,
        signal_state=signal_state,
        market_direction=direction,
        score_audit=score_audit,
        execution_status=execution_status,
        decision_confidence=confidence,
        hedge_required=leader in {"CE SELL", "PE SELL", "IRON CONDOR"},
        reasons=final_reasons[:3],
        blocker=blocker,
        outlook=outlook,
        status=status,
        ce_buy=ce_buy_eval,
        pe_buy=pe_buy_eval,
    )
