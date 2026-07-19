from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import pandas as pd

from config import CONFIG
from models import FlowWindow, OIWall, OptionIntelligence, PCRBundle


NUMERIC_COLUMNS = (
    "strike",
    "last_price",
    "oi",
    "volume",
    "previous_oi",
    "previous_volume",
    "previous_close_price",
    "day_oi_change",
    "day_price_change",
    "implied_volatility",
)


def _rows_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    for column in NUMERIC_COLUMNS:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["side"] = frame["side"].astype(str).str.upper()
    return frame.sort_values(["strike", "side"]).reset_index(drop=True)


def _safe_sum(series: pd.Series) -> float:
    return float(pd.to_numeric(series, errors="coerce").fillna(0.0).sum())


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _classify_flow(
    *,
    price_delta: float,
    oi_delta: float,
    premium_reference: float,
    oi_reference: float,
) -> str:
    price_threshold = max(
        CONFIG.option_min_price_move_points,
        abs(premium_reference) * CONFIG.option_min_price_move_pct / 100.0,
    )
    oi_threshold = max(1.0, abs(oi_reference) * CONFIG.option_min_oi_move_pct / 100.0)
    price_up = price_delta >= price_threshold
    price_down = price_delta <= -price_threshold
    oi_up = oi_delta >= oi_threshold
    oi_down = oi_delta <= -oi_threshold
    if price_up and oi_up:
        return "LONG BUILDUP"
    if price_down and oi_up:
        return "SHORT BUILDUP"
    if price_up and oi_down:
        return "SHORT COVERING"
    if price_down and oi_down:
        return "LONG UNWINDING"
    return "NOISE / FLAT"


def _direction_for(side: str, classification: str) -> str:
    mapping = {
        ("CE", "LONG BUILDUP"): "BULLISH",
        ("CE", "SHORT BUILDUP"): "BEARISH",
        ("CE", "SHORT COVERING"): "BULLISH",
        ("CE", "LONG UNWINDING"): "BEARISH",
        ("PE", "LONG BUILDUP"): "BEARISH",
        ("PE", "SHORT BUILDUP"): "BULLISH",
        ("PE", "SHORT COVERING"): "BEARISH",
        ("PE", "LONG UNWINDING"): "BULLISH",
    }
    return mapping.get((side, classification), "NEUTRAL")


def _flow_strength(
    *,
    oi_delta: float,
    oi_reference: float,
    volume_delta: float,
    strike: float,
    spot: float,
) -> float:
    oi_fraction = abs(oi_delta) / max(abs(oi_reference), 1.0)
    oi_component = min(
        3.0, oi_fraction * 100.0 / max(CONFIG.option_min_oi_move_pct, 0.01)
    )
    volume_component = 1.20 if volume_delta > 0 else 0.80
    distance = abs(strike - spot)
    proximity_component = max(0.55, 1.0 - distance / 500.0)
    return round(oi_component * volume_component * proximity_component, 4)


def _merge_flow(
    current: pd.DataFrame,
    previous: pd.DataFrame | None,
    spot: float,
) -> tuple[pd.DataFrame, str]:
    base = current.copy()
    if base.empty:
        return base, "UNAVAILABLE"
    if previous is not None and not previous.empty:
        prior = previous[["strike", "side", "last_price", "oi", "volume"]].rename(
            columns={
                "last_price": "prior_last_price",
                "oi": "prior_oi",
                "volume": "prior_volume",
            }
        )
        base = base.merge(prior, on=["strike", "side"], how="left")
        base["price_delta"] = base["last_price"] - base["prior_last_price"]
        base["oi_delta"] = base["oi"] - base["prior_oi"]
        base["volume_delta"] = (base["volume"] - base["prior_volume"]).clip(lower=0)
        basis = "INTRADAY SNAPSHOT DELTA"
    else:
        base["prior_last_price"] = base.get("previous_close_price")
        base["prior_oi"] = base.get("previous_oi")
        base["prior_volume"] = base.get("previous_volume")
        base["price_delta"] = base.get("day_price_change", 0.0)
        base["oi_delta"] = base.get("day_oi_change", 0.0)
        base["volume_delta"] = pd.to_numeric(
            base.get("volume", 0.0), errors="coerce"
        ).fillna(0.0)
        basis = "DAY CHANGE — FIRST SNAPSHOT"

    rows: list[dict[str, Any]] = []
    for raw in base.to_dict(orient="records"):
        strike = float(raw.get("strike") or 0.0)
        side = str(raw.get("side") or "").upper()
        premium = float(raw.get("last_price") or 0.0)
        oi = float(raw.get("oi") or 0.0)
        price_delta = float(raw.get("price_delta") or 0.0)
        oi_delta = float(raw.get("oi_delta") or 0.0)
        volume_delta = float(raw.get("volume_delta") or 0.0)
        classification = _classify_flow(
            price_delta=price_delta,
            oi_delta=oi_delta,
            premium_reference=premium,
            oi_reference=oi,
        )
        direction = _direction_for(side, classification)
        strength = _flow_strength(
            oi_delta=oi_delta,
            oi_reference=oi,
            volume_delta=volume_delta,
            strike=strike,
            spot=spot,
        )
        raw.update(
            {
                "price_delta": round(price_delta, 4),
                "oi_delta": round(oi_delta, 4),
                "volume_delta": round(volume_delta, 4),
                "classification": classification,
                "directional_bias": direction,
                "flow_strength": strength,
            }
        )
        rows.append(raw)
    return pd.DataFrame(rows).sort_values(["strike", "side"]).reset_index(
        drop=True
    ), basis


def _choose_history_sample(
    history: list[dict[str, Any]],
    current_time: datetime,
    target_seconds: int,
) -> tuple[dict[str, Any] | None, float | None]:
    tolerance = target_seconds * CONFIG.option_window_tolerance_ratio
    minimum_age = max(10.0, target_seconds - tolerance)
    maximum_age = target_seconds + tolerance
    candidates: list[tuple[float, dict[str, Any]]] = []
    for item in history:
        try:
            captured_at = datetime.fromisoformat(str(item["captured_at"]))
        except (KeyError, TypeError, ValueError):
            continue
        age = (current_time - captured_at).total_seconds()
        if minimum_age <= age <= maximum_age:
            candidates.append((abs(age - target_seconds), item))
    if not candidates:
        return None, None
    _, chosen = min(candidates, key=lambda pair: pair[0])
    actual = (
        current_time - datetime.fromisoformat(str(chosen["captured_at"]))
    ).total_seconds()
    return chosen, round(actual, 1)


def _window(
    *,
    label: str,
    target_seconds: int,
    current: pd.DataFrame,
    spot: float,
    current_time: datetime,
    history: list[dict[str, Any]],
) -> FlowWindow:
    sample, age = _choose_history_sample(history, current_time, target_seconds)
    if sample is None:
        return FlowWindow(
            label=label,
            target_seconds=target_seconds,
            actual_age_seconds=None,
            ce_oi_delta=None,
            pe_oi_delta=None,
            ce_premium_delta=None,
            pe_premium_delta=None,
            ce_volume_delta=None,
            pe_volume_delta=None,
            bias="UNAVAILABLE",
            status="INSUFFICIENT CONTINUITY",
        )
    previous = _rows_to_frame(sample.get("rows") or [])
    flow, _ = _merge_flow(current, previous, spot)
    if flow.empty:
        bias = "UNAVAILABLE"
    else:
        directional = flow.apply(
            lambda row: row["flow_strength"]
            if row["directional_bias"] == "BULLISH"
            else -row["flow_strength"]
            if row["directional_bias"] == "BEARISH"
            else 0.0,
            axis=1,
        ).sum()
        bias = (
            "BULLISH"
            if directional > 0.75
            else "BEARISH"
            if directional < -0.75
            else "MIXED"
        )

    def side_sum(side: str, column: str) -> float:
        return round(_safe_sum(flow.loc[flow["side"] == side, column]), 4)

    return FlowWindow(
        label=label,
        target_seconds=target_seconds,
        actual_age_seconds=age,
        ce_oi_delta=side_sum("CE", "oi_delta"),
        pe_oi_delta=side_sum("PE", "oi_delta"),
        ce_premium_delta=side_sum("CE", "price_delta"),
        pe_premium_delta=side_sum("PE", "price_delta"),
        ce_volume_delta=side_sum("CE", "volume_delta"),
        pe_volume_delta=side_sum("PE", "volume_delta"),
        bias=bias,
        status="READY",
    )


def _wall(
    side: str,
    current: pd.DataFrame,
    previous: pd.DataFrame | None,
) -> OIWall:
    subset = current[current["side"] == side].dropna(subset=["strike", "oi"]).copy()
    if subset.empty:
        return OIWall(side, None, None, None, None, None, None, "UNAVAILABLE")
    top = subset.loc[subset["oi"].idxmax()]
    strike = float(top["strike"])
    oi = float(top["oi"])
    previous_strike = None
    migration = None
    if previous is not None and not previous.empty:
        prior = previous[previous["side"] == side].dropna(subset=["strike", "oi"])
        if not prior.empty:
            previous_strike = float(prior.loc[prior["oi"].idxmax(), "strike"])
            migration = round(strike - previous_strike, 2)

    strikes = sorted(subset["strike"].unique())
    cluster_values: list[tuple[float, float]] = []
    for center in strikes:
        index = strikes.index(center)
        selected = set(strikes[max(0, index - 1) : min(len(strikes), index + 2)])
        cluster_oi = _safe_sum(subset.loc[subset["strike"].isin(selected), "oi"])
        cluster_values.append((cluster_oi, float(center)))
    cluster_oi, cluster_center = max(cluster_values)
    return OIWall(
        side=side,
        strike=strike,
        oi=round(oi, 2),
        previous_strike=previous_strike,
        migration_points=migration,
        cluster_center=cluster_center,
        cluster_oi=round(cluster_oi, 2),
        status="READY",
    )


def _pcr(current: pd.DataFrame, flow: pd.DataFrame) -> PCRBundle:
    ce = current[current["side"] == "CE"]
    pe = current[current["side"] == "PE"]
    ce_oi = _safe_sum(ce["oi"])
    pe_oi = _safe_sum(pe["oi"])
    ce_day_add = _safe_sum(
        ce.get("day_oi_change", pd.Series(dtype=float)).clip(lower=0)
    )
    pe_day_add = _safe_sum(
        pe.get("day_oi_change", pd.Series(dtype=float)).clip(lower=0)
    )
    ce_volume = _safe_sum(ce["volume"])
    pe_volume = _safe_sum(pe["volume"])
    ce_intraday = _safe_sum(flow.loc[flow["side"] == "CE", "oi_delta"].clip(lower=0))
    pe_intraday = _safe_sum(flow.loc[flow["side"] == "PE", "oi_delta"].clip(lower=0))
    oi_pcr = _ratio(pe_oi, ce_oi)
    day_pcr = _ratio(pe_day_add, ce_day_add)
    intraday_pcr = _ratio(pe_intraday, ce_intraday)
    volume_pcr = _ratio(pe_volume, ce_volume)
    context_value = (
        intraday_pcr
        if intraday_pcr is not None
        else day_pcr
        if day_pcr is not None
        else oi_pcr
    )
    if context_value is None:
        state = "UNAVAILABLE"
    elif context_value >= 1.20:
        state = "PE OI DOMINANT"
    elif context_value <= 0.80:
        state = "CE OI DOMINANT"
    else:
        state = "BALANCED"
    return PCRBundle(
        near_atm_oi_pcr=oi_pcr,
        day_addition_pcr=day_pcr,
        intraday_addition_pcr=intraday_pcr,
        volume_pcr=volume_pcr,
        state=state,
        status="READY" if oi_pcr is not None else "UNAVAILABLE",
    )


def _snapshot_bias(current: dict[str, Any], previous: dict[str, Any]) -> str:
    current_frame = _rows_to_frame(current.get("rows") or [])
    previous_frame = _rows_to_frame(previous.get("rows") or [])
    if current_frame.empty or previous_frame.empty:
        return "UNAVAILABLE"
    spot = float(current.get("spot") or current_frame["strike"].median())
    flow, _ = _merge_flow(current_frame, previous_frame, spot)
    score = 0.0
    for row in flow.to_dict(orient="records"):
        if row["directional_bias"] == "BULLISH":
            score += float(row["flow_strength"])
        elif row["directional_bias"] == "BEARISH":
            score -= float(row["flow_strength"])
    return "BULLISH" if score > 0.75 else "BEARISH" if score < -0.75 else "MIXED"


def _persistence(
    history: list[dict[str, Any]], current_snapshot: dict[str, Any], current_bias: str
) -> str:
    sequence = [*history, current_snapshot]
    pair_biases: list[str] = []
    for index in range(
        max(1, len(sequence) - CONFIG.option_persistence_lookback), len(sequence)
    ):
        if index <= 0:
            continue
        pair_biases.append(_snapshot_bias(sequence[index], sequence[index - 1]))
    usable = [item for item in pair_biases if item in {"BULLISH", "BEARISH", "MIXED"}]
    if not usable:
        return "WARMING UP"
    trailing = 0
    for item in reversed(usable):
        if item == current_bias:
            trailing += 1
        else:
            break
    if current_bias in {"BULLISH", "BEARISH"} and trailing >= 3:
        return f"{current_bias} PERSISTENT ×{trailing}"
    if current_bias in {"BULLISH", "BEARISH"} and trailing >= 2:
        return f"{current_bias} DEVELOPING ×{trailing}"
    return "MIXED / NOT PERSISTENT"


def _normalized_scores(
    flow: pd.DataFrame, pcr: PCRBundle
) -> tuple[float, float, float, str]:
    bullish = 0.0
    bearish = 0.0
    neutral = 1.0
    for row in flow.to_dict(orient="records"):
        strength = float(row.get("flow_strength") or 0.0)
        direction = row.get("directional_bias")
        if direction == "BULLISH":
            bullish += strength
        elif direction == "BEARISH":
            bearish += strength
        else:
            neutral += max(0.25, strength * 0.25)
    pcr_value = pcr.intraday_addition_pcr or pcr.day_addition_pcr or pcr.near_atm_oi_pcr
    if pcr_value is not None:
        if pcr_value >= 1.20:
            bullish += min(2.0, (pcr_value - 1.0) * 2.0)
        elif pcr_value <= 0.80:
            bearish += min(2.0, (1.0 - pcr_value) * 2.0)
        else:
            neutral += 0.5
    total = bullish + bearish + neutral
    if total <= 0:
        return 0.0, 0.0, 100.0, "MIXED"
    bull_pct = round(bullish / total * 100.0, 1)
    bear_pct = round(bearish / total * 100.0, 1)
    range_pct = round(max(0.0, 100.0 - bull_pct - bear_pct), 1)
    bias = (
        "BULLISH"
        if bull_pct >= max(bear_pct, range_pct) + 8
        else "BEARISH"
        if bear_pct >= max(bull_pct, range_pct) + 8
        else "MIXED"
    )
    return bull_pct, bear_pct, range_pct, bias


def calculate_option_intelligence(
    *,
    current_frame: pd.DataFrame,
    spot: float,
    expiry: str | None,
    captured_at: datetime,
    history: list[dict[str, Any]],
    current_snapshot: dict[str, Any],
    is_live: bool,
) -> OptionIntelligence:
    empty_wall_ce = OIWall("CE", None, None, None, None, None, None, "UNAVAILABLE")
    empty_wall_pe = OIWall("PE", None, None, None, None, None, None, "UNAVAILABLE")
    empty_pcr = PCRBundle(None, None, None, None, "UNAVAILABLE", "UNAVAILABLE")
    empty_windows = tuple(
        FlowWindow(
            label,
            seconds,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "UNAVAILABLE",
            "INSUFFICIENT CONTINUITY",
        )
        for label, seconds in (("1 minute", 60), ("3 minute", 180), ("5 minute", 300))
    )
    if current_frame.empty or not expiry:
        return OptionIntelligence(
            as_of=captured_at,
            basis="UNAVAILABLE",
            snapshot_count=len(history),
            bullish_score=0.0,
            bearish_score=0.0,
            range_score=100.0,
            confidence=0.0,
            market_bias="UNAVAILABLE",
            persistence="UNAVAILABLE",
            ce_wall=empty_wall_ce,
            pe_wall=empty_wall_pe,
            pcr=empty_pcr,
            windows=empty_windows,
            flow_rows=(),
            reasons=(),
            blockers=("Option chain unavailable",),
            status="UNAVAILABLE",
        )

    current = current_frame.copy()
    for column in NUMERIC_COLUMNS:
        if column in current.columns:
            current[column] = pd.to_numeric(current[column], errors="coerce")
    previous_snapshot = history[-1] if history else None
    previous_frame = (
        _rows_to_frame(previous_snapshot.get("rows") or [])
        if previous_snapshot
        else None
    )
    flow, basis = _merge_flow(current, previous_frame, spot)
    pcr = _pcr(current, flow)
    ce_wall = _wall("CE", current, previous_frame)
    pe_wall = _wall("PE", current, previous_frame)
    windows = tuple(
        _window(
            label=label,
            target_seconds=seconds,
            current=current,
            spot=spot,
            current_time=captured_at,
            history=history,
        )
        for label, seconds in (("1 minute", 60), ("3 minute", 180), ("5 minute", 300))
    )
    bullish, bearish, range_score, market_bias = _normalized_scores(flow, pcr)
    persistence = _persistence(history, current_snapshot, market_bias)
    ready_windows = sum(item.status == "READY" for item in windows)
    if previous_snapshot is None:
        confidence = 38.0
    else:
        confidence = 58.0 + ready_windows * 8.0
        if persistence.endswith("×3") or "PERSISTENT" in persistence:
            confidence += 8.0
    confidence = round(min(90.0, confidence), 1)

    reasons: list[str] = []
    if market_bias != "MIXED":
        reasons.append(f"Option flow mix is {market_bias}")
    reasons.append(pcr.state)
    if ce_wall.strike is not None and pe_wall.strike is not None:
        reasons.append(f"CE wall {ce_wall.strike:.0f} / PE wall {pe_wall.strike:.0f}")
    blockers: list[str] = []
    if previous_snapshot is None:
        blockers.append("First snapshot: intraday delta is warming up")
    if ready_windows < 3:
        blockers.append(f"Movement windows ready {ready_windows}/3")
    if not is_live:
        blockers.append("Reference-only market session")
    status = (
        "REFERENCE ONLY"
        if not is_live
        else "READY"
        if previous_snapshot is not None
        else "WARMING UP"
    )

    display_columns = (
        "strike",
        "side",
        "is_atm",
        "last_price",
        "price_delta",
        "oi",
        "oi_delta",
        "volume",
        "volume_delta",
        "classification",
        "directional_bias",
        "flow_strength",
        "implied_volatility",
    )
    flow_rows = tuple(
        {
            key: (None if isinstance(value, float) and math.isnan(value) else value)
            for key, value in row.items()
            if key in display_columns
        }
        for row in flow.to_dict(orient="records")
    )
    return OptionIntelligence(
        as_of=captured_at,
        basis=basis,
        snapshot_count=len(history) + (1 if is_live else 0),
        bullish_score=bullish,
        bearish_score=bearish,
        range_score=range_score,
        confidence=confidence,
        market_bias=market_bias,
        persistence=persistence,
        ce_wall=ce_wall,
        pe_wall=pe_wall,
        pcr=pcr,
        windows=windows,
        flow_rows=flow_rows,
        reasons=tuple(reasons[:3]),
        blockers=tuple(blockers),
        status=status,
    )
