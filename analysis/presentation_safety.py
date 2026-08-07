from __future__ import annotations

"""Presentation-only integrity helpers for Nifty Seller Lite.

These helpers never calculate a strategy or alter the authoritative runtime snapshot.
They only make user-facing text internally consistent before it is rendered on screen
or frozen into a PDF report.
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable


_OLD_NEWS_MARKERS = (
    "older market news",
    "old market news",
    "old news",
    "news purani",
    "low decision weight",
    "low-weight context",
)


@dataclass(frozen=True)
class NewsDisplay:
    status: str
    risk: str
    bias: str
    note: str


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def is_old_news_only_text(value: Any) -> bool:
    lowered = _text(value).lower()
    return bool(lowered) and any(marker in lowered for marker in _OLD_NEWS_MARKERS)


def without_old_news_only(items: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(
        _text(item)
        for item in (items or ())
        if _text(item) and not is_old_news_only_text(item)
    )


def normalized_news_display(news: Any) -> NewsDisplay:
    status = _upper(getattr(news, "status", "UNAVAILABLE")) or "UNAVAILABLE"
    bias = _upper(getattr(news, "bias", "NEUTRAL")) or "NEUTRAL"
    risk = _upper(getattr(news, "risk_level", "LOW")) or "LOW"
    age = getattr(news, "newest_age_minutes", None)
    try:
        age_value = float(age) if age is not None else None
    except (TypeError, ValueError):
        age_value = None

    if status == "READY" and (age_value is None or age_value < 90.0):
        note = "Recent live context" if age_value is None else f"Recent · newest {age_value:.0f}m"
        return NewsDisplay(status="READY", risk=risk, bias=bias, note=note)

    if age_value is not None and age_value < 180.0:
        return NewsDisplay(
            status="OLD / LOW WEIGHT",
            risk="LOW WEIGHT",
            bias=bias,
            note=f"Newest {age_value:.0f}m · directional weight reduced",
        )

    if status in {"OLD", "STALE"} or (age_value is not None and age_value >= 180.0):
        age_note = f"Newest {age_value:.0f}m" if age_value is not None else "Stale context"
        return NewsDisplay(
            status="STALE / ZERO WEIGHT",
            risk="NO LIVE RISK WEIGHT",
            bias="NEUTRAL",
            note=f"{age_note} · decision weight zero",
        )

    return NewsDisplay(
        status="UNAVAILABLE / ZERO WEIGHT",
        risk="NO LIVE RISK WEIGHT",
        bias="NEUTRAL",
        note="No fresh verified headline context",
    )


def _core_state(snapshot: Any) -> str:
    return _upper(getattr(getattr(snapshot, "core_evidence", None), "market_state", ""))


def _option_bias(snapshot: Any) -> str:
    options = getattr(snapshot, "option_intelligence", None)
    for name in ("bias", "signal_state", "state"):
        value = _upper(getattr(options, name, ""))
        if value:
            return value
    decision = getattr(snapshot, "decision", None)
    return _upper(getattr(decision, "signal_state", ""))


def market_rukh_display(snapshot: Any) -> tuple[str, float, str]:
    """Return a truthful headline label, evidence score and short note.

    A mixed core tape must not be presented as an unconditional UP/DOWN call merely
    because the option-flow module has a directional lean.
    """

    core = getattr(snapshot, "core_evidence", None)
    core_state = _core_state(snapshot)
    option_bias = _option_bias(snapshot)
    bullish = float(getattr(core, "bullish_score", 0.0) or 0.0)
    bearish = float(getattr(core, "bearish_score", 0.0) or 0.0)
    range_score = float(getattr(core, "range_score", 0.0) or 0.0)
    confidence = float(getattr(core, "confidence", 0.0) or 0.0)

    mixed = (
        "MIXED" in core_state
        or "NO CLEAR" in core_state
        or range_score >= max(bullish, bearish) - 8.0
        or abs(bullish - bearish) < 15.0
    )
    if mixed:
        lean = ""
        if "BEAR" in option_bias:
            lean = "Options/OI bearish pressure"
        elif "BULL" in option_bias:
            lean = "Options/OI bullish support"
        note = f"Core {core_state or 'MIXED'}"
        if lean:
            note += f" · {lean}"
        return "MIXED", max(bullish, bearish, range_score), note

    decision_direction = _upper(getattr(getattr(snapshot, "decision", None), "market_direction", ""))
    if "BULL" in decision_direction:
        return "UP", bullish, f"Core {core_state or 'BULLISH'}"
    if "BEAR" in decision_direction:
        return "DOWN", bearish, f"Core {core_state or 'BEARISH'}"
    if "RANGE" in decision_direction:
        return "RANGE", range_score, f"Core {core_state or 'RANGE'}"
    return decision_direction or "MIXED", confidence, f"Core {core_state or 'UNRESOLVED'}"


def _zone(level: Any) -> str | None:
    if level is None:
        return None
    lower = getattr(level, "lower", None)
    upper = getattr(level, "upper", None)
    if lower is None or upper is None:
        return None
    try:
        return f"{float(lower):,.0f}–{float(upper):,.0f}"
    except (TypeError, ValueError):
        return None


def safe_direction_evidence_score(snapshot: Any) -> float:
    """Presentation score paired with :func:`market_rukh_display`."""

    return float(market_rukh_display(snapshot)[1])


def safe_brain_hinglish_line(snapshot: Any, previous_snapshot: Any | None = None) -> str:
    """Build one consistent explanation from canonical snapshot fields."""

    decision = getattr(snapshot, "decision", None)
    final_action = _upper(getattr(decision, "final_action", "WAIT")) or "WAIT"
    rukh, _score, rukh_note = market_rukh_display(snapshot)
    option_bias = _option_bias(snapshot)
    heavyweights = getattr(snapshot, "heavyweights", None)
    top7_state = _upper(getattr(heavyweights, "state", ""))
    barrier = getattr(snapshot, "barrier_map", None)
    resistance = _zone(getattr(barrier, "nearest_resistance", None))
    support = _zone(getattr(barrier, "nearest_support", None))

    parts: list[str] = []
    if rukh == "MIXED":
        parts.append("Market abhi mixed hai")
    elif rukh == "UP":
        parts.append("Market upar ja sakta hai")
    elif rukh == "DOWN":
        parts.append("Market neeche ja sakta hai")
    else:
        parts.append(f"Market ka rukh {rukh.lower()} hai")

    evidence: list[str] = []
    weighted_move = getattr(heavyweights, "weighted_move_pct", None)
    prior_heavy = getattr(previous_snapshot, "heavyweights", None)
    prior_move = getattr(prior_heavy, "weighted_move_pct", None)
    if weighted_move is not None:
        top7_text = f"Top-7 weighted move {float(weighted_move):+.2f}% hai"
        if prior_move is not None:
            top7_text += f" aur last snapshot se {float(weighted_move) - float(prior_move):+.2f}% badla"
        evidence.append(top7_text)
    if "BEAR" in option_bias:
        evidence.append("Options/OI bearish pressure dikha raha hai")
    elif "BULL" in option_bias:
        evidence.append("Options/OI bullish support dikha raha hai")
    if evidence:
        parts.append(", lekin ".join(evidence) if len(evidence) > 1 else evidence[0])
    elif rukh_note:
        parts.append(rukh_note)

    level_bits: list[str] = []
    r_level = getattr(barrier, "nearest_resistance", None)
    s_level = getattr(barrier, "nearest_support", None)
    range_bias = _upper(getattr(getattr(barrier, "trading_range", None), "breakout_bias", ""))
    if resistance and r_level is not None:
        r_strength = float(getattr(r_level, "strength", 0.0) or 0.0)
        r_pressure = float(getattr(r_level, "break_pressure", 0.0) or 0.0)
        r_verdict = "tootne ka risk" if r_pressure > r_strength else "filhaal majboot"
        level_bits.append(
            f"R1 {resistance}: bachne ki taakat {r_strength:.0f}, "
            f"tootne ka pressure {r_pressure:.0f}—{r_verdict}; "
            f"{float(r_level.upper):,.0f} ke upar close par confirm"
        )
    if support and s_level is not None:
        s_strength = float(getattr(s_level, "strength", 0.0) or 0.0)
        s_pressure = float(getattr(s_level, "break_pressure", 0.0) or 0.0)
        s_verdict = "tootne ka risk" if s_pressure > s_strength else "filhaal majboot"
        level_bits.append(
            f"S1 {support}: bachne ki taakat {s_strength:.0f}, "
            f"tootne ka pressure {s_pressure:.0f}—{s_verdict}; "
            f"{float(s_level.lower):,.0f} ke neeche close par confirm"
        )
    if level_bits:
        chosen = level_bits[0] if "UPSIDE" in range_bias else level_bits[-1] if "DOWNSIDE" in range_bias else " aur ".join(level_bits)
        parts.append(chosen)

    news = normalized_news_display(getattr(snapshot, "news_context", None))
    if news.status == "READY" and news.risk not in {"LOW", "NONE"}:
        parts.append(f"fresh news risk {news.risk.lower()} hai")
    elif news.status != "READY":
        parts.append("purani/unavailable news ko live direction me weight nahi diya gaya")

    if final_action == "WAIT":
        parts.append("isliye fresh entry ke liye WAIT better hai jab tak confirmation strong na ho")
    else:
        parts.append(f"One-Brain final action {final_action} hai; hedge aur execution guard verify karo")

    return ". ".join(part.rstrip(". ") for part in parts if part) + "."


def display_main_blocker(snapshot: Any) -> str:
    decision = getattr(snapshot, "decision", None)
    clean_reasons = without_old_news_only(getattr(decision, "reasons", ()))
    preferred_markers = (
        "no strategy meets",
        "confirmation",
        "threshold",
        "timeframes mixed",
        "limited",
    )
    for marker in preferred_markers:
        for reason in clean_reasons:
            if marker in reason.lower():
                return reason

    blocker = _text(getattr(decision, "blocker", ""))
    if blocker and not is_old_news_only_text(blocker) and blocker.lower() != "none":
        return blocker

    guard = getattr(snapshot, "execution_guard", None)
    guard_blockers = without_old_news_only(getattr(guard, "blockers", ()))
    for marker in (
        "new-entry window",
        "flow confidence",
        "windows ready",
        "consecutive fresh confirmations",
        "no protected setup",
    ):
        for item in guard_blockers:
            if marker in item.lower():
                return item
    if guard_blockers:
        return guard_blockers[0]
    if clean_reasons:
        return clean_reasons[0]
    return "Strategy edge / confirmation threshold abhi complete nahi hai"


def candidate_invalidation_text(snapshot: Any, candidate_name: str) -> str | None:
    barrier = getattr(snapshot, "barrier_map", None)
    name = _upper(candidate_name)
    if name == "PE SELL":
        level = getattr(barrier, "nearest_support", None)
        zone = _zone(level)
        lower = getattr(level, "lower", None) if level is not None else None
        if zone and lower is not None:
            return (
                f"PE SELL reference invalid: {zone} support ke neeche 3-minute close/acceptance aaye "
                f"(guide below {float(lower):,.0f})."
            )
    if name == "CE SELL":
        level = getattr(barrier, "nearest_resistance", None)
        zone = _zone(level)
        upper = getattr(level, "upper", None) if level is not None else None
        if zone and upper is not None:
            return (
                f"CE SELL reference invalid: {zone} resistance ke upar 3-minute close/acceptance aaye "
                f"(guide above {float(upper):,.0f})."
            )
    return None


def _force_set(obj: Any, name: str, value: Any) -> None:
    if obj is None or not hasattr(obj, name):
        return
    try:
        setattr(obj, name, value)
    except Exception:
        try:
            object.__setattr__(obj, name, value)
        except Exception:
            return


def _clean_strategy(strategy: Any) -> None:
    if strategy is None:
        return
    for field in ("reasons", "cautions", "blockers"):
        if hasattr(strategy, field):
            original = getattr(strategy, field, ())
            cleaned = without_old_news_only(original)
            _force_set(strategy, field, cleaned)


def prepare_snapshot_for_presentation(snapshot: Any) -> Any:
    """Return a deep-copied, presentation-safe snapshot.

    Scores, final action, readiness, strikes and market data are never changed.
    Only explanatory labels/reasons that are internally inconsistent are normalized.
    """

    try:
        view = deepcopy(snapshot)
    except Exception:
        return snapshot

    decision = getattr(view, "decision", None)
    if decision is not None:
        _force_set(decision, "blocker", display_main_blocker(view))
        if hasattr(decision, "reasons"):
            _force_set(decision, "reasons", without_old_news_only(getattr(decision, "reasons", ())))
        for field in ("ce_sell", "pe_sell", "iron_condor", "wait_need"):
            _clean_strategy(getattr(decision, field, None))
        rukh, _score, _note = market_rukh_display(view)
        if rukh == "MIXED":
            _force_set(decision, "market_direction", "MIXED")

    guard = getattr(view, "execution_guard", None)
    if guard is not None and hasattr(guard, "blockers"):
        _force_set(guard, "blockers", without_old_news_only(getattr(guard, "blockers", ())))

    trade_plan = getattr(view, "trade_plan", None)
    if trade_plan is not None:
        blocker = _text(getattr(trade_plan, "blocker", ""))
        if is_old_news_only_text(blocker):
            _force_set(trade_plan, "blocker", "Final One-Brain action / confirmation threshold")
        for field in ("ce_sell", "pe_sell", "iron_condor"):
            plan = getattr(trade_plan, field, None)
            if plan is None:
                continue
            plan_blocker = _text(getattr(plan, "blocker", ""))
            if is_old_news_only_text(plan_blocker):
                _force_set(plan, "blocker", "REFERENCE ONLY — final entry checks incomplete")
            _clean_strategy(plan)

    news = getattr(view, "news_context", None)
    if news is not None:
        display = normalized_news_display(news)
        if display.status.startswith("OLD"):
            _force_set(news, "status", "OLD")
        elif display.status.startswith("STALE"):
            _force_set(news, "status", "STALE")
        elif display.status.startswith("UNAVAILABLE"):
            _force_set(news, "status", "UNAVAILABLE")
        else:
            _force_set(news, "status", "READY")
        _force_set(news, "risk_level", display.risk)
        _force_set(news, "bias", display.bias)
        _force_set(news, "summary", f"{display.status} — {display.note}")
        if display.status != "READY":
            for headline in getattr(news, "headlines", ()) or ():
                impact = _upper(getattr(headline, "impact", ""))
                if impact and "OLD" not in impact and "LIVE WEIGHT" not in impact:
                    _force_set(headline, "impact", f"{impact} (OLD / NO LIVE WEIGHT)")

        feed_status = getattr(view, "feed_status", None)
        if isinstance(feed_status, dict) and "news" in feed_status:
            news_feed = feed_status.get("news")
            _force_set(news_feed, "use_state", display.status)
            _force_set(news_feed, "message", f"{display.status}; {display.note}")

    return view


def install_runtime_presentation_patches() -> None:
    """Patch already-imported presenter/PDF module globals without replacing modules."""

    try:
        import services.summary_presenter as presenter

        presenter.brain_hinglish_line = safe_brain_hinglish_line
        presenter.direction_evidence_score = safe_direction_evidence_score
    except Exception:
        pass
    try:
        import services.pdf_report as pdf_report

        for attr_name, attr_value in tuple(vars(pdf_report).items()):
            function_name = getattr(attr_value, "__name__", "")
            if attr_name == "brain_hinglish_line" or function_name == "brain_hinglish_line":
                setattr(pdf_report, attr_name, safe_brain_hinglish_line)
            elif attr_name == "direction_evidence_score" or function_name == "direction_evidence_score":
                setattr(pdf_report, attr_name, safe_direction_evidence_score)
    except Exception:
        pass
