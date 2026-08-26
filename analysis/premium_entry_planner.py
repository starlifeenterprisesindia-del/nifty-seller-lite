from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PremiumEntry:
    entry_no: int
    premium: float
    lots: int
    condition: str


@dataclass(frozen=True)
class PremiumEntryPlan:
    best_entry_premium: float
    average_premium: float
    entries: tuple[PremiumEntry, ...]
    warning: str


def build_premium_entry_plan(
    *,
    position: str,
    current_premium: float,
    bid: float | None,
    ask: float | None,
    total_lots: int,
    entries: int = 3,
) -> PremiumEntryPlan:
    """Liquidity-aware staggered limit plan; never blind averaging."""
    position = str(position).upper()
    total_lots = max(1, int(total_lots))
    entries = max(1, min(3, int(entries), total_lots))
    last = max(0.05, float(current_premium))
    valid_book = bid is not None and ask is not None and 0 <= float(bid) <= float(ask)
    if valid_book:
        bid_value, ask_value = float(bid), float(ask)
        spread = max(0.05, ask_value - bid_value)
        best = min(ask_value, max(bid_value, last)) if position == "BUY" else max(bid_value, min(ask_value, last))
    else:
        spread = max(0.10, last * 0.008)
        best = last

    # First entry is executable around the book. Later entries are conditional
    # retest/confirmation limits, not automatic averaging instructions.
    offsets = (0.0, -0.65, -1.30) if position == "BUY" else (0.0, 0.65, 1.30)
    lot_base, remainder = divmod(total_lots, entries)
    rows = []
    for index in range(entries):
        lots = lot_base + (1 if index < remainder else 0)
        premium = max(0.05, best + offsets[index] * spread)
        condition = (
            "Initial limit near live bid/ask"
            if index == 0
            else "Only if One-Brain direction + OI/Big Player setup remains valid"
        )
        rows.append(PremiumEntry(index + 1, round(premium, 2), lots, condition))
    average = sum(item.premium * item.lots for item in rows) / sum(item.lots for item in rows)
    return PremiumEntryPlan(
        best_entry_premium=round(best, 2),
        average_premium=round(average, 2),
        entries=tuple(rows),
        warning="Entry 2/3 automatic averaging nahi—setup invalid ho to CANCEL.",
    )
