"""Strong aligned confirmations only, not autonomous trade recommendations."""


def aligned_pattern_alert(snapshot):
    if not snapshot.market_session.is_live:
        return None
    if not all(getattr(snapshot.feed_status.get(x), "use_state", "") == "LIVE" for x in ("quotes", "candles", "option_chain")):
        return None
    direction = snapshot.decision.market_direction  # pattern points excluded upstream
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
    signals = [s for s in (patterns.wm_3m, patterns.candle_3m)
               if s.stage == "CONFIRMED" and s.direction == direction and s.status == "READY"
               and s.confidence >= 65 and s.strength in {"STRONG", "VERY STRONG"} and s.level_label]
    if not signals:
        return None
    # Same directional move shares a 3m confirmation bucket; per-pattern IDs
    # prevent refresh spam while allowing genuinely new patterns later.
    names = " + ".join(s.name for s in signals)
    ids = [f"{s.family}:{s.name}:{s.detected_at}:{s.neckline}" for s in signals]
    return {"direction": direction, "names": names, "pattern_ids": ids,
            "captured_at": snapshot.created_at.isoformat(),
            "message": f"{direction} CONFIRMATION STRONGER — {names}\nCore + options aligned; strong completed 3m trigger.\n"
                       + " | ".join(f"{s.name}: invalid {s.invalidation_level}" for s in signals)
                       + "\nPattern confirmation only; no automatic trade/order."}
