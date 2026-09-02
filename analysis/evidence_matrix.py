from __future__ import annotations

from math import floor
from typing import Any

from analysis.technical_utils import clamp
from models import MarketSnapshot, PatternSignal, TimeframeIndicators


def _normalise(
    bullish: float, bearish: float, neutral: float
) -> tuple[float, float, float]:
    values = [
        max(0.0, float(bullish)),
        max(0.0, float(bearish)),
        max(0.0, float(neutral)),
    ]
    total = sum(values)
    if total <= 0:
        return 0.0, 0.0, 0.0

    # Largest-remainder allocation in tenths guarantees that every directional
    # row displays exactly 100.0 after rounding.
    scaled = [value / total * 1000.0 for value in values]
    tenths = [floor(value) for value in scaled]
    remainder = 1000 - sum(tenths)
    order = sorted(
        range(len(scaled)),
        key=lambda index: scaled[index] - tenths[index],
        reverse=True,
    )
    for index in order[:remainder]:
        tenths[index] += 1
    return tuple(value / 10.0 for value in tenths)  # type: ignore[return-value]


def _dominant_label(bullish: float, bearish: float, neutral: float) -> str:
    leader = max(
        (("BULLISH", bullish), ("BEARISH", bearish), ("NEUTRAL / RANGE", neutral)),
        key=lambda item: item[1],
    )
    return leader[0]


def _weighted_scores(
    rows: list[tuple[float, float, float, float]],
) -> tuple[float, float, float]:
    usable = [item for item in rows if item[3] > 0]
    if not usable:
        return 0.0, 0.0, 0.0
    weight_total = sum(item[3] for item in usable)
    bullish = sum(item[0] * item[3] for item in usable) / weight_total
    bearish = sum(item[1] * item[3] for item in usable) / weight_total
    neutral = sum(item[2] * item[3] for item in usable) / weight_total
    return _normalise(bullish, bearish, neutral)


def _indicator_vote(item: TimeframeIndicators) -> tuple[float, float, float]:
    if item.status != "READY":
        return 0.0, 0.0, 0.0

    bullish = bearish = neutral = 0.0
    ema = item.ema_state.upper()
    if ema == "BULLISH ALIGNED":
        bullish += 1.0
    elif "BULLISH STRUCTURE" in ema:
        bullish += 0.65
        neutral += 0.35
    elif ema == "BEARISH ALIGNED":
        bearish += 1.0
    elif "BEARISH STRUCTURE" in ema:
        bearish += 0.65
        neutral += 0.35
    else:
        neutral += 1.0

    macd = item.macd_state.upper()
    if macd == "BULLISH":
        bullish += 1.0
    elif "BULLISH" in macd:
        bullish += 0.65
        neutral += 0.35
    elif macd == "BEARISH":
        bearish += 1.0
    elif "BEARISH" in macd:
        bearish += 0.65
        neutral += 0.35
    else:
        neutral += 1.0

    rsi = item.rsi_state.upper()
    if "BULLISH" in rsi and "OVEREXTENDED" in rsi:
        bullish += 0.55
        neutral += 0.45
    elif "BULLISH" in rsi:
        bullish += 0.80
        neutral += 0.20
    elif "BEARISH" in rsi and "OVERSOLD" in rsi:
        bearish += 0.55
        neutral += 0.45
    elif "BEARISH" in rsi:
        bearish += 0.80
        neutral += 0.20
    else:
        neutral += 1.0
    return _normalise(bullish, bearish, neutral)


def _level_scores(snapshot: MarketSnapshot) -> tuple[float, float, float]:
    levels = snapshot.levels
    if levels.status != "READY":
        return 0.0, 0.0, 100.0

    bullish, bearish, neutral = 25.0, 25.0, 50.0
    support = levels.immediate_support
    resistance = levels.immediate_resistance

    if support and support.status == "BROKEN":
        bearish += 45.0
        neutral -= 25.0
    elif support and any(token in support.status for token in ("HOLDING", "REJECTED")):
        bullish += 35.0
        neutral -= 20.0

    if resistance and resistance.status == "BROKEN":
        bullish += 45.0
        neutral -= 25.0
    elif resistance and "REJECTED" in resistance.status:
        bearish += 35.0
        neutral -= 20.0

    if levels.current_position == "NEAR SUPPORT":
        bullish += 18.0
        neutral -= 8.0
    elif levels.current_position == "NEAR RESISTANCE":
        bearish += 18.0
        neutral -= 8.0
    elif levels.upside_room is not None and levels.downside_room is not None:
        gap = levels.upside_room - levels.downside_room
        if gap >= 12:
            bullish += 10.0
            neutral -= 5.0
        elif gap <= -12:
            bearish += 10.0
            neutral -= 5.0

    return _normalise(bullish, bearish, max(0.0, neutral))


def _volume_scores(snapshot: MarketSnapshot) -> tuple[float, float, float]:
    state = snapshot.volume.overall_view.upper()
    if "BULLISH" in state:
        return 70.0, 10.0, 20.0
    if "BEARISH" in state:
        return 10.0, 70.0, 20.0
    if "UNAVAILABLE" in state:
        return 0.0, 0.0, 100.0
    if "WEAK" in state:
        return 20.0, 20.0, 60.0
    return 25.0, 25.0, 50.0


def _heavyweight_scores(snapshot: MarketSnapshot) -> tuple[float, float, float]:
    from analysis.decision import _heavyweight_scores as recent_scores
    # The canonical brain uses these raw bounded strengths.  Normalising
    # (60, 5, 0) to (92, 8, 0) made a mild 15m recovery look like 92/100
    # conviction and did not match the actual weighted decision input.
    return tuple(round(clamp(value, 0.0, 100.0), 1) for value in recent_scores(snapshot.heavyweights))


def _legacy_day_heavyweight_scores(snapshot: MarketSnapshot) -> tuple[float, float, float]:
    if snapshot.heavyweights.status not in {"READY", "CAUTION"}:
        return 0.0, 0.0, 100.0
    state = snapshot.heavyweights.state.upper()
    if "BROAD BULLISH" in state:
        return 80.0, 8.0, 12.0
    if "NARROW BULLISH" in state:
        return 60.0, 18.0, 22.0
    if "BROAD BEARISH" in state:
        return 8.0, 80.0, 12.0
    if "NARROW BEARISH" in state:
        return 18.0, 60.0, 22.0
    return 25.0, 25.0, 50.0


def _institutional_scores(snapshot: MarketSnapshot) -> tuple[float, float, float]:
    context = snapshot.institutional_context
    state = context.state.upper()
    if context.status == "MISSING":
        return 0.0, 0.0, 100.0
    if "SUPPORT" in state or "FII BUYING" in state:
        return 62.0, 18.0, 20.0
    if "PRESSURE" in state or "FII SELLING" in state:
        return 18.0, 62.0, 20.0
    return 25.0, 25.0, 50.0


def _barrier_scores(snapshot: MarketSnapshot) -> tuple[float, float, float]:
    """Summarise the existing Barrier Map for display; never add decision weight."""
    barrier = getattr(snapshot, "barrier_map", None)
    if barrier is None or str(getattr(barrier, "status", "")).upper() not in {
        "READY",
        "PARTIAL",
    }:
        return 0.0, 0.0, 100.0
    resistance = getattr(barrier, "nearest_resistance", None)
    support = getattr(barrier, "nearest_support", None)
    range_item = getattr(barrier, "trading_range", None)
    bullish = bearish = 0.0
    if resistance is not None:
        bullish += float(getattr(resistance, "break_pressure", 0.0) or 0.0)
        bearish += float(getattr(resistance, "strength", 0.0) or 0.0)
    if support is not None:
        bullish += float(getattr(support, "strength", 0.0) or 0.0)
        bearish += float(getattr(support, "break_pressure", 0.0) or 0.0)
    neutral = float(getattr(range_item, "confidence", 0.0) or 0.0) * 1.4
    return _normalise(bullish, bearish, neutral)


def _big_player_scores(snapshot: MarketSnapshot) -> tuple[float, float, float]:
    activity = getattr(snapshot, "big_player_activity", None)
    if activity is None or str(getattr(activity, "status", "")).upper() != "READY":
        return 0.0, 0.0, 100.0
    buy = max(0.0, float(getattr(activity, "buy_score", 0.0) or 0.0))
    sell = max(0.0, float(getattr(activity, "sell_score", 0.0) or 0.0))
    neutral = max(0.0, 100.0 - max(buy, sell))
    return _normalise(buy, sell, neutral)


def _feed_confidence(snapshot: MarketSnapshot) -> float:
    statuses = list(snapshot.feed_status.values())
    if not statuses:
        return 0.0
    usable = sum(status.ok for status in statuses)
    live = sum(status.use_state == "LIVE" for status in statuses)
    base = usable / len(statuses) * 70.0 + live / len(statuses) * 30.0
    if not snapshot.market_session.is_live:
        base = min(base, 55.0)
    return round(clamp(base, 0.0, 100.0), 1)


def build_module_impact_audit(
    snapshot: MarketSnapshot,
    rows: list[dict[str, Any]],
) -> tuple[str, dict[str, str]]:
    """Explain canonical module weights without producing another decision.

    The selected setup is used when available; during WAIT the highest existing
    strategy score is the reference architecture. Active points are a display audit
    of that module's current dominant evidence, never an order signal.
    """

    decision = getattr(snapshot, "decision", None)
    evaluations = {}
    if decision is not None:
        evaluations = {
            name: evaluation
            for name, evaluation in (
                ("CE BUY", getattr(decision, "ce_buy", None)),
                ("PE BUY", getattr(decision, "pe_buy", None)),
                ("CE SELL", getattr(decision, "ce_sell", None)),
                ("PE SELL", getattr(decision, "pe_sell", None)),
                ("IRON CONDOR", getattr(decision, "iron_condor", None)),
            )
            if evaluation is not None
        }
    final_action = str(getattr(decision, "final_action", "WAIT"))
    selected = final_action.replace(" WITH HEDGE", "")
    reference = selected if selected in evaluations and final_action != "WAIT" else "IRON CONDOR"
    if evaluations and (reference not in evaluations or final_action == "WAIT"):
        reference = max(
            evaluations,
            key=lambda name: float(getattr(evaluations[name], "score", 0.0)),
        )
    score_audit = dict(getattr(decision, "score_audit", {}).get(reference, {}))
    core_points = float(score_audit.get("15m permission + 3m trigger + indicators 40%", 0.0) or 0.0)
    oi_points = float(score_audit.get("OI / Options flow 15%", 0.0) or 0.0)
    volume_points = float(score_audit.get("Futures volume 10%", 0.0) or 0.0)
    activity_points = float(score_audit.get("Buying/Selling activity (Futures + Top-9) 10%", 0.0) or 0.0)
    barrier_points = float(score_audit.get("Barrier space 15%", 0.0) or 0.0)
    pattern_points = float(score_audit.get("Special candle / W-M 10%", 0.0) or 0.0)
    audit: dict[str, str] = {}
    for row in rows:
        module = str(row["Module"])
        if module in {"Price Action", "EMA / MACD / RSI"}:
            audit[module] = f"Shared canonical Core 40% · combined actual {core_points:.1f} pts"
            continue
        if module == "OI & Options Flow":
            audit[module] = f"Canonical OI/Options 15% · actual {oi_points:.1f} pts"
            continue
        if module == "Barrier / Levels / Volume":
            audit[module] = (
                f"Futures volume 10% = {volume_points:.1f} pts · "
                f"Barrier room 15% = {barrier_points:.1f} pts"
            )
            continue
        if module == "Big Player Activity":
            activity = getattr(snapshot, "big_player_activity", None)
            direction = str(getattr(activity, "direction", "UNAVAILABLE"))
            score = float(getattr(activity, "score", 0.0) or 0.0)
            confirmations = int(getattr(activity, "confirmation_count", 0) or 0)
            audit[module] = (
                f"{direction} {score:.0f}/100 · confirm {confirmations} · "
                f"shared Futures+Top-9 activity 10% = {activity_points:.1f} pts; "
                "composite gate extra 0"
            )
            continue
        if module in {"3M W/M Pattern", "Special Candle"}:
            audit[module] = f"Shared pattern component 10% · combined actual {pattern_points:.1f} pts"
            continue
        if module == "NIFTY Top-9":
            audit[module] = f"Shared Futures+Top-9 activity 10% · combined actual {activity_points:.1f} pts"
            continue
        if module == "FII/DII (15 Sessions)":
            audit[module] = "Background/history context · live direction weight 0"
            continue
        if module == "News / Event Risk":
            news = getattr(snapshot, "news_context", None)
            status = str(getattr(news, "status", "UNAVAILABLE")).upper()
            risk = str(getattr(news, "risk_level", "NONE")).upper()
            bias = str(getattr(news, "bias", "NEUTRAL")).upper()
            if status == "READY":
                points = {"HIGH": 9, "MEDIUM": 5, "LOW": 2}.get(risk, 0)
            elif status == "OLD":
                points = 0
            else:
                points = 0
            severity = (
                "DANGEROUS" if risk == "HIGH" and status == "READY"
                else "CAUTION" if points >= 3 or status == "OLD"
                else "NORMAL" if points > 0
                else "NO LIVE NEWS"
            )
            audit[module] = f"{bias} · {severity} · Risk context; net score effect neeche"
            continue
        elif module == "VIX / Data Integrity":
            vix = getattr(snapshot, "vix_context", None)
            value = getattr(vix, "last_price", None)
            change = getattr(vix, "change_pct", None)
            regime = str(getattr(vix, "regime", "UNAVAILABLE")).upper()
            movement = str(getattr(vix, "movement", "UNAVAILABLE")).upper()
            value_text = f"{float(value):.2f}" if value is not None else "NA"
            change_text = f"{float(change):+.2f}%" if change is not None else "NA"
            audit[module] = (
                f"VIX {value_text} ({change_text}) · {regime}/{movement}"
                " · Risk context; extra direction weight 0"
            )
            continue
        audit[module] = "Display evidence only; canonical point mapping unavailable"
    return reference, audit


def _short_structure(value: str) -> str:
    upper = str(value or "").upper()
    if "BULLISH HH/HL" in upper:
        return "HH/HL UP"
    if "BEARISH LH/LL" in upper:
        return "LH/LL DOWN"
    if "RANGE" in upper:
        return "RANGE"
    if "MIXED" in upper or "TRANSITION" in upper:
        return "MIXED"
    return "NA"


def _short_direction(value: str) -> str:
    upper = str(value or "").upper()
    if "BULL" in upper:
        return "BULLISH"
    if "BEAR" in upper:
        return "BEARISH"
    if "RANGE" in upper or "MIXED" in upper or "FLAT" in upper:
        return "MIXED"
    if "MISSING" in upper or "UNAVAILABLE" in upper or "INVALID" in upper:
        return "NA"
    return upper[:18] if upper else "NA"


def _short_persistence(value: str) -> str:
    upper = str(value or "").upper()
    if "WARMING" in upper:
        return "WARMING"
    if "PERSISTENT" in upper:
        return "STABLE"
    if "UNAVAILABLE" in upper:
        return "NA"
    return upper[:14]


def _short_pcr(value: str) -> str:
    upper = str(value or "").upper()
    if "PE OI" in upper or "BULLISH SUPPORT" in upper:
        return "PE OI HIGH"
    if "CE OI" in upper or "BEARISH" in upper:
        return "CE OI HIGH"
    if "BALANCED" in upper or "NEUTRAL" in upper:
        return "PCR BALANCED"
    return "PCR NA" if "UNAVAILABLE" in upper else f"PCR {upper[:12]}"


def _short_ema(value: str) -> str:
    upper = str(value or "").upper()
    if "BULLISH ALIGNED" in upper:
        return "TREND UP"
    if "BEARISH ALIGNED" in upper:
        return "TREND DOWN"
    if "BULLISH STRUCTURE" in upper:
        return "STRUCTURE UP"
    if "BEARISH STRUCTURE" in upper:
        return "STRUCTURE DOWN"
    return "MIXED" if "MIXED" in upper else "NEUTRAL"


def _short_position(value: str) -> str:
    upper = str(value or "").upper()
    if "NEAR SUPPORT" in upper:
        return "SUPPORT PAAS"
    if "NEAR RESISTANCE" in upper:
        return "RESIST PAAS"
    if "BETWEEN" in upper:
        return "LEVELS KE BEECH"
    return "LEVEL NA" if "UNAVAILABLE" in upper else upper[:18]


def _short_volume(value: str) -> str:
    upper = str(value or "").upper()
    if "BULLISH" in upper:
        return "BUYING"
    if "BEARISH" in upper:
        return "SELLING"
    if "WEAK" in upper or "LOW" in upper:
        return "VOLUME WEAK"
    if "UNAVAILABLE" in upper:
        return "VOLUME NA"
    return "VOLUME MIXED"


def _short_heavy(value: str) -> str:
    upper = str(value or "").upper()
    if "BROAD BULLISH" in upper:
        return "BULLISH"
    if "NARROW BULLISH" in upper:
        return "SLIGHT UP"
    if "BROAD BEARISH" in upper:
        return "BEARISH"
    if "NARROW BEARISH" in upper:
        return "SLIGHT DOWN"
    if "MIXED" in upper or "FLAT" in upper:
        return "MIXED"
    return "NA"


def _short_institutional(value: str) -> str:
    upper = str(value or "").upper()
    if "SUPPORT" in upper or "BUYING" in upper:
        return "SUPPORT"
    if "PRESSURE" in upper or "SELLING" in upper:
        return "PRESSURE"
    if "MISSING" in upper or "UNAVAILABLE" in upper:
        return "NA"
    return "MIXED"


def _blank_pattern(family: str, name: str) -> PatternSignal:
    return PatternSignal(
        family=family,
        name=name,
        direction="NEUTRAL",
        stage="NONE",
        strength="NONE",
        confidence=0.0,
        bullish_score=0.0,
        bearish_score=0.0,
        neutral_score=0.0,
        level_label="",
        level_value=None,
        neckline=None,
        age_candles=None,
        reasons=(),
        status="UNAVAILABLE",
    )


def _wm_result(item: PatternSignal) -> str:
    if item.name == "NO VALID W/M" or item.direction == "NEUTRAL":
        return "KOI VALID W/M NAHI"
    state = "UP" if item.direction == "BULLISH" else "DOWN"
    if item.stage == "FORMING":
        head = f"{item.name} BAN RAHA"
    else:
        head = f"{item.name} {state} · {item.strength}"
    parts = [head]
    if item.level_value is not None:
        level_prefix = "S" if item.direction == "BULLISH" else "R"
        parts.append(f"{level_prefix} {item.level_value:,.0f}")
    if item.neckline is not None:
        parts.append(f"NL {item.neckline:,.0f}")
    return " · ".join(parts)


def _candle_result(item: PatternSignal) -> str:
    if item.name == "NO IMPORTANT CANDLE":
        return "KOI IMPORTANT CANDLE NAHI"
    parts = [item.name]
    if item.direction == "NEUTRAL":
        parts.append("NEUTRAL")
    else:
        parts.append(item.strength)
    if item.level_value is not None:
        parts.append("SUPPORT" if item.direction == "BULLISH" else "RESIST")
    return " · ".join(parts)


def _row(
    module: str,
    bullish: float | None,
    bearish: float | None,
    neutral: float | None,
    confidence: float,
    result: str,
) -> dict[str, Any]:
    return {
        "Module": module,
        "Bullish %": bullish,
        "Bearish %": bearish,
        "Neutral %": neutral,
        "Confidence %": round(clamp(confidence, 0.0, 100.0), 1),
        "Result": result,
    }


def build_compact_evidence_matrix(
    snapshot: MarketSnapshot,
    previous_snapshot: MarketSnapshot | None = None,
) -> list[dict[str, Any]]:
    """Build compact rows from the same authoritative snapshot."""

    pa3 = snapshot.price_action.three_minute
    pa15 = snapshot.price_action.fifteen_minute
    price_bull, price_bear, price_neutral = _weighted_scores(
        [
            (
                pa3.bullish_score,
                pa3.bearish_score,
                pa3.range_score,
                0.20 if pa3.status == "READY" else 0.0,
            ),
            (
                pa15.bullish_score,
                pa15.bearish_score,
                pa15.range_score,
                0.80 if pa15.status == "READY" else 0.0,
            ),
        ]
    )
    price_result = (
        f"{_dominant_label(price_bull, price_bear, price_neutral)} · "
        f"3m {_short_structure(pa3.structure)} · 15m {_short_structure(pa15.structure)}"
    )

    options = snapshot.option_intelligence
    option_bull, option_bear, option_neutral = _normalise(
        options.bullish_score, options.bearish_score, options.range_score
    )
    windows_ready = sum(item.status == "READY" for item in options.windows)
    option_result = (
        f"{_short_direction(options.market_bias)} · {_short_persistence(options.persistence)} · "
        f"{windows_ready}/3 · {_short_pcr(options.pcr.state)}"
    )

    from analysis.directional_core import indicator_scores
    indicator_bull, indicator_bear, indicator_neutral = _normalise(*indicator_scores(snapshot.indicators))
    indicator_confidence = 100.0 if snapshot.indicators.fifteen_minute.status == "READY" else 0.0
    indicator_result = (
        f"{_dominant_label(indicator_bull, indicator_bear, indicator_neutral)} · "
        f"15m {_short_ema(snapshot.indicators.fifteen_minute.ema_state)}"
    )

    level_scores = _level_scores(snapshot)
    volume_scores = _volume_scores(snapshot)
    barrier_scores = _barrier_scores(snapshot)
    levels_bull, levels_bear, levels_neutral = _weighted_scores(
        [(*level_scores, 0.35), (*volume_scores, 0.20), (*barrier_scores, 0.45)]
    )
    barrier_item = getattr(snapshot, "barrier_map", None)
    barrier_ready = str(getattr(barrier_item, "status", "")).upper() in {
        "READY",
        "PARTIAL",
    }
    levels_confidence = (
        (70.0 if snapshot.levels.status == "READY" else 0.0) * 0.35
        + snapshot.volume.confidence * 0.20
        + (float(getattr(getattr(barrier_item, "trading_range", None), "confidence", 0.0) or 0.0) if barrier_ready else 0.0) * 0.45
    )
    up_room = snapshot.levels.upside_room
    down_room = snapshot.levels.downside_room
    up_text = f"{up_room:.0f}" if up_room is not None else "—"
    down_text = f"{down_room:.0f}" if down_room is not None else "—"
    barrier = getattr(snapshot, "barrier_map", None)
    range_item = getattr(barrier, "trading_range", None)
    break_bias = str(getattr(range_item, "breakout_bias", "UNRESOLVED"))
    levels_result = (
        f"{_short_position(snapshot.levels.current_position)} · "
        f"{_short_volume(snapshot.volume.overall_view)} · {break_bias} · "
        f"UP {up_text} / DN {down_text}"
    )

    heavy_scores = _heavyweight_scores(snapshot)
    inst_scores = _institutional_scores(snapshot)
    inst_available = (
        snapshot.institutional_context.status != "MISSING"
        and snapshot.institutional_context.confidence > 0
    )
    heavy_bull, heavy_bear, heavy_neutral = heavy_scores
    move = getattr(snapshot.heavyweights, "recent_15m_move_pct", None)
    move_text = f"{move:+.3f}%" if move is not None else "NA"
    prior_move = (
        getattr(previous_snapshot.heavyweights, "recent_15m_move_pct", None)
        if previous_snapshot is not None
        else None
    )
    if move is not None and prior_move is not None:
        elapsed = max(
            0,
            round((snapshot.created_at - previous_snapshot.created_at).total_seconds() / 60),
        )
        delta_text = f" · {elapsed}m badlav {move - prior_move:+.3f}%"
    else:
        delta_text = " · badlav warming"
    heavy_result = (
        f"15m WEIGHTED {move_text}{delta_text} · current-day breadth "
        f"{getattr(snapshot.heavyweights, 'advancing', 0)} UP / {getattr(snapshot.heavyweights, 'declining', 0)} DOWN"
    )
    inst_bull, inst_bear, inst_neutral = inst_scores
    inst_result = (
        f"FII/DII {_short_institutional(snapshot.institutional_context.state)} · "
        f"{getattr(snapshot.institutional_context, 'observations', 0)}/15 SESSIONS"
        if inst_available
        else "FII/DII DATA MISSING (ZERO WEIGHT)"
    )

    big_bull, big_bear, big_neutral = _big_player_scores(snapshot)
    activity = getattr(snapshot, "big_player_activity", None)
    if activity is None:
        big_result = "BIG PLAYER DATA UNAVAILABLE"
        big_confidence = 0.0
    else:
        big_result = (
            f"{getattr(activity, 'direction', 'MIXED')} · "
            f"{float(getattr(activity, 'score', 0.0) or 0.0):.0f}/100 · "
            f"{int(getattr(activity, 'confirmation_count', 0) or 0)}/"
            f"{int(getattr(activity, 'confirmation_total', 0) or 0)}"
        )
        big_confidence = float(getattr(activity, "score", 0.0) or 0.0)

    vix = snapshot.vix_context
    news = getattr(snapshot, "news_context", None)
    session_code = str(getattr(snapshot.market_session, "code", "") or "")
    if snapshot.market_session.is_live:
        session_text = "MARKET LIVE"
    elif session_code == "PRE_OPEN":
        session_text = "PRE-OPEN"
    elif session_code == "CLOSED_OR_STALE_SESSION" or "NOT CONFIRMED" in str(
        getattr(snapshot.market_session, "label", "")
    ).upper():
        session_text = "SESSION NOT CONFIRMED"
    else:
        session_text = "MARKET CLOSED"
    vix_last = getattr(vix, "last_price", None)
    vix_change_value = getattr(vix, "change_pct", None)
    vix_value = f"{vix_last:.2f}" if vix_last is not None else "NA"
    vix_change = (
        f"{vix_change_value:+.2f}%" if vix_change_value is not None else "NA"
    )
    risk_result = (
        f"{session_text} · VIX {vix_value} ({vix_change}) · "
        f"{getattr(vix, 'regime', 'UNAVAILABLE')}/"
        f"{getattr(vix, 'movement', 'UNAVAILABLE')} · "
        f"{_short_direction(vix.seller_environment)}"
    )
    if news is None or news.status not in {"READY", "OLD"}:
        news_result = f"EVENT {snapshot.event_risk.level} · FRESH NEWS NA · ZERO DIRECTION WEIGHT"
        news_confidence = 0.0
    elif news.status == "OLD":
        news_result = f"EVENT {snapshot.event_risk.level} · NEWS OLD/ZERO WEIGHT · {news.bias}"
        news_confidence = 0.0
    elif news.status == "CONTEXT ONLY":
        news_result = f"EVENT {snapshot.event_risk.level} · NEWS CONTEXT/0 WEIGHT · {news.bias}"
        news_confidence = 0.0
    else:
        news_result = f"EVENT {snapshot.event_risk.level} · NEWS {news.risk_level}/{news.bias} · FRESH"
        news_confidence = 80.0

    patterns = getattr(snapshot, "patterns", None)
    wm = (
        patterns.wm_3m
        if patterns is not None
        else _blank_pattern("3M W/M", "NO VALID W/M")
    )
    candle = (
        patterns.candle_3m
        if patterns is not None
        else _blank_pattern("3M CANDLE", "NO IMPORTANT CANDLE")
    )

    return [
        _row(
            "Price Action",
            price_bull,
            price_bear,
            price_neutral,
            snapshot.price_action.confidence,
            price_result,
        ),
        _row(
            "3M W/M Pattern",
            wm.bullish_score,
            wm.bearish_score,
            wm.neutral_score,
            wm.confidence,
            _wm_result(wm),
        ),
        _row(
            "Special Candle",
            candle.bullish_score,
            candle.bearish_score,
            candle.neutral_score,
            candle.confidence,
            _candle_result(candle),
        ),
        _row(
            "OI & Options Flow",
            option_bull,
            option_bear,
            option_neutral,
            options.confidence,
            option_result,
        ),
        _row(
            "EMA / MACD / RSI",
            indicator_bull,
            indicator_bear,
            indicator_neutral,
            indicator_confidence,
            indicator_result,
        ),
        _row(
            "Barrier / Levels / Volume",
            levels_bull,
            levels_bear,
            levels_neutral,
            levels_confidence,
            levels_result,
        ),
        _row(
            "Big Player Activity",
            big_bull,
            big_bear,
            big_neutral,
            big_confidence,
            big_result,
        ),
        _row(
            "NIFTY Top-9",
            heavy_bull,
            heavy_bear,
            heavy_neutral,
            snapshot.heavyweights.confidence,
            heavy_result,
        ),
        _row(
            "FII/DII (15 Sessions)",
            inst_bull,
            inst_bear,
            inst_neutral,
            snapshot.institutional_context.confidence if inst_available else 0.0,
            inst_result,
        ),
        _row(
            "VIX / Data Integrity",
            None,
            None,
            None,
            _feed_confidence(snapshot),
            risk_result,
        ),
        _row(
            "News / Event Risk",
            None,
            None,
            None,
            news_confidence,
            news_result,
        ),
    ]
