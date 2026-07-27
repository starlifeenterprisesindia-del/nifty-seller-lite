from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from analysis.technical_utils import clamp
from config import CONFIG
from models import LevelBundle, OptionIntelligence, PreTouchBarrier, PreTouchBarrierBundle


@dataclass(frozen=True)
class _Anchor:
    price: float
    weight: float
    source: str


def _valid_price(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _zone_half_width(levels: LevelBundle) -> float:
    if levels.zone_width is not None and levels.zone_width > 0:
        return max(3.0, min(15.0, float(levels.zone_width) / 2.0))
    return 6.0


def _append_level_anchors(
    anchors: list[_Anchor],
    *,
    midpoint: float | None,
    strength: float | None,
    sources: Iterable[str] = (),
    default_source: str,
) -> None:
    price = _valid_price(midpoint)
    if price is None:
        return
    source_list = tuple(str(item) for item in sources if str(item).strip())
    label = " + ".join(source_list[:3]) if source_list else default_source
    anchors.append(
        _Anchor(
            price=price,
            weight=clamp(float(strength or 55.0), 35.0, 100.0),
            source=label,
        )
    )


def _pick_barrier(
    *,
    side: str,
    spot: float,
    anchors: list[_Anchor],
    half_width: float,
) -> PreTouchBarrier | None:
    if side == "RESISTANCE":
        relevant = [item for item in anchors if item.price >= spot - half_width]
        relevant.sort(key=lambda item: max(0.0, item.price - spot))
    else:
        relevant = [item for item in anchors if item.price <= spot + half_width]
        relevant.sort(key=lambda item: max(0.0, spot - item.price))
    if not relevant:
        return None

    seed = relevant[0]
    merge_distance = max(12.0, half_width * 2.2)
    cluster = [item for item in relevant if abs(item.price - seed.price) <= merge_distance]
    total_weight = sum(max(1.0, item.weight) for item in cluster)
    midpoint = sum(item.price * max(1.0, item.weight) for item in cluster) / total_weight

    distinct_sources: list[str] = []
    for item in cluster:
        for token in item.source.split(" + "):
            clean = token.strip()
            if clean and clean not in distinct_sources:
                distinct_sources.append(clean)

    base_strength = sum(item.weight for item in cluster) / len(cluster)
    confluence_bonus = min(22.0, max(0, len(distinct_sources) - 1) * 6.0)
    strength = round(clamp(base_strength * 0.80 + 15.0 + confluence_bonus, 0.0, 96.0), 1)
    lower = midpoint - half_width
    upper = midpoint + half_width
    if side == "RESISTANCE":
        distance = max(0.0, lower - spot)
    else:
        distance = max(0.0, spot - upper)

    if distance <= 0:
        proximity = "AT ZONE"
    elif distance <= CONFIG.pretouch_warning_distance_points:
        proximity = "VERY NEAR"
    elif distance <= CONFIG.pretouch_watch_distance_points:
        proximity = "AHEAD"
    else:
        proximity = "FAR"

    source_text = " + ".join(distinct_sources[:4]) or "market structure"
    if side == "RESISTANCE":
        message = (
            f"Upar {lower:,.0f}–{upper:,.0f} ke paas resistance aa sakta hai; "
            f"spot se lagbhag {distance:.0f} points door. Karan: {source_text}."
        )
    else:
        message = (
            f"Neeche {lower:,.0f}–{upper:,.0f} ke paas support aa sakta hai; "
            f"spot se lagbhag {distance:.0f} points door. Karan: {source_text}."
        )

    return PreTouchBarrier(
        side=side,
        lower=round(lower, 2),
        upper=round(upper, 2),
        midpoint=round(midpoint, 2),
        strength=strength,
        distance_points=round(distance, 1),
        proximity=proximity,
        sources=tuple(distinct_sources[:6]),
        message=message,
    )


def calculate_pre_touch_barriers(
    *,
    levels: LevelBundle,
    options: OptionIntelligence,
    spot: float,
) -> PreTouchBarrierBundle:
    """Build forward-looking support/resistance zones before the price touches them.

    This is an early-warning layer, not a second strategy brain. It combines the already
    calculated price-structure levels with current CE/PE OI walls and clusters.
    """

    if spot <= 0:
        return PreTouchBarrierBundle(support=None, resistance=None, status="UNAVAILABLE")

    half_width = _zone_half_width(levels)
    resistance_anchors: list[_Anchor] = []
    support_anchors: list[_Anchor] = []

    if levels.immediate_resistance is not None:
        _append_level_anchors(
            resistance_anchors,
            midpoint=levels.immediate_resistance.midpoint,
            strength=levels.immediate_resistance.strength,
            sources=levels.immediate_resistance.sources,
            default_source="Immediate Resistance",
        )
    if levels.strong_resistance is not None:
        _append_level_anchors(
            resistance_anchors,
            midpoint=levels.strong_resistance.midpoint,
            strength=levels.strong_resistance.strength,
            sources=levels.strong_resistance.sources,
            default_source="Strong Resistance",
        )
    if levels.previous_day_high is not None:
        resistance_anchors.append(_Anchor(levels.previous_day_high, 78.0, "Previous Day High"))
    if levels.opening_range_high is not None:
        resistance_anchors.append(_Anchor(levels.opening_range_high, 72.0, "Opening Range High"))
    if options.ce_wall.strike is not None:
        resistance_anchors.append(_Anchor(float(options.ce_wall.strike), 84.0, "CE OI Wall"))
    if options.ce_wall.cluster_center is not None:
        resistance_anchors.append(_Anchor(float(options.ce_wall.cluster_center), 88.0, "CE OI Cluster"))

    if levels.immediate_support is not None:
        _append_level_anchors(
            support_anchors,
            midpoint=levels.immediate_support.midpoint,
            strength=levels.immediate_support.strength,
            sources=levels.immediate_support.sources,
            default_source="Immediate Support",
        )
    if levels.strong_support is not None:
        _append_level_anchors(
            support_anchors,
            midpoint=levels.strong_support.midpoint,
            strength=levels.strong_support.strength,
            sources=levels.strong_support.sources,
            default_source="Strong Support",
        )
    if levels.previous_day_low is not None:
        support_anchors.append(_Anchor(levels.previous_day_low, 78.0, "Previous Day Low"))
    if levels.opening_range_low is not None:
        support_anchors.append(_Anchor(levels.opening_range_low, 72.0, "Opening Range Low"))
    if options.pe_wall.strike is not None:
        support_anchors.append(_Anchor(float(options.pe_wall.strike), 84.0, "PE OI Wall"))
    if options.pe_wall.cluster_center is not None:
        support_anchors.append(_Anchor(float(options.pe_wall.cluster_center), 88.0, "PE OI Cluster"))

    resistance = _pick_barrier(
        side="RESISTANCE",
        spot=spot,
        anchors=resistance_anchors,
        half_width=half_width,
    )
    support = _pick_barrier(
        side="SUPPORT",
        spot=spot,
        anchors=support_anchors,
        half_width=half_width,
    )
    status = "READY" if resistance is not None or support is not None else "UNAVAILABLE"
    return PreTouchBarrierBundle(support=support, resistance=resistance, status=status)
