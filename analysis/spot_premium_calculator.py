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
    intrinsic_value: float
    time_value: float
    spot_move_effect: float
    theta_effect: float
    iv_effect: float
    chain_smile_effect: float


@dataclass(frozen=True)
class SidewaysDecayEstimate:
    minutes: int
    estimated_premium: float
    premium_change: float
    intrinsic_value: float
    remaining_time_value: float
    pnl_per_quantity: float
    total_pnl: float
    outcome: str
    reliability: float
    note: str


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
    iv_change_points: float
    target_iv: float | None
    current_intrinsic_value: float
    current_time_value: float
    current_time_value_share_pct: float
    lower: PremiumRangeEstimate
    current: PremiumRangeEstimate
    upper: PremiumRangeEstimate
    decay_scenarios: tuple[SidewaysDecayEstimate, ...]
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


def _intrinsic_value(*, side: str, strike: float, spot: float) -> float:
    if side == "CE":
        return max(0.0, spot - strike)
    return max(0.0, strike - spot)


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

    # Keep approximately the same moneyness. The same-expiry live smile therefore
    # anchors the estimate instead of relying on a fixed Delta alone.
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


def _greek_components(
    *,
    current_spot: float,
    target_spot: float,
    delta: float | None,
    gamma: float | None,
    theta: float | None,
    target_minutes: int,
    vega: float | None,
    iv_change_points: float,
) -> tuple[float, float, float]:
    move = target_spot - current_spot
    spot_effect = 0.0
    if delta is not None:
        spot_effect = delta * move
        if gamma is not None:
            spot_effect += 0.5 * gamma * move * move
    theta_effect = theta * (target_minutes / 1440.0) if theta is not None and target_minutes > 0 else 0.0
    iv_effect = vega * iv_change_points if vega is not None else 0.0
    return spot_effect, theta_effect, iv_effect


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
    vega: float | None,
    iv_change_points: float,
) -> float | None:
    if delta is None:
        return None
    spot_effect, theta_effect, iv_effect = _greek_components(
        current_spot=current_spot,
        target_spot=target_spot,
        delta=delta,
        gamma=gamma,
        theta=theta,
        target_minutes=target_minutes,
        vega=vega,
        iv_change_points=iv_change_points,
    )
    intrinsic = _intrinsic_value(side=side, strike=strike, spot=target_spot)
    return max(intrinsic, current_premium + spot_effect + theta_effect + iv_effect, 0.0)


def _position_pnl(*, position: str, entry_premium: float, estimated_premium: float) -> float:
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
    iv_change_points: float,
    feed_state: str,
    anchor_current: bool = False,
) -> PremiumRangeEstimate:
    intrinsic = _intrinsic_value(side=side, strike=strike, spot=target_spot)
    spot_effect, theta_effect, iv_effect = _greek_components(
        current_spot=current_spot,
        target_spot=target_spot,
        delta=delta,
        gamma=gamma,
        theta=theta,
        target_minutes=target_minutes,
        vega=vega,
        iv_change_points=iv_change_points,
    )

    if anchor_current:
        estimates = [("CURRENT PREMIUM", current_premium)]
        equivalent_strike = strike
        spot_effect = theta_effect = iv_effect = 0.0
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
        if chain_estimate is not None:
            chain_estimate = max(intrinsic, chain_estimate + iv_effect)
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
            vega=vega,
            iv_change_points=iv_change_points,
        )
        estimates: list[tuple[str, float]] = []
        if chain_estimate is not None:
            estimates.append(("OPTION CHAIN SHIFT", chain_estimate))
        if greek_estimate is not None:
            estimates.append(("DELTA + GAMMA + THETA + VEGA", greek_estimate))
        if not estimates:
            fallback_delta = 0.5 if side == "CE" else -0.5
            fallback = current_premium + fallback_delta * (target_spot - current_spot)
            if theta is not None:
                fallback += theta_effect
            if vega is not None:
                fallback += iv_effect
            estimates.append(("LOW-CONFIDENCE FALLBACK", max(intrinsic, fallback, 0.0)))

    weighted_total = 0.0
    weight_total = 0.0
    for method, value in estimates:
        weight = 0.65 if method == "OPTION CHAIN SHIFT" else 0.35
        if len(estimates) == 1:
            weight = 1.0
        weighted_total += value * weight
        weight_total += weight
    best = max(intrinsic, weighted_total / max(weight_total, 1e-9), 0.0)

    method_values = [value for _, value in estimates]
    disagreement = (max(method_values) - min(method_values)) / 2.0 if len(method_values) > 1 else 0.0
    spread_half = 0.0
    if bid is not None and ask is not None and ask >= bid >= 0:
        spread_half = (ask - bid) / 2.0
    iv_shift_points = max(0.75, min(2.5, (iv or 12.0) * 0.08))
    iv_uncertainty = abs(vega or 0.0) * iv_shift_points
    movement_uncertainty = abs(target_spot - current_spot) * 0.015
    half_width = max(0.75, best * 0.025, spread_half, iv_uncertainty, disagreement, movement_uncertainty)
    half_width = min(half_width, max(3.0, best * 0.35 + 2.0))
    low = max(intrinsic, best - half_width)
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
    if "DELTA + GAMMA + THETA + VEGA" in methods:
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
    if abs(iv_change_points) > 5.0:
        reliability -= min(15.0, (abs(iv_change_points) - 5.0) * 2.0)
    if disagreement > max(2.0, best * 0.12):
        reliability -= 12.0
    if equivalent_strike is None:
        reliability -= 10.0
    reliability = max(10.0, min(95.0, reliability))
    if feed_state != "LIVE":
        reliability = min(reliability, 48.0)

    # Residual versus the pure Greek path represents the live chain/smile adjustment.
    greek_path = max(intrinsic, current_premium + spot_effect + theta_effect + iv_effect, 0.0)
    chain_smile_effect = best - greek_path if not anchor_current else 0.0
    time_value = max(0.0, best - intrinsic)

    notes: list[str] = []
    if equivalent_strike is not None and "OPTION CHAIN SHIFT" in methods:
        notes.append(f"Chain proxy strike {equivalent_strike:,.1f}")
    if target_minutes > 0 and theta is not None:
        notes.append(f"Theta adjusted for {target_minutes} min")
    if abs(iv_change_points) > 1e-9 and vega is not None:
        notes.append(f"IV scenario {iv_change_points:+.2f} points")
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
        intrinsic_value=round(intrinsic, 2),
        time_value=round(time_value, 2),
        spot_move_effect=round(spot_effect, 2),
        theta_effect=round(theta_effect, 2),
        iv_effect=round(iv_effect, 2),
        chain_smile_effect=round(chain_smile_effect, 2),
    )


def _sideways_decay_estimate(
    *,
    side: str,
    position: str,
    strike: float,
    current_spot: float,
    current_premium: float,
    entry_premium: float,
    theta: float | None,
    minutes: int,
    lot_size: int,
    lots: int,
    feed_state: str,
) -> SidewaysDecayEstimate:
    intrinsic = _intrinsic_value(side=side, strike=strike, spot=current_spot)
    theta_effect = theta * (minutes / 1440.0) if theta is not None else 0.0
    estimated = max(intrinsic, current_premium + theta_effect, 0.0)
    pnl_per_quantity = _position_pnl(
        position=position, entry_premium=entry_premium, estimated_premium=estimated
    )
    total_pnl = pnl_per_quantity * lot_size * lots
    if pnl_per_quantity > 0.25:
        outcome = "PROFIT ZONE"
    elif pnl_per_quantity < -0.25:
        outcome = "LOSS ZONE"
    else:
        outcome = "NEAR ENTRY"
    reliability = 72.0 if theta is not None and feed_state == "LIVE" else 38.0
    note = "Spot aur IV same maan kar Theta-only estimate"
    if theta is None:
        note = "Theta unavailable; premium unchanged reference"
    return SidewaysDecayEstimate(
        minutes=minutes,
        estimated_premium=round(estimated, 2),
        premium_change=round(estimated - current_premium, 2),
        intrinsic_value=round(intrinsic, 2),
        remaining_time_value=round(max(0.0, estimated - intrinsic), 2),
        pnl_per_quantity=round(pnl_per_quantity, 2),
        total_pnl=round(total_pnl, 2),
        outcome=outcome,
        reliability=reliability,
        note=note,
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
    iv_change_points: float = 0.0,
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
    iv_change = _number(iv_change_points)
    if iv_change is None or not -50.0 <= iv_change <= 50.0:
        raise ValueError("Expected IV change -50 se +50 points ke beech hona chahiye")
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
    raw_iv = _number(row.get("implied_volatility")) if row is not None else None
    raw_delta = _number(row.get("delta")) if row is not None else None
    raw_gamma = _number(row.get("gamma")) if row is not None else None
    raw_theta = _number(row.get("theta")) if row is not None else None
    raw_vega = _number(row.get("vega")) if row is not None else None

    # Different brokers can publish different IV/Greeks because their model inputs,
    # timestamp and rounding differ. Invalid fields become missing and safe fallbacks
    # take over; they must never crash or silently create extreme prices.
    iv = raw_iv if raw_iv is not None and 0.0 < raw_iv <= 200.0 else None
    delta = raw_delta if raw_delta is not None and -1.05 <= raw_delta <= 1.05 else None
    gamma = raw_gamma if raw_gamma is not None and 0.0 <= raw_gamma <= 1.0 else None
    theta = raw_theta if raw_theta is not None and -5000.0 <= raw_theta <= 5000.0 else None
    vega = raw_vega if raw_vega is not None and 0.0 <= raw_vega <= 5000.0 else None

    target_iv = round(iv + iv_change, 2) if iv is not None else None
    if target_iv is not None and target_iv <= 0.0:
        raise ValueError("Expected IV change se target IV zero/negative ho rahi hai")

    shared = dict(
        side=side,
        position=position,
        strike=float(strike),
        current_spot=float(current_spot),
        current_premium=float(current_premium),
        entry_premium=float(entry_premium),
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
    lower = _estimate_one(
        label="LOWER RANGE",
        target_spot=float(lower_spot),
        target_minutes=int(target_minutes),
        iv_change_points=float(iv_change),
        **shared,
    )
    current = _estimate_one(
        label="CURRENT SPOT NOW",
        target_spot=float(current_spot),
        target_minutes=0,
        iv_change_points=0.0,
        anchor_current=True,
        **shared,
    )
    upper = _estimate_one(
        label="UPPER RANGE",
        target_spot=float(upper_spot),
        target_minutes=int(target_minutes),
        iv_change_points=float(iv_change),
        **shared,
    )

    decay_scenarios = tuple(
        _sideways_decay_estimate(
            side=side,
            position=position,
            strike=float(strike),
            current_spot=float(current_spot),
            current_premium=float(current_premium),
            entry_premium=float(entry_premium),
            theta=theta,
            minutes=minutes,
            lot_size=int(lot_size),
            lots=int(lots),
            feed_state=feed_state,
        )
        for minutes in (15, 30, 60)
    )

    current_intrinsic = _intrinsic_value(side=side, strike=float(strike), spot=float(current_spot))
    current_time_value = max(0.0, float(current_premium) - current_intrinsic)
    time_share = current_time_value / float(current_premium) * 100.0 if current_premium > 0 else 0.0

    overall_reliability = round(min(lower.reliability, upper.reliability), 1)
    status = "LIVE ESTIMATE" if feed_state == "LIVE" else "REFERENCE ONLY"
    favorable = max((lower, upper), key=lambda item: item.total_pnl)
    adverse = min((lower, upper), key=lambda item: item.total_pnl)
    action_word = "sell-exit" if position == "BUY" else "buyback"
    iv_text = ""
    if abs(iv_change) > 1e-9:
        iv_text = f"; IV scenario {iv_change:+.2f} points"
    summary = (
        f"{strike:,.0f} {side} {position}: {favorable.label.title()} {favorable.target_spot:,.0f} par "
        f"{action_word} best estimate ₹{favorable.best_price:,.2f} aur total P&L ₹{favorable.total_pnl:,.0f}; "
        f"adverse side {adverse.target_spot:,.0f} par estimate ₹{adverse.best_price:,.2f} aur P&L ₹{adverse.total_pnl:,.0f}"
        f"{iv_text}."
    )

    warnings: list[str] = [
        "Estimated zone guarantee nahi hai; IV, speed, bid-ask aur liquidity se actual premium badal sakta hai.",
        "Dhan chain IV/Greeks aur dusre broker ke IV/Greeks exact match karna zaroori nahi; premium/bid-ask final verify karo.",
        "Demand/OI/volume ko exact rupee contribution nahi diya gaya; live chain/smile adjustment uska market-price proxy hai.",
        "Calculator Main AI ke BUY/SELL/WAIT decision ko change nahi karta.",
    ]
    if raw_iv is not None and iv is None:
        warnings.append("Dhan chain IV invalid/out-of-range tha; IV ko ignore karke safe fallback use hua.")
    if raw_delta is not None and delta is None:
        warnings.append("Dhan chain Delta invalid/out-of-range tha; Delta ko ignore kiya gaya.")
    if float(current_premium) + 0.05 < current_intrinsic:
        warnings.append(
            "Current premium intrinsic value se neeche hai; quote/spot timestamp mismatch ho sakta hai. "
            "Broker bid-ask aur spot dobara verify karo."
        )
    if abs(iv_change) > 1e-9 and vega is None:
        warnings.append("IV scenario diya gaya, lekin valid Vega nahi mila; IV effect apply nahi hua.")
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
        iv_change_points=round(float(iv_change), 2),
        target_iv=target_iv,
        current_intrinsic_value=round(current_intrinsic, 2),
        current_time_value=round(current_time_value, 2),
        current_time_value_share_pct=round(time_share, 1),
        lower=lower,
        current=current,
        upper=upper,
        decay_scenarios=decay_scenarios,
        overall_reliability=overall_reliability,
        status=status,
        summary=summary,
        warnings=tuple(warnings),
    )
