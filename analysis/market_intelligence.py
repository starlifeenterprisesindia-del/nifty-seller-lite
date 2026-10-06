"""One Brain Market Intelligence Engine (OB-MIE).

Shadow-only market-state and impulse intelligence built from the *existing* authoritative
MarketSnapshot.  This module intentionally:

* performs no broker/network/API calls;
* never mutates One-Brain scores, decisions, trade plans or execution guards;
* treats missing evidence as NO VOTE;
* uses only bounded recent snapshot/candle/option data;
* returns evidence/path scores, not calibrated probabilities.

The first release is deliberately rules-first and explainable.  Every output is suitable
for shadow validation before any future decision weight is considered.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Iterable

import pandas as pd


# Directional expert families.  The weights sum to 1.00 before regime/reliability
# multipliers.  Correlated sub-signals are intentionally kept inside one family.
_BASE_WEIGHTS: dict[str, float] = {
    "structure": 0.16,
    "trend": 0.14,
    "futures": 0.16,
    "options": 0.17,
    "barriers": 0.13,
    "breadth": 0.10,
    "volatility": 0.06,
    "momentum": 0.08,
}

_REGIME_MULTIPLIERS: dict[str, dict[str, float]] = {
    "TREND": {
        "structure": 1.05, "trend": 1.30, "futures": 1.20, "options": 1.05,
        "barriers": 0.90, "breadth": 1.10, "volatility": 0.90, "momentum": 1.15,
    },
    "RANGE": {
        "structure": 1.20, "trend": 0.80, "futures": 0.90, "options": 1.10,
        "barriers": 1.30, "breadth": 0.90, "volatility": 1.00, "momentum": 0.80,
    },
    "TRANSITION": {
        "structure": 1.20, "trend": 0.80, "futures": 1.25, "options": 1.25,
        "barriers": 1.10, "breadth": 1.10, "volatility": 1.10, "momentum": 1.15,
    },
    "EVENT-DRIVEN": {
        "structure": 0.85, "trend": 0.75, "futures": 1.20, "options": 1.15,
        "barriers": 0.85, "breadth": 0.90, "volatility": 1.40, "momentum": 1.10,
    },
    "UNCERTAIN": {name: 1.0 for name in _BASE_WEIGHTS},
}


@dataclass(frozen=True)
class ExpertEvidence:
    name: str
    available: bool
    bullish: float
    bearish: float
    range_score: float
    reliability: float
    freshness: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PathEvidence:
    up: float
    down: float
    range_score: float

    def to_dict(self) -> dict[str, float]:
        return {"up": self.up, "down": self.down, "range": self.range_score}


@dataclass(frozen=True)
class MarketIntelligenceResult:
    engine: str
    mode: str
    status: str
    market_state: str
    direction: str
    bull_pressure: float
    bear_pressure: float
    range_pressure: float
    expansion_pressure: float
    pressure_velocity: float | None
    pressure_persistence: int
    impulse_state: str
    move_potential: str
    volatility_state: str
    structure_event: str
    breakout_direction: str
    breakout_quality: float
    reversal_direction: str
    reversal_quality: float
    institutional_pressure: str
    evidence_coverage: float
    evidence_conflict: str
    conflict_score: float
    fake_move_risk: str
    system_status: str
    path_5m: PathEvidence
    path_15m: PathEvidence
    path_30m: PathEvidence
    one_brain_direction: str
    one_brain_alignment: str
    invalidation: str
    alerts: tuple[dict[str, Any], ...]
    experts: tuple[ExpertEvidence, ...]
    reasons: tuple[str, ...]
    cautions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path_5m"] = self.path_5m.to_dict()
        data["path_15m"] = self.path_15m.to_dict()
        data["path_30m"] = self.path_30m.to_dict()
        data["experts"] = [item.to_dict() for item in self.experts]
        data["alerts"] = [dict(item) for item in self.alerts]
        return data


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _status_ready(value: Any) -> bool:
    status = str(getattr(value, "status", "") or "").upper()
    return status in {"READY", "CAUTION", "PARTIAL", "REFERENCE ONLY", "WARMING UP"}


def _feed_freshness(snapshot: Any, *names: str) -> float:
    if not names:
        return 1.0
    values: list[float] = []
    feeds = getattr(snapshot, "feed_status", {}) or {}
    for name in names:
        item = feeds.get(name)
        if item is None:
            continue
        state = str(getattr(item, "use_state", "") or "").upper()
        ok = bool(getattr(item, "ok", False))
        if ok and state == "LIVE":
            values.append(1.0)
        elif ok and state in {"REFERENCE", "CAUTION"}:
            values.append(0.65)
        elif ok and state in {"STALE", "DELAYED"}:
            values.append(0.35)
        else:
            values.append(0.0)
    return sum(values) / len(values) if values else 0.0


def _completed(frame: pd.DataFrame | None, tail: int = 30) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    source = frame.copy()
    if "is_complete" in source.columns:
        source = source[source["is_complete"].fillna(False).astype(bool)]
    if "timestamp" in source.columns:
        source = source.sort_values("timestamp").drop_duplicates("timestamp")
    return source.tail(tail)


def _state_direction(text: Any) -> str:
    upper = str(text or "").upper()
    if any(token in upper for token in ("BULL", "UP", "ADVANC", "BUY")):
        return "BULLISH"
    if any(token in upper for token in ("BEAR", "DOWN", "DECLIN", "SELL")):
        return "BEARISH"
    return "MIXED"


def _direction_triplet(direction: str, strength: float = 70.0, neutral: float = 25.0) -> tuple[float, float, float]:
    strength = _clamp(strength)
    neutral = _clamp(neutral)
    if direction == "BULLISH":
        return strength, max(0.0, 100.0 - strength - neutral / 2.0), neutral
    if direction == "BEARISH":
        return max(0.0, 100.0 - strength - neutral / 2.0), strength, neutral
    return 25.0, 25.0, 70.0


def _blend_triplets(parts: Iterable[tuple[tuple[float, float, float], float]]) -> tuple[float, float, float]:
    items = [(values, weight) for values, weight in parts if weight > 0]
    total = sum(weight for _, weight in items)
    if total <= 0:
        return 0.0, 0.0, 0.0
    return tuple(
        _clamp(sum(values[idx] * weight for values, weight in items) / total)
        for idx in range(3)
    )  # type: ignore[return-value]


def _ema_triplet(item: Any) -> tuple[float, float, float]:
    if str(getattr(item, "status", "")).upper() != "READY":
        return 0.0, 0.0, 0.0
    state = str(getattr(item, "ema_state", "") or "").upper()
    if "BULLISH ALIGNED" in state:
        return 82.0, 10.0, 18.0
    if "BEARISH ALIGNED" in state:
        return 10.0, 82.0, 18.0
    if "BULLISH" in state:
        return 65.0, 20.0, 35.0
    if "BEARISH" in state:
        return 20.0, 65.0, 35.0
    return 25.0, 25.0, 70.0


def _price_velocity(frame: pd.DataFrame | None, bars: int = 4) -> tuple[float | None, float | None]:
    source = _completed(frame, tail=max(8, bars + 2))
    if len(source) < bars + 1 or "close" not in source:
        return None, None
    values = pd.to_numeric(source["close"], errors="coerce").dropna()
    if len(values) < bars + 1:
        return None, None
    move = float(values.iloc[-1] - values.iloc[-(bars + 1)])
    one = float(values.iloc[-1] - values.iloc[-2])
    return move, one


def _body_acceleration(frame: pd.DataFrame | None) -> float | None:
    source = _completed(frame, tail=8)
    if len(source) < 6 or not {"open", "close"}.issubset(source.columns):
        return None
    bodies = (pd.to_numeric(source["close"], errors="coerce") - pd.to_numeric(source["open"], errors="coerce")).abs().dropna()
    if len(bodies) < 6:
        return None
    old = float(bodies.iloc[-6:-3].mean())
    recent = float(bodies.iloc[-3:].mean())
    if old <= 0:
        return None
    return recent / old


def _range_compression(frame: pd.DataFrame | None) -> tuple[float, float | None]:
    """Return compression evidence 0-100 and latest-range expansion ratio.

    Compression compares the last three completed one-minute ranges with the three
    preceding ranges.  It is intentionally local and bounded; no history scan.
    """
    source = _completed(frame, tail=9)
    if len(source) < 7 or not {"high", "low"}.issubset(source.columns):
        return 0.0, None
    ranges = (pd.to_numeric(source["high"], errors="coerce") - pd.to_numeric(source["low"], errors="coerce")).dropna()
    if len(ranges) < 7:
        return 0.0, None
    prior = float(ranges.iloc[-7:-4].median())
    recent = float(ranges.iloc[-4:-1].median())
    latest = float(ranges.iloc[-1])
    if prior <= 0:
        return 0.0, None
    shrink = 1.0 - recent / prior
    compression = _clamp(35.0 + shrink * 140.0 if shrink > 0 else 20.0 + shrink * 50.0)
    release_ratio = latest / max(recent, 0.01)
    return round(compression, 1), round(release_ratio, 3)


def _atm_option_metrics(snapshot: Any, previous_snapshot: Any | None) -> dict[str, float | None]:
    """Compute ATM straddle/IV changes from already-loaded option-chain frames."""
    current = getattr(snapshot, "option_chain", None)
    previous = getattr(previous_snapshot, "option_chain", None) if previous_snapshot is not None else None
    spot = _num((getattr(snapshot, "nifty_quote", {}) or {}).get("last_price"))
    if spot is None or current is None or current.empty or "strike" not in current or "side" not in current:
        return {"straddle": None, "straddle_change_pct": None, "atm_iv": None, "iv_change": None}

    def metrics(frame: pd.DataFrame | None, spot_value: float) -> tuple[float | None, float | None]:
        if frame is None or frame.empty:
            return None, None
        work = frame.copy()
        work["strike"] = pd.to_numeric(work["strike"], errors="coerce")
        work = work.dropna(subset=["strike"])
        if work.empty:
            return None, None
        strikes = work["strike"].drop_duplicates()
        atm = float(strikes.iloc[(strikes - spot_value).abs().argsort().iloc[0]])
        pair = work[work["strike"].eq(atm)]
        ce = pair[pair["side"].astype(str).str.upper().eq("CE")]
        pe = pair[pair["side"].astype(str).str.upper().eq("PE")]
        if ce.empty or pe.empty:
            return None, None

        def fair(row: pd.Series) -> float | None:
            last = _num(row.get("last_price"))
            bid = _num(row.get("top_bid_price"))
            ask = _num(row.get("top_ask_price"))
            if bid is not None and ask is not None and bid > 0 and ask >= bid:
                mid = (bid + ask) / 2.0
                if mid > 0 and (ask - bid) / mid <= 0.20:
                    return mid
            return last

        ce_p, pe_p = fair(ce.iloc[0]), fair(pe.iloc[0])
        straddle = ce_p + pe_p if ce_p is not None and pe_p is not None else None
        iv_values = []
        for row in (ce.iloc[0], pe.iloc[0]):
            iv = _num(row.get("implied_volatility"))
            if iv is not None and iv > 0:
                iv_values.append(iv)
        return straddle, (sum(iv_values) / len(iv_values) if iv_values else None)

    current_straddle, current_iv = metrics(current, spot)
    previous_spot = _num((getattr(previous_snapshot, "nifty_quote", {}) or {}).get("last_price"), spot) if previous_snapshot is not None else spot
    prior_straddle, prior_iv = metrics(previous, float(previous_spot or spot))
    straddle_change = None
    if current_straddle is not None and prior_straddle not in (None, 0):
        straddle_change = (current_straddle - float(prior_straddle)) / abs(float(prior_straddle)) * 100.0
    iv_change = None
    if current_iv is not None and prior_iv is not None:
        iv_change = current_iv - prior_iv
    return {
        "straddle": round(current_straddle, 3) if current_straddle is not None else None,
        "straddle_change_pct": round(straddle_change, 3) if straddle_change is not None else None,
        "atm_iv": round(current_iv, 3) if current_iv is not None else None,
        "iv_change": round(iv_change, 3) if iv_change is not None else None,
    }


def _expert_structure(snapshot: Any) -> ExpertEvidence:
    pa = getattr(snapshot, "price_action", None)
    if pa is None:
        return ExpertEvidence("Structure", False, 0, 0, 0, 0, 0, ("Price action unavailable",))
    p3, p15 = pa.three_minute, pa.fifteen_minute
    parts = []
    reasons: list[str] = []
    if str(getattr(p3, "status", "")).upper() == "READY":
        parts.append(((float(p3.bullish_score), float(p3.bearish_score), float(p3.range_score)), 0.58))
        reasons.append(f"3m {p3.structure} · {p3.event}")
    if str(getattr(p15, "status", "")).upper() == "READY":
        parts.append(((float(p15.bullish_score), float(p15.bearish_score), float(p15.range_score)), 0.42))
        reasons.append(f"15m {p15.structure} · {p15.event}")
    if not parts:
        return ExpertEvidence("Structure", False, 0, 0, 0, 0, 0, tuple(reasons) or ("Structure warming up",))
    bull, bear, rng = _blend_triplets(parts)
    reliability = _clamp(float(getattr(pa, "confidence", 0.0) or 0.0)) / 100.0
    return ExpertEvidence("Structure", True, bull, bear, rng, reliability, _feed_freshness(snapshot, "candles"), tuple(reasons[:3]))


def _expert_trend(snapshot: Any) -> ExpertEvidence:
    pa = getattr(snapshot, "price_action", None)
    ind = getattr(snapshot, "indicators", None)
    if pa is None or ind is None:
        return ExpertEvidence("Trend", False, 0, 0, 0, 0, 0, ("Trend inputs unavailable",))
    parts: list[tuple[tuple[float, float, float], float]] = []
    reasons: list[str] = []
    p15 = pa.fifteen_minute
    if str(getattr(p15, "status", "")).upper() == "READY":
        parts.append(((float(p15.bullish_score), float(p15.bearish_score), float(p15.range_score)), 0.62))
        reasons.append(f"15m structure {p15.structure}")
    ema15 = _ema_triplet(ind.fifteen_minute)
    if any(ema15):
        parts.append((ema15, 0.25))
        reasons.append(f"15m EMA {ind.fifteen_minute.ema_state}")
    ema3 = _ema_triplet(ind.three_minute)
    if any(ema3):
        parts.append((ema3, 0.13))
        reasons.append(f"3m EMA {ind.three_minute.ema_state}")
    if not parts:
        return ExpertEvidence("Trend", False, 0, 0, 0, 0, 0, ("Trend warming up",))
    bull, bear, rng = _blend_triplets(parts)
    ready_count = int(str(ind.fifteen_minute.status).upper() == "READY") + int(str(p15.status).upper() == "READY")
    reliability = 0.70 if ready_count == 1 else 0.90 if ready_count >= 2 else 0.45
    return ExpertEvidence("Trend", True, bull, bear, rng, reliability, _feed_freshness(snapshot, "candles"), tuple(reasons[:3]))


def _expert_futures(snapshot: Any) -> ExpertEvidence:
    frame = _completed(getattr(snapshot, "future_candles_1m", None), tail=12)
    activity = getattr(snapshot, "big_player_activity", None)
    if frame.empty and activity is None:
        return ExpertEvidence("Futures", False, 0, 0, 0, 0, 0, ("Futures unavailable",))
    bull = bear = 20.0
    rng = 35.0
    reasons: list[str] = []
    move3, move1 = _price_velocity(frame, 3)
    if move3 is not None:
        scale = _clamp(abs(move3) * 3.0, 0, 35)
        if move3 > 0:
            bull += scale
            reasons.append(f"Futures 3m +{move3:.1f} pts")
        elif move3 < 0:
            bear += scale
            reasons.append(f"Futures 3m {move3:.1f} pts")
        else:
            rng += 10
    if move1 is not None and abs(move1) >= 2:
        bonus = _clamp(abs(move1) * 2.0, 0, 15)
        bull += bonus if move1 > 0 else 0
        bear += bonus if move1 < 0 else 0
    setup = str(getattr(activity, "futures_setup", "") or "").upper() if activity is not None else ""
    oi_change = _num(getattr(activity, "futures_oi_change_pct", None)) if activity is not None else None
    if "LONG BUILD" in setup:
        bull += 25; rng -= 8; reasons.append("Futures price↑ + OI↑")
    elif "SHORT BUILD" in setup:
        bear += 25; rng -= 8; reasons.append("Futures price↓ + OI↑")
    elif "SHORT COVER" in setup:
        bull += 16; reasons.append("Futures short covering")
    elif "LONG UNWIND" in setup:
        bear += 16; reasons.append("Futures long unwinding")
    elif "MIXED" in setup:
        rng += 12
    ratio = _num(getattr(activity, "futures_volume_ratio", None)) if activity is not None else None
    if ratio is not None and ratio >= 1.5:
        dominant = max(bull, bear)
        if bull > bear:
            bull += min(15.0, (ratio - 1.0) * 12.0)
        elif bear > bull:
            bear += min(15.0, (ratio - 1.0) * 12.0)
        reasons.append(f"Futures volume {ratio:.2f}×")
    if oi_change is not None:
        reasons.append(f"Futures OI Δ {oi_change:+.2f}%")
    total_signal = max(bull, bear)
    if abs(bull - bear) < 8:
        rng += 15
    reliability = 0.90 if len(frame) >= 4 and setup and "UNAVAILABLE" not in setup and "WARMING" not in setup else 0.68 if len(frame) >= 4 else 0.45
    return ExpertEvidence("Futures", True, _clamp(bull), _clamp(bear), _clamp(rng), reliability, _feed_freshness(snapshot, "future_volume", "quotes"), tuple(reasons[:4]))


def _expert_options(snapshot: Any) -> ExpertEvidence:
    item = getattr(snapshot, "option_intelligence", None)
    if item is None or str(getattr(item, "status", "")).upper() not in {"READY", "WARMING UP", "REFERENCE ONLY"}:
        return ExpertEvidence("Options Flow", False, 0, 0, 0, 0, 0, ("Option intelligence unavailable",))
    bull = float(getattr(item, "bullish_score", 0.0) or 0.0)
    bear = float(getattr(item, "bearish_score", 0.0) or 0.0)
    rng = float(getattr(item, "range_score", 0.0) or 0.0)
    reasons: list[str] = [f"Composite flow {getattr(item, 'market_bias', 'MIXED')}"]
    windows = [w for w in getattr(item, "windows", ()) if str(getattr(w, "status", "")).upper() == "READY"]
    fast_weights = {60: 1.0, 180: 0.75, 300: 0.55}
    signed = 0.0
    available_weight = 0.0
    for window in windows:
        weight = fast_weights.get(int(getattr(window, "target_seconds", 0) or 0), 0.35)
        bias = str(getattr(window, "bias", "") or "").upper()
        if bias == "BULLISH":
            signed += weight
        elif bias == "BEARISH":
            signed -= weight
        available_weight += weight
    if available_weight > 0:
        velocity_bias = signed / available_weight
        if velocity_bias >= 0.35:
            bull += min(18.0, velocity_bias * 18.0); reasons.append("Fast option-flow windows bullish")
        elif velocity_bias <= -0.35:
            bear += min(18.0, abs(velocity_bias) * 18.0); reasons.append("Fast option-flow windows bearish")
        else:
            rng += 8
    confidence = _clamp(float(getattr(item, "confidence", 0.0) or 0.0))
    available = confidence > 0 and (bull + bear + rng) > 0
    return ExpertEvidence("Options Flow", available, _clamp(bull), _clamp(bear), _clamp(rng), max(0.35, confidence / 100.0), _feed_freshness(snapshot, "option_chain"), tuple(reasons[:4]))


def _wall_change(current_wall: Any, previous_wall: Any | None) -> float | None:
    now = _num(getattr(current_wall, "oi", None))
    old = _num(getattr(previous_wall, "oi", None)) if previous_wall is not None else None
    if now is None or old in (None, 0):
        return None
    return (now - float(old)) / abs(float(old)) * 100.0


def _expert_barriers(snapshot: Any, previous_snapshot: Any | None) -> ExpertEvidence:
    item = getattr(snapshot, "barrier_map", None)
    options = getattr(snapshot, "option_intelligence", None)
    if item is None or str(getattr(item, "status", "")).upper() not in {"READY", "REFERENCE ONLY"}:
        return ExpertEvidence("Barriers / Walls", False, 0, 0, 0, 0, 0, ("Barrier map unavailable",))
    bull = bear = 20.0
    rng = 35.0
    reasons: list[str] = []
    resistance = getattr(item, "nearest_resistance", None)
    support = getattr(item, "nearest_support", None)
    if resistance is not None:
        bp = float(getattr(resistance, "break_pressure", 0.0) or 0.0)
        strength = float(getattr(resistance, "strength", 0.0) or 0.0)
        bull += bp * 0.45
        bear += strength * 0.18
        if bp >= 70:
            reasons.append(f"Resistance break pressure {bp:.0f}")
    if support is not None:
        bp = float(getattr(support, "break_pressure", 0.0) or 0.0)
        strength = float(getattr(support, "strength", 0.0) or 0.0)
        bear += bp * 0.45
        bull += strength * 0.18
        if bp >= 70:
            reasons.append(f"Support break pressure {bp:.0f}")
    range_ctx = getattr(item, "trading_range", None)
    if range_ctx is not None:
        confidence = float(getattr(range_ctx, "confidence", 0.0) or 0.0)
        state = str(getattr(range_ctx, "state", "") or "")
        if "STRONG RANGE" in state or "RANGE ACTIVE" in state:
            rng += confidence * 0.45
        reasons.append(f"Range {state}")

    prev_options = getattr(previous_snapshot, "option_intelligence", None) if previous_snapshot is not None else None
    for side, wall, prev_wall in (
        ("CE", getattr(options, "ce_wall", None), getattr(prev_options, "ce_wall", None) if prev_options else None),
        ("PE", getattr(options, "pe_wall", None), getattr(prev_options, "pe_wall", None) if prev_options else None),
    ):
        if wall is None:
            continue
        migration = _num(getattr(wall, "migration_points", None), 0.0) or 0.0
        oi_change = _wall_change(wall, prev_wall)
        if side == "CE":
            if migration > 0:
                bull += min(12, abs(migration) / 10); reasons.append("CE wall migrated upward")
            elif migration < 0:
                bear += min(12, abs(migration) / 10); reasons.append("CE wall migrated downward")
            if oi_change is not None and oi_change <= -8:
                bull += min(12, abs(oi_change) * 0.45); reasons.append(f"CE wall OI weakening {oi_change:.1f}%")
        else:
            if migration > 0:
                bull += min(12, abs(migration) / 10); reasons.append("PE wall migrated upward")
            elif migration < 0:
                bear += min(12, abs(migration) / 10); reasons.append("PE wall migrated downward")
            if oi_change is not None and oi_change <= -8:
                bear += min(12, abs(oi_change) * 0.45); reasons.append(f"PE wall OI weakening {oi_change:.1f}%")
    if abs(bull - bear) < 8:
        rng += 10
    return ExpertEvidence("Barriers / Walls", True, _clamp(bull), _clamp(bear), _clamp(rng), 0.88, _feed_freshness(snapshot, "candles", "option_chain"), tuple(dict.fromkeys(reasons))[:5])


def _expert_breadth(snapshot: Any) -> ExpertEvidence:
    item = getattr(snapshot, "heavyweights", None)
    if item is None or str(getattr(item, "status", "")).upper() not in {"READY", "CAUTION", "REFERENCE ONLY"}:
        return ExpertEvidence("Heavyweight Breadth", False, 0, 0, 0, 0, 0, ("Heavyweight breadth unavailable",))
    move3 = _num(getattr(item, "recent_3m_move_pct", None))
    move15 = _num(getattr(item, "recent_15m_move_pct", None))
    rows = tuple(getattr(item, "rows", ()) or ())
    bull = bear = 18.0
    rng = 32.0
    reasons: list[str] = []
    coverage = _num(getattr(item, "recent_coverage_pct", None), 0.0) or 0.0
    covered = max(_num(getattr(item, "covered_weight_pct", None), 0.0) or 0.0, 0.01)
    coverage_ratio = _clamp(coverage / covered * 100.0) / 100.0
    if move3 is not None:
        strength = min(38.0, abs(move3) * 260.0)
        if move3 > 0.02:
            bull += strength; reasons.append(f"Top-9 3m {move3:+.2f}%")
        elif move3 < -0.02:
            bear += strength; reasons.append(f"Top-9 3m {move3:+.2f}%")
        else:
            rng += 12
    if move15 is not None:
        strength = min(24.0, abs(move15) * 150.0)
        if move15 > 0.03:
            bull += strength
        elif move15 < -0.03:
            bear += strength
        else:
            rng += 8
    votes = []
    for row in rows:
        value = _num(getattr(row, "change_3m_pct", None))
        if value is not None:
            votes.append(value)
    if votes:
        up = sum(value > 0.02 for value in votes)
        down = sum(value < -0.02 for value in votes)
        flat = len(votes) - up - down
        breadth_edge = (up - down) / max(len(votes), 1)
        if breadth_edge > 0.25:
            bull += min(18.0, breadth_edge * 24.0)
        elif breadth_edge < -0.25:
            bear += min(18.0, abs(breadth_edge) * 24.0)
        else:
            rng += 8
        reasons.append(f"Breadth {up}↑/{down}↓/{flat}↔")
    reliability = max(0.35, min(0.95, coverage_ratio))
    return ExpertEvidence("Heavyweight Breadth", True, _clamp(bull), _clamp(bear), _clamp(rng), reliability, _feed_freshness(snapshot, "quotes"), tuple(reasons[:4]))


def _expert_momentum(snapshot: Any) -> ExpertEvidence:
    ind = getattr(snapshot, "indicators", None)
    if ind is None:
        return ExpertEvidence("Momentum Acceleration", False, 0, 0, 0, 0, 0, ("Momentum unavailable",))
    three = ind.three_minute
    frame = getattr(snapshot, "candles_1m", None)
    evidence = 0
    bull = bear = 15.0
    rng = 28.0
    reasons: list[str] = []
    rsi = _num(getattr(three, "rsi14", None))
    prev_rsi = _num(getattr(three, "previous_rsi14", None))
    if rsi is not None and prev_rsi is not None:
        evidence += 1
        delta = rsi - prev_rsi
        if delta >= 1:
            bull += min(28.0, abs(delta) * 4.0); reasons.append(f"RSI velocity +{delta:.1f}")
        elif delta <= -1:
            bear += min(28.0, abs(delta) * 4.0); reasons.append(f"RSI velocity {delta:.1f}")
        else:
            rng += 10
    hist = _num(getattr(three, "macd_histogram", None))
    prev_hist = _num(getattr(three, "previous_macd_histogram", None))
    if hist is not None and prev_hist is not None:
        evidence += 1
        delta = hist - prev_hist
        scale = min(24.0, abs(delta) * 14.0)
        if delta > 0:
            bull += scale; reasons.append("MACD histogram accelerating up")
        elif delta < 0:
            bear += scale; reasons.append("MACD histogram accelerating down")
        else:
            rng += 6
    move3, move1 = _price_velocity(frame, 3)
    if move3 is not None:
        evidence += 1
        strength = min(28.0, abs(move3) * 2.2)
        if move3 > 0:
            bull += strength; reasons.append(f"Spot 3m velocity +{move3:.1f}")
        elif move3 < 0:
            bear += strength; reasons.append(f"Spot 3m velocity {move3:.1f}")
    accel = _body_acceleration(frame)
    if accel is not None and accel >= 1.25 and move1 is not None:
        evidence += 1
        bonus = min(16.0, (accel - 1.0) * 22.0)
        bull += bonus if move1 > 0 else 0
        bear += bonus if move1 < 0 else 0
        reasons.append(f"Candle-body acceleration {accel:.2f}×")
    if evidence == 0:
        return ExpertEvidence("Momentum Acceleration", False, 0, 0, 0, 0, 0, ("Momentum warming up",))
    if abs(bull - bear) < 8:
        rng += 10
    reliability = min(0.95, 0.45 + evidence * 0.12)
    return ExpertEvidence("Momentum Acceleration", True, _clamp(bull), _clamp(bear), _clamp(rng), reliability, _feed_freshness(snapshot, "candles"), tuple(reasons[:4]))


def _volatility_state(snapshot: Any, previous_snapshot: Any | None, atm: dict[str, float | None]) -> tuple[str, float, tuple[str, ...]]:
    speed = getattr(getattr(snapshot, "barrier_map", None), "market_speed", None)
    speed_score = _num(getattr(speed, "score", None), 0.0) or 0.0
    vix = getattr(snapshot, "vix_context", None)
    vix_move = str(getattr(vix, "movement", "") or "").upper()
    compression, release = _range_compression(getattr(snapshot, "candles_1m", None))
    straddle_change = atm.get("straddle_change_pct")
    iv_change = atm.get("iv_change")
    expansion = speed_score * 0.42
    reasons = [f"Market speed {speed_score:.0f}", f"Compression {compression:.0f}"]
    if straddle_change is not None:
        expansion += _clamp(straddle_change * 10.0, -15.0, 25.0)
        reasons.append(f"ATM straddle Δ {straddle_change:+.2f}%")
    if iv_change is not None:
        expansion += _clamp(iv_change * 5.0, -10.0, 18.0)
        reasons.append(f"ATM IV Δ {iv_change:+.2f}")
    if "RISING FAST" in vix_move:
        expansion += 18
    elif "RISING" in vix_move:
        expansion += 10
    if release is not None and release >= 1.6:
        expansion += min(18.0, (release - 1.0) * 15.0)
        reasons.append(f"1m range release {release:.2f}×")
    expansion = _clamp(expansion)
    if expansion >= 82 or "RISING FAST" in vix_move:
        state = "SHOCK" if speed_score >= 85 else "EXPANDING"
    elif expansion >= 58:
        state = "EXPANDING"
    elif compression >= 65 and expansion < 48:
        state = "COMPRESSED"
    else:
        state = "NORMAL"
    return state, round(expansion, 1), tuple(reasons[:4])


def _expert_volatility(snapshot: Any, previous_snapshot: Any | None, atm: dict[str, float | None]) -> ExpertEvidence:
    state, expansion, reasons = _volatility_state(snapshot, previous_snapshot, atm)
    # Volatility is primarily a RANGE vs EXPANSION family, not a directional vote.
    bull = bear = 18.0
    rng = 75.0 if state == "COMPRESSED" else 45.0 if state == "NORMAL" else 20.0
    if state in {"EXPANDING", "SHOCK"}:
        # Give only a weak direction hint from current spot velocity; expansion itself
        # must not invent bullish/bearish direction.
        move, _ = _price_velocity(getattr(snapshot, "candles_1m", None), 3)
        if move is not None and abs(move) >= 3:
            bonus = min(22.0, abs(move) * 1.8)
            bull += bonus if move > 0 else 0
            bear += bonus if move < 0 else 0
    available = getattr(snapshot, "vix_context", None) is not None or getattr(snapshot, "barrier_map", None) is not None
    return ExpertEvidence("Volatility", available, _clamp(bull), _clamp(bear), _clamp(rng), 0.78 if available else 0.0, _feed_freshness(snapshot, "candles", "vix"), reasons)


def _event_risk_high(snapshot: Any) -> bool:
    event = getattr(snapshot, "event_risk", None)
    level = str(getattr(event, "level", "") or "").upper()
    verified = bool(getattr(event, "verified", False))
    if verified and level in {"HIGH", "VERY HIGH", "SEVERE", "CRITICAL"}:
        return True
    news = getattr(snapshot, "news_context", None)
    news_level = str(getattr(news, "risk_level", "") or "").upper()
    news_status = str(getattr(news, "status", "") or "").upper()
    age = _num(getattr(news, "newest_age_minutes", None))
    return news_status == "READY" and news_level in {"HIGH", "SEVERE", "CRITICAL"} and age is not None and age <= 90


def _provisional_regime(snapshot: Any, experts: Iterable[ExpertEvidence]) -> str:
    if _event_risk_high(snapshot):
        return "EVENT-DRIVEN"
    pa = getattr(snapshot, "price_action", None)
    barrier = getattr(snapshot, "barrier_map", None)
    if pa is None:
        return "UNCERTAIN"
    p3, p15 = pa.three_minute, pa.fifteen_minute
    trend_strength = max(float(getattr(p15, "bullish_score", 0.0) or 0.0), float(getattr(p15, "bearish_score", 0.0) or 0.0))
    range_strength = float(getattr(p15, "range_score", 0.0) or 0.0)
    range_ctx = getattr(barrier, "trading_range", None)
    range_conf = float(getattr(range_ctx, "confidence", 0.0) or 0.0) if range_ctx is not None else 0.0
    relation = str(getattr(pa, "relationship", "") or "").upper()
    event3 = str(getattr(p3, "event", "") or "").upper()
    event15 = str(getattr(p15, "event", "") or "").upper()
    if any(token in event3 + " " + event15 for token in ("BREAK", "REVERS", "REJECT", "TRANSITION")) or "CONFLICT" in relation or "OPPOS" in relation:
        return "TRANSITION"
    if trend_strength >= 62 and trend_strength >= range_strength + 10:
        return "TREND"
    if max(range_strength, range_conf) >= 60:
        return "RANGE"
    ready = sum(item.available for item in experts)
    return "TRANSITION" if ready >= 5 else "UNCERTAIN"


def _aggregate(experts: Iterable[ExpertEvidence], regime: str, horizon: str = "15m") -> tuple[float, float, float, float]:
    horizon_mult: dict[str, dict[str, float]] = {
        "5m": {"structure": 1.05, "trend": 0.72, "futures": 1.32, "options": 1.28, "barriers": 1.08, "breadth": 1.08, "volatility": 1.05, "momentum": 1.35},
        "15m": {name: 1.0 for name in _BASE_WEIGHTS},
        "30m": {"structure": 1.12, "trend": 1.30, "futures": 0.82, "options": 0.82, "barriers": 1.08, "breadth": 1.0, "volatility": 1.05, "momentum": 0.72},
    }[horizon]
    regime_mult = _REGIME_MULTIPLIERS.get(regime, _REGIME_MULTIPLIERS["UNCERTAIN"])
    bull_num = bear_num = range_num = denom = 0.0
    max_denom = 0.0
    for expert in experts:
        key = {
            "Structure": "structure",
            "Trend": "trend",
            "Futures": "futures",
            "Options Flow": "options",
            "Barriers / Walls": "barriers",
            "Heavyweight Breadth": "breadth",
            "Volatility": "volatility",
            "Momentum Acceleration": "momentum",
        }[expert.name]
        theoretical = _BASE_WEIGHTS[key] * regime_mult[key] * horizon_mult[key]
        max_denom += theoretical
        if not expert.available:
            continue
        quality = _clamp(expert.reliability * expert.freshness, 0.0, 1.0)
        if quality <= 0:
            continue
        weight = theoretical * quality
        denom += weight
        bull_num += expert.bullish * weight
        bear_num += expert.bearish * weight
        range_num += expert.range_score * weight
    if denom <= 0:
        return 0.0, 0.0, 0.0, 0.0
    coverage = _clamp(denom / max(max_denom, 0.0001) * 100.0)
    return (
        round(_clamp(bull_num / denom), 1),
        round(_clamp(bear_num / denom), 1),
        round(_clamp(range_num / denom), 1),
        round(coverage, 1),
    )


def _conflict(experts: Iterable[ExpertEvidence], final_direction: str) -> tuple[str, float]:
    votes = []
    for expert in experts:
        if not expert.available or expert.reliability * expert.freshness < 0.35:
            continue
        values = {"BULLISH": expert.bullish, "BEARISH": expert.bearish, "RANGE": expert.range_score}
        ordered = sorted(values.items(), key=lambda kv: kv[1], reverse=True)
        if ordered[0][1] - ordered[1][1] < 8:
            votes.append("MIXED")
        else:
            votes.append(ordered[0][0])
    if not votes:
        return "HIGH", 100.0
    directional = [vote for vote in votes if vote != "MIXED"]
    if not directional:
        return "HIGH", 80.0
    target = "RANGE" if final_direction == "MIXED" else final_direction
    disagree = sum(vote != target for vote in directional)
    mixed = len(votes) - len(directional)
    score = _clamp((disagree / len(directional)) * 75.0 + (mixed / len(votes)) * 25.0)
    state = "LOW" if score < 28 else "MEDIUM" if score < 55 else "HIGH"
    return state, round(score, 1)


def _liquidity_sweep(snapshot: Any) -> tuple[str, str]:
    frame = _completed(getattr(snapshot, "candles_1m", None), tail=3)
    barrier = getattr(snapshot, "barrier_map", None)
    if frame.empty or not {"high", "low", "close"}.issubset(frame.columns) or barrier is None:
        return "NONE", ""
    candle = frame.iloc[-1]
    high, low, close = (_num(candle.get(k)) for k in ("high", "low", "close"))
    if None in (high, low, close):
        return "NONE", ""
    support = getattr(barrier, "nearest_support", None)
    resistance = getattr(barrier, "nearest_resistance", None)
    if support is not None:
        lower = _num(getattr(support, "lower", None))
        upper = _num(getattr(support, "upper", None))
        if lower is not None and upper is not None and low < lower and close > upper:
            return "BULLISH LIQUIDITY SWEEP", f"1m pierced support {lower:.0f}-{upper:.0f} and reclaimed"
    if resistance is not None:
        lower = _num(getattr(resistance, "lower", None))
        upper = _num(getattr(resistance, "upper", None))
        if lower is not None and upper is not None and high > upper and close < lower:
            return "BEARISH LIQUIDITY SWEEP", f"1m pierced resistance {lower:.0f}-{upper:.0f} and rejected"
    return "NONE", ""


def _structure_event(snapshot: Any, bull: float, bear: float, range_score: float) -> tuple[str, str, float, str, float, tuple[str, ...]]:
    barrier = getattr(snapshot, "barrier_map", None)
    pa = getattr(snapshot, "price_action", None)
    reasons: list[str] = []
    sweep, sweep_reason = _liquidity_sweep(snapshot)
    if sweep != "NONE":
        reasons.append(sweep_reason)
    resistance = getattr(barrier, "nearest_resistance", None) if barrier is not None else None
    support = getattr(barrier, "nearest_support", None) if barrier is not None else None
    up_break = float(getattr(resistance, "break_pressure", 0.0) or 0.0) if resistance is not None else 0.0
    down_break = float(getattr(support, "break_pressure", 0.0) or 0.0) if support is not None else 0.0
    event_text = " ".join(
        str(getattr(item, "event", "") or "").upper()
        for item in ((pa.three_minute, pa.fifteen_minute) if pa is not None else ())
    )
    breakout_direction = "MIXED"
    breakout_quality = max(up_break, down_break) * 0.45
    if up_break >= down_break + 8:
        breakout_direction = "BULLISH"
        breakout_quality += bull * 0.35 + max(0.0, 100.0 - range_score) * 0.20
        reasons.append(f"Upside break pressure {up_break:.0f}")
    elif down_break >= up_break + 8:
        breakout_direction = "BEARISH"
        breakout_quality += bear * 0.35 + max(0.0, 100.0 - range_score) * 0.20
        reasons.append(f"Downside break pressure {down_break:.0f}")
    else:
        breakout_quality += max(bull, bear) * 0.20
    if "BREAKOUT" in event_text and breakout_direction == "BULLISH":
        breakout_quality += 10
    if "BREAKDOWN" in event_text and breakout_direction == "BEARISH":
        breakout_quality += 10
    breakout_quality = _clamp(breakout_quality)

    trend_dir = _state_direction(getattr(pa.fifteen_minute, "structure", "") if pa is not None else "")
    reversal_direction = "MIXED"
    reversal_quality = 15.0
    if sweep.startswith("BULLISH"):
        reversal_direction = "BULLISH"; reversal_quality += 30
    elif sweep.startswith("BEARISH"):
        reversal_direction = "BEARISH"; reversal_quality += 30
    if "REJECT" in event_text or "REVERS" in event_text or "FAILED" in event_text:
        reversal_quality += 15
    if trend_dir == "BULLISH" and bear >= bull + 10:
        reversal_direction = "BEARISH"; reversal_quality += min(30.0, (bear - bull) * 0.8)
    elif trend_dir == "BEARISH" and bull >= bear + 10:
        reversal_direction = "BULLISH"; reversal_quality += min(30.0, (bull - bear) * 0.8)
    reversal_quality = _clamp(reversal_quality)

    if sweep != "NONE":
        structure = "LIQUIDITY SWEEP"
    elif reversal_quality >= 65:
        structure = "REVERSAL DEVELOPING"
    elif breakout_quality >= 78:
        structure = "BREAKOUT CONFIRMED"
    elif breakout_quality >= 58:
        structure = "BREAKOUT DEVELOPING"
    else:
        structure = "NONE"
    return structure, breakout_direction, round(breakout_quality, 1), reversal_direction, round(reversal_quality, 1), tuple(reasons[:4])


def _institutional_pressure(experts: dict[str, ExpertEvidence], snapshot: Any) -> str:
    futures = experts.get("Futures")
    options = experts.get("Options Flow")
    activity = getattr(snapshot, "big_player_activity", None)
    buy = sell = 0.0
    available = 0
    for expert in (futures, options):
        if expert is None or not expert.available:
            continue
        available += 1
        buy += max(0.0, expert.bullish - expert.bearish)
        sell += max(0.0, expert.bearish - expert.bullish)
    if activity is not None and str(getattr(activity, "status", "")).upper() in {"READY", "PARTIAL", "REFERENCE ONLY"}:
        available += 1
        direction = str(getattr(activity, "direction", "") or "").upper()
        score = float(getattr(activity, "score", 0.0) or 0.0) * 0.6
        if direction == "BUYING":
            buy += score
        elif direction == "SELLING":
            sell += score
    if available < 2:
        return "UNCLEAR"
    if buy >= sell + 18:
        return "BUYING"
    if sell >= buy + 18:
        return "SELLING"
    return "MIXED"


def _expansion_pressure(
    snapshot: Any,
    previous_snapshot: Any | None,
    experts: dict[str, ExpertEvidence],
    volatility_state: str,
    volatility_expansion: float,
    breakout_quality: float,
) -> tuple[float, tuple[str, ...]]:
    compression, release = _range_compression(getattr(snapshot, "candles_1m", None))
    activity = getattr(snapshot, "big_player_activity", None)
    futures_ratio = _num(getattr(activity, "futures_volume_ratio", None), 1.0) or 1.0
    speed = getattr(getattr(snapshot, "barrier_map", None), "market_speed", None)
    speed_score = _num(getattr(speed, "score", None), 0.0) or 0.0
    momentum = experts.get("Momentum Acceleration")
    futures = experts.get("Futures")
    options = experts.get("Options Flow")
    breadth = experts.get("Heavyweight Breadth")
    barriers = experts.get("Barriers / Walls")

    def directional_strength(expert: ExpertEvidence | None) -> float:
        if expert is None or not expert.available:
            return 0.0
        return max(expert.bullish, expert.bearish) - min(expert.bullish, expert.bearish) * 0.25

    components: list[tuple[float, float]] = [
        (compression, 0.16),
        (volatility_expansion, 0.17),
        (speed_score, 0.14),
        (directional_strength(futures), 0.14),
        (directional_strength(options), 0.12),
        (directional_strength(momentum), 0.11),
        (directional_strength(breadth), 0.07),
        (max(directional_strength(barriers), breakout_quality), 0.09),
    ]
    numerator = sum(_clamp(value) * weight for value, weight in components)
    denom = sum(weight for _, weight in components)
    score = numerator / max(denom, 0.001)
    if futures_ratio >= 1.5:
        score += min(8.0, (futures_ratio - 1.0) * 7.0)
    if release is not None and release >= 1.5:
        score += min(10.0, (release - 1.0) * 8.0)
    # Compression is useful before release; once the move is already expanding, do
    # not require compression to have been present in the same snapshot.
    if volatility_state in {"EXPANDING", "SHOCK"}:
        score += 5
    reasons = [
        f"Compression {compression:.0f}",
        f"Market speed {speed_score:.0f}",
        f"Volatility expansion {volatility_expansion:.0f}",
    ]
    if futures_ratio >= 1.2:
        reasons.append(f"Futures volume {futures_ratio:.2f}×")
    return round(_clamp(score), 1), tuple(reasons[:4])


def _impulse_state(expansion: float, direction: str) -> str:
    if expansion < 40:
        return "NORMAL"
    if expansion < 60:
        return "PRESSURE FORMING"
    prefix = "BULLISH " if direction == "BULLISH" else "BEARISH " if direction == "BEARISH" else "LARGE "
    if expansion < 75:
        return prefix + "MOVE BUILDING"
    if expansion < 85:
        return prefix + "STRONG BUILD-UP"
    return prefix + "EXPANSION / HIGH PRESSURE"


def _move_potential(expansion: float) -> str:
    if expansion < 40:
        return "LOW"
    if expansion < 60:
        return "NORMAL"
    if expansion < 80:
        return "STRONG"
    return "EXPANSION"


def _one_brain_direction(snapshot: Any) -> tuple[str, str]:
    simple = (getattr(snapshot, "metadata", {}) or {}).get("simple_brain") or {}
    direction = str(simple.get("direction") or "").upper()
    mapped = "BULLISH" if direction == "UP" else "BEARISH" if direction == "DOWN" else "MIXED"
    common = (getattr(snapshot, "metadata", {}) or {}).get("common_decision") or {}
    action = str(common.get("final_action") or simple.get("final_action") or getattr(getattr(snapshot, "decision", None), "final_action", "WAIT") or "WAIT").upper()
    return mapped, action


def _alignment(one_direction: str, mie_direction: str, action: str, coverage: float, conflict: str, expansion: float) -> str:
    if mie_direction not in {"BULLISH", "BEARISH"} or one_direction not in {"BULLISH", "BEARISH"}:
        return "NO CLEAR ALIGNMENT"
    if one_direction != mie_direction:
        return "SYSTEM CONFLICT"
    if coverage >= 70 and conflict != "HIGH" and expansion >= 60:
        if action != "WAIT":
            return "STRONG EVIDENCE ALIGNMENT"
        return "ALIGNMENT WATCH"
    return "DIRECTION ALIGNED"


def _system_status(snapshot: Any, direction: str, coverage: float, conflict: str, expansion: float, breakout: float, reversal: float) -> str:
    if not bool(getattr(getattr(snapshot, "market_session", None), "is_live", False)):
        return "WAIT"
    critical = getattr(snapshot, "feed_status", {}) or {}
    for key in ("quotes", "candles", "option_chain"):
        feed = critical.get(key)
        if feed is None or str(getattr(feed, "use_state", "") or "").upper() != "LIVE":
            return "WAIT"
    if coverage < 42 or conflict == "HIGH":
        return "WAIT"
    if direction == "MIXED":
        return "WATCH" if expansion >= 58 else "WAIT"
    if expansion >= 78 and coverage >= 68 and conflict == "LOW" and max(breakout, reversal) >= 62:
        return "CONDITIONS MET"
    if expansion >= 60 or max(breakout, reversal) >= 58:
        return "CONDITIONS BUILDING"
    if max(breakout, reversal) >= 42 or coverage >= 65:
        return "WATCH"
    return "WAIT"


def _fake_move_risk(direction: str, breakout_quality: float, experts: dict[str, ExpertEvidence], conflict: str) -> str:
    if direction == "MIXED":
        return "HIGH" if conflict == "HIGH" else "MEDIUM"
    confirmations = 0
    oppositions = 0
    for key in ("Futures", "Options Flow", "Heavyweight Breadth", "Momentum Acceleration"):
        expert = experts.get(key)
        if expert is None or not expert.available:
            continue
        edge = expert.bullish - expert.bearish
        aligned = edge >= 8 if direction == "BULLISH" else edge <= -8
        opposed = edge <= -8 if direction == "BULLISH" else edge >= 8
        confirmations += int(aligned)
        oppositions += int(opposed)
    if breakout_quality >= 72 and confirmations >= 3 and oppositions == 0 and conflict == "LOW":
        return "LOW"
    if breakout_quality < 45 or oppositions >= 2 or conflict == "HIGH":
        return "HIGH"
    return "MEDIUM"


def _invalidation(snapshot: Any, direction: str, structure_event: str) -> str:
    pa = getattr(snapshot, "price_action", None)
    levels = getattr(snapshot, "levels", None)
    if direction == "BULLISH":
        value = _num(getattr(getattr(pa, "three_minute", None), "invalidation_level", None))
        if value is None:
            value = _num(getattr(getattr(levels, "immediate_support", None), "midpoint", None))
        return f"Bullish view weak below {value:,.0f}" if value is not None else "Bullish view invalidates on 3m structure failure"
    if direction == "BEARISH":
        value = _num(getattr(getattr(pa, "three_minute", None), "invalidation_level", None))
        if value is None:
            value = _num(getattr(getattr(levels, "immediate_resistance", None), "midpoint", None))
        return f"Bearish view weak above {value:,.0f}" if value is not None else "Bearish view invalidates on 3m structure failure"
    return "Mixed view — wait for structure resolution"


def _alerts(
    *, snapshot: Any, direction: str, expansion: float, velocity: float | None,
    system_status: str, alignment: str, coverage: float, conflict: str,
    fake_risk: str, previous: dict[str, Any] | None,
) -> tuple[dict[str, Any], ...]:
    if not bool(getattr(getattr(snapshot, "market_session", None), "is_live", False)):
        return ()
    output: list[dict[str, Any]] = []
    previous_expansion = _num((previous or {}).get("expansion_pressure"))
    previous_state = str((previous or {}).get("impulse_state") or "")
    spot = _num((getattr(snapshot, "nifty_quote", {}) or {}).get("last_price"))
    stamp = getattr(snapshot, "created_at", None)

    def add(kind: str, priority: str, title: str, message: str) -> None:
        output.append({
            "kind": kind,
            "priority": priority,
            "direction": direction,
            "title": title,
            "message": message,
            "captured_at": stamp.isoformat() if stamp is not None else "",
            "nifty_ltp": spot,
            "score": round(expansion, 1),
        })

    crossed_60 = expansion >= 60 and (previous_expansion is None or previous_expansion < 60)
    crossed_75 = expansion >= 75 and (previous_expansion is None or previous_expansion < 75)
    if crossed_60 and coverage >= 55 and conflict != "HIGH":
        label = direction if direction != "MIXED" else "DIRECTION UNCLEAR"
        add("PRESSURE_WATCH", "WATCH", "Pressure building", f"{label} · Expansion pressure {expansion:.0f}/100 · {system_status}")
    if crossed_75 and coverage >= 65 and conflict != "HIGH":
        add("STRONG_BUILDUP", "HIGH", "Strong move build-up", f"{direction} · Expansion pressure {expansion:.0f}/100 · Evidence coverage {coverage:.0f}%")
    if alignment in {"ALIGNMENT WATCH", "STRONG EVIDENCE ALIGNMENT"} and fake_risk != "HIGH":
        previous_alignment = str((previous or {}).get("one_brain_alignment") or "")
        if alignment != previous_alignment:
            add("ONE_BRAIN_ALIGNMENT", "HIGH" if alignment.startswith("STRONG") else "WATCH", "One Brain alignment", f"One Brain + Market Intelligence {direction} aligned · {alignment}")
    if alignment == "SYSTEM CONFLICT":
        previous_alignment = str((previous or {}).get("one_brain_alignment") or "")
        if previous_alignment != alignment:
            add("SYSTEM_CONFLICT", "CAUTION", "System conflict", "One Brain and Market Intelligence direction disagree — do not chase")
    if previous_expansion is not None and previous_expansion >= 72 and expansion <= previous_expansion - 20 and expansion < 60:
        add("BUILDUP_FAILED", "CAUTION", "Build-up failed", f"Expansion pressure fell {previous_expansion:.0f} → {expansion:.0f}")
    # Rapid pressure acceleration is informative even before absolute threshold.
    if velocity is not None and velocity >= 18 and expansion >= 52 and not any(item["kind"] in {"PRESSURE_WATCH", "STRONG_BUILDUP"} for item in output):
        add("PRESSURE_ACCELERATION", "WATCH", "Pressure accelerating", f"Expansion pressure velocity +{velocity:.0f} since prior snapshot")
    return tuple(output[:4])


def calculate_market_intelligence(snapshot: Any, previous_snapshot: Any | None = None) -> MarketIntelligenceResult:
    """Calculate shadow-only Market Intelligence from one authoritative snapshot."""
    atm = _atm_option_metrics(snapshot, previous_snapshot)
    initial_experts = [
        _expert_structure(snapshot),
        _expert_trend(snapshot),
        _expert_futures(snapshot),
        _expert_options(snapshot),
        _expert_barriers(snapshot, previous_snapshot),
        _expert_breadth(snapshot),
        _expert_momentum(snapshot),
    ]
    volatility_expert = _expert_volatility(snapshot, previous_snapshot, atm)
    experts = tuple([*initial_experts, volatility_expert])
    regime = _provisional_regime(snapshot, experts)
    bull, bear, rng, coverage = _aggregate(experts, regime, "15m")

    # Direction requires a meaningful edge.  A high range score is not silently
    # converted into bullish/bearish direction.
    if bull >= 55 and bull >= bear + 8 and bull >= rng - 3:
        direction = "BULLISH"
    elif bear >= 55 and bear >= bull + 8 and bear >= rng - 3:
        direction = "BEARISH"
    else:
        direction = "MIXED"

    volatility_state, volatility_expansion, volatility_reasons = _volatility_state(snapshot, previous_snapshot, atm)
    structure_event, breakout_direction, breakout_quality, reversal_direction, reversal_quality, structure_reasons = _structure_event(snapshot, bull, bear, rng)
    expert_map = {item.name: item for item in experts}
    expansion, expansion_reasons = _expansion_pressure(
        snapshot, previous_snapshot, expert_map, volatility_state, volatility_expansion, breakout_quality
    )

    previous_mie = None
    if previous_snapshot is not None:
        previous_mie = (getattr(previous_snapshot, "metadata", {}) or {}).get("market_intelligence")
    previous_expansion = _num((previous_mie or {}).get("expansion_pressure"))
    velocity = round(expansion - previous_expansion, 1) if previous_expansion is not None else None
    previous_direction = str((previous_mie or {}).get("direction") or "")
    previous_persistence = int((previous_mie or {}).get("pressure_persistence") or 0)
    persistence = previous_persistence + 1 if previous_direction == direction and expansion >= 60 else 1 if expansion >= 60 else 0

    conflict, conflict_score = _conflict(experts, direction)
    institutional = _institutional_pressure(expert_map, snapshot)
    impulse = _impulse_state(expansion, direction)
    potential = _move_potential(expansion)
    one_direction, action = _one_brain_direction(snapshot)
    alignment = _alignment(one_direction, direction, action, coverage, conflict, expansion)
    fake_risk = _fake_move_risk(direction, breakout_quality, expert_map, conflict)
    status = _system_status(snapshot, direction, coverage, conflict, expansion, breakout_quality, reversal_quality)

    path5 = _aggregate(experts, regime, "5m")
    path15 = (bull, bear, rng, coverage)
    path30 = _aggregate(experts, regime, "30m")
    path_5m = PathEvidence(path5[0], path5[1], path5[2])
    path_15m = PathEvidence(path15[0], path15[1], path15[2])
    path_30m = PathEvidence(path30[0], path30[1], path30[2])

    alerts = _alerts(
        snapshot=snapshot, direction=direction, expansion=expansion, velocity=velocity,
        system_status=status, alignment=alignment, coverage=coverage, conflict=conflict,
        fake_risk=fake_risk, previous=previous_mie,
    )

    reasons = tuple(dict.fromkeys([
        *structure_reasons,
        *expansion_reasons,
        *volatility_reasons,
        f"Institutional pressure {institutional}",
        f"Evidence coverage {coverage:.0f}% · conflict {conflict}",
    ]))[:8]
    cautions: list[str] = [
        "Shadow-only: zero One-Brain decision weight",
        "Scores are evidence/path scores, not calibrated probabilities",
    ]
    if coverage < 60:
        cautions.append("Evidence coverage limited — missing evidence has NO VOTE")
    if conflict == "HIGH":
        cautions.append("Experts materially disagree")
    if not bool(getattr(getattr(snapshot, "market_session", None), "is_live", False)):
        cautions.append("Market closed/reference data")

    return MarketIntelligenceResult(
        engine="ONE_BRAIN_MARKET_INTELLIGENCE_V1",
        mode="SHADOW_ONLY_ZERO_CORE_WEIGHT",
        status="READY" if coverage >= 45 else "PARTIAL",
        market_state=regime,
        direction=direction,
        bull_pressure=round(bull, 1),
        bear_pressure=round(bear, 1),
        range_pressure=round(rng, 1),
        expansion_pressure=round(expansion, 1),
        pressure_velocity=velocity,
        pressure_persistence=persistence,
        impulse_state=impulse,
        move_potential=potential,
        volatility_state=volatility_state,
        structure_event=structure_event,
        breakout_direction=breakout_direction,
        breakout_quality=breakout_quality,
        reversal_direction=reversal_direction,
        reversal_quality=reversal_quality,
        institutional_pressure=institutional,
        evidence_coverage=round(coverage, 1),
        evidence_conflict=conflict,
        conflict_score=conflict_score,
        fake_move_risk=fake_risk,
        system_status=status,
        path_5m=path_5m,
        path_15m=path_15m,
        path_30m=path_30m,
        one_brain_direction=one_direction,
        one_brain_alignment=alignment,
        invalidation=_invalidation(snapshot, direction, structure_event),
        alerts=alerts,
        experts=experts,
        reasons=reasons,
        cautions=tuple(cautions[:5]),
    )
