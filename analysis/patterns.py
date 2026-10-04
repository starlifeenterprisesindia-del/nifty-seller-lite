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



def _library_candle_parts(row: pd.Series) -> dict[str, float | bool]:
    """Small, allocation-light OHLC geometry helper used by the shadow library."""
    o = float(row["open"])
    h = float(row["high"])
    l = float(row["low"])
    c = float(row["close"])
    rng = max(h - l, 0.01)
    body = abs(c - o)
    upper = max(0.0, h - max(o, c))
    lower = max(0.0, min(o, c) - l)
    return {
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "range": rng,
        "body": body,
        "upper": upper,
        "lower": lower,
        "bull": c > o,
        "bear": c < o,
    }


def _library_trend(source: pd.DataFrame, end_index: int) -> str:
    """Context before a pattern; avoids calling a hammer after a rally a hammer."""
    start = max(0, end_index - 5)
    prior = source.iloc[start:end_index]
    if len(prior) < 3:
        return "FLAT"
    closes = pd.to_numeric(prior["close"], errors="coerce").dropna()
    if len(closes) < 3:
        return "FLAT"
    ranges = (
        pd.to_numeric(prior["high"], errors="coerce")
        - pd.to_numeric(prior["low"], errors="coerce")
    ).dropna()
    typical = float(ranges.median()) if not ranges.empty else 1.0
    delta = float(closes.iloc[-1] - closes.iloc[0])
    threshold = max(typical * 0.45, 0.01)
    if delta > threshold:
        return "UPTREND"
    if delta < -threshold:
        return "DOWNTREND"
    return "FLAT"


def _library_matches_at(
    source: pd.DataFrame,
    index: int,
    timeframe: str,
) -> list[dict[str, object]]:
    """Detect standard candle geometry at one completed bar.

    This library is intentionally SHADOW/DISPLAY ONLY.  It does not feed the
    canonical One Brain, alerts, strategy selection or execution gate.
    """
    if index < 0 or index >= len(source):
        return []
    row = source.iloc[index]
    cur = _library_candle_parts(row)
    trend = _library_trend(source, index)
    timeframe = str(timeframe).upper()
    ts = str(row.get("timestamp", ""))
    matches: list[dict[str, object]] = []

    def add(name: str, direction: str, quality: float, note: str) -> None:
        key = (name, direction)
        if any((item["name"], item["direction"]) == key for item in matches):
            return
        matches.append(
            {
                "name": name,
                "direction": direction,
                "quality": round(clamp(float(quality), 0.0, 92.0), 1),
                "context": trend,
                "timeframe": timeframe,
                "detected_at": ts,
                "close": round(float(cur["close"]), 2),
                "note": note,
                "mode": "SHADOW ONLY",
                "decision_weight": 0,
            }
        )

    rng = float(cur["range"])
    body = float(cur["body"])
    upper = float(cur["upper"])
    lower = float(cur["lower"])
    bull = bool(cur["bull"])
    bear = bool(cur["bear"])
    body_ratio = body / rng
    upper_ratio = upper / rng
    lower_ratio = lower / rng

    # Single-candle families.
    if body_ratio <= 0.10:
        if lower_ratio >= 0.60 and upper_ratio <= 0.15:
            add("DRAGONFLY DOJI", "BULLISH", 62, "Long lower rejection; context confirmation required")
        elif upper_ratio >= 0.60 and lower_ratio <= 0.15:
            add("GRAVESTONE DOJI", "BEARISH", 62, "Long upper rejection; context confirmation required")
        else:
            add("DOJI", "NEUTRAL", 45, "Indecision candle; next completed candle required")

    if body_ratio >= 0.80 and upper_ratio <= 0.12 and lower_ratio <= 0.12:
        add(
            "BULLISH MARUBOZU" if bull else "BEARISH MARUBOZU" if bear else "MARUBOZU",
            "BULLISH" if bull else "BEARISH" if bear else "NEUTRAL",
            70,
            "Large body with very small wicks",
        )

    lower_rejection = lower >= max(body * 2.0, rng * 0.45) and upper <= max(body * 0.75, rng * 0.18)
    upper_rejection = upper >= max(body * 2.0, rng * 0.45) and lower <= max(body * 0.75, rng * 0.18)
    if lower_rejection:
        if trend == "DOWNTREND":
            add("HAMMER", "BULLISH", 68, "Lower-wick rejection after decline")
        elif trend == "UPTREND":
            add("HANGING MAN", "BEARISH", 58, "Hammer geometry after rally; bearish confirmation needed")
        else:
            add("BULLISH PIN BAR", "BULLISH", 56, "Lower-wick rejection in neutral context")
    if upper_rejection:
        if trend == "UPTREND":
            add("SHOOTING STAR", "BEARISH", 68, "Upper-wick rejection after rally")
        elif trend == "DOWNTREND":
            add("INVERTED HAMMER", "BULLISH", 58, "Upper-wick rejection after decline; confirmation needed")
        else:
            add("BEARISH PIN BAR", "BEARISH", 56, "Upper-wick rejection in neutral context")

    if index >= 1:
        prev = _library_candle_parts(source.iloc[index - 1])
        po, pc = float(prev["open"]), float(prev["close"])
        prev_body = float(prev["body"])
        prev_rng = float(prev["range"])

        if bool(prev["bear"]) and bull and float(cur["open"]) <= pc and float(cur["close"]) >= po and body >= max(prev_body * 0.90, rng * 0.35):
            add("BULLISH ENGULFING", "BULLISH", 74, "Bull body engulfs prior bear body")
        if bool(prev["bull"]) and bear and float(cur["open"]) >= pc and float(cur["close"]) <= po and body >= max(prev_body * 0.90, rng * 0.35):
            add("BEARISH ENGULFING", "BEARISH", 74, "Bear body engulfs prior bull body")

        midpoint = (po + pc) / 2.0
        if bool(prev["bear"]) and bull and float(cur["close"]) > midpoint and float(cur["close"]) < po:
            add("PIERCING LINE", "BULLISH", 64, "Bull recovery closes above prior bear midpoint")
        if bool(prev["bull"]) and bear and float(cur["close"]) < midpoint and float(cur["close"]) > po:
            add("DARK CLOUD COVER", "BEARISH", 64, "Bear reversal closes below prior bull midpoint")

        if float(cur["high"]) < float(prev["high"]) and float(cur["low"]) > float(prev["low"]):
            add("INSIDE BAR", "NEUTRAL", 52, "Compression; breakout direction not confirmed")
        if float(cur["high"]) > float(prev["high"]) and float(cur["low"]) < float(prev["low"]):
            direction = "BULLISH" if bull else "BEARISH" if bear else "NEUTRAL"
            add("OUTSIDE BAR", direction, 60, "Range engulfs prior candle; close sets directional lean")

        tolerance = max(min(prev_rng, rng) * 0.10, 0.01)
        if abs(float(cur["low"]) - float(prev["low"])) <= tolerance and bool(prev["bear"]) and bull:
            add("TWEEZER BOTTOM", "BULLISH", 62, "Two-bar low rejection")
        if abs(float(cur["high"]) - float(prev["high"])) <= tolerance and bool(prev["bull"]) and bear:
            add("TWEEZER TOP", "BEARISH", 62, "Two-bar high rejection")

    if index >= 2:
        a = _library_candle_parts(source.iloc[index - 2])
        b = _library_candle_parts(source.iloc[index - 1])
        c = cur
        b_small = float(b["body"]) <= max(float(a["body"]) * 0.45, float(b["range"]) * 0.20)
        a_mid = (float(a["open"]) + float(a["close"])) / 2.0
        if bool(a["bear"]) and b_small and bull and float(c["close"]) >= a_mid:
            add("MORNING STAR", "BULLISH", 76, "Three-candle bullish reversal structure")
        if bool(a["bull"]) and b_small and bear and float(c["close"]) <= a_mid:
            add("EVENING STAR", "BEARISH", 76, "Three-candle bearish reversal structure")

        last3 = [_library_candle_parts(source.iloc[j]) for j in range(index - 2, index + 1)]
        if all(bool(x["bull"]) and float(x["body"]) / float(x["range"]) >= 0.45 for x in last3):
            higher = float(last3[0]["close"]) < float(last3[1]["close"]) < float(last3[2]["close"])
            opens_inside = (
                min(float(last3[0]["open"]), float(last3[0]["close"])) <= float(last3[1]["open"]) <= max(float(last3[0]["open"]), float(last3[0]["close"]))
                and min(float(last3[1]["open"]), float(last3[1]["close"])) <= float(last3[2]["open"]) <= max(float(last3[1]["open"]), float(last3[1]["close"]))
            )
            if higher and opens_inside:
                add("THREE WHITE SOLDIERS", "BULLISH", 78, "Three strong rising bullish bodies")
        if all(bool(x["bear"]) and float(x["body"]) / float(x["range"]) >= 0.45 for x in last3):
            lower_closes = float(last3[0]["close"]) > float(last3[1]["close"]) > float(last3[2]["close"])
            opens_inside = (
                min(float(last3[0]["open"]), float(last3[0]["close"])) <= float(last3[1]["open"]) <= max(float(last3[0]["open"]), float(last3[0]["close"]))
                and min(float(last3[1]["open"]), float(last3[1]["close"])) <= float(last3[2]["open"]) <= max(float(last3[1]["open"]), float(last3[1]["close"]))
            )
            if lower_closes and opens_inside:
                add("THREE BLACK CROWS", "BEARISH", 78, "Three strong falling bearish bodies")

    # User-shared social-media setup, renamed by geometry rather than treating the
    # label as a textbook pattern: three bullish candles followed by bullish lower-
    # wick rejection.  It remains shadow-only until live validation proves value.
    if index >= 3:
        prior3 = [_library_candle_parts(source.iloc[j]) for j in range(index - 3, index)]
        three_bull = all(bool(x["bull"]) for x in prior3)
        rising = float(prior3[0]["close"]) < float(prior3[1]["close"]) < float(prior3[2]["close"])
        if three_bull and rising and bull and lower_rejection:
            add(
                "3-BULL + LOWER-WICK CONTINUATION",
                "BULLISH",
                66,
                "User-shared setup: 3 rising bull candles + lower-wick rejection; same geometry can be Hanging Man after an uptrend, so next-candle confirmation is mandatory",
            )

    return matches


def _library_outcomes(
    item: dict[str, object],
    outcome_times: pd.DatetimeIndex,
    outcome_closes: list[float],
    timeframe_minutes: int,
) -> dict[str, object]:
    """Evaluate 5/15/30m follow-through from prepared completed 1m bars."""
    result: dict[str, object] = {"5m": "PENDING", "15m": "PENDING", "30m": "PENDING"}
    if len(outcome_times) == 0 or not outcome_closes:
        return result
    try:
        detected = pd.Timestamp(str(item.get("detected_at") or ""))
    except Exception:
        return result
    completed_at = detected + pd.Timedelta(minutes=timeframe_minutes)
    baseline = float(item.get("close") or 0.0)
    if baseline <= 0:
        return result
    direction = str(item.get("direction") or "NEUTRAL")
    for minutes in (5, 15, 30):
        target = completed_at + pd.Timedelta(minutes=minutes)
        pos = int(outcome_times.searchsorted(target, side="left"))
        if pos >= len(outcome_times) or pos >= len(outcome_closes):
            continue
        try:
            close = float(outcome_closes[pos])
        except Exception:
            continue
        move = close - baseline
        if direction == "BULLISH":
            verdict = "FOLLOW" if move > 0 else "AGAINST" if move < 0 else "FLAT"
        elif direction == "BEARISH":
            verdict = "FOLLOW" if move < 0 else "AGAINST" if move > 0 else "FLAT"
        else:
            verdict = "MOVE" if abs(move) > 0 else "FLAT"
        result[f"{minutes}m"] = f"{verdict} {move:+.1f}pt"
    return result


def build_candle_pattern_library(
    candles_1m: pd.DataFrame,
    candles_3m: pd.DataFrame,
    candles_5m: pd.DataFrame | None = None,
    candles_15m: pd.DataFrame | None = None,
    *,
    history_bars: int = 20,
    max_recent: int = 18,
) -> dict[str, object]:
    """Build a zero-vote candle-pattern shadow library from cached candles only.

    No broker/API request is made here.  The output is stored in snapshot metadata so
    evidence/replay can inspect it, but it is intentionally excluded from One Brain
    scoring until live-market validation is complete.
    """
    frames: list[tuple[str, int, pd.DataFrame]] = [("3M", 3, candles_3m)]
    if candles_5m is not None:
        frames.append(("5M", 5, candles_5m))
    if candles_15m is not None:
        frames.append(("15M", 15, candles_15m))

    # Prepare the 1m outcome lookup once.  This keeps the shadow tracker off the
    # critical path: all detections reuse the same cached arrays.
    one = _current_session(candles_1m)
    if one.empty:
        outcome_times = pd.DatetimeIndex([])
        outcome_closes: list[float] = []
    else:
        outcome_times = pd.DatetimeIndex(pd.to_datetime(one["timestamp"], errors="coerce"))
        outcome_closes = [float(x) for x in pd.to_numeric(one["close"], errors="coerce").fillna(0.0)]

    current: dict[str, list[dict[str, object]]] = {}
    recent: list[dict[str, object]] = []
    for label, minutes, frame in frames:
        source = _current_session(frame)
        if source.empty:
            current[label] = []
            continue
        latest = _library_matches_at(source, len(source) - 1, label)
        current[label] = latest[:6]
        start = max(0, len(source) - max(8, int(history_bars)))
        for i in range(start, len(source)):
            for item in _library_matches_at(source, i, label):
                enriched = dict(item)
                enriched["outcomes"] = _library_outcomes(enriched, outcome_times, outcome_closes, minutes)
                recent.append(enriched)

    recent.sort(key=lambda x: str(x.get("detected_at") or ""), reverse=True)
    return {
        "mode": "SHADOW / DISPLAY ONLY",
        "decision_weight": 0,
        "extra_api_calls": 0,
        "current": current,
        "recent": recent[: max(1, int(max_recent))],
        "patterns_supported": (
            "3-Bull + Lower-Wick Continuation",
            "Bullish/Bearish Engulfing",
            "Hammer / Hanging Man",
            "Inverted Hammer / Shooting Star",
            "Morning / Evening Star",
            "Three White Soldiers / Three Black Crows",
            "Piercing Line / Dark Cloud Cover",
            "Inside / Outside Bar",
            "Tweezer Top / Bottom",
            "Marubozu",
            "Doji / Dragonfly / Gravestone",
            "Bullish / Bearish Pin Bar",
        ),
    }

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
