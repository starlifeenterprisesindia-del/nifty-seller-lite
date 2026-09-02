from __future__ import annotations

from dataclasses import dataclass

from models import SetupPlan


@dataclass(frozen=True)
class EntryGuidance:
    current: str
    preferred_zone: str
    minimum: str
    status: str
    instruction: str


def build_entry_guidance(plan: SetupPlan | None, *, entry_ready: bool, live: bool) -> EntryGuidance:
    """Deterministic limit-price guidance; never recommends a naked short leg."""
    if plan is None or not plan.available:
        return EntryGuidance("—", "—", "—", "AVOID", "Usable protected strike pair unavailable")
    is_buy = bool(plan.is_buy)
    value = plan.estimated_debit_points if is_buy else plan.estimated_credit_points
    if value is None or value <= 0:
        return EntryGuidance("—", "—", "—", "AVOID", "Executable package premium unavailable")
    value = float(value)
    if is_buy:
        low, high = value * 0.95, value * 1.03
        minimum = f"Max debit {high:.2f} pts"
        instruction = "Protected spread ko limit order se lo; price chase mat karo"
    else:
        low, high = value * 0.97, value * 1.08
        minimum = f"Min net credit {low:.2f} pts"
        instruction = "Short+hedge ek package me; staged ho to hedge-first, naked short kabhi nahi"
    status = "TAKE NOW (LIMIT)" if live and entry_ready else "WAIT CONFIRMATION" if live else "REFERENCE ONLY"
    return EntryGuidance(
        current=f"{'Debit' if is_buy else 'Net credit'} {value:.2f} pts",
        preferred_zone=f"{low:.2f}–{high:.2f} pts",
        minimum=minimum,
        status=status,
        instruction=instruction,
    )
