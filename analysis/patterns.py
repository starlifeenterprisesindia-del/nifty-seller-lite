from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

from analysis.technical_utils import atr_value, clamp, completed_candles, confirmed_swings
from models import LevelBundle, PatternEvidenceBundle, PatternSignal, VolumeBundle


@dataclass(frozen=True)
class _LevelMatch:
    label: str
    value: float | None
    distance: float | None
    near: bool


@dataclass(frozen=True)
class _WMCandidate:
    name: str
    direction: str
    stage: str
    first_price: float
    second_price: float
    neckline: float
    second_index: int
    age_candles: int
    depth: float
    symmetry_gap: float


def _empty_signal(family: str, name: str, status: str) -> PatternSignal:
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
        status=status,
    )


def _current_session(frame: pd.DataFrame) -> pd.DataFrame:
    source = completed_candles(frame)
    if source.empty:
        return source
    dates = pd.to_datetime(source["timestamp"]).dt.date
    return source.loc[dates == dates.iloc[-1]].reset_index(drop=True)


def _distance_to_zone(value: float, lower: float, upper: float) -> float:
    if lower <= value <= upper:
        return 0.0
    return min(abs(value - lower), abs(value - upper))


def _nearest_level(
    *,
    value: float,
    side: str,
    levels: LevelBundle,
    tolerance: float,
) -> _LevelMatch:
    candidates: list[tuple[str, float, float, float]] = []
    if levels.status == "READY":
        if side == "SUPPORT":
            for label, item in (
                ("S", levels.immediate_support),
                ("SS", levels.strong_support),
            ):
                if item is not None:
                    candidates.append((label, float(item.midpoint), float(item.lower), float(item.upper)))
            if levels.opening_range_low is not None:
                raw = float(levels.opening_range_low)
                candidates.append(("ORL", raw, raw, raw))
            if levels.previous_day_low is not None:
                raw = float(levels.previous_day_low)
                candidates.append(("PDL", raw, raw, raw))
        else:
            for label, item in (
                ("R", levels.immediate_resistance),
                ("SR", levels.strong_resistance),
            ):
                if item is not None:
                    candidates.append((label, float(item.midpoint), float(item.lower), float(item.upper)))
            if levels.opening_range_high is not None:
                raw = float(levels.opening_range_high)
                candidates.append(("ORH", raw, raw, raw))
            if levels.previous_day_high is not None:
                raw = float(levels.previous_day_high)
                candidates.append(("PDH", raw, raw, raw))

    if not candidates:
        return _LevelMatch("", None, None, False)

    ranked = sorted(
        (
            (_distance_to_zone(value, lower, upper), label, midpoint)
            for label, midpoint, lower, upper in candidates
        ),
        key=lambda item: item[0],
    )
    distance, label, midpoint = ranked[0]
    return _LevelMatch(label, midpoint, distance, distance <= tolerance)


def _volume_direction(volume: VolumeBundle) -> str:
    if volume.status != "READY":
        return "UNAVAILABLE"
    text = volume.overall_view.upper()
    if "BULLISH" in text:
        return "BULLISH"
    if "BEARISH" in text:
        return "BEARISH"
    if "WEAK" in text or "LOW" in text:
        return "WEAK"
    return "NEUTRAL"


def _directional_scores(
    direction: str,
    stage: str,
    strength: str,
) -> tuple[float, float, float]:
    dominant = {
        ("FORMING", "NORMAL"): 52.0,
        ("FORMING", "STRONG"): 58.0,
        ("CONFIRMED", "NORMAL"): 64.0,
        ("CONFIRMED", "STRONG"): 74.0,
        ("CONFIRMED", "VERY STRONG"): 84.0,
    }.get((stage, strength), 55.0)
    neutral = 100.0 - dominant - 8.0
    if direction == "BULLISH":
        return dominant, 8.0, neutral
    if direction == "BEARISH":
        return 8.0, dominant, neutral
    return 0.0, 0.0, 100.0


def _strength(confidence: float, *, forming: bool = False) -> str:
    if forming:
        return "STRONG" if confidence >= 62 else "NORMAL"
    if confidence >= 80:
        return "VERY STRONG"
    if confidence >= 65:
        return "STRONG"
    return "NORMAL"


def _wm_candidates(source: pd.DataFrame, atr: float) -> list[_WMCandidate]:
    highs, lows = confirmed_swings(source)
    candidates: list[_WMCandidate] = []
    close = float(source.iloc[-1]["close"])
    pivot_tolerance = max(2.5, atr * 0.45)
    break_tolerance = max(0.5, atr * 0.08)
    minimum_depth = max(4.0, atr * 0.55)

    recent_lows = lows[-6:]
    for first, second in zip(recent_lows, recent_lows[1:]):
        gap = second.index - first.index
        if gap < 3 or gap > 18:
            continue
        between = [item for item in highs if first.index < item.index < second.index]
        if not between:
            continue
        neckline = max(item.price for item in between)
        symmetry = abs(second.price - first.price)
        depth = neckline - ((first.price + second.price) / 2.0)
        age = len(source) - 1 - second.index
        if symmetry > pivot_tolerance or depth < minimum_depth or age > 8:
            continue
        if close < min(first.price, second.price) - break_tolerance:
            continue
        if close > neckline + break_tolerance:
            stage = "CONFIRMED" if float(source.iloc[-2]["close"]) > neckline + break_tolerance else "BREAK DETECTED"
        elif age <= 4 and close >= min(first.price, second.price) + depth * 0.30:
            stage = "FORMING"
        else:
            continue
        candidates.append(
            _WMCandidate(
                name="W",
                direction="BULLISH",
                stage=stage,
                first_price=first.price,
                second_price=second.price,
                neckline=neckline,
                second_index=second.index,
                age_candles=age,
                depth=depth,
                symmetry_gap=symmetry,
            )
        )

    recent_highs = highs[-6:]
    for first, second in zip(recent_highs, recent_highs[1:]):
        gap = second.index - first.index
        if gap < 3 or gap > 18:
            continue
        between = [item for item in lows if first.index < item.index < second.index]
        if not between:
            continue
        neckline = min(item.price for item in between)
        symmetry = abs(second.price - first.price)
        depth = ((first.price + second.price) / 2.0) - neckline
        age = len(source) - 1 - second.index
        if symmetry > pivot_tolerance or depth < minimum_depth or age > 8:
            continue
        if close > max(first.price, second.price) + break_tolerance:
            continue
        if close < neckline - break_tolerance:
            stage = "CONFIRMED" if float(source.iloc[-2]["close"]) < neckline - break_tolerance else "BREAK DETECTED"
        elif age <= 4 and close <= max(first.price, second.price) - depth * 0.30:
            stage = "FORMING"
        else:
            continue
        candidates.append(
            _WMCandidate(
                name="M",
                direction="BEARISH",
                stage=stage,
                first_price=first.price,
                second_price=second.price,
                neckline=neckline,
                second_index=second.index,
                age_candles=age,
                depth=depth,
                symmetry_gap=symmetry,
            )
        )
    return candidates


def detect_wm_pattern(
    candles_3m: pd.DataFrame,
    levels: LevelBundle,
    volume: VolumeBundle,
) -> PatternSignal:
    source = _current_session(candles_3m)
    if len(source) < 12:
        return _empty_signal("3M W/M", "NO VALID W/M", f"INSUFFICIENT 3M CANDLES ({len(source)}/12)")
    atr = atr_value(source)
    if atr is None or atr <= 0:
        return _empty_signal("3M W/M", "NO VALID W/M", "ATR UNAVAILABLE")

    candidates = _wm_candidates(source, atr)
    if not candidates:
        return _empty_signal("3M W/M", "NO VALID W/M", "READY")

    candidate = sorted(
        candidates,
        key=lambda item: (item.second_index, item.stage == "CONFIRMED", item.depth),
        reverse=True,
    )[0]
    anchor = (candidate.first_price + candidate.second_price) / 2.0
    side = "SUPPORT" if candidate.direction == "BULLISH" else "RESISTANCE"
    level = _nearest_level(
        value=anchor,
        side=side,
        levels=levels,
        tolerance=max(5.0, atr * 0.75),
    )
    confidence = 43.0
    reasons: list[str] = [
        f"{candidate.name} {candidate.stage.lower()} on completed 3-minute candles",
        f"Pivot gap {candidate.symmetry_gap:.2f}; depth {candidate.depth:.2f}",
    ]
    if candidate.stage == "CONFIRMED":
        confidence += 20.0
    else:
        confidence += 5.0
    if level.near:
        confidence += 15.0
        reasons.append(f"Near {side.lower()} {level.value:.2f}")
    elif level.value is not None:
        confidence -= 6.0
    if candidate.symmetry_gap <= atr * 0.22:
        confidence += 6.0
    if candidate.depth >= atr * 1.15:
        confidence += 6.0
    volume_direction = _volume_direction(volume)
    if volume_direction == candidate.direction:
        confidence += 8.0
        reasons.append("Futures volume confirms direction")
    elif volume_direction in {"BULLISH", "BEARISH"}:
        confidence -= 8.0
        reasons.append("Futures volume does not confirm")
    if candidate.age_candles <= 2:
        confidence += 4.0
    elif candidate.age_candles > 5:
        confidence -= 8.0
    if candidate.stage == "FORMING":
        confidence = min(confidence, 69.0)
    confidence = round(clamp(confidence, 20.0, 92.0), 1)
    strength = _strength(confidence, forming=candidate.stage == "FORMING")
    bullish, bearish, neutral = _directional_scores(
        candidate.direction, candidate.stage, strength
    )
    return PatternSignal(
        family="3M W/M",
        name=candidate.name,
        direction=candidate.direction,
        stage=candidate.stage,
        strength=strength,
        confidence=confidence,
        bullish_score=bullish,
        bearish_score=bearish,
        neutral_score=neutral,
        level_label=level.label if level.near else "",
        level_value=level.value if level.near else None,
        neckline=round(candidate.neckline, 2),
        age_candles=candidate.age_candles,
        reasons=tuple(reasons[:4]),
        status="READY",
        detected_at=str(source.iloc[candidate.second_index]["timestamp"]),
        invalidation_level=min(candidate.first_price, candidate.second_price) if candidate.direction == "BULLISH" else max(candidate.first_price, candidate.second_price),
    )


def _relative_volume(source: pd.DataFrame) -> float | None:
    if "volume" not in source.columns or len(source) < 6:
        return None
    values = pd.to_numeric(source["volume"], errors="coerce").dropna()
    positive = values[values > 0]
    if len(positive) < 6:
        return None
    baseline = float(positive.iloc[-11:-1].median()) if len(positive) > 10 else float(positive.iloc[:-1].median())
    if baseline <= 0:
        return None
    return float(positive.iloc[-1]) / baseline


def _candle_pattern(source: pd.DataFrame) -> tuple[str, str, int] | None:
    if len(source) < 3:
        return None
    last = source.iloc[-1]
    prev = source.iloc[-2]
    prev2 = source.iloc[-3]

    def parts(row: pd.Series) -> tuple[float, float, float, float, float, bool, bool]:
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        full_range = max(high - low, 0.01)
        body = abs(close - open_price)
        upper = high - max(open_price, close)
        lower = min(open_price, close) - low
        return full_range, body, upper, lower, close, close > open_price, close < open_price

    lr, lb, lu, ll, lc, l_bull, l_bear = parts(last)
    pr, pb, _, _, pc, p_bull, p_bear = parts(prev)
    _, p2b, _, _, p2c, p2_bull, p2_bear = parts(prev2)
    lo = float(last["open"])
    po = float(prev["open"])
    p2o = float(prev2["open"])

    if p2_bear and pb <= max(p2b * 0.45, 0.15 * pr) and l_bull and lc >= (p2o + p2c) / 2.0:
        return "MORNING STAR", "BULLISH", 3
    if p2_bull and pb <= max(p2b * 0.45, 0.15 * pr) and l_bear and lc <= (p2o + p2c) / 2.0:
        return "EVENING STAR", "BEARISH", 3

    if p_bear and l_bull and lo <= pc and lc >= po and lb >= max(pb * 0.90, lr * 0.35):
        return "BULL ENGULF", "BULLISH", 2
    if p_bull and l_bear and lo >= pc and lc <= po and lb >= max(pb * 0.90, lr * 0.35):
        return "BEAR ENGULF", "BEARISH", 2

    if lb / lr <= 0.12:
        return "DOJI", "NEUTRAL", 1
    if ll >= max(lb * 2.0, lr * 0.45) and lu <= max(lb * 0.75, lr * 0.18) and lc >= float(last["low"]) + lr * 0.58:
        return "HAMMER", "BULLISH", 1
    if lu >= max(lb * 2.0, lr * 0.45) and ll <= max(lb * 0.75, lr * 0.18) and lc <= float(last["low"]) + lr * 0.42:
        return "SHOOTING STAR", "BEARISH", 1
    return None


def _detect_special_candle_geometry(
    candles_3m: pd.DataFrame,
    levels: LevelBundle,
    volume: VolumeBundle,
    timeframe: str = "3M",
) -> PatternSignal:
    timeframe = str(timeframe or "3M").upper()
    family = f"{timeframe} CANDLE"
    timeframe_words = {
        "3M": "3-minute",
        "5M": "5-minute",
        "15M": "15-minute",
    }.get(timeframe, timeframe)
    source = _current_session(candles_3m)
    if len(source) < 6:
        return _empty_signal(family, "NO IMPORTANT CANDLE", f"INSUFFICIENT {timeframe} CANDLES ({len(source)}/6)")
    atr = atr_value(source)
    if atr is None or atr <= 0:
        return _empty_signal(family, "NO IMPORTANT CANDLE", "ATR UNAVAILABLE")
    detected = _candle_pattern(source)
    if detected is None:
        return _empty_signal(family, "NO IMPORTANT CANDLE", "READY")

    name, direction, bars = detected
    recent = source.tail(bars)
    bullish = direction == "BULLISH"
    bearish = direction == "BEARISH"
    anchor = float(recent["low"].min()) if bullish else float(recent["high"].max()) if bearish else float(source.iloc[-1]["close"])
    if bullish:
        level = _nearest_level(value=anchor, side="SUPPORT", levels=levels, tolerance=max(5.0, atr * 0.70))
        side = "SUPPORT"
    elif bearish:
        level = _nearest_level(value=anchor, side="RESISTANCE", levels=levels, tolerance=max(5.0, atr * 0.70))
        side = "RESISTANCE"
    else:
        support = _nearest_level(value=anchor, side="SUPPORT", levels=levels, tolerance=max(5.0, atr * 0.60))
        resistance = _nearest_level(value=anchor, side="RESISTANCE", levels=levels, tolerance=max(5.0, atr * 0.60))
        choices = [item for item in (support, resistance) if item.value is not None]
        level = min(choices, key=lambda item: item.distance or 0.0) if choices else _LevelMatch("", None, None, False)
        side = "LEVEL"

    base = {
        "MORNING STAR": 60.0,
        "EVENING STAR": 60.0,
        "BULL ENGULF": 58.0,
        "BEAR ENGULF": 58.0,
        "HAMMER": 50.0,
        "SHOOTING STAR": 50.0,
        "DOJI": 38.0,
    }[name]
    confidence = base
    reasons: list[str] = [f"{name} on latest completed {timeframe_words} candle(s)"]
    if level.near:
        confidence += 16.0
        reasons.append(f"Near {side.lower()} {level.value:.2f}")
    elif name in {"HAMMER", "SHOOTING STAR", "DOJI"}:
        # These shapes are noisy in the middle of a range, so hide them rather than
        # filling the compact screen with low-quality pattern labels.
        return _empty_signal(family, "NO IMPORTANT CANDLE", "READY")
    else:
        confidence -= 6.0

    ratio = _relative_volume(source)
    if ratio is not None and ratio >= 1.20:
        confidence += min(10.0, (ratio - 1.0) * 12.0)
        reasons.append(f"Candle volume {ratio:.2f}x baseline")
    volume_direction = _volume_direction(volume)
    if direction in {"BULLISH", "BEARISH"} and volume_direction == direction:
        confidence += 7.0
        reasons.append("Futures volume confirms direction")
    elif direction in {"BULLISH", "BEARISH"} and volume_direction in {"BULLISH", "BEARISH"}:
        confidence -= 7.0
    if direction == "NEUTRAL":
        confidence = min(confidence, 58.0)
    confidence = round(clamp(confidence, 20.0, 90.0), 1)
    strength = _strength(confidence)
    if direction == "NEUTRAL":
        bull_score, bear_score, neutral_score = 12.0, 12.0, 76.0
        strength = "NORMAL"
    else:
        bull_score, bear_score, neutral_score = _directional_scores(direction, "CONFIRMED", strength)
    return PatternSignal(
        family=family,
        name=name,
        direction=direction,
        stage="DETECTED",
        strength=strength,
        confidence=confidence,
        bullish_score=bull_score,
        bearish_score=bear_score,
        neutral_score=neutral_score,
        level_label=level.label if level.near else "",
        level_value=level.value if level.near else None,
        neckline=None,
        age_candles=0,
        reasons=tuple(reasons[:4]),
        status="READY",
        detected_at=str(source.iloc[-1]["timestamp"]),
        invalidation_level=float(recent["low"].min()) if bullish else float(recent["high"].max()) if bearish else None,
    )


def detect_special_candle(candles_3m, levels, volume, timeframe="3M"):
    source = _current_session(candles_3m)
    current = _detect_special_candle_geometry(source, levels, volume, timeframe)
    if len(source) < 8:
        return current
    atr = atr_value(source) or 1.0
    for age in (1, 2, 3):
        prior = source.iloc[:-age]
        signal = _detect_special_candle_geometry(prior, levels, volume, timeframe)
        if signal.direction not in {"BULLISH", "BEARISH"} or signal.confidence < 65 or not signal.level_label:
            continue
        candle = prior.iloc[-1]
        # Tiny shapes in quiet noise must not qualify as strong triggers.
        if float(candle.high - candle.low) < atr * .6:
            continue
        sign = 1 if signal.direction == "BULLISH" else -1
        following = source.iloc[-age:]
        invalid = signal.invalidation_level
        if invalid is not None and any((float(x) - invalid) * sign < 0 for x in following.close):
            return replace(signal, stage="FAILED", strength="NONE", age_candles=age)
        trigger = float(candle.high) + atr * .08 if sign == 1 else float(candle.low) - atr * .08
        if (float(source.iloc[-1].close) - trigger) * sign > 0:
            return replace(signal, stage="CONFIRMED", age_candles=age, neckline=trigger,
                           reasons=(*signal.reasons[:3], "Subsequent completed candle confirmed trigger"))
    return current


def calculate_pattern_evidence(
    candles_3m: pd.DataFrame,
    levels: LevelBundle,
    volume: VolumeBundle,
    *,
    candles_5m: pd.DataFrame | None = None,
    candles_15m: pd.DataFrame | None = None,
) -> PatternEvidenceBundle:
    wm = detect_wm_pattern(candles_3m, levels, volume)
    candle_3m = detect_special_candle(candles_3m, levels, volume, "3M")
    candle_5m = (
        detect_special_candle(candles_5m, levels, volume, "5M")
        if candles_5m is not None
        else candle_3m
    )
    candle_15m = (
        detect_special_candle(candles_15m, levels, volume, "15M")
        if candles_15m is not None
        else None
    )
    candle = candle_3m
    usable = [
        item
        for item in (wm, candle)
        if item.status == "READY" and item.direction in {"BULLISH", "BEARISH"}
    ]
    if not usable:
        combined = "NEUTRAL"
        confidence = max(wm.confidence, candle.confidence)
    else:
        bull = sum(item.bullish_score * max(item.confidence, 1.0) for item in usable)
        bear = sum(item.bearish_score * max(item.confidence, 1.0) for item in usable)
        if abs(bull - bear) <= max(bull, bear) * 0.12:
            combined = "MIXED"
        else:
            combined = "BULLISH" if bull > bear else "BEARISH"
        confidence = sum(item.confidence for item in usable) / len(usable)
    as_of = None
    source = _current_session(candles_3m)
    if not source.empty:
        as_of = pd.Timestamp(source.iloc[-1]["timestamp"]).to_pydatetime()
    return PatternEvidenceBundle(
        as_of=as_of,
        wm_3m=wm,
        candle_3m=candle_3m,
        combined_direction=combined,
        combined_confidence=round(clamp(confidence, 0.0, 92.0), 1),
        status="READY" if not source.empty else "UNAVAILABLE",
        candle_5m=candle_5m,
        candle_15m=candle_15m,
    )
