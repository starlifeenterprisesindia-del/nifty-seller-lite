from __future__ import annotations

from typing import Any


def heavy_activity_alert_qualifies(activity: Any | None) -> bool:
    """Ring only for confirmed very-strong/extreme directional activity."""

    if activity is None or str(getattr(activity, "status", "")) != "READY":
        return False
    return bool(
        str(getattr(activity, "direction", "")) in {"BUYING", "SELLING"}
        and float(getattr(activity, "score", 0.0) or 0.0) >= 75.0
        and int(getattr(activity, "confirmation_count", 0) or 0) >= 2
        and str(getattr(activity, "state", ""))
        in {"VERY STRONG", "EXTREME ACTIVITY"}
    )


def target_crossed(*, armed_spot: float, current_spot: float, target: float) -> bool:
    """Detect a target reached from either side without requiring an exact tick."""

    if target >= armed_spot:
        return current_spot >= target
    return current_spot <= target


def heavy_activity_signature(activity: Any) -> str:
    return "|".join(
        (
            str(getattr(activity, "direction", "")),
            str(getattr(activity, "activity_type", "")),
        )
    )
