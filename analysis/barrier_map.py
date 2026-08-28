from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import pandas as pd

from analysis.technical_utils import clamp, completed_candles
from config import CONFIG
from models import (
    BarrierMap,
    BarrierMapLevel,
    BarrierRangeContext,
    CoreMarketEvidence,
    HeavyweightBundle,
    IndicatorBundle,
    LevelBundle,
    MarketSession,
    MarketSpeedContext,
    OptionIntelligence,
    PriceActionBundle,
    VixContext,
    VolumeBundle,
)


@dataclass(frozen=True)
class _Anchor:
    price: float
    weight: float
    source: str
    side: str


@dataclass(frozen=True)
class _Cluster:
    side: str
    lower: float
    upper: float
    midpoint: float
    structural_score: float
    sources: tuple[str, ...]


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _zone_width(levels: LevelBundle) -> float:
    width = _number(levels.zone_width)
    return clamp(width if width and width > 0 else 10.0, 6.0, 24.0)


def _append_level(
    anchors: list[_Anchor],
    level: object | None,
    *,
    side: str,
    fallback: str,
) -> None:
    if level is None:
        return
    price = _number(getattr(level, "midpoint", None))
    if price is None or price <= 0:
        return
    strength = clamp(_number(getattr(level, "strength", None)) or 55.0, 25.0, 100.0)
    sources = tuple(str(item) for item in getattr(level, "sources", ()) if str(item).strip())
    label = " + ".join(sources[:3]) if sources else fallback
    anchors.append(_Anchor(price=price, weight=strength, source=label, side=side))


def _anchor(anchors: list[_Anchor], price: object, weight: float, source: str, side: str) -> None:
    value = _number(price)
    if value is not None and value > 0:
        anchors.append(_Anchor(value, weight, source, side))


def _build_anchors(
    *,
    levels: LevelBundle,
    options: OptionIntelligence,
    price_action: PriceActionBundle,
) -> list[_Anchor]:
    anchors: list[_Anchor] = []
    _append_level(anchors, levels.immediate_resistance, side="RESISTANCE", fallback="Immediate Resistance")
    _append_level(anchors, levels.strong_resistance, side="RESISTANCE", fallback="Strong Resistance")
    _append_level(anchors, levels.immediate_support, side="SUPPORT", fallback="Immediate Support")
    _append_level(anchors, levels.strong_support, side="SUPPORT", fallback="Strong Support")

    _anchor(anchors, levels.previous_day_high, 82.0, "Previous Day High", "RESISTANCE")
    _anchor(anchors, levels.opening_range_high, 76.0, "Opening Range High", "RESISTANCE")
    _anchor(anchors, levels.previous_day_low, 82.0, "Previous Day Low", "SUPPORT")
    _anchor(anchors, levels.opening_range_low, 76.0, "Opening Range Low", "SUPPORT")

    three = price_action.three_minute
    fifteen = price_action.fifteen_minute
    _anchor(anchors, fifteen.last_swing_high, 84.0, "15m Swing High", "RESISTANCE")
    _anchor(anchors, fifteen.prior_swing_high, 70.0, "15m Prior Swing High", "RESISTANCE")
    _anchor(anchors, three.last_swing_high, 68.0, "3m Swing High", "RESISTANCE")
    _anchor(anchors, three.prior_swing_high, 56.0, "3m Prior Swing High", "RESISTANCE")
    _anchor(anchors, fifteen.last_swing_low, 84.0, "15m Swing Low", "SUPPORT")
    _anchor(anchors, fifteen.prior_swing_low, 70.0, "15m Prior Swing Low", "SUPPORT")
    _anchor(anchors, three.last_swing_low, 68.0, "3m Swing Low", "SUPPORT")
    _anchor(anchors, three.prior_swing_low, 56.0, "3m Prior Swing Low", "SUPPORT")

    _anchor(anchors, options.ce_wall.strike, 90.0, "CE OI Wall", "RESISTANCE")
    _anchor(anchors, options.ce_wall.cluster_center, 94.0, "CE OI Cluster", "RESISTANCE")
    _anchor(anchors, options.pe_wall.strike, 90.0, "PE OI Wall", "SUPPORT")
    _anchor(anchors, options.pe_wall.cluster_center, 94.0, "PE OI Cluster", "SUPPORT")
    return anchors


def _clusters(anchors: Iterable[_Anchor], *, side: str, width: float, spot: float) -> list[_Cluster]:
    relevant = [item for item in anchors if item.side == side]
    # Keep a level only while spot is below/inside a resistance zone or above/inside a
    # support zone. Once a completed move clears the zone, the next barrier is promoted.
    allowance = width / 2.0
    if side == "RESISTANCE":
        relevant = [item for item in relevant if item.price >= spot - allowance]
    else:
        relevant = [item for item in relevant if item.price <= spot + allowance]
    relevant.sort(key=lambda item: item.price)
    if not relevant:
        return []

    merge_distance = max(12.0, width * 1.6)
    groups: list[list[_Anchor]] = []
    for item in relevant:
        if not groups or item.price - groups[-1][-1].price > merge_distance:
            groups.append([item])
        else:
            groups[-1].append(item)

    result: list[_Cluster] = []
    for group in groups:
        total = sum(max(1.0, item.weight) for item in group)
        midpoint = sum(item.price * max(1.0, item.weight) for item in group) / total
        source_tokens: list[str] = []
        for item in group:
            for token in item.source.split(" + "):
                token = token.strip()
                if token and token not in source_tokens:
                    source_tokens.append(token)
        mean_weight = sum(item.weight for item in group) / len(group)
        confluence = min(20.0, max(0, len(source_tokens) - 1) * 5.0)
        structural = clamp(mean_weight * 0.78 + 12.0 + confluence, 25.0, 97.0)
        result.append(
            _Cluster(
                side=side,
                lower=round(min(item.price for item in group) - width / 2.0, 2),
                upper=round(max(item.price for item in group) + width / 2.0, 2),
                midpoint=round(midpoint, 2),
                structural_score=round(structural, 1),
                sources=tuple(source_tokens[:8]),
            )
        )

    if side == "RESISTANCE":
        result.sort(key=lambda item: max(0.0, item.lower - spot))
    else:
        result.sort(key=lambda item: max(0.0, spot - item.upper))
    return result


def _weighted_available(parts: Iterable[tuple[float | None, float]], default: float = 50.0) -> float:
    total_value = 0.0
    total_weight = 0.0
    for value, weight in parts:
        if value is None:
            continue
        total_value += clamp(float(value), 0.0, 100.0) * weight
        total_weight += weight
    if total_weight <= 0:
        return default
    return total_value / total_weight


def _option_zone_scores(options: OptionIntelligence, cluster: _Cluster) -> tuple[float, float]:
    side = "CE" if cluster.side == "RESISTANCE" else "PE"
    rows = [
        row
        for row in options.flow_rows
        if str(row.get("side") or "").upper() == side
        and _number(row.get("strike")) is not None
        and cluster.lower - 25 <= float(row["strike"]) <= cluster.upper + 25
    ]
    wall = options.ce_wall if side == "CE" else options.pe_wall
    wall_near = any(
        value is not None and cluster.lower - 25 <= float(value) <= cluster.upper + 25
        for value in (wall.strike, wall.cluster_center)
    )

    if not rows:
        defense = 72.0 if wall_near else 50.0
        return defense, 100.0 - defense

    defend = 0.0
    attack = 0.0
    for row in rows:
        strength = max(0.2, _number(row.get("flow_strength")) or 0.2)
        classification = str(row.get("classification") or "")
        if side == "CE":
            if classification in {"SHORT BUILDUP", "LONG UNWINDING"}:
                defend += strength
            elif classification in {"SHORT COVERING", "LONG BUILDUP"}:
                attack += strength
        else:
            if classification in {"SHORT BUILDUP", "LONG UNWINDING"}:
                defend += strength
            elif classification in {"SHORT COVERING", "LONG BUILDUP"}:
                attack += strength
    total = defend + attack
    if total <= 0:
        defense = 72.0 if wall_near else 50.0
    else:
        defense = 50.0 + 50.0 * (defend - attack) / total
        if wall_near:
            defense += 8.0
    defense = clamp(defense, 0.0, 100.0)
    return defense, 100.0 - defense


def _reaction_score(
    candles_1m: pd.DataFrame,
    cluster: _Cluster,
    atr_reference: float | None,
) -> tuple[float | None, bool, bool]:
    source = completed_candles(candles_1m)
    if source.empty:
        return None, False, False
    source = source.copy().tail(120).reset_index(drop=True)
    atr = max(5.0, float(atr_reference or 20.0))
    reactions: list[float] = []
    recent_touch = False
    for index in range(len(source)):
        row = source.iloc[index]
        if cluster.side == "RESISTANCE":
            touched = float(row["high"]) >= cluster.lower and float(row["low"]) <= cluster.upper
        else:
            touched = float(row["low"]) <= cluster.upper and float(row["high"]) >= cluster.lower
        if not touched:
            continue
        if index >= len(source) - 4:
            recent_touch = True
        future = source.iloc[index + 1 : index + 4]
        if future.empty:
            continue
        if cluster.side == "RESISTANCE":
            excursion = max(0.0, cluster.lower - float(future["low"].min()))
        else:
            excursion = max(0.0, float(future["high"].max()) - cluster.upper)
        reactions.append(excursion / atr)
    if not reactions:
        return None, recent_touch, False
    average = sum(reactions) / len(reactions)
    score = clamp(average / 0.85 * 100.0, 0.0, 100.0)
    weakening = len(reactions) >= 2 and reactions[-1] < reactions[0] * 0.60
    return score, recent_touch, weakening


def _momentum_hold_score(side: str, price_action: PriceActionBundle, core: CoreMarketEvidence) -> float:
    three = price_action.three_minute
    fifteen = price_action.fifteen_minute
    if side == "RESISTANCE":
        pa = three.bearish_score * 0.60 + fifteen.bearish_score * 0.40
        directional = core.bearish_score
    else:
        pa = three.bullish_score * 0.60 + fifteen.bullish_score * 0.40
        directional = core.bullish_score
    return clamp(pa * 0.65 + directional * 0.35, 0.0, 100.0)


def _heavyweight_hold_score(side: str, heavyweights: HeavyweightBundle) -> float | None:
    if heavyweights.status != "READY":
        return None
    move = _number(heavyweights.weighted_move_pct)
    if move is None:
        return None
    # +/-0.75% weighted move is treated as an extreme intraday directional contribution.
    directional = clamp(50.0 + move / 0.75 * 50.0, 0.0, 100.0)
    return 100.0 - directional if side == "RESISTANCE" else directional


def _volume_hold_score(side: str, volume: VolumeBundle) -> float | None:
    three = volume.three_minute
    if three.status != "READY" or three.relative_volume is None:
        return None
    ratio = float(three.relative_volume)
    activity = clamp((ratio - 0.65) / (1.85 - 0.65) * 100.0, 0.0, 100.0)
    desired = "DOWN" if side == "RESISTANCE" else "UP"
    if three.price_direction == desired:
        return clamp(55.0 + activity * 0.45, 0.0, 100.0)
    if three.price_direction in {"UP", "DOWN"}:
        return clamp(45.0 - activity * 0.35, 0.0, 100.0)
    return 50.0


def _history_sample(history: list[dict], current: datetime, seconds: int) -> dict | None:
    tolerance = max(20.0, seconds * 0.45)
    candidates: list[tuple[float, dict]] = []
    for item in history:
        try:
            ts = datetime.fromisoformat(str(item.get("captured_at")))
        except (TypeError, ValueError):
            continue
        age = (current - ts).total_seconds()
        if max(10.0, seconds - tolerance) <= age <= seconds + tolerance:
            candidates.append((abs(age - seconds), item))
    return min(candidates, key=lambda pair: pair[0])[1] if candidates else None


def _history_delta(
    history: list[dict], current: datetime, seconds: int, current_value: float | None, key: str
) -> float | None:
    if current_value is None:
        return None
    sample = _history_sample(history, current, seconds)
    if sample is None:
        return None
    old = _number(sample.get(key))
    if old is None:
        return None
    return current_value - old


def _history_pct_change(
    history: list[dict], current: datetime, seconds: int, current_value: float | None, key: str
) -> float | None:
    if current_value is None:
        return None
    sample = _history_sample(history, current, seconds)
    if sample is None:
        return None
    old = _number(sample.get(key))
    if old is None or old <= 0:
        return None
    return (current_value - old) / old * 100.0


def _speed_component(move: float | None, expected: float) -> float | None:
    if move is None or expected <= 0:
        return None
    ratio = abs(move) / expected
    # 0.5x typical move -> calm, 1x -> active, 2x -> danger territory.
    return clamp((ratio - 0.35) / (2.0 - 0.35) * 100.0, 0.0, 100.0)


def _option_shock_score(options: OptionIntelligence) -> float:
    row_count = max(1, len(options.flow_rows))
    scores: list[float] = []
    thresholds = {60: 1.6, 180: 3.2, 300: 5.0}
    for window in options.windows:
        if window.status != "READY":
            continue
        total = abs(float(window.ce_premium_delta or 0.0)) + abs(float(window.pe_premium_delta or 0.0))
        average = total / row_count
        threshold = thresholds.get(window.target_seconds, 4.0)
        scores.append(clamp(average / threshold * 70.0, 0.0, 100.0))
    return round(max(scores) if scores else 0.0, 1)


def _volume_speed_score(volume: VolumeBundle) -> tuple[float | None, float | None]:
    ratios = [
        float(item.relative_volume)
        for item in (volume.three_minute, volume.fifteen_minute)
        if item.status == "READY" and item.relative_volume is not None
    ]
    if not ratios:
        return None, None
    ratio = max(ratios)
    score = clamp((ratio - 0.65) / (2.20 - 0.65) * 100.0, 0.0, 100.0)
    return score, ratio


def _vix_risk_score(vix: VixContext, change_5m: float | None, change_15m: float | None) -> float:
    """Estimate volatility danger without treating a VIX fall like a VIX spike.

    India VIX level supplies the base regime. Fast positive VIX changes add strong risk;
    fast negative changes can still indicate a regime transition but receive a much
    smaller shock contribution because falling implied volatility normally cools, rather
    than amplifies, option-seller danger.
    """

    base = {"LOW": 20.0, "NORMAL": 30.0, "ELEVATED": 60.0, "HIGH": 82.0}.get(vix.regime, 35.0)

    def shock_component(change: float, rise_threshold: float) -> float:
        if change >= 0:
            return clamp(change / rise_threshold * 100.0, 0.0, 100.0)
        # A rapid VIX fall is informative, but it should not be scored as equally
        # dangerous as the same-sized rise. Cap the cooling-transition contribution.
        return clamp(abs(change) / rise_threshold * 35.0, 0.0, 35.0)

    shocks: list[float] = []
    if change_5m is not None:
        shocks.append(shock_component(change_5m, 4.0))
    if change_15m is not None:
        shocks.append(shock_component(change_15m, 6.0))
    if not shocks and vix.change_pct is not None:
        shocks.append(shock_component(float(vix.change_pct), 8.0) * 0.65)
    shock = max(shocks) if shocks else 20.0
    return clamp(base * 0.55 + shock * 0.45, 0.0, 100.0)


def _time_risk(now: datetime, expiry: str | None) -> float:
    minute = now.hour * 60 + now.minute
    open_min = 9 * 60 + 15
    close_min = 15 * 60 + 30
    score = 10.0
    if open_min <= minute <= open_min + 20:
        score = 100.0
    elif open_min + 20 < minute <= 10 * 60:
        score = 45.0
    elif 14 * 60 + 45 <= minute <= close_min:
        score = 80.0
    if expiry:
        try:
            expiry_date = pd.Timestamp(expiry).date()
            if expiry_date == now.date():
                score = max(score, 100.0)
        except Exception:
            pass
    return score


def _market_speed(
    *,
    spot: float,
    current: datetime,
    option_history: list[dict],
    current_vix: float | None,
    vix: VixContext,
    price_action: PriceActionBundle,
    volume: VolumeBundle,
    options: OptionIntelligence,
    heavyweights: HeavyweightBundle,
    expiry: str | None,
) -> MarketSpeedContext:
    move_1m = _history_delta(option_history, current, 60, spot, "spot")
    move_3m = _history_delta(option_history, current, 180, spot, "spot")
    move_5m = _history_delta(option_history, current, 300, spot, "spot")
    vix_5m = _history_pct_change(option_history, current, 300, current_vix, "vix")
    vix_15m = _history_pct_change(option_history, current, 900, current_vix, "vix")

    atr3 = float(price_action.three_minute.atr14 or 20.0)
    expected_1m = max(3.0, atr3 / math.sqrt(3.0))
    expected_3m = max(5.0, atr3)
    expected_5m = max(6.0, atr3 * math.sqrt(5.0 / 3.0))
    movement_score = _weighted_available(
        (
            (_speed_component(move_1m, expected_1m), 0.30),
            (_speed_component(move_3m, expected_3m), 0.40),
            (_speed_component(move_5m, expected_5m), 0.30),
        ),
        default=20.0,
    )
    volume_score, volume_ratio = _volume_speed_score(volume)
    option_shock = _option_shock_score(options)
    vix_score = _vix_risk_score(vix, vix_5m, vix_15m)

    sync_score = 50.0
    if heavyweights.status == "READY":
        total = max(1, heavyweights.advancing + heavyweights.declining + heavyweights.unchanged)
        dominant = max(heavyweights.advancing, heavyweights.declining) / total
        sync_score = clamp((dominant - 0.45) / 0.55 * 100.0, 0.0, 100.0)
    time_score = _time_risk(current, expiry)
    danger = _weighted_available(
        (
            (movement_score, 0.35),
            (volume_score, 0.20),
            (option_shock, 0.20),
            (vix_score, 0.15),
            (sync_score, 0.05),
            (time_score, 0.05),
        ),
        default=25.0,
    )

    directional_values: list[tuple[float, float]] = []
    for move, weight in ((move_1m, 0.25), (move_3m, 0.40), (move_5m, 0.35)):
        if move is not None:
            directional_values.append((move, weight))
    directional = sum(value * weight for value, weight in directional_values)
    directional += (options.bullish_score - options.bearish_score) * 0.08
    if heavyweights.weighted_move_pct is not None:
        directional += float(heavyweights.weighted_move_pct) * 12.0
    direction = "UP" if directional > 1.5 else "DOWN" if directional < -1.5 else "MIXED"

    if danger >= 80:
        state = "DANGER"
    elif danger >= 60:
        state = "FAST"
    elif danger >= 40:
        state = "ACTIVE"
    else:
        state = "NORMAL"

    reasons: list[str] = []
    if move_3m is not None:
        reasons.append(f"3m move {move_3m:+.1f} pts")
    if volume_ratio is not None:
        reasons.append(f"volume {volume_ratio:.2f}x baseline")
    if vix_5m is not None:
        reasons.append(f"VIX 5m {vix_5m:+.1f}%")
    if option_shock >= 60:
        reasons.append("option premium movement fast")
    if time_score >= 80:
        reasons.append("high-risk market time window")
    return MarketSpeedContext(
        score=round(danger, 1),
        state=state,
        direction=direction,
        move_1m_points=round(move_1m, 1) if move_1m is not None else None,
        move_3m_points=round(move_3m, 1) if move_3m is not None else None,
        move_5m_points=round(move_5m, 1) if move_5m is not None else None,
        vix_change_5m_pct=round(vix_5m, 2) if vix_5m is not None else None,
        vix_change_15m_pct=round(vix_15m, 2) if vix_15m is not None else None,
        volume_ratio=round(volume_ratio, 2) if volume_ratio is not None else None,
        option_shock_score=option_shock,
        reasons=tuple(reasons[:5]),
        status="READY" if directional_values or options.status in {"READY", "WARMING UP"} else "WARMING UP",
    )


def _vix_expected_moves(
    *, spot: float, vix: VixContext, now: datetime, market_session: MarketSession
) -> tuple[float | None, float | None, str]:
    if vix.last_price is None or vix.last_price <= 0:
        return None, None, "UNAVAILABLE"
    daily = spot * (float(vix.last_price) / 100.0) / math.sqrt(252.0)
    remaining = None
    if market_session.is_live:
        close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        minutes_left = clamp((close - now).total_seconds() / 60.0, 0.0, 375.0)
        remaining = daily * math.sqrt(minutes_left / 375.0) if minutes_left > 0 else 0.0
    risk = "HIGH" if vix.regime == "HIGH" or vix.movement == "RISING FAST" else "ELEVATED" if vix.regime == "ELEVATED" or vix.movement == "RISING" else "NORMAL"
    return round(daily, 1), round(remaining, 1) if remaining is not None else None, risk


def _proximity_score(cluster: _Cluster, spot: float) -> tuple[float, float]:
    distance = max(0.0, cluster.lower - spot) if cluster.side == "RESISTANCE" else max(0.0, spot - cluster.upper)
    score = clamp((CONFIG.pretouch_watch_distance_points - distance) / CONFIG.pretouch_watch_distance_points * 100.0, 0.0, 100.0)
    return score, distance


def _toward_barrier_momentum(side: str, price_action: PriceActionBundle, core: CoreMarketEvidence) -> float:
    if side == "RESISTANCE":
        return clamp(price_action.three_minute.bullish_score * 0.55 + price_action.fifteen_minute.bullish_score * 0.25 + core.bullish_score * 0.20, 0.0, 100.0)
    return clamp(price_action.three_minute.bearish_score * 0.55 + price_action.fifteen_minute.bearish_score * 0.25 + core.bearish_score * 0.20, 0.0, 100.0)


def _heavyweight_break_score(side: str, heavyweights: HeavyweightBundle) -> float | None:
    hold = _heavyweight_hold_score(side, heavyweights)
    return None if hold is None else 100.0 - hold


def _volume_break_score(side: str, volume: VolumeBundle) -> float | None:
    hold = _volume_hold_score(side, volume)
    return None if hold is None else 100.0 - hold


def _barrier_level(
    *,
    cluster: _Cluster,
    label: str,
    spot: float,
    candles_1m: pd.DataFrame,
    price_action: PriceActionBundle,
    core: CoreMarketEvidence,
    volume: VolumeBundle,
    options: OptionIntelligence,
    heavyweights: HeavyweightBundle,
    vix_risk_score: float,
) -> BarrierMapLevel:
    option_defense, option_attack = _option_zone_scores(options, cluster)
    reaction, recent_touch, weakening = _reaction_score(candles_1m, cluster, price_action.three_minute.atr14)
    momentum_hold = _momentum_hold_score(cluster.side, price_action, core)
    heavy_hold = _heavyweight_hold_score(cluster.side, heavyweights)
    volume_hold = _volume_hold_score(cluster.side, volume)
    strength = _weighted_available(
        (
            (cluster.structural_score, 0.35),
            (option_defense, 0.25),
            (reaction, 0.15),
            (momentum_hold, 0.10),
            (heavy_hold, 0.10),
            (volume_hold, 0.05),
        ),
        default=cluster.structural_score,
    )
    proximity, distance = _proximity_score(cluster, spot)
    toward = _toward_barrier_momentum(cluster.side, price_action, core)
    heavy_break = _heavyweight_break_score(cluster.side, heavyweights)
    volume_break = _volume_break_score(cluster.side, volume)
    weakening_score = None if reaction is None else clamp(100.0 - reaction + (20.0 if weakening else 0.0), 0.0, 100.0)
    break_pressure = _weighted_available(
        (
            (proximity, 0.15),
            (toward, 0.20),
            (option_attack, 0.20),
            (heavy_break, 0.10),
            (volume_break, 0.10),
            (weakening_score, 0.15),
            (vix_risk_score, 0.10),
        ),
        default=40.0,
    )

    inside = cluster.lower <= spot <= cluster.upper
    hold_margin = strength - break_pressure
    if inside:
        state = "TESTING"
    elif distance <= CONFIG.pretouch_warning_distance_points and hold_margin >= 15:
        state = "HOLDING / STRONG"
    elif recent_touch and reaction is not None and reaction >= 65 and break_pressure < 65:
        state = "HOLDING"
    elif break_pressure >= 75 or (weakening and break_pressure > strength):
        state = "WEAKENING / BREAK RISK"
    elif distance <= CONFIG.pretouch_warning_distance_points:
        state = "APPROACHING"
    elif distance <= CONFIG.pretouch_watch_distance_points:
        state = "AHEAD"
    else:
        state = "FAR"

    source_text = " + ".join(cluster.sources[:4]) or "market structure"
    if cluster.side == "RESISTANCE":
        explanation = f"Upar barrier: {source_text}. Strength {strength:.0f}/100; break pressure {break_pressure:.0f}/100."
    else:
        explanation = f"Neeche barrier: {source_text}. Strength {strength:.0f}/100; break pressure {break_pressure:.0f}/100."
    return BarrierMapLevel(
        label=label,
        side=cluster.side,
        lower=cluster.lower,
        upper=cluster.upper,
        midpoint=cluster.midpoint,
        strength=round(strength, 1),
        break_pressure=round(break_pressure, 1),
        distance_points=round(distance, 1),
        state=state,
        sources=cluster.sources,
        explanation=explanation,
    )


def _range_context(
    *,
    spot: float,
    support: BarrierMapLevel | None,
    resistance: BarrierMapLevel | None,
    next_support: BarrierMapLevel | None,
    next_resistance: BarrierMapLevel | None,
    options: OptionIntelligence,
    core: CoreMarketEvidence,
    speed: MarketSpeedContext,
) -> BarrierRangeContext:
    if support is None or resistance is None or resistance.midpoint <= support.midpoint:
        return BarrierRangeContext(
            lower=support.midpoint if support else None,
            upper=resistance.midpoint if resistance else None,
            confidence=0.0,
            position_pct=None,
            state="UNRESOLVED",
            upside_next_lower=None,
            upside_next_upper=None,
            downside_next_lower=None,
            downside_next_upper=None,
            breakout_bias="UNRESOLVED",
            explanation="Support aur resistance dono reliable tarah resolve nahi hue.",
        )

    if support.upper >= resistance.lower:
        return BarrierRangeContext(
            lower=None, upper=None, confidence=0.0, position_pct=None,
            state="OVERLAPPING ZONES", upside_next_lower=None, upside_next_upper=None,
            downside_next_lower=None, downside_next_upper=None,
            breakout_bias="UNCLEAR",
            explanation="Support/resistance zones overlap — clear trading range nahi. Original levels unchanged; outer boundary reaction ka wait.",
        )

    lower = support.midpoint
    upper = resistance.midpoint
    position = clamp((spot - lower) / max(upper - lower, 1.0) * 100.0, 0.0, 100.0)
    max_break = max(support.break_pressure, resistance.break_pressure)
    base = (
        min(support.strength, resistance.strength) * 0.35
        + options.range_score * 0.25
        + core.range_score * 0.20
        + (100.0 - max_break) * 0.20
    )
    if speed.score > 55:
        base -= (speed.score - 55.0) * 0.35
    confidence = clamp(base, 0.0, 95.0)
    if speed.score >= 80 or max_break >= 82:
        state = "RANGE BREAK DANGER"
    elif max_break >= 70:
        state = "RANGE BREAK RISK"
    elif confidence >= 75:
        state = "STRONG RANGE"
    elif confidence >= 58:
        state = "RANGE ACTIVE"
    else:
        state = "RANGE WEAK"

    difference = resistance.break_pressure - support.break_pressure
    breakout_bias = "UPSIDE RISK" if difference >= 10 else "DOWNSIDE RISK" if difference <= -10 else "BALANCED"
    explanation = (
        f"Current probable range {lower:,.0f}–{upper:,.0f}; price range ke {position:.0f}% position par hai. "
        f"Range confidence {confidence:.0f}/100; break bias {breakout_bias}."
    )
    return BarrierRangeContext(
        lower=round(lower, 2),
        upper=round(upper, 2),
        confidence=round(confidence, 1),
        position_pct=round(position, 1),
        state=state,
        upside_next_lower=round(resistance.upper, 2) if next_resistance else None,
        upside_next_upper=round(next_resistance.lower, 2) if next_resistance else None,
        downside_next_lower=round(next_support.upper, 2) if next_support else None,
        downside_next_upper=round(support.lower, 2) if next_support else None,
        breakout_bias=breakout_bias,
        explanation=explanation,
    )


def calculate_barrier_map(
    *,
    spot: float,
    captured_at: datetime,
    market_session: MarketSession,
    expiry: str | None,
    candles_1m: pd.DataFrame,
    levels: LevelBundle,
    indicators: IndicatorBundle,
    price_action: PriceActionBundle,
    core: CoreMarketEvidence,
    volume: VolumeBundle,
    options: OptionIntelligence,
    heavyweights: HeavyweightBundle,
    vix: VixContext,
    option_history: list[dict],
) -> BarrierMap:
    del indicators  # levels already contain EMA-derived structural zones.
    if spot <= 0:
        empty_speed = MarketSpeedContext(0.0, "UNAVAILABLE", "MIXED", None, None, None, None, None, None, 0.0, (), "UNAVAILABLE")
        empty_range = BarrierRangeContext(None, None, 0.0, None, "UNRESOLVED", None, None, None, None, "UNRESOLVED", "Spot unavailable.")
        return BarrierMap(None, None, None, None, None, empty_range, empty_speed, None, None, "UNAVAILABLE", "Barrier map unavailable.", "UNAVAILABLE")

    current_vix = _number(vix.last_price)
    speed = _market_speed(
        spot=spot,
        current=captured_at,
        option_history=option_history,
        current_vix=current_vix,
        vix=vix,
        price_action=price_action,
        volume=volume,
        options=options,
        heavyweights=heavyweights,
        expiry=expiry,
    )
    _, _, _vix_risk = _vix_expected_moves(spot=spot, vix=vix, now=captured_at, market_session=market_session)
    vix_5 = speed.vix_change_5m_pct
    vix_15 = speed.vix_change_15m_pct
    vix_score = _vix_risk_score(vix, vix_5, vix_15)

    anchors = _build_anchors(levels=levels, options=options, price_action=price_action)
    width = _zone_width(levels)
    resistance_clusters = _clusters(anchors, side="RESISTANCE", width=width, spot=spot)
    support_clusters = _clusters(anchors, side="SUPPORT", width=width, spot=spot)

    resistance_levels = [
        _barrier_level(
            cluster=cluster,
            label=f"R{index + 1}",
            spot=spot,
            candles_1m=candles_1m,
            price_action=price_action,
            core=core,
            volume=volume,
            options=options,
            heavyweights=heavyweights,
            vix_risk_score=vix_score,
        )
        for index, cluster in enumerate(resistance_clusters[:3])
    ]
    support_levels = [
        _barrier_level(
            cluster=cluster,
            label=f"S{index + 1}",
            spot=spot,
            candles_1m=candles_1m,
            price_action=price_action,
            core=core,
            volume=volume,
            options=options,
            heavyweights=heavyweights,
            vix_risk_score=vix_score,
        )
        for index, cluster in enumerate(support_clusters[:3])
    ]
    nearest_r = resistance_levels[0] if resistance_levels else None
    next_r = resistance_levels[1] if len(resistance_levels) > 1 else None
    nearest_s = support_levels[0] if support_levels else None
    next_s = support_levels[1] if len(support_levels) > 1 else None

    trading_range = _range_context(
        spot=spot,
        support=nearest_s,
        resistance=nearest_r,
        next_support=next_s,
        next_resistance=next_r,
        options=options,
        core=core,
        speed=speed,
    )
    daily_move, remaining_move, vix_risk = _vix_expected_moves(
        spot=spot, vix=vix, now=captured_at, market_session=market_session
    )

    if nearest_r and nearest_s:
        if trading_range.breakout_bias == "UPSIDE RISK":
            path = (
                f"R1 ko bachane ki taakat {nearest_r.strength:.0f}, todne ka pressure "
                f"{nearest_r.break_pressure:.0f}—upar break risk; "
                + (f"next R {next_r.lower:,.0f}–{next_r.upper:,.0f}." if next_r else "confirmation tak WAIT.")
            )
        elif trading_range.breakout_bias == "DOWNSIDE RISK":
            path = (
                f"S1 ko bachane ki taakat {nearest_s.strength:.0f}, todne ka pressure "
                f"{nearest_s.break_pressure:.0f}—neeche break risk; "
                + (f"next S {next_s.lower:,.0f}–{next_s.upper:,.0f}." if next_s else "confirmation tak WAIT.")
            )
        else:
            path = "Dono taraf takkar barabar hai—confirmation tak WAIT."
        summary = (
            f"Range {nearest_s.midpoint:,.0f}–{nearest_r.midpoint:,.0f} | "
            f"Speed {speed.state} {speed.score:.0f}/100. {path}"
        )
    else:
        summary = f"Barrier map partial hai | Market speed {speed.state} {speed.score:.0f}/100."

    if trading_range.state == "OVERLAPPING ZONES":
        summary = trading_range.explanation

    return BarrierMap(
        current_price=round(spot, 2),
        nearest_resistance=nearest_r,
        next_resistance=next_r,
        nearest_support=nearest_s,
        next_support=next_s,
        trading_range=trading_range,
        market_speed=speed,
        vix_expected_daily_move_points=daily_move,
        vix_expected_remaining_move_points=remaining_move,
        vix_risk=vix_risk,
        summary=summary,
        status="READY" if nearest_r or nearest_s else "PARTIAL",
    )
