from __future__ import annotations

import os
from typing import Any


def public_mode_enabled() -> bool:
    """UI-only public mode. Never changes strategy calculations or snapshot data."""
    raw = str(os.getenv("NSL_PUBLIC_MODE", "0") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on", "public"}


def public_action_label(action: str | None, *, public_mode: bool | None = None) -> str:
    """Return neutral display wording while preserving the underlying action key."""
    text = str(action or "WAIT").strip().upper()
    mode = public_mode_enabled() if public_mode is None else bool(public_mode)
    if not mode:
        return text
    if text == "WAIT":
        return "WAIT"
    replacements = {
        "CE BUY": "CE BUY SETUP",
        "PE BUY": "PE BUY SETUP",
        "CE SELL": "CE SELL SETUP",
        "PE SELL": "PE SELL SETUP",
        "CE SELL WITH HEDGE": "CE SELL + HEDGE SETUP",
        "PE SELL WITH HEDGE": "PE SELL + HEDGE SETUP",
        "IRON CONDOR": "IRON CONDOR SETUP",
    }
    return replacements.get(text, f"{text} SETUP")


def ready_banner_label(*, public_mode: bool | None = None) -> str:
    mode = public_mode_enabled() if public_mode is None else bool(public_mode)
    return "SETUP READY" if mode else "TAKE NOW"


def entry_metric_label(*, public_mode: bool | None = None) -> str:
    mode = public_mode_enabled() if public_mode is None else bool(public_mode)
    return "CONDITIONS" if mode else "ENTRY"


def strategy_status_label(status: str, *, public_mode: bool | None = None) -> str:
    mode = public_mode_enabled() if public_mode is None else bool(public_mode)
    text = str(status or "")
    if not mode:
        return text
    return (
        text.replace("ENTRY READY", "CONDITIONS MET")
        .replace("ENTRY GATE CLOSED", "CONDITIONS PENDING")
        .replace("ENTRY", "SETUP")
    )


def _leg_strikes(plan: Any | None, attr: str) -> list[float]:
    values: list[float] = []
    if plan is None:
        return values
    for leg in getattr(plan, attr, ()) or ():
        strike = getattr(leg, "strike", None)
        try:
            if strike is not None:
                values.append(float(strike))
        except (TypeError, ValueError):
            continue
    return values


def smart_focus_tags(snapshot: Any, *, atm: float | None = None) -> dict[float, tuple[str, ...]]:
    """Build display-only focus tags from already-computed snapshot objects.

    No API calls and no strategy recomputation. Tags explain why a strike is visually
    important on the live board: ATM, OI walls/clusters, or current best protected plan.
    """
    tags: dict[float, list[str]] = {}

    def add(strike: Any, label: str) -> None:
        try:
            value = float(strike)
        except (TypeError, ValueError):
            return
        if value <= 0:
            return
        bucket = tags.setdefault(value, [])
        if label not in bucket:
            bucket.append(label)

    if atm is not None:
        add(atm, "ATM")

    item = getattr(snapshot, "option_intelligence", None)
    if item is not None:
        ce_wall = getattr(item, "ce_wall", None)
        pe_wall = getattr(item, "pe_wall", None)
        if ce_wall is not None:
            add(getattr(ce_wall, "strike", None), "CE WALL")
            add(getattr(ce_wall, "cluster_center", None), "CE CLUSTER")
        if pe_wall is not None:
            add(getattr(pe_wall, "strike", None), "PE WALL")
            add(getattr(pe_wall, "cluster_center", None), "PE CLUSTER")

    metadata = getattr(snapshot, "metadata", {}) or {}
    common = metadata.get("common_decision") or {}
    leader = str(common.get("best_strategy") or common.get("final_action") or "WAIT")
    bundle = getattr(snapshot, "trade_plan", None)
    if bundle is not None:
        plan_map = {
            "CE BUY": getattr(bundle, "ce_buy", None),
            "PE BUY": getattr(bundle, "pe_buy", None),
            "CE SELL": getattr(bundle, "ce_sell", None),
            "PE SELL": getattr(bundle, "pe_sell", None),
            "IRON CONDOR": getattr(bundle, "iron_condor", None),
        }
        plan = plan_map.get(leader)
        if plan is not None and getattr(plan, "available", False):
            for strike in _leg_strikes(plan, "long_legs"):
                add(strike, "FOCUS LONG")
            for strike in _leg_strikes(plan, "short_legs"):
                add(strike, "FOCUS SHORT")
            for strike in _leg_strikes(plan, "hedge_legs"):
                add(strike, "HEDGE")

    return {strike: tuple(values) for strike, values in tags.items()}
