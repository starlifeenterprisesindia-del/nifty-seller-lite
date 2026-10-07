"""Liquidity / stop-cascade intelligence for One Brain Market Intelligence.

This module is deliberately shadow-only and zero-network.  It consumes the same
MarketSnapshot already built by One Brain plus already-fused Market Intelligence
pressures.  It does not recalculate directional expert families and therefore cannot
add a duplicate vote back into One Brain or the OB-MIE directional aggregate.

The output is a *probable liquidity map*, not knowledge of real customer stops or
participant intent.  Reach/hunt values are evidence scores, never probabilities.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from math import isfinite
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class LiquidityZone:
    side: str
    lower: float
    upper: float
    midpoint: float
    attraction_score: float
    distance_points: float
    strength: str
    sources: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiquidityIntelligence:
    state: str
    hunt_bias: str
    hunt_strength: str
    upside_hunt_pressure: float
    downside_hunt_pressure: float
    primary_zone: LiquidityZone | None
    extension_zone: LiquidityZone | None
    reach_score: float
    reach_state: str
    path_clearance: float
    sweep_state: str
    sweep_outcome: str
    sweep_quality: float
    sweep_anchor_zone: LiquidityZone | None
    sweep_anchor_at: str | None
    acceptance_state: str
    sweep_age_seconds: float | None
    reasons: tuple[str, ...]
    cautions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["primary_zone"] = self.primary_zone.to_dict() if self.primary_zone else None
        data["extension_zone"] = self.extension_zone.to_dict() if self.extension_zone else None
        data["sweep_anchor_zone"] = self.sweep_anchor_zone.to_dict() if self.sweep_anchor_zone else None
        return data


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _completed(frame: pd.DataFrame | None, tail: int = 6) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    source = frame.copy()
    if "is_complete" in source.columns:
        source = source[source["is_complete"].fillna(False).astype(bool)]
    if "timestamp" in source.columns:
        source = source.sort_values("timestamp").drop_duplicates("timestamp")
    return source.tail(tail)


def _atr(snapshot: Any) -> float:
    pa = getattr(snapshot, "price_action", None)
    for frame in (getattr(pa, "three_minute", None), getattr(pa, "fifteen_minute", None)):
        value = _num(getattr(frame, "atr14", None))
        if value is not None and value > 0:
            return value
    candles = _completed(getattr(snapshot, "candles_1m", None), tail=12)
    if len(candles) >= 5 and {"high", "low"}.issubset(candles.columns):
        ranges = (
            pd.to_numeric(candles["high"], errors="coerce")
            - pd.to_numeric(candles["low"], errors="coerce")
        ).dropna()
        if not ranges.empty:
            return max(4.0, float(ranges.median()) * 2.2)
    return 12.0


def _strength_label(score: float) -> str:
    if score >= 78:
        return "VERY HIGH"
    if score >= 62:
        return "HIGH"
    if score >= 46:
        return "MEDIUM"
    return "LOW"


def _hunt_label(score: float) -> str:
    if score >= 78:
        return "HIGH"
    if score >= 62:
        return "MODERATE-HIGH"
    if score >= 48:
        return "MODERATE"
    return "LOW"


def _category(label: str) -> str:
    upper = label.upper()
    if "WALL" in upper:
        return "option_wall"
    if any(token in upper for token in ("PDH", "PDL", "OPENING RANGE", "ORH", "ORL")):
        return "session"
    if "ROUND" in upper:
        return "round"
    if "BARRIER" in upper:
        return "barrier"
    if "SWING" in upper:
        return "structure"
    if "PATTERN" in upper or "NECKLINE" in upper:
        return "pattern"
    return "level"


def _point_width(atr: float) -> float:
    return max(2.0, min(7.0, atr * 0.12))


def _zone_candidates(snapshot: Any, spot: float, atr: float) -> list[dict[str, Any]]:
    """Collect bounded candidate pools from data already present in the snapshot."""
    candidates: list[dict[str, Any]] = []
    width = _point_width(atr)

    def add(side: str, lower: Any, upper: Any, strength: Any, label: str) -> None:
        lo = _num(lower)
        hi = _num(upper)
        if lo is None and hi is None:
            return
        if lo is None:
            lo = hi
        if hi is None:
            hi = lo
        assert lo is not None and hi is not None
        lo, hi = min(lo, hi), max(lo, hi)
        if abs(hi - lo) < 0.5:
            lo -= width
            hi += width
        mid = (lo + hi) / 2.0
        actual_side = "UPSIDE" if mid > spot else "DOWNSIDE" if mid < spot else side
        # Do not keep a nominal resistance below spot or support above spot unless its
        # geometry itself says which side it now occupies.
        if actual_side not in {"UPSIDE", "DOWNSIDE"}:
            return
        # Short-horizon map stays bounded. Far levels remain available through the
        # normal barrier/level UI instead of bloating this detector.
        if abs(mid - spot) > max(350.0, atr * 20.0):
            return
        candidates.append({
            "side": actual_side,
            "lower": lo,
            "upper": hi,
            "midpoint": mid,
            "raw_strength": _clamp(_num(strength, 50.0) or 50.0),
            "label": label,
            "category": _category(label),
        })

    levels = getattr(snapshot, "levels", None)
    if levels is not None:
        for attr, side, label in (
            ("immediate_resistance", "UPSIDE", "Immediate resistance"),
            ("strong_resistance", "UPSIDE", "Strong resistance"),
            ("immediate_support", "DOWNSIDE", "Immediate support"),
            ("strong_support", "DOWNSIDE", "Strong support"),
        ):
            item = getattr(levels, attr, None)
            if item is not None:
                add(side, getattr(item, "lower", getattr(item, "midpoint", None)),
                    getattr(item, "upper", getattr(item, "midpoint", None)),
                    getattr(item, "strength", 62.0), label)
        for attr, side, label in (
            ("previous_day_high", "UPSIDE", "PDH"),
            ("opening_range_high", "UPSIDE", "Opening Range High"),
            ("previous_day_low", "DOWNSIDE", "PDL"),
            ("opening_range_low", "DOWNSIDE", "Opening Range Low"),
        ):
            value = getattr(levels, attr, None)
            add(side, value, value, 72.0 if "PD" in label else 66.0, label)

    barrier = getattr(snapshot, "barrier_map", None)
    if barrier is not None:
        for attr, side, label in (
            ("nearest_resistance", "UPSIDE", "Nearest barrier"),
            ("next_resistance", "UPSIDE", "Next barrier"),
            ("nearest_support", "DOWNSIDE", "Nearest barrier"),
            ("next_support", "DOWNSIDE", "Next barrier"),
        ):
            item = getattr(barrier, attr, None)
            if item is not None:
                add(side, getattr(item, "lower", None), getattr(item, "upper", None),
                    getattr(item, "strength", 60.0), f"{label} {side.lower()}")

    pa = getattr(snapshot, "price_action", None)
    for tf_name, tf in (("3m", getattr(pa, "three_minute", None)), ("15m", getattr(pa, "fifteen_minute", None))):
        if tf is None:
            continue
        for attr, side, label in (
            ("last_swing_high", "UPSIDE", f"{tf_name} swing high"),
            ("prior_swing_high", "UPSIDE", f"{tf_name} prior swing high"),
            ("last_swing_low", "DOWNSIDE", f"{tf_name} swing low"),
            ("prior_swing_low", "DOWNSIDE", f"{tf_name} prior swing low"),
        ):
            value = getattr(tf, attr, None)
            add(side, value, value, 68.0 if "last" in attr else 58.0, label)

    options = getattr(snapshot, "option_intelligence", None)
    if options is not None:
        ce = getattr(options, "ce_wall", None)
        pe = getattr(options, "pe_wall", None)
        if ce is not None:
            add("UPSIDE", getattr(ce, "strike", None), getattr(ce, "strike", None), 68.0, "CE wall")
            center = getattr(ce, "cluster_center", None)
            add("UPSIDE", center, center, 62.0, "CE wall cluster")
        if pe is not None:
            add("DOWNSIDE", getattr(pe, "strike", None), getattr(pe, "strike", None), 68.0, "PE wall")
            center = getattr(pe, "cluster_center", None)
            add("DOWNSIDE", center, center, 62.0, "PE wall cluster")

    # Round levels are only a modest supporting category; they can never dominate a
    # pool by themselves.
    for step in (50.0, 100.0):
        below = (spot // step) * step
        above = below + step
        if above > spot:
            add("UPSIDE", above, above, 42.0 if step == 50 else 50.0, f"Round {int(step)}")
        if below < spot:
            add("DOWNSIDE", below, below, 42.0 if step == 50 else 50.0, f"Round {int(step)}")

    patterns = getattr(snapshot, "patterns", None)
    if patterns is not None:
        for signal in (
            getattr(patterns, "wm_3m", None), getattr(patterns, "candle_3m", None),
            getattr(patterns, "candle_5m", None), getattr(patterns, "candle_15m", None),
        ):
            if signal is None:
                continue
            direction = str(getattr(signal, "direction", "") or "").upper()
            side = "UPSIDE" if "BULL" in direction else "DOWNSIDE" if "BEAR" in direction else ""
            if not side:
                continue
            value = getattr(signal, "level_value", None)
            add(side, value, value, min(72.0, _num(getattr(signal, "confidence", None), 52.0) or 52.0), "Pattern level")
            neckline = getattr(signal, "neckline", None)
            add(side, neckline, neckline, 56.0, "Pattern neckline")

    return candidates


def _cluster_candidates(snapshot: Any, spot: float, candidates: list[dict[str, Any]], atr: float) -> list[LiquidityZone]:
    if not candidates:
        return []
    tolerance = max(4.0, min(14.0, atr * 0.22))
    output: list[LiquidityZone] = []
    category_caps = {
        "barrier": 25.0,
        "level": 22.0,
        "structure": 20.0,
        "session": 20.0,
        "option_wall": 18.0,
        "pattern": 12.0,
        "round": 9.0,
    }
    for side in ("UPSIDE", "DOWNSIDE"):
        rows = sorted((x for x in candidates if x["side"] == side), key=lambda x: x["midpoint"])
        clusters: list[list[dict[str, Any]]] = []
        for row in rows:
            if not clusters:
                clusters.append([row]); continue
            last = clusters[-1]
            center = sum(x["midpoint"] for x in last) / len(last)
            if abs(row["midpoint"] - center) <= tolerance or row["lower"] <= max(x["upper"] for x in last) + tolerance * 0.35:
                last.append(row)
            else:
                clusters.append([row])
        for cluster in clusters:
            lower = min(x["lower"] for x in cluster)
            upper = max(x["upper"] for x in cluster)
            mid = sum(x["midpoint"] for x in cluster) / len(cluster)
            if side == "UPSIDE" and upper <= spot:
                continue
            if side == "DOWNSIDE" and lower >= spot:
                continue
            category_best: dict[str, float] = {}
            for row in cluster:
                category = row["category"]
                cap = category_caps.get(category, 12.0)
                contribution = cap * (0.55 + 0.45 * row["raw_strength"] / 100.0)
                category_best[category] = max(category_best.get(category, 0.0), contribution)
            score = 12.0 + sum(category_best.values())
            if len(category_best) >= 3:
                score += 8.0
            elif len(category_best) == 2:
                score += 3.0
            score = _clamp(score)
            distance = max(0.0, lower - spot) if side == "UPSIDE" else max(0.0, spot - upper)
            output.append(LiquidityZone(
                side=side,
                lower=round(lower, 2),
                upper=round(upper, 2),
                midpoint=round(mid, 2),
                attraction_score=round(score, 1),
                distance_points=round(distance, 1),
                strength=_strength_label(score),
                sources=tuple(dict.fromkeys(str(x["label"]) for x in cluster))[:7],
            ))
    return output


def _path_clearance(snapshot: Any, side: str) -> float:
    barrier = getattr(snapshot, "barrier_map", None)
    if barrier is None:
        return 50.0
    level = getattr(barrier, "nearest_resistance" if side == "UPSIDE" else "nearest_support", None)
    if level is None:
        return 58.0
    break_pressure = _clamp(_num(getattr(level, "break_pressure", None), 50.0) or 50.0)
    strength = _clamp(_num(getattr(level, "strength", None), 50.0) or 50.0)
    # Existing barrier engine already calculated break pressure.  Reuse it; do not
    # reconstruct OI/wall/big-player evidence here.
    return round(_clamp(break_pressure * 0.72 + (100.0 - strength) * 0.28), 1)


def _select_target(snapshot: Any, zones: list[LiquidityZone], side: str, atr: float) -> tuple[LiquidityZone | None, LiquidityZone | None, float]:
    candidates = [z for z in zones if z.side == side]
    if not candidates:
        return None, None, 50.0
    reach_budget = max(35.0, min(180.0, atr * 5.0))

    def pull(zone: LiquidityZone) -> float:
        distance_fit = _clamp(100.0 - (zone.distance_points / reach_budget) * 65.0)
        return zone.attraction_score * 0.72 + distance_fit * 0.28

    ordered = sorted(candidates, key=lambda z: (-pull(z), z.distance_points))
    primary = ordered[0]
    # An extension must be beyond primary in the same direction and should not simply
    # be another overlapping representation of the same cluster.
    if side == "UPSIDE":
        beyond = [z for z in candidates if z.lower > primary.upper + 2.0]
    else:
        beyond = [z for z in candidates if z.upper < primary.lower - 2.0]
    extension = sorted(beyond, key=lambda z: (z.distance_points, -z.attraction_score))[0] if beyond else None
    return primary, extension, _path_clearance(snapshot, side)


def _zone_from_dict(value: Any) -> LiquidityZone | None:
    if not isinstance(value, dict):
        return None
    try:
        return LiquidityZone(
            side=str(value.get("side") or ""),
            lower=float(value["lower"]),
            upper=float(value["upper"]),
            midpoint=float(value.get("midpoint") or (float(value["lower"]) + float(value["upper"])) / 2.0),
            attraction_score=float(value.get("attraction_score") or 0.0),
            distance_points=float(value.get("distance_points") or 0.0),
            strength=str(value.get("strength") or ""),
            sources=tuple(value.get("sources") or ()),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _sweep_state(
    snapshot: Any,
    previous_mie: dict[str, Any] | None,
    *, direction: str,
    early_direction: str,
    bull_pressure: float,
    bear_pressure: float,
    expansion_pressure: float,
    breakout_quality: float,
    reversal_quality: float,
) -> tuple[str, str, float, tuple[str, ...], LiquidityZone | None, str | None, str, float | None]:
    """Evaluate a liquidity interaction with post-breach acceptance memory.

    A single close beyond a pool is now only a *breach pending acceptance*.
    Continuation requires follow-through; a quick reclaim becomes reversal watch.
    The exact pool that was breached is carried for a bounded five-minute window so
    target recalculation cannot move the goalpost after the event.
    """
    previous_liq = (previous_mie or {}).get("liquidity") if isinstance((previous_mie or {}).get("liquidity"), dict) else {}
    previous_state = str((previous_liq or {}).get("sweep_state") or "NONE")
    previous_anchor = _zone_from_dict((previous_liq or {}).get("sweep_anchor_zone"))
    previous_anchor_at = (previous_liq or {}).get("sweep_anchor_at")
    now = getattr(snapshot, "created_at", None)
    age_seconds: float | None = None
    anchor_valid = False
    if previous_anchor is not None and previous_anchor_at and now is not None:
        try:
            stamp = datetime.fromisoformat(str(previous_anchor_at))
            if stamp.tzinfo is None and getattr(now, "tzinfo", None) is not None:
                stamp = stamp.replace(tzinfo=now.tzinfo)
            age_seconds = max(0.0, (now - stamp).total_seconds())
            anchor_valid = age_seconds <= 300.0
        except (TypeError, ValueError):
            anchor_valid = False

    target = previous_anchor if anchor_valid else _zone_from_dict((previous_liq or {}).get("primary_zone"))
    anchor_at = str(previous_anchor_at) if anchor_valid else (now.isoformat() if now is not None and target is not None else None)
    if target is None:
        return "NONE", "UNCLEAR", 0.0, (), None, None, "NONE", None

    frame = _completed(getattr(snapshot, "candles_1m", None), tail=4)
    if frame.empty or not {"open", "high", "low", "close"}.issubset(frame.columns):
        return "NONE", "UNCLEAR", 0.0, (), target, anchor_at, "NONE", age_seconds
    last = frame.iloc[-1]
    op = _num(last.get("open")); high = _num(last.get("high")); low = _num(last.get("low")); close = _num(last.get("close"))
    if None in (op, high, low, close):
        return "NONE", "UNCLEAR", 0.0, (), target, anchor_at, "NONE", age_seconds

    closes = pd.to_numeric(frame["close"], errors="coerce").dropna().tolist()
    reasons: list[str] = []
    state = "NONE"
    outcome = "UNCLEAR"
    acceptance = "NONE"
    quality = 0.0
    active_prior = any(token in previous_state for token in ("BREACH", "ACCEPTANCE", "RECLAIM", "TARGET TESTING")) and anchor_valid

    if target.side == "UPSIDE" and (high >= target.lower or active_prior):
        if high > target.upper and close < target.lower:
            state = "UPSIDE SWEEP REJECTED"
            quality = 58.0 + min(24.0, reversal_quality * 0.24)
            outcome = "REVERSAL FAVORED" if bear_pressure >= 45 or reversal_quality >= 52 else "REVERSAL WATCH"
            acceptance = "REJECTED"
            reasons.append("Upside pool breached then closed back below zone")
        elif active_prior and close < target.lower:
            state = "UPSIDE BREACH RECLAIMED"
            quality = 60.0 + min(20.0, reversal_quality * 0.22)
            outcome = "REVERSAL FAVORED" if bear_pressure >= 45 or reversal_quality >= 52 else "REVERSAL WATCH"
            acceptance = "RECLAIMED"
            reasons.append("Prior upside breach reclaimed below the original pool")
        elif close > target.upper:
            two_accept = len(closes) >= 2 and closes[-1] > target.upper and closes[-2] > target.upper
            clean_follow = low > target.lower and close >= op
            if active_prior and two_accept and clean_follow:
                state = "UPSIDE ACCEPTANCE CONFIRMED"
                quality = 55.0 + expansion_pressure * 0.18 + breakout_quality * 0.16
                outcome = "CONTINUATION FAVORED" if early_direction == "BULLISH" and expansion_pressure >= 55 else "CONTINUATION WATCH"
                acceptance = "ACCEPTED"
                reasons.append("Two completed closes accepted above original upside pool")
            else:
                state = "UPSIDE LIQUIDITY BREACHED"
                quality = 50.0 + expansion_pressure * 0.15 + breakout_quality * 0.12
                outcome = "PENDING ACCEPTANCE"
                acceptance = "PENDING"
                reasons.append("Upside pool breached; follow-through close still required")
        else:
            state = "UPSIDE TARGET TESTING"
            quality = 44.0
            outcome = "PENDING ACCEPTANCE" if active_prior else "UNCLEAR"
            acceptance = "PENDING" if active_prior else "TESTING"
            reasons.append("Price entered original upside liquidity zone")

    elif target.side == "DOWNSIDE" and (low <= target.upper or active_prior):
        if low < target.lower and close > target.upper:
            state = "DOWNSIDE SWEEP REJECTED"
            quality = 58.0 + min(24.0, reversal_quality * 0.24)
            outcome = "REVERSAL FAVORED" if bull_pressure >= 45 or reversal_quality >= 52 else "REVERSAL WATCH"
            acceptance = "REJECTED"
            reasons.append("Downside pool breached then closed back above zone")
        elif active_prior and close > target.upper:
            state = "DOWNSIDE BREACH RECLAIMED"
            quality = 60.0 + min(20.0, reversal_quality * 0.22)
            outcome = "REVERSAL FAVORED" if bull_pressure >= 45 or reversal_quality >= 52 else "REVERSAL WATCH"
            acceptance = "RECLAIMED"
            reasons.append("Prior downside breach reclaimed above the original pool")
        elif close < target.lower:
            two_accept = len(closes) >= 2 and closes[-1] < target.lower and closes[-2] < target.lower
            clean_follow = high < target.upper and close <= op
            if active_prior and two_accept and clean_follow:
                state = "DOWNSIDE ACCEPTANCE CONFIRMED"
                quality = 55.0 + expansion_pressure * 0.18 + breakout_quality * 0.16
                outcome = "CONTINUATION FAVORED" if early_direction == "BEARISH" and expansion_pressure >= 55 else "CONTINUATION WATCH"
                acceptance = "ACCEPTED"
                reasons.append("Two completed closes accepted below original downside pool")
            else:
                state = "DOWNSIDE LIQUIDITY BREACHED"
                quality = 50.0 + expansion_pressure * 0.15 + breakout_quality * 0.12
                outcome = "PENDING ACCEPTANCE"
                acceptance = "PENDING"
                reasons.append("Downside pool breached; follow-through close still required")
        else:
            state = "DOWNSIDE TARGET TESTING"
            quality = 44.0
            outcome = "PENDING ACCEPTANCE" if active_prior else "UNCLEAR"
            acceptance = "PENDING" if active_prior else "TESTING"
            reasons.append("Price entered original downside liquidity zone")

    return (
        state, outcome, round(_clamp(quality), 1), tuple(reasons[:3]),
        target, anchor_at, acceptance, round(age_seconds, 1) if age_seconds is not None else None,
    )


def calculate_liquidity_intelligence(
    snapshot: Any,
    previous_snapshot: Any | None,
    *,
    direction: str,
    early_direction: str = "MIXED",
    direction_context: str = "MIXED",
    bull_pressure: float,
    bear_pressure: float,
    expansion_pressure: float,
    breakout_quality: float,
    reversal_quality: float,
    conflict: str,
) -> LiquidityIntelligence:
    """Calculate probable liquidity path without adding a new directional vote."""
    spot = _num((getattr(snapshot, "nifty_quote", {}) or {}).get("last_price"))
    if spot is None:
        return LiquidityIntelligence(
            state="UNAVAILABLE", hunt_bias="UNCLEAR", hunt_strength="LOW",
            upside_hunt_pressure=0.0, downside_hunt_pressure=0.0,
            primary_zone=None, extension_zone=None, reach_score=0.0,
            reach_state="UNCLEAR", path_clearance=0.0, sweep_state="NONE",
            sweep_outcome="UNCLEAR", sweep_quality=0.0,
            sweep_anchor_zone=None, sweep_anchor_at=None, acceptance_state="NONE", sweep_age_seconds=None,
            reasons=("Spot unavailable",), cautions=("Probable liquidity map unavailable",),
        )

    atr = max(8.0, _atr(snapshot))
    candidates = _zone_candidates(snapshot, spot, atr)
    zones = _cluster_candidates(snapshot, spot, candidates, atr)
    up_zone, up_ext, up_clear = _select_target(snapshot, zones, "UPSIDE", atr)
    down_zone, down_ext, down_clear = _select_target(snapshot, zones, "DOWNSIDE", atr)

    def zone_pull(zone: LiquidityZone | None) -> float:
        if zone is None:
            return 0.0
        fit = _clamp(100.0 - zone.distance_points / max(45.0, atr * 5.0) * 60.0)
        return zone.attraction_score * 0.75 + fit * 0.25

    up_pull = zone_pull(up_zone)
    down_pull = zone_pull(down_zone)
    # Direction/expansion are already fused common features.  Liquidity contributes
    # target quality/path clearance, but does not feed back into those common scores.
    up_hunt = _clamp(bull_pressure * 0.38 + expansion_pressure * 0.22 + up_pull * 0.24 + up_clear * 0.16)
    down_hunt = _clamp(bear_pressure * 0.38 + expansion_pressure * 0.22 + down_pull * 0.24 + down_clear * 0.16)

    # Strong early-direction consensus may break a near-tie, but it never creates
    # a target when the corresponding liquidity pressure is weak.  This is a state
    # tie-break, not a second raw-evidence vote.
    up_margin = 4.0 if early_direction == "BULLISH" and expansion_pressure >= 50 else 9.0
    down_margin = 4.0 if early_direction == "BEARISH" and expansion_pressure >= 50 else 9.0
    if up_hunt >= 54 and up_hunt >= down_hunt + up_margin:
        bias = "UPSIDE"
        primary, extension, clearance = up_zone, up_ext, up_clear
        hunt = up_hunt
    elif down_hunt >= 54 and down_hunt >= up_hunt + down_margin:
        bias = "DOWNSIDE"
        primary, extension, clearance = down_zone, down_ext, down_clear
        hunt = down_hunt
    else:
        bias = "BALANCED" if (up_zone or down_zone) else "UNCLEAR"
        # In a balanced state show the stronger *zone* only as a map reference, not a
        # directional target.
        options = [z for z in (up_zone, down_zone) if z is not None]
        primary = max(options, key=lambda z: z.attraction_score) if options else None
        extension = None
        clearance = 50.0
        hunt = max(up_hunt, down_hunt)

    if primary is not None:
        directional_pressure = bull_pressure if primary.side == "UPSIDE" else bear_pressure
        reach_score = _clamp(
            hunt * 0.35
            + primary.attraction_score * 0.25
            + clearance * 0.18
            + expansion_pressure * 0.22
        )
    else:
        directional_pressure = 0.0
        reach_score = 0.0

    if bias in {"UPSIDE", "DOWNSIDE"} and reach_score >= 72 and conflict != "HIGH":
        reach_state = "LIKELY"
    elif primary is not None and reach_score >= 55:
        reach_state = "POSSIBLE"
    elif primary is not None:
        reach_state = "NOT YET SUPPORTED"
    else:
        reach_state = "UNCLEAR"

    previous_mie = None
    if previous_snapshot is not None:
        previous_mie = (getattr(previous_snapshot, "metadata", {}) or {}).get("market_intelligence")
    (
        sweep_state, sweep_outcome, sweep_quality, sweep_reasons, sweep_anchor_zone,
        sweep_anchor_at, acceptance_state, sweep_age_seconds,
    ) = _sweep_state(
        snapshot,
        previous_mie,
        direction=direction, early_direction=early_direction,
        bull_pressure=bull_pressure,
        bear_pressure=bear_pressure,
        expansion_pressure=expansion_pressure,
        breakout_quality=breakout_quality,
        reversal_quality=reversal_quality,
    )

    # Before a pool is actually breached, show only a LEAN.  "Favored" is reserved
    # for post-breach acceptance/reclaim evidence so one candle cannot prematurely
    # declare continuation.
    if sweep_outcome == "UNCLEAR" and primary is not None and bias in {"UPSIDE", "DOWNSIDE"}:
        aligned = (bias == "UPSIDE" and early_direction == "BULLISH") or (bias == "DOWNSIDE" and early_direction == "BEARISH")
        if aligned and expansion_pressure >= 65 and breakout_quality >= 58 and conflict != "HIGH" and direction_context == "WITH TREND":
            sweep_outcome = "CONTINUATION LEAN"
        elif reversal_quality >= 68 and breakout_quality < 58:
            sweep_outcome = "REVERSAL LEAN"

    if bias == "UPSIDE":
        state = "UPSIDE LIQUIDITY ATTRACTING"
    elif bias == "DOWNSIDE":
        state = "DOWNSIDE LIQUIDITY ATTRACTING"
    elif bias == "BALANCED":
        state = "LIQUIDITY BALANCED"
    else:
        state = "NO CLEAR LIQUIDITY MAP"

    reasons: list[str] = []
    if primary is not None:
        reasons.append(
            f"{primary.side.title()} pool {primary.lower:.0f}-{primary.upper:.0f} · attraction {primary.attraction_score:.0f}"
        )
    reasons.append(f"Hunt pressure up {up_hunt:.0f} / down {down_hunt:.0f}")
    reasons.append(f"Path clearance {clearance:.0f}")
    reasons.extend(sweep_reasons)
    cautions = [
        "Liquidity zones infer probable clustered interest; exact stop money/participant intent is not observable",
        "Hunt/reach values are evidence scores, not probabilities",
    ]
    if conflict == "HIGH":
        cautions.append("High evidence conflict — target direction downgraded")

    return LiquidityIntelligence(
        state=state,
        hunt_bias=bias,
        hunt_strength=_hunt_label(hunt),
        upside_hunt_pressure=round(up_hunt, 1),
        downside_hunt_pressure=round(down_hunt, 1),
        primary_zone=primary,
        extension_zone=extension,
        reach_score=round(reach_score, 1),
        reach_state=reach_state,
        path_clearance=round(clearance, 1),
        sweep_state=sweep_state,
        sweep_outcome=sweep_outcome,
        sweep_quality=sweep_quality,
        sweep_anchor_zone=sweep_anchor_zone,
        sweep_anchor_at=sweep_anchor_at,
        acceptance_state=acceptance_state,
        sweep_age_seconds=sweep_age_seconds,
        reasons=tuple(dict.fromkeys(reasons))[:6],
        cautions=tuple(cautions[:3]),
    )
