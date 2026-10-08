"""Institutional Opportunity Window for One Brain Market Intelligence.

Shadow-only / zero-network research layer.  It does **not** claim to identify a real
institution, hidden order, stop hunt, or trader intent.  It asks a narrower, testable
question using evidence already present in the authoritative snapshot:

    "Are conditions becoming unusually attractive for large directional participation?"

The live state is intentionally gated by six independent opportunity dimensions:
Directional Edge, Opposition Weakness, Path Clearance, Participation Capacity Proxy,
Trigger Readiness and Pressure Effectiveness.  W/M and strong-candle patterns are
supportive context only and never mandatory gates.  Missing evidence is NO VOTE.

OPEN requires all six core gates plus a live-data safety gate.  No persistence wait is
required, so the detector can alert on the first authoritative snapshot where all core
conditions are present.  STRONG is an evidence-quality upgrade, not a trade signal.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Mapping


@dataclass(frozen=True)
class OpportunityGate:
    name: str
    available: bool
    passed: bool
    score: float | None
    state: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InstitutionalOpportunityWindow:
    direction: str
    state: str
    opportunity_score: float
    gates_ready: int
    gates_total: int
    data_safety_state: str
    alert_eligible: bool
    transition: str
    directional_edge: OpportunityGate
    opposition_weakness: OpportunityGate
    path_clearance: OpportunityGate
    participation_capacity: OpportunityGate
    trigger_readiness: OpportunityGate
    pressure_effectiveness: OpportunityGate
    supportive_signals: tuple[str, ...]
    missing_gates: tuple[str, ...]
    reasons: tuple[str, ...]
    cautions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in (
            "directional_edge", "opposition_weakness", "path_clearance",
            "participation_capacity", "trigger_readiness", "pressure_effectiveness",
        ):
            data[key] = getattr(self, key).to_dict()
        return data


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _expert_map(experts: Any) -> dict[str, Any]:
    if isinstance(experts, Mapping):
        return {str(k): v for k, v in experts.items()}
    output: dict[str, Any] = {}
    for item in experts or ():
        if isinstance(item, Mapping):
            name = str(item.get("name") or "")
        else:
            name = str(getattr(item, "name", "") or "")
        if name:
            output[name] = item
    return output


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _gate(name: str, score: float | None, threshold: float, *, reason: str,
          unavailable_reason: str = "Evidence unavailable") -> OpportunityGate:
    if score is None:
        return OpportunityGate(name, False, False, None, "NO VOTE", unavailable_reason)
    value = round(_clamp(score), 1)
    passed = value >= threshold
    if value >= max(72.0, threshold + 10.0):
        state = "STRONG"
    elif passed:
        state = "READY"
    elif value >= threshold - 10.0:
        state = "NEAR"
    else:
        state = "WEAK"
    return OpportunityGate(name, True, passed, value, state, reason)


def _directional_edge(
    direction: str,
    bull_pressure: float,
    bear_pressure: float,
    range_pressure: float,
    fast_confirmation_count: int,
) -> OpportunityGate:
    if direction not in {"BULLISH", "BEARISH"}:
        return _gate("Directional Edge", None, 55.0, reason="", unavailable_reason="Direction is MIXED")
    directional = bull_pressure if direction == "BULLISH" else bear_pressure
    opposite = bear_pressure if direction == "BULLISH" else bull_pressure
    gap = max(0.0, directional - opposite)
    fast = _clamp(fast_confirmation_count * 25.0)
    score = directional * 0.70 + min(100.0, gap * 2.0) * 0.20 + fast * 0.10
    score -= max(0.0, range_pressure - 50.0) * 0.20
    return _gate(
        "Directional Edge", score, 55.0,
        reason=f"{direction} pressure {directional:.0f}, opposite {opposite:.0f}, fast families {fast_confirmation_count}",
    )


def _opposition_weakness(integrity: Any) -> OpportunityGate:
    if not integrity:
        return _gate("Opposition Weakness", None, 55.0, reason="")
    attack = _num(_field(integrity, "barrier_attack_score"))
    fake = _num(_field(integrity, "fake_pressure_score"))
    oppositions = int(_num(_field(integrity, "family_oppositions"), 0.0) or 0)
    barrier_state = str(_field(integrity, "barrier_state", "") or "").upper()
    components: list[tuple[float, float]] = []
    if attack is not None:
        components.append((_clamp(attack), 0.55))
    if fake is not None:
        components.append((_clamp(100.0 - fake), 0.20))
    components.append((_clamp(100.0 - oppositions * 28.0), 0.25))
    if not components:
        return _gate("Opposition Weakness", None, 55.0, reason="")
    total = sum(w for _, w in components)
    score = sum(v * w for v, w in components) / total
    if barrier_state == "HOLDING STRONG":
        score -= 10.0
    return _gate(
        "Opposition Weakness", score, 55.0,
        reason=f"Barrier {barrier_state or 'unknown'} · attack {attack if attack is not None else '—'} · opposing families {oppositions}",
    )


def _path_clearance(direction: str, liquidity: Any) -> OpportunityGate:
    if not liquidity:
        return _gate("Path Clearance", None, 55.0, reason="")
    clearance = _num(_field(liquidity, "path_clearance"))
    bias = str(_field(liquidity, "hunt_bias", "UNCLEAR") or "UNCLEAR").upper()
    next_zone = _field(liquidity, "next_hunt_zone")
    money = _field(liquidity, "money_concentration", {}) or {}
    money_bias = str(_field(money, "bias", "UNCLEAR") or "UNCLEAR").upper()
    money_conf = _num(_field(money, "confidence"), 0.0) or 0.0
    money_up = _num(_field(money, "upside_score"), 0.0) or 0.0
    money_down = _num(_field(money, "downside_score"), 0.0) or 0.0
    if clearance is None:
        return _gate("Path Clearance", None, 55.0, reason="")
    aligned_bias = "UPSIDE" if direction == "BULLISH" else "DOWNSIDE" if direction == "BEARISH" else ""
    score = float(clearance)
    if aligned_bias and bias == aligned_bias:
        score += 8.0
    elif bias in {"BALANCED", "UNCLEAR"}:
        score -= 5.0
    elif aligned_bias and bias not in {"", aligned_bias}:
        score -= 18.0
    # The Money Concentration layer is deliberately independent of directional
    # pressure.  Alignment increases confidence that there is visible option-market
    # participation/fuel on the same side; a strong opposite concentration reduces
    # path quality.  Balanced/unknown concentration remains neutral.
    if aligned_bias and money_bias == aligned_bias:
        score += 7.0 if money_conf >= 45 else 4.0
    elif aligned_bias and money_bias in {"UPSIDE", "DOWNSIDE"} and money_bias != aligned_bias:
        score -= 14.0 if money_conf >= 45 else 8.0
    if next_zone:
        score += 4.0
    return _gate(
        "Path Clearance", score, 55.0,
        reason=(
            f"Path {clearance:.0f}/100 · hunt bias {bias} · money magnet {money_bias} "
            f"(up {money_up:.0f}/down {money_down:.0f})"
            + (" · next pool mapped" if next_zone else "")
        ),
    )


def _participation_capacity(activity: Any, experts: Any) -> OpportunityGate:
    """Execution-liquidity proxy from already-present participation data.

    This is deliberately named a *proxy*: we do not have full order-book depth or an
    institution's private execution schedule.  Relative futures volume + existing
    activity + Futures/Options/Breadth data quality provide a conservative observable
    approximation of whether enough two-sided participation exists for a large move.
    """
    amap = _expert_map(experts)
    components: list[tuple[float, float]] = []
    notes: list[str] = []

    if activity:
        status = str(_field(activity, "status", "") or "").upper()
        score = _num(_field(activity, "score"))
        ratio = _num(_field(activity, "futures_volume_ratio"))
        if score is not None and status in {"READY", "CAUTION", "REFERENCE ONLY"}:
            components.append((_clamp(score), 0.35))
            notes.append(f"activity {score:.0f}")
        if ratio is not None:
            # 0.8x is quiet, 1.0x normal, 1.8x active, 2.5x+ very active.
            ratio_score = _clamp(25.0 + max(0.0, ratio - 0.8) * 55.0)
            components.append((ratio_score, 0.30))
            notes.append(f"futures vol {ratio:.2f}x")

    quality_values: list[float] = []
    for name in ("Futures", "Options Flow", "Heavyweight Breadth"):
        item = amap.get(name)
        if item is None or not bool(_field(item, "available", False)):
            continue
        reliability = _num(_field(item, "reliability"), 0.0) or 0.0
        freshness = _num(_field(item, "freshness"), 0.0) or 0.0
        quality_values.append(_clamp(reliability * 100.0 * 0.55 + freshness * 100.0 * 0.45))
    if quality_values:
        data_quality = sum(quality_values) / len(quality_values)
        components.append((data_quality, 0.35))
        notes.append(f"market-flow data {data_quality:.0f}")

    # Require at least two independent observable components. Missing capacity data
    # stays NO VOTE instead of being invented as neutral.
    if len(components) < 2:
        return _gate(
            "Participation Capacity", None, 50.0, reason="",
            unavailable_reason="Participation/liquidity proxy has insufficient independent inputs",
        )
    total = sum(w for _, w in components)
    score = sum(v * w for v, w in components) / total
    return _gate(
        "Participation Capacity", score, 50.0,
        reason=" · ".join(notes[:3]) + " · proxy only",
    )


def _trigger_readiness(
    direction: str,
    integrity: Any,
    expansion_pressure: float,
    pressure_velocity: float | None,
    structure_event: str,
    breakout_direction: str,
    breakout_quality: float,
    reversal_direction: str,
    reversal_quality: float,
) -> OpportunityGate:
    if direction not in {"BULLISH", "BEARISH"} or not integrity:
        return _gate("Trigger Readiness", None, 58.0, reason="")
    attack = _num(_field(integrity, "barrier_attack_score"), 50.0) or 50.0
    move_attack = str(_field(integrity, "move_attack_state", "NORMAL") or "NORMAL").upper()
    velocity_score = _clamp(50.0 + (pressure_velocity or 0.0) * 2.5)
    score = attack * 0.42 + _clamp(expansion_pressure) * 0.38 + velocity_score * 0.20

    if move_attack == "ATTACK":
        score += 6.0
    elif move_attack == "BREAK / EXPANSION":
        score += 10.0
    elif move_attack in {"REJECTED / FAKE RISK", "FLIP WATCH"}:
        score -= 12.0

    if breakout_direction == direction and breakout_quality >= 58:
        score += min(8.0, (breakout_quality - 50.0) * 0.22)
    if reversal_direction == direction and reversal_quality >= 62:
        score += min(6.0, (reversal_quality - 55.0) * 0.18)
    reason = (
        f"Barrier attack {attack:.0f} · move pressure {expansion_pressure:.0f} · "
        f"velocity {(pressure_velocity or 0.0):+.0f} · {move_attack}"
    )
    return _gate("Trigger Readiness", score, 58.0, reason=reason)


def _pressure_effectiveness(integrity: Any) -> OpportunityGate:
    if not integrity:
        return _gate("Pressure Effectiveness", None, 56.0, reason="")
    quality = _num(_field(integrity, "quality_score"))
    real = _num(_field(integrity, "real_pressure_score"))
    fake = _num(_field(integrity, "fake_pressure_score"))
    price = _num(_field(integrity, "price_response_score"))
    state = str(_field(integrity, "quality_state", "UNVERIFIED") or "UNVERIFIED").upper()
    values: list[tuple[float, float]] = []
    if quality is not None:
        values.append((_clamp(quality), 0.35))
    if real is not None:
        values.append((_clamp(real), 0.30))
    if price is not None:
        values.append((_clamp(price), 0.20))
    if fake is not None:
        values.append((_clamp(100.0 - fake), 0.15))
    if not values:
        return _gate("Pressure Effectiveness", None, 56.0, reason="")
    total = sum(w for _, w in values)
    score = sum(v * w for v, w in values) / total
    if state in {"ABSORPTION RISK", "BUILD-UP FAILED", "FLIP WATCH"}:
        score -= 18.0
    elif state in {"VERIFIED", "REALIZED", "FLIP CONFIRMED"}:
        score += 5.0
    gate = _gate(
        "Pressure Effectiveness", score, 56.0,
        reason=f"Pressure {state} · quality {quality if quality is not None else '—'} · real {real if real is not None else '—'} · price response {price if price is not None else '—'}",
    )
    # A known fake/absorbed state must never pass solely because old component scores
    # were high before the failure became visible.
    if gate.available and state in {"ABSORPTION RISK", "BUILD-UP FAILED", "FLIP WATCH"}:
        return OpportunityGate(gate.name, True, False, gate.score, "BLOCKED", gate.reason)
    return gate


def _data_safety(snapshot: Any | None, coverage: float, conflict: str, *, recorded_sync: Any = None,
                 recorded_feeds: Any = None, live_override: bool | None = None) -> tuple[str, bool, str]:
    is_live = live_override
    if snapshot is not None:
        is_live = bool(getattr(getattr(snapshot, "market_session", None), "is_live", False))
    if is_live is False:
        return "REFERENCE", False, "Market session is not live"

    if coverage < 55:
        return "LIMITED", False, f"Evidence coverage {coverage:.0f}% < 55%"
    if str(conflict or "").upper() == "HIGH":
        return "CAUTION", False, "Evidence conflict HIGH"

    # Prefer authoritative live FeedStatus when available.
    if snapshot is not None:
        feeds = getattr(snapshot, "feed_status", {}) or {}
        states = [str(getattr(feeds.get(name), "use_state", "") or "").upper() for name in ("quotes", "candles", "option_chain")]
        if any(state != "LIVE" for state in states):
            return "LIMITED", False, "Core feeds not all LIVE"
        return "GOOD", True, "Core feeds LIVE and evidence coverage acceptable"

    if isinstance(recorded_sync, Mapping):
        state = str(recorded_sync.get("state") or "").upper()
        core_live = int(_num(recorded_sync.get("core_live"), 0.0) or 0)
        core_total = int(_num(recorded_sync.get("core_total"), 3.0) or 3)
        if state == "GOOD" and core_live >= core_total:
            return "GOOD", True, "Recorded core feeds synchronized"
        if state in {"REFERENCE", "LIMITED", "CAUTION"}:
            return state, False, f"Recorded sync {state}"

    if isinstance(recorded_feeds, Mapping):
        live_count = 0
        for name in ("quotes", "candles", "option_chain"):
            item = recorded_feeds.get(name)
            state = str((item or {}).get("use_state") or (item or {}).get("state") or "").upper() if isinstance(item, Mapping) else ""
            live_count += int(state == "LIVE")
        if live_count == 3:
            return "GOOD", True, "Recorded core feeds LIVE"
        if live_count:
            return "LIMITED", False, f"Recorded core feeds LIVE {live_count}/3"

    # Historical versions without the newer sync diagnostic can still be replayed for
    # research, but are intentionally not called alert-eligible.
    return "UNKNOWN", False, "Historical data-safety metadata unavailable"


def _transition(previous_state: str, current_state: str) -> str:
    previous = str(previous_state or "CLOSED").upper()
    current = str(current_state or "CLOSED").upper()
    if previous == current:
        return "UNCHANGED"
    if current == "STRONG" and previous == "OPEN":
        return "UPGRADED TO STRONG"
    if current in {"OPEN", "STRONG"} and previous not in {"OPEN", "STRONG"}:
        return "WINDOW OPENED"
    if previous in {"OPEN", "STRONG"} and current not in {"OPEN", "STRONG"}:
        return "WINDOW CLOSED"
    return f"{previous} → {current}"


def calculate_institutional_window(
    *,
    direction: str,
    bull_pressure: float,
    bear_pressure: float,
    range_pressure: float,
    fast_confirmation_count: int,
    expansion_pressure: float,
    pressure_velocity: float | None,
    coverage: float,
    conflict: str,
    structure_event: str,
    breakout_direction: str,
    breakout_quality: float,
    reversal_direction: str,
    reversal_quality: float,
    experts: Any,
    pressure_integrity: Any,
    liquidity: Any,
    activity: Any = None,
    snapshot: Any | None = None,
    previous_window: Mapping[str, Any] | None = None,
    recorded_sync: Any = None,
    recorded_feeds: Any = None,
    live_override: bool | None = None,
) -> InstitutionalOpportunityWindow:
    direction = str(direction or "MIXED").upper()
    previous_window = previous_window or {}

    gates = (
        _directional_edge(direction, bull_pressure, bear_pressure, range_pressure, fast_confirmation_count),
        _opposition_weakness(pressure_integrity),
        _path_clearance(direction, liquidity),
        _participation_capacity(activity, experts),
        _trigger_readiness(
            direction, pressure_integrity, expansion_pressure, pressure_velocity,
            structure_event, breakout_direction, breakout_quality,
            reversal_direction, reversal_quality,
        ),
        _pressure_effectiveness(pressure_integrity),
    )
    ready = sum(1 for gate in gates if gate.passed)
    available_scores = [float(gate.score) for gate in gates if gate.score is not None]
    opportunity_score = sum(available_scores) / len(available_scores) if available_scores else 0.0

    data_state, data_ok, data_reason = _data_safety(
        snapshot, coverage, conflict, recorded_sync=recorded_sync,
        recorded_feeds=recorded_feeds, live_override=live_override,
    )

    money = _field(liquidity, "money_concentration", {}) or {}
    money_bias = str(_field(money, "bias", "UNCLEAR") or "UNCLEAR").upper()
    money_conf = _num(_field(money, "confidence"), 0.0) or 0.0
    desired_money_bias = "UPSIDE" if direction == "BULLISH" else "DOWNSIDE" if direction == "BEARISH" else ""
    strong_money_conflict = bool(
        desired_money_bias
        and money_bias in {"UPSIDE", "DOWNSIDE"}
        and money_bias != desired_money_bias
        and money_conf >= 45.0
    )

    if ready == 6 and data_ok:
        strong_core = (
            opportunity_score >= 72.0
            and gates[0].score is not None and gates[0].score >= 68.0
            and gates[4].score is not None and gates[4].score >= 68.0
            and gates[5].score is not None and gates[5].score >= 66.0
            and not strong_money_conflict
        )
        state = "STRONG" if strong_core else "OPEN"
    elif ready >= 4:
        state = "FORMING"
    else:
        state = "CLOSED"

    current_support = list(_field(pressure_integrity, "supportive_signals", ()) or ()) if pressure_integrity else []
    missing = tuple(gate.name for gate in gates if not gate.passed)
    prev_state = str(previous_window.get("state") or "CLOSED")
    transition = _transition(prev_state, state)

    reasons = [
        f"Core gates {ready}/6 · opportunity {opportunity_score:.0f}/100",
        f"Data safety {data_state} · {data_reason}",
    ]
    if money_bias in {"UPSIDE", "DOWNSIDE", "BALANCED"}:
        reasons.append(f"Money magnet {money_bias} · confidence {money_conf:.0f}/100")
    reasons.extend(f"{gate.name}: {gate.state} {gate.score:.0f}/100" for gate in gates if gate.score is not None)

    cautions = (
        "Opportunity Window infers favorable public-market conditions; it cannot identify a real institution or hidden intent",
        "Participation Capacity is a proxy; full private order-book depth/execution schedule is not observable here",
        "OPEN/STRONG are research attention states, not trade signals or calibrated profit probabilities",
        "W/M and strong candles are supportive only and never mandatory gates",
    )

    return InstitutionalOpportunityWindow(
        direction=direction,
        state=state,
        opportunity_score=round(_clamp(opportunity_score), 1),
        gates_ready=ready,
        gates_total=6,
        data_safety_state=data_state,
        alert_eligible=bool(state in {"OPEN", "STRONG"} and data_ok),
        transition=transition,
        directional_edge=gates[0],
        opposition_weakness=gates[1],
        path_clearance=gates[2],
        participation_capacity=gates[3],
        trigger_readiness=gates[4],
        pressure_effectiveness=gates[5],
        supportive_signals=tuple(dict.fromkeys(str(x) for x in current_support if str(x).strip()))[:4],
        missing_gates=missing,
        reasons=tuple(reasons[:8]),
        cautions=cautions,
    )


def calculate_institutional_window_from_record(
    mie: Mapping[str, Any],
    *,
    activity: Mapping[str, Any] | None = None,
    recorded_sync: Mapping[str, Any] | None = None,
    recorded_feeds: Mapping[str, Any] | None = None,
    previous_window: Mapping[str, Any] | None = None,
    live_override: bool | None = None,
) -> InstitutionalOpportunityWindow:
    """Backfill/replay helper for already-recorded historical MI rows.

    It uses only fields that were available in the historical record at that timestamp;
    future outcome columns are never inputs.
    """
    integrity = mie.get("pressure_integrity") if isinstance(mie.get("pressure_integrity"), Mapping) else {}
    liquidity = mie.get("liquidity") if isinstance(mie.get("liquidity"), Mapping) else {}
    experts = mie.get("experts") if isinstance(mie.get("experts"), (list, tuple)) else []
    direction = str(mie.get("early_direction") or mie.get("direction") or "MIXED").upper()
    return calculate_institutional_window(
        direction=direction,
        bull_pressure=float(_num(mie.get("bull_pressure"), 0.0) or 0.0),
        bear_pressure=float(_num(mie.get("bear_pressure"), 0.0) or 0.0),
        range_pressure=float(_num(mie.get("range_pressure"), 0.0) or 0.0),
        fast_confirmation_count=int(_num(mie.get("fast_confirmation_count"), 0.0) or 0),
        expansion_pressure=float(_num(mie.get("expansion_pressure"), 0.0) or 0.0),
        pressure_velocity=_num(mie.get("pressure_velocity")),
        coverage=float(_num(mie.get("evidence_coverage"), 0.0) or 0.0),
        conflict=str(mie.get("evidence_conflict") or "HIGH"),
        structure_event=str(mie.get("structure_event") or ""),
        breakout_direction=str(mie.get("breakout_direction") or "MIXED"),
        breakout_quality=float(_num(mie.get("breakout_quality"), 0.0) or 0.0),
        reversal_direction=str(mie.get("reversal_direction") or "MIXED"),
        reversal_quality=float(_num(mie.get("reversal_quality"), 0.0) or 0.0),
        experts=experts,
        pressure_integrity=integrity,
        liquidity=liquidity,
        activity=activity or {},
        snapshot=None,
        previous_window=previous_window,
        recorded_sync=recorded_sync or {},
        recorded_feeds=recorded_feeds or {},
        live_override=live_override,
    )
