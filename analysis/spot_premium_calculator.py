from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class PremiumRangeEstimate:
    label: str
    target_spot: float
    best_price: float
    low_price: float
    high_price: float
    pnl_per_quantity: float
    pnl_per_lot: float
    total_pnl: float
    outcome: str
    exit_action: str
    reliability: float
    methods: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class SpotPremiumCalculation:
    side: str
    position: str
    strike: float
    current_spot: float
    current_premium: float
    entry_premium: float
    lower_spot: float
    upper_spot: float
    target_minutes: int
    lot_size: int
    lots: int
    feed_state: str
    current_bid: float | None
    current_ask: float | None
    current_iv: float | None
    current_delta: float | None
    current_gamma: float | None
    current_theta: float | None
    current_vega: float | None
    lower: PremiumRangeEstimate
    current: PremiumRangeEstimate
    upper: PremiumRangeEstimate
    overall_reliability: float
    status: str
    summary: str
    warnings: tuple[str, ...]


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(result):
        return None
    return result


def _contract_row(frame: pd.DataFrame, *, strike: float, side: str) -> pd.Series | None:
    if frame.empty or not {"strike", "side"}.issubset(frame.columns):
        return None
    rows = frame[frame["side"].astype(str).str.upper().eq(side)]
    if rows.empty:
        return None
    distances = (pd.to_numeric(rows["strike"], errors="coerce") - strike).abs()
    if distances.dropna().empty:
        return None
    idx = distances.idxmin()
    selected = rows.loc[idx]
    selected_strike = _number(selected.get("strike"))
    strikes = sorted(pd.to_numeric(rows["strike"], errors="coerce").dropna().unique())
    typical_step = min(
        (right - left for left, right in zip(strikes, strikes[1:]) if right > left),
        default=50.0,
    )
    if selected_strike is None or abs(selected_strike - strike) > max(1.0, typical_step * 0.25):
        return None
    return selected


def _interpolate(values: list[tuple[float, float]], x: float) -> float | None:
    cleaned = sorted({float(a): float(b) for a, b in values if isfinite(a) and isfinite(b)}.items())
    if not cleaned or x < cleaned[0][0] or x > cleaned[-1][0]:
        return None
    for strike, price in cleaned:
        if abs(strike - x) < 1e-9:
            return max(0.0, price)
    for (left_x, left_y), (right_x, right_y) in zip(cleaned, cleaned[1:]):
        if left_x <= x <= right_x and right_x > left_x:
            ratio = (x - left_x) / (right_x - left_x)
            return max(0.0, left_y + ratio * (right_y - left_y))
    return None


def _chain_shift_estimate(
    frame: pd.DataFrame,
    *,
    side: str,
    strike: float,
    current_spot: float,
    target_spot: float,
    current_premium: float,
    chain_contract_price: float | None,
    theta: float | None,
    target_minutes: int,
) -> tuple[float | None, float | None]:
    if (
        frame.empty
        or current_spot <= 0
        or target_spot <= 0
        or not {"side", "strike", "last_price"}.issubset(frame.columns)
    ):
        return None, None
    rows = frame[frame["side"].astype(str).str.upper().eq(side)].copy()
    if rows.empty:
        return None, None
    rows["strike"] = pd.to_numeric(rows["strike"], errors="coerce")
    rows["last_price"] = pd.to_numeric(rows["last_price"], errors="coerce")
    rows = rows.dropna(subset=["strike", "last_price"])
    rows = rows[rows["last_price"] >= 0]
    if rows.empty:
        return None, None

    # Keep approximately the same moneyness. This lets the live option-chain smile
    # anchor the estimate instead of relying on a fixed Delta alone.
    equivalent_strike = strike * current_spot / target_spot
    price = _interpolate(
        [(float(row.strike), float(row.last_price)) for row in rows.itertuples()],
        equivalent_strike,
    )
    if price is None:
        return None, equivalent_strike

    if chain_contract_price is not None:
        price += current_premium - chain_contract_price
    if theta is not None and target_minutes > 0:
        price += theta * (target_minutes / 1440.0)
    return max(0.0, price), equivalent_strike


def _greek_estimate(
    *,
    side: str,
    strike: float,
    current_spot: float,
    target_spot: float,
    current_premium: float,
    delta: float | None,
    gamma: float | None,
    theta: float | None,
    target_minutes: int,
) -> float | None:
    if delta is None:
        return None
    move = target_spot - current_spot
    price = current_premium + delta * move
    if gamma is not None:
        price += 0.5 * gamma * move * move
    if theta is not None and target_minutes > 0:
        price += theta * (target_minutes / 1440.0)
    intrinsic = max(0.0, target_spot - strike) if side == "CE" else max(0.0, strike - target_spot)
    return max(intrinsic, price, 0.0)


def _position_pnl(
    *, position: str, entry_premium: float, estimated_premium: float
) -> float:
    if position == "BUY":
        return estimated_premium - entry_premium
    return entry_premium - estimated_premium


def _estimate_one(
    *,
    label: str,
    target_spot: float,
    side: str,
    position: str,
    strike: float,
    current_spot: float,
    current_premium: float,
    entry_premium: float,
    target_minutes: int,
    lot_size: int,
    lots: int,
    frame: pd.DataFrame,
    chain_contract_price: float | None,
    bid: float | None,
    ask: float | None,
    iv: float | None,
    delta: float | None,
    gamma: float | None,
    theta: float | None,
    vega: float | None,
    feed_state: str,
) -> PremiumRangeEstimate:
    if abs(target_spot - current_spot) < 1e-9:
        estimates = [("CURRENT PREMIUM", current_premium)]
        equivalent_strike = strike
    else:
        chain_estimate, equivalent_strike = _chain_shift_estimate(
            frame,
            side=side,
            strike=strike,
            current_spot=current_spot,
            target_spot=target_spot,
            current_premium=current_premium,
            chain_contract_price=chain_contract_price,
            theta=theta,
            target_minutes=target_minutes,
        )
        greek_estimate = _greek_estimate(
            side=side,
            strike=strike,
            current_spot=current_spot,
            target_spot=target_spot,
            current_premium=current_premium,
            delta=delta,
            gamma=gamma,
            theta=theta,
            target_minutes=target_minutes,
        )
        estimates = []
        if chain_estimate is not None:
            estimates.append(("OPTION CHAIN SHIFT", chain_estimate))
        if greek_estimate is not None:
            estimates.append(("DELTA + GAMMA", greek_estimate))
        if not estimates:
            # Last-resort intrinsic-aware linear fallback. It is deliberately low
            # reliability and is only used when the chain row has incomplete Greeks.
            fallback_delta = 0.5 if side == "CE" else -0.5
            fallback = current_premium + fallback_delta * (target_spot - current_spot)
            intrinsic = (
                max(0.0, target_spot - strike)
                if side == "CE"
                else max(0.0, strike - target_spot)
            )
            estimates.append(("LOW-CONFIDENCE FALLBACK", max(intrinsic, fallback, 0.0)))

    # Chain-shift receives more weight because it uses the actual same-expiry smile.
    weighted_total = 0.0
    weight_total = 0.0
    for method, value in estimates:
        weight = 0.65 if method == "OPTION CHAIN SHIFT" else 0.35
        if len(estimates) == 1:
            weight = 1.0
        weighted_total += value * weight
        weight_total += weight
    best = max(0.0, weighted_total / max(weight_total, 1e-9))

    method_values = [value for _, value in estimates]
    disagreement = (max(method_values) - min(method_values)) / 2.0 if len(method_values) > 1 else 0.0
    spread_half = 0.0
    if bid is not None and ask is not None and ask >= bid >= 0:
        spread_half = (ask - bid) / 2.0
    iv_shift_points = max(0.75, min(2.5, (iv or 12.0) * 0.08))
    iv_uncertainty = abs(vega or 0.0) * iv_shift_points
    movement_uncertainty = abs(target_spot - current_spot) * 0.015
    half_width = max(0.75, best * 0.025, spread_half, iv_uncertainty, disagreement, movement_uncertainty)
    # Avoid a misleadingly enormous band while still keeping a useful uncertainty zone.
    half_width = min(half_width, max(3.0, best * 0.35 + 2.0))
    low = max(0.0, best - half_width)
    high = best + half_width

    pnl_per_quantity = _position_pnl(
        position=position, entry_premium=entry_premium, estimated_premium=best
    )
    pnl_per_lot = pnl_per_quantity * lot_size
    total_pnl = pnl_per_lot * lots
    if pnl_per_quantity > 0.25:
        outcome = "PROFIT ZONE"
    elif pnl_per_quantity < -0.25:
        outcome = "LOSS ZONE"
    else:
        outcome = "NEAR ENTRY"

    reliability = 28.0
    methods = tuple(method for method, _ in estimates)
    if "OPTION CHAIN SHIFT" in methods:
        reliability += 28.0
    if "DELTA + GAMMA" in methods:
        reliability += 18.0
    if iv is not None and vega is not None:
        reliability += 8.0
    if bid is not None and ask is not None:
        reliability += 6.0
    if feed_state == "LIVE":
        reliability += 10.0
    else:
        reliability -= 12.0
    move_points = abs(target_spot - current_spot)
    if move_points > 200:
        reliability -= min(20.0, (move_points - 200.0) / 10.0)
    if target_minutes > 120:
        reliability -= min(15.0, (target_minutes - 120.0) / 20.0)
    if disagreement > max(2.0, best * 0.12):
        reliability -= 12.0
    if equivalent_strike is None:
        reliability -= 10.0
    reliability = max(10.0, min(95.0, reliability))
    if feed_state != "LIVE":
        reliability = min(reliability, 48.0)

    notes: list[str] = []
    if equivalent_strike is not None and "OPTION CHAIN SHIFT" in methods:
        notes.append(f"Chain proxy strike {equivalent_strike:,.1f}")
    if target_minutes > 0 and theta is not None:
        notes.append(f"Theta adjusted for {target_minutes} min")
    if feed_state != "LIVE":
        notes.append("Reference-only option-chain data")
    if "LOW-CONFIDENCE FALLBACK" in methods:
        notes.append("Greeks/chain coverage incomplete")

    return PremiumRangeEstimate(
        label=label,
        target_spot=round(target_spot, 2),
        best_price=round(best, 2),
        low_price=round(low, 2),
        high_price=round(high, 2),
        pnl_per_quantity=round(pnl_per_quantity, 2),
        pnl_per_lot=round(pnl_per_lot, 2),
        total_pnl=round(total_pnl, 2),
        outcome=outcome,
        exit_action="SELL EXIT" if position == "BUY" else "BUY BACK",
        reliability=round(reliability, 1),
        methods=methods,
        notes=tuple(notes),
    )


def calculate_spot_premium_range(
    *,
    option_chain: pd.DataFrame,
    side: str,
    position: str,
    strike: float,
    current_spot: float,
    current_premium: float,
    entry_premium: float,
    lower_spot: float,
    upper_spot: float,
    target_minutes: int,
    lot_size: int,
    lots: int,
    feed_state: str = "UNAVAILABLE",
) -> SpotPremiumCalculation:
    side = str(side).upper().strip()
    position = str(position).upper().strip()
    feed_state = str(feed_state).upper().strip() or "UNAVAILABLE"
    if side not in {"CE", "PE"}:
        raise ValueError("Option side CE ya PE hona chahiye")
    if position not in {"BUY", "SELL"}:
        raise ValueError("Position BUY ya SELL honi chahiye")
    numeric_values = {
        "strike": strike,
        "current_spot": current_spot,
        "current_premium": current_premium,
        "entry_premium": entry_premium,
        "lower_spot": lower_spot,
        "upper_spot": upper_spot,
    }
    for name, value in numeric_values.items():
        if _number(value) is None or float(value) <= 0:
            raise ValueError(f"{name} valid positive number hona chahiye")
    if lower_spot >= upper_spot:
        raise ValueError("Lower range, upper range se chhoti honi chahiye")
    if target_minutes < 0 or target_minutes > 1440:
        raise ValueError("Target time 0 se 1440 minute ke beech hona chahiye")
    if lot_size < 1 or lots < 1:
        raise ValueError("Lot size aur lots minimum 1 hone chahiye")

    frame = option_chain.copy() if isinstance(option_chain, pd.DataFrame) else pd.DataFrame()
    row = _contract_row(frame, strike=float(strike), side=side)
    chain_contract_price = _number(row.get("last_price")) if row is not None else None
    bid = _number(row.get("top_bid_price")) if row is not None else None
    ask = _number(row.get("top_ask_price")) if row is not None else None
    iv = _number(row.get("implied_volatility")) if row is not None else None
    delta = _number(row.get("delta")) if row is not None else None
    gamma = _number(row.get("gamma")) if row is not None else None
    theta = _number(row.get("theta")) if row is not None else None
    vega = _number(row.get("vega")) if row is not None else None

    shared = dict(
        side=side,
        position=position,
        strike=float(strike),
        current_spot=float(current_spot),
        current_premium=float(current_premium),
        entry_premium=float(entry_premium),
        target_minutes=int(target_minutes),
        lot_size=int(lot_size),
        lots=int(lots),
        frame=frame,
        chain_contract_price=chain_contract_price,
        bid=bid,
        ask=ask,
        iv=iv,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        feed_state=feed_state,
    )
    lower = _estimate_one(label="LOWER RANGE", target_spot=float(lower_spot), **shared)
    current = _estimate_one(label="CURRENT SPOT", target_spot=float(current_spot), **shared)
    upper = _estimate_one(label="UPPER RANGE", target_spot=float(upper_spot), **shared)

    overall_reliability = round(min(lower.reliability, upper.reliability), 1)
    status = "LIVE ESTIMATE" if feed_state == "LIVE" else "REFERENCE ONLY"
    favorable = max((lower, upper), key=lambda item: item.total_pnl)
    adverse = min((lower, upper), key=lambda item: item.total_pnl)
    action_word = "sell-exit" if position == "BUY" else "buyback"
    summary = (
        f"{strike:,.0f} {side} {position}: {favorable.label.title()} {favorable.target_spot:,.0f} par "
        f"{action_word} best estimate ₹{favorable.best_price:,.2f} aur total P&L ₹{favorable.total_pnl:,.0f}; "
        f"adverse side {adverse.target_spot:,.0f} par estimate ₹{adverse.best_price:,.2f} aur P&L ₹{adverse.total_pnl:,.0f}."
    )

    warnings: list[str] = [
        "Estimated zone guarantee nahi hai; IV, speed, bid-ask aur liquidity se actual premium badal sakta hai.",
        "Calculator Main AI ke BUY/SELL/WAIT decision ko change nahi karta.",
    ]
    if row is None:
        warnings.append("Selected strike ka exact option-chain row nahi mila; reliability low rahegi.")
    if not (lower_spot <= current_spot <= upper_spot):
        warnings.append("Current NIFTY manual range ke bahar hai; dono endpoints future scenarios ki tarah read karo.")
    if feed_state != "LIVE":
        warnings.append("Live option-chain unavailable hai, isliye broker price verify karna zaroori hai.")

    return SpotPremiumCalculation(
        side=side,
        position=position,
        strike=round(float(strike), 2),
        current_spot=round(float(current_spot), 2),
        current_premium=round(float(current_premium), 2),
        entry_premium=round(float(entry_premium), 2),
        lower_spot=round(float(lower_spot), 2),
        upper_spot=round(float(upper_spot), 2),
        target_minutes=int(target_minutes),
        lot_size=int(lot_size),
        lots=int(lots),
        feed_state=feed_state,
        current_bid=bid,
        current_ask=ask,
        current_iv=iv,
        current_delta=delta,
        current_gamma=gamma,
        current_theta=theta,
        current_vega=vega,
        lower=lower,
        current=current,
        upper=upper,
        overall_reliability=overall_reliability,
        status=status,
        summary=summary,
        warnings=tuple(warnings),
    )
