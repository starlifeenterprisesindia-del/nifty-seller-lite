"""Strong aligned confirmations only, not autonomous trade recommendations."""

from analysis.alerts import early_activity_alert_qualifies, heavy_activity_alert_qualifies


def _brain_direction(snapshot):
    simple = (getattr(snapshot, "metadata", {}) or {}).get("simple_brain") or {}
    value = str(simple.get("direction") or getattr(snapshot.decision, "market_direction", "")).upper()
    if value in {"UP", "BULLISH"}:
        return "BULLISH"
    if value in {"DOWN", "BEARISH"}:
        return "BEARISH"
    return "MIXED"


def aligned_pattern_alert(snapshot):
    if not snapshot.market_session.is_live:
        return None
    if not all(getattr(snapshot.feed_status.get(x), "use_state", "") == "LIVE" for x in ("quotes", "candles", "option_chain")):
        return None
    direction = _brain_direction(snapshot)
    if direction not in {"BULLISH", "BEARISH"}:
        return None
    core = snapshot.core_evidence
    options = snapshot.option_intelligence
    if direction == "BULLISH":
        aligned = core.bullish_score > core.bearish_score + 8 and options.bullish_score > options.bearish_score + 8
    else:
        aligned = core.bearish_score > core.bullish_score + 8 and options.bearish_score > options.bullish_score + 8
    if not aligned or options.status != "READY":
        return None
    patterns = snapshot.patterns
    if patterns is None:
        return None
    signals = [
        s for s in (patterns.wm_3m, patterns.candle_3m)
        if s.stage == "CONFIRMED"
        and s.direction == direction
        and s.status == "READY"
        and s.confidence >= 65
        and s.strength in {"STRONG", "VERY STRONG"}
        and s.level_label
    ]
    if not signals:
        return None
    names = " + ".join(s.name for s in signals)
    ids = [f"{s.family}:{s.name}:{s.detected_at}:{s.neckline}" for s in signals]
    return {
        "direction": direction,
        "names": names,
        "pattern_ids": ids,
        "captured_at": snapshot.created_at.isoformat(),
        "message": (
            f"{direction} CONFIRMATION STRONGER — {names}\n"
            "Core + options aligned; strong completed 3m trigger.\n"
            + " | ".join(f"{s.name}: invalid {s.invalidation_level}" for s in signals)
            + "\nPattern confirmation only; no automatic trade/order."
        ),
    }


def combined_signal_alert(snapshot):
    """Merge W/M, special candle and Big Player into one deduplicated alert payload.

    The function only packages already-computed evidence.  It makes no API call and
    performs no new market calculation, so it cannot slow or alter One-Brain.
    """
    if not snapshot.market_session.is_live:
        return None

    pattern = aligned_pattern_alert(snapshot)
    activity = getattr(snapshot, "big_player_activity", None)
    heavy = heavy_activity_alert_qualifies(activity)
    early = bool(not heavy and early_activity_alert_qualifies(activity))
    if pattern is None and not (heavy or early):
        return None

    pattern_direction = str((pattern or {}).get("direction") or "")
    activity_direction = (
        "BULLISH" if str(getattr(activity, "direction", "")).upper() == "BUYING"
        else "BEARISH" if str(getattr(activity, "direction", "")).upper() == "SELLING"
        else ""
    )
    direction = pattern_direction or activity_direction or "MIXED"
    conflict = bool(pattern_direction and activity_direction and pattern_direction != activity_direction)

    ids = list((pattern or {}).get("pattern_ids") or [])
    labels = []
    if pattern is not None:
        labels.append(str(pattern.get("names") or "3m pattern"))
    if heavy or early:
        stage = "CONFIRMED" if heavy else "EARLY"
        activity_type = str(getattr(activity, "activity_type", "ACTIVITY") or "ACTIVITY")
        ids.append(f"BIG:{stage}:{activity_direction}:{activity_type}")
        labels.append(f"Big Player {stage} {getattr(activity, 'direction', 'MIXED')}")

    if conflict:
        title = "⚠️ SIGNAL CONFLICT"
        summary = "Pattern aur Big Player opposite hain — WAIT/confirmation better."
        direction = pattern_direction
    elif pattern is not None and (heavy or early):
        icon = "🔥" if direction == "BULLISH" else "🔴"
        title = f"{icon} STRONG {direction} CONFIRMATION"
        summary = "3m pattern/candle + Big Player same direction confirm kar rahe hain."
    elif pattern is not None:
        icon = "🟢" if direction == "BULLISH" else "🔴"
        title = f"{icon} STRONG 3M {direction} SIGNAL"
        summary = "Completed 3m W/M/candle confirmation; Big Player confirmation abhi nahi."
    else:
        stage = "CONFIRMED" if heavy else "EARLY"
        icon = "🐘"
        title = f"{icon} BIG PLAYER {stage} — {getattr(activity, 'direction', 'MIXED')}"
        summary = "Large-activity evidence; candle/W-M confirmation alag se dekho."

    simple = (getattr(snapshot, "metadata", {}) or {}).get("simple_brain") or {}
    barrier = ((simple.get("blocks") or {}).get("barrier_entry") or {})
    barrier_note = str(barrier.get("note") or "")
    lines = [title, " + ".join(labels), summary]
    if barrier_note:
        lines.append("Barrier: " + barrier_note)
    if activity is not None and (heavy or early):
        lines.append(
            f"Big Player score {float(getattr(activity, 'score', 0) or 0):.0f}/100 · "
            f"{int(getattr(activity, 'confirmation_count', 0) or 0)}/{int(getattr(activity, 'confirmation_total', 0) or 0)}"
        )
    lines.append("Alert-only; automatic order nahi lagaya gaya.")

    return {
        "direction": direction if direction in {"BULLISH", "BEARISH"} else "BULLISH",
        "names": " + ".join(labels),
        "pattern_ids": ids,
        "captured_at": snapshot.created_at.isoformat(),
        "message": "\n".join(lines),
        "conflict": conflict,
    }
