from __future__ import annotations

import json
from typing import Any

import pandas as pd


_MAX_POINTS = {"1m": 240, "3m": 180, "15m": 120}
_FLOW_WEIGHTS = {60: 0.20, 180: 0.35, 300: 0.45}


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not pd.notna(out):
        return None
    return out


def _epoch_seconds(value: Any) -> int | None:
    try:
        stamp = pd.Timestamp(value)
    except Exception:
        return None
    if pd.isna(stamp):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("Asia/Kolkata")
    return int(stamp.timestamp())


def _current_session_frame(frame: pd.DataFrame, session_date: Any) -> pd.DataFrame:
    if frame is None or frame.empty or "timestamp" not in frame.columns:
        return pd.DataFrame()
    data = frame.copy()
    try:
        timestamps = pd.to_datetime(data["timestamp"], errors="coerce")
        session_mask = timestamps.dt.date == session_date
        current = data.loc[session_mask]
        if not current.empty:
            data = current
    except Exception:
        pass
    return data


def _candle_records(frame: pd.DataFrame, *, session_date: Any, limit: int) -> list[dict[str, Any]]:
    data = _current_session_frame(frame, session_date)
    if data.empty:
        return []
    needed = ["timestamp", "open", "high", "low", "close"]
    if any(column not in data.columns for column in needed):
        return []
    rows: list[dict[str, Any]] = []
    for item in data.tail(max(30, int(limit))).to_dict("records"):
        stamp = _epoch_seconds(item.get("timestamp"))
        open_ = _num(item.get("open"))
        high = _num(item.get("high"))
        low = _num(item.get("low"))
        close = _num(item.get("close"))
        if stamp is None or None in {open_, high, low, close}:
            continue
        rows.append(
            {
                "time": stamp,
                "open": round(open_, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
            }
        )
    unique = {row["time"]: row for row in rows}
    return [unique[key] for key in sorted(unique)]


def _ema_records(
    frame: pd.DataFrame,
    *,
    session_date: Any,
    span: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Build chart-only EMA points from candles already present in the snapshot.

    This never fetches data and never feeds back into One Brain.  The EMA is
    calculated over the full in-memory lookback first, so the visible current-day
    values have the same warm-up convention as the canonical indicator engine.
    """

    if frame is None or frame.empty or "timestamp" not in frame.columns or "close" not in frame.columns:
        return []
    data = frame[["timestamp", "close"]].copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data = data.dropna(subset=["timestamp", "close"]).sort_values("timestamp")
    if data.empty:
        return []
    data["ema"] = data["close"].ewm(span=int(span), adjust=False).mean()
    current = data.loc[data["timestamp"].dt.date == session_date]
    if not current.empty:
        data = current
    rows: list[dict[str, Any]] = []
    for item in data.tail(max(30, int(limit))).to_dict("records"):
        stamp = _epoch_seconds(item.get("timestamp"))
        value = _num(item.get("ema"))
        if stamp is None or value is None:
            continue
        rows.append({"time": stamp, "value": round(value, 2)})
    unique = {row["time"]: row for row in rows}
    return [unique[key] for key in sorted(unique)]


def _barrier_payload(snapshot: Any) -> list[dict[str, Any]]:
    barrier_map = getattr(snapshot, "barrier_map", None)
    if barrier_map is None:
        return []
    output: list[dict[str, Any]] = []
    for attr, fallback in (
        ("nearest_resistance", "R1"),
        ("next_resistance", "R2"),
        ("nearest_support", "S1"),
        ("next_support", "S2"),
    ):
        level = getattr(barrier_map, attr, None)
        if level is None:
            continue
        lower = _num(getattr(level, "lower", None))
        upper = _num(getattr(level, "upper", None))
        midpoint = _num(getattr(level, "midpoint", None))
        if lower is None or upper is None:
            continue
        output.append(
            {
                "label": str(getattr(level, "label", None) or fallback),
                "side": str(getattr(level, "side", "")),
                "lower": round(lower, 2),
                "upper": round(upper, 2),
                "midpoint": round(midpoint if midpoint is not None else (lower + upper) / 2.0, 2),
                "strength": round(_num(getattr(level, "strength", None)) or 0.0),
                "pressure": round(_num(getattr(level, "break_pressure", None)) or 0.0),
                "state": str(getattr(level, "state", "")),
            }
        )
    return output


def _money_tag(score: float | None) -> str:
    if score is None:
        return "ACTIVE"
    if score >= 80:
        return "VERY HIGH"
    if score >= 60:
        return "HIGH"
    if score >= 35:
        return "MEDIUM"
    return "LOW"


def _weighted_share(options: Any, side: str, field_suffix: str) -> float | None:
    windows = tuple(getattr(options, "windows", ()) or ())
    weighted = 0.0
    used = 0.0
    side = side.lower()
    other = "pe" if side == "ce" else "ce"
    for item in windows:
        if str(getattr(item, "status", "")).upper() != "READY":
            continue
        target = int(getattr(item, "target_seconds", 0) or 0)
        weight = _FLOW_WEIGHTS.get(target, 0.25)
        own = _num(getattr(item, f"{side}_{field_suffix}", None))
        opp = _num(getattr(item, f"{other}_{field_suffix}", None))
        if own is None or opp is None:
            continue
        own_abs, opp_abs = abs(own), abs(opp)
        total = own_abs + opp_abs
        if total <= 0:
            continue
        weighted += (own_abs / total * 100.0) * weight
        used += weight
    if used <= 0:
        return None
    return max(0.0, min(100.0, weighted / used))


def _side_behavior(options: Any, side: str) -> str:
    """Classify the most mature ready OI/premium pair for display only."""

    windows = [
        item
        for item in tuple(getattr(options, "windows", ()) or ())
        if str(getattr(item, "status", "")).upper() == "READY"
    ]
    if not windows:
        return "NO FRESH FLOW"
    item = max(windows, key=lambda value: int(getattr(value, "target_seconds", 0) or 0))
    prefix = side.lower()
    oi_delta = _num(getattr(item, f"{prefix}_oi_delta", None))
    premium_delta = _num(getattr(item, f"{prefix}_premium_delta", None))
    if oi_delta is None or premium_delta is None:
        return "ACTIVITY"
    if oi_delta > 0 and premium_delta < 0:
        return "WRITING"
    if oi_delta > 0 and premium_delta > 0:
        return "BUYING"
    if oi_delta < 0 and premium_delta > 0:
        return "SHORT COVERING"
    if oi_delta < 0 and premium_delta < 0:
        return "LONG UNWINDING"
    return "MIXED"


def _money_wall_payload(snapshot: Any) -> list[dict[str, Any]]:
    """Display-only Heavy Money/OI context from already-computed evidence.

    Money Score is a visualization proxy, not rupee capital and not a new One-Brain
    vote. It combines existing wall OI, cluster OI, fresh OI activity and option
    volume activity. No option-chain scan, history read or market request occurs.
    """

    options = getattr(snapshot, "option_intelligence", None)
    if options is None:
        return []
    global_walls = (getattr(snapshot, "metadata", {}) or {}).get("global_oi_walls") or {}
    walls = {
        "CE": getattr(options, "ce_wall", None),
        "PE": getattr(options, "pe_wall", None),
    }
    wall_oi = {key: _num(getattr(value, "oi", None)) if value is not None else None for key, value in walls.items()}
    cluster_oi = {
        key: _num(getattr(value, "cluster_oi", None)) if value is not None else None
        for key, value in walls.items()
    }
    wall_total = sum(value for value in wall_oi.values() if value is not None and value > 0)
    cluster_total = sum(value for value in cluster_oi.values() if value is not None and value > 0)

    output: list[dict[str, Any]] = []
    for side in ("CE", "PE"):
        wall = walls[side]
        if wall is None:
            continue
        strike = _num(getattr(wall, "strike", None))
        oi = wall_oi[side]
        cluster = _num(getattr(wall, "cluster_center", None))
        cluster_value = cluster_oi[side]
        if strike is None:
            continue
        global_info = global_walls.get(side) or global_walls.get(side.lower()) or {}
        global_oi = _num(global_info.get("oi"))
        relative = None
        if oi is not None and global_oi not in (None, 0):
            relative = max(0.0, min(100.0, oi / global_oi * 100.0))

        wall_share = (oi / wall_total * 100.0) if oi is not None and wall_total > 0 else relative
        cluster_share = (
            cluster_value / cluster_total * 100.0
            if cluster_value is not None and cluster_total > 0
            else None
        )
        oi_activity = _weighted_share(options, side, "oi_delta")
        volume_activity = _weighted_share(options, side, "volume_delta")
        components: list[tuple[float, float]] = []
        if wall_share is not None:
            components.append((wall_share, 0.45))
        if cluster_share is not None:
            components.append((cluster_share, 0.20))
        if oi_activity is not None:
            components.append((oi_activity, 0.20))
        if volume_activity is not None:
            components.append((volume_activity, 0.15))
        denom = sum(weight for _, weight in components)
        money_score = (
            max(0.0, min(100.0, sum(value * weight for value, weight in components) / denom))
            if denom > 0
            else relative
        )
        previous_strike = _num(getattr(wall, "previous_strike", None))
        migration = _num(getattr(wall, "migration_points", None))
        output.append(
            {
                "side": side,
                "strike": round(strike, 2),
                "oi": round(oi, 2) if oi is not None else None,
                "cluster": round(cluster, 2) if cluster is not None else None,
                "clusterOi": round(cluster_value, 2) if cluster_value is not None else None,
                "relativePct": round(relative, 1) if relative is not None else None,
                "moneyScore": round(money_score, 1) if money_score is not None else None,
                "tag": _money_tag(relative),
                "moneyTag": _money_tag(money_score if money_score is not None else relative),
                "behavior": _side_behavior(options, side),
                "oiActivityShare": round(oi_activity, 1) if oi_activity is not None else None,
                "volumeActivityShare": round(volume_activity, 1) if volume_activity is not None else None,
                "previousStrike": round(previous_strike, 2) if previous_strike is not None else None,
                "migrationPoints": round(migration, 2) if migration is not None else None,
                "status": str(getattr(wall, "status", "")),
            }
        )
    return output


def _attach_money_to_barriers(
    barriers: list[dict[str, Any]], money_walls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_side = {str(item.get("side", "")).upper(): item for item in money_walls}
    output: list[dict[str, Any]] = []
    for raw in barriers:
        item = dict(raw)
        wall_side = "CE" if "RESIST" in str(item.get("side", "")).upper() else "PE"
        wall = by_side.get(wall_side)
        if wall and wall.get("strike") is not None:
            strike = float(wall["strike"])
            lower, upper = float(item["lower"]), float(item["upper"])
            distance = 0.0 if lower <= strike <= upper else min(abs(strike - lower), abs(strike - upper))
            proximity_limit = max(25.0, abs(upper - lower) * 1.5)
            aligned = distance <= proximity_limit
            item.update(
                {
                    "moneySide": wall_side,
                    "moneyScore": wall.get("moneyScore"),
                    "moneyTag": wall.get("moneyTag") or wall.get("tag"),
                    "moneyBehavior": wall.get("behavior"),
                    "moneyDistance": round(distance, 1),
                    "moneyAligned": bool(aligned),
                }
            )
        output.append(item)
    return output


def _big_player_payload(snapshot: Any) -> dict[str, Any]:
    item = getattr(snapshot, "big_player_activity", None)
    if item is None:
        return {"status": "UNAVAILABLE"}
    created_at = getattr(snapshot, "created_at", None)
    return {
        "direction": str(getattr(item, "direction", "MIXED")),
        "state": str(getattr(item, "state", "")),
        "score": round(_num(getattr(item, "score", None)) or 0.0, 1),
        "confirm": int(getattr(item, "confirmation_count", 0) or 0),
        "confirmTotal": int(getattr(item, "confirmation_total", 0) or 0),
        "persistence": str(getattr(item, "persistence", "")),
        "reversalRisk": str(getattr(item, "reversal_risk", "")),
        "volumeRatio": _num(getattr(item, "futures_volume_ratio", None)),
        "oiChangePct": _num(getattr(item, "futures_oi_change_pct", None)),
        "setup": str(getattr(item, "futures_setup", "")),
        "optionConfirmation": str(getattr(item, "option_confirmation", "")),
        "levelReaction": str(getattr(item, "level_reaction", "")),
        "activityType": str(getattr(item, "activity_type", "")),
        "moveState": str(getattr(item, "move_state", "")),
        "priceShockState": str(getattr(item, "price_shock_state", "")),
        "nextConfirmation": str(getattr(item, "next_confirmation", "")),
        "asOf": created_at.isoformat() if created_at is not None else "",
        "status": str(getattr(item, "status", "")),
    }


def _ai_brain_payload(snapshot: Any) -> dict[str, Any]:
    metadata = getattr(snapshot, "metadata", {}) or {}
    simple = metadata.get("simple_brain") or {}
    decision = getattr(snapshot, "decision", None)
    final_action = str(simple.get("final_action") or getattr(decision, "final_action", "WAIT") or "WAIT")
    direction = str(simple.get("direction") or getattr(decision, "market_direction", "MIXED") or "MIXED")
    readiness = _num(simple.get("entry_readiness"))
    trigger = str(simple.get("trigger") or "Confirmation ka wait")
    reasons = simple.get("reasons") or getattr(decision, "reasons", ()) or ()
    if isinstance(reasons, str):
        reasons = (reasons,)
    reason = str(next((x for x in reasons if str(x).strip()), "Market evidence ko confirm hone do"))
    if len(reason) > 96:
        reason = reason[:93].rstrip() + "..."
    if len(trigger) > 88:
        trigger = trigger[:85].rstrip() + "..."
    return {
        "action": final_action,
        "direction": direction,
        "readiness": round(readiness, 1) if readiness is not None else None,
        "reason": reason,
        "trigger": trigger,
        "entryState": str(simple.get("entry_state") or ""),
    }


def build_live_barrier_chart_payload(snapshot: Any) -> dict[str, Any]:
    created_at = getattr(snapshot, "created_at", None)
    session_date = created_at.date() if created_at is not None else pd.Timestamp.now(tz="Asia/Kolkata").date()
    spot = _num(getattr(getattr(snapshot, "barrier_map", None), "current_price", None))
    if spot is None:
        spot = _num((getattr(snapshot, "nifty_quote", {}) or {}).get("last_price"))
    options = getattr(snapshot, "option_intelligence", None)
    money_walls = _money_wall_payload(snapshot)
    barriers = _attach_money_to_barriers(_barrier_payload(snapshot), money_walls)
    candles_1m = getattr(snapshot, "candles_1m", pd.DataFrame())
    candles_3m = getattr(snapshot, "candles_3m", pd.DataFrame())
    candles_15m = getattr(snapshot, "candles_15m", pd.DataFrame())
    market_session = getattr(snapshot, "market_session", None)
    return {
        "snapshotId": str(getattr(snapshot, "snapshot_id", "")),
        "createdAt": created_at.isoformat() if created_at is not None else "",
        "spot": round(spot, 2) if spot is not None else None,
        "defaultTf": "15m",
        "session": {
            "code": str(getattr(market_session, "code", "")),
            "label": str(getattr(market_session, "label", "")),
            "isLive": bool(getattr(market_session, "is_live", False)),
        },
        "candles": {
            "1m": _candle_records(candles_1m, session_date=session_date, limit=_MAX_POINTS["1m"]),
            "3m": _candle_records(candles_3m, session_date=session_date, limit=_MAX_POINTS["3m"]),
            "15m": _candle_records(candles_15m, session_date=session_date, limit=_MAX_POINTS["15m"]),
        },
        "ema": {
            "1m": {
                "20": _ema_records(candles_1m, session_date=session_date, span=20, limit=_MAX_POINTS["1m"]),
                "50": _ema_records(candles_1m, session_date=session_date, span=50, limit=_MAX_POINTS["1m"]),
            },
            "3m": {
                "20": _ema_records(candles_3m, session_date=session_date, span=20, limit=_MAX_POINTS["3m"]),
                "50": _ema_records(candles_3m, session_date=session_date, span=50, limit=_MAX_POINTS["3m"]),
            },
            "15m": {
                "20": _ema_records(candles_15m, session_date=session_date, span=20, limit=_MAX_POINTS["15m"]),
                "50": _ema_records(candles_15m, session_date=session_date, span=50, limit=_MAX_POINTS["15m"]),
            },
        },
        "barriers": barriers,
        "moneyWalls": money_walls,
        "bigPlayer": _big_player_payload(snapshot),
        "aiBrain": _ai_brain_payload(snapshot),
        "optionFlow": {
            "bias": str(getattr(options, "market_bias", "")) if options is not None else "",
            "confidence": round(_num(getattr(options, "confidence", None)) or 0.0, 1) if options is not None else 0.0,
            "persistence": str(getattr(options, "persistence", "")) if options is not None else "",
        },
    }


def render_live_barrier_chart(snapshot: Any) -> None:
    """Render the integrated One-Brain broker-style chart.

    Golden Rule: no Dhan/Railway/API request, no journal/history read, no canonical
    mutation and no new One-Brain vote. Small display-only EMA/money calculations
    use only the snapshot already in memory; browser rendering handles the chart.
    """

    import streamlit as st
    import streamlit.components.v1 as components

    payload = build_live_barrier_chart_payload(snapshot)
    if not any(payload["candles"].values()):
        st.caption("📊 Live chart unavailable — completed candle data ka wait.")
        return

    safe_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).replace("<", "\\u003c")
    html = f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<script src="https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root{{--bg:#07101d;--panel:#0b1626;--text:#edf5ff;--muted:#91a4bc;--line:rgba(148,163,184,.18);--cyan:#18d3ff;--purple:#a86cff;--orange:#ff9f43;--red:#ff4d6d;--green:#25d98a;--teal:#00c9a7;}}
  *{{box-sizing:border-box}}
  html,body{{margin:0;padding:0;background:transparent;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;color:var(--text)}}
  .wrap{{position:relative;border:1px solid rgba(77,175,255,.30);border-radius:16px;overflow:hidden;background:linear-gradient(145deg,#08111e 0%,#0a1423 48%,#0d1121 100%);box-shadow:0 12px 36px rgba(0,0,0,.25)}}
  .head{{display:flex;gap:8px;align-items:center;justify-content:space-between;padding:9px 11px;border-bottom:1px solid var(--line);flex-wrap:wrap;background:linear-gradient(90deg,rgba(24,211,255,.11),rgba(168,108,255,.10),rgba(255,77,109,.07))}}
  .left{{display:flex;align-items:center;gap:8px;min-width:0;flex-wrap:wrap}}
  .title{{font-size:14px;font-weight:850;white-space:nowrap;letter-spacing:.1px}}
  .spot{{font-size:13px;font-weight:850;white-space:nowrap;color:#fff}}
  .live{{font-size:10px;font-weight:850;padding:3px 6px;border-radius:999px;background:rgba(37,217,138,.15);color:#65f7b6;border:1px solid rgba(37,217,138,.32)}}
  .live.ref{{background:rgba(255,209,102,.13);color:#ffe08a;border-color:rgba(255,209,102,.34)}}
  .buttons{{display:flex;gap:5px;align-items:center;flex-wrap:wrap}}
  button{{border:1px solid rgba(148,163,184,.30);background:#101c2e;color:#c9d6e7;padding:5px 9px;border-radius:8px;font-size:12px;cursor:pointer;transition:.15s}}
  button:hover{{transform:translateY(-1px);border-color:rgba(24,211,255,.55)}}
  button.active{{background:linear-gradient(135deg,#155e75,#5037a7);color:#fff;border-color:#43d9ff}}
  #fullBtn{{font-weight:900;min-width:36px;background:linear-gradient(135deg,rgba(24,211,255,.16),rgba(168,108,255,.18))}}
  .statusRow{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;padding:8px 10px;background:rgba(6,12,23,.60);border-bottom:1px solid var(--line)}}
  .pill{{min-width:0;padding:7px 9px;border-radius:10px;border:1px solid rgba(148,163,184,.16);background:rgba(255,255,255,.035);font-size:11px;line-height:1.3}}
  .pill b{{display:block;font-size:10px;color:var(--muted);margin-bottom:2px;text-transform:uppercase;letter-spacing:.35px}}
  .pill strong{{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block}}
  .pill small{{display:block;margin-top:2px;color:#9fb0c7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .moneyCE{{border-color:rgba(255,159,67,.34);background:linear-gradient(135deg,rgba(255,159,67,.10),rgba(255,77,109,.05))}}
  .moneyPE{{border-color:rgba(37,217,138,.34);background:linear-gradient(135deg,rgba(37,217,138,.10),rgba(0,201,167,.05))}}
  .big{{border-color:rgba(24,211,255,.32);background:linear-gradient(135deg,rgba(24,211,255,.09),rgba(168,108,255,.08))}}
  .chartWrap{{position:relative;background:#07101d}}
  #chart{{position:relative;z-index:2;width:100%;height:455px}}
  #bands{{position:absolute;z-index:3;left:0;right:58px;top:0;bottom:0;pointer-events:none;overflow:hidden}}
  .barrierBand{{position:absolute;left:0;right:0;border-top:1px solid;border-bottom:1px solid;opacity:.72}}
  .barrierBand span{{position:absolute;left:4px;top:1px;padding:1px 5px;border-radius:5px;font-size:9px;font-weight:900;letter-spacing:.2px;background:rgba(5,10,18,.76);white-space:nowrap}}
  .brain{{position:absolute;left:12px;top:11px;z-index:6;width:min(310px,calc(100% - 24px));border:1px solid rgba(168,108,255,.48);border-radius:12px;padding:8px 10px;background:linear-gradient(135deg,rgba(11,18,35,.91),rgba(39,20,67,.88));backdrop-filter:blur(7px);box-shadow:0 8px 25px rgba(0,0,0,.28);pointer-events:none}}
  .brainTop{{display:flex;align-items:center;justify-content:space-between;gap:7px;margin-bottom:3px}}
  .brainTitle{{font-size:10px;font-weight:900;color:#d5bdff;letter-spacing:.55px}}
  .brainAction{{font-size:13px;font-weight:950;padding:2px 7px;border-radius:999px}}
  .brainMeta{{font-size:10.5px;color:#b8c7da;margin-bottom:3px}}
  .brainReason{{font-size:11.5px;font-weight:750;color:#f6f9ff;line-height:1.32}}
  .brainTrigger{{font-size:10.5px;color:#8ee8ff;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .foot{{display:flex;justify-content:space-between;gap:8px;align-items:center;padding:6px 10px 8px;font-size:10.5px;color:#8fa2ba;border-top:1px solid var(--line);flex-wrap:wrap;background:rgba(5,10,19,.62)}}
  .legend{{display:flex;gap:10px;flex-wrap:wrap}}
  .dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;box-shadow:0 0 8px currentColor}}
  .r1{{background:var(--orange)}} .r2{{background:var(--red)}} .s1{{background:var(--green)}} .s2{{background:var(--teal)}} .ce{{background:#ffbf69}} .pe{{background:#51f0ba}} .bp{{background:var(--cyan)}} .e20{{background:#ffd166}} .e50{{background:#a86cff}}
  .review{{color:#aebbd0}}
  .wrap:fullscreen{{border-radius:0;border:none;background:#050b14;width:100vw;height:100vh}}
  .wrap:fullscreen #chart{{height:calc(100vh - 168px)}}
  .wrap.focusMode{{border-radius:10px}}
  .wrap.focusMode #chart{{height:78vh;min-height:620px}}
  @media(max-width:760px){{#chart{{height:390px}}.statusRow{{grid-template-columns:1fr 1fr}}.big{{grid-column:1/-1}}.head{{padding:7px 8px}}.title{{font-size:13px}}button{{padding:5px 7px}}.brain{{width:min(280px,calc(100% - 18px));left:9px;top:9px}}.wrap.focusMode #chart{{height:72vh;min-height:500px}}}}
  @media(max-width:430px){{.statusRow{{grid-template-columns:1fr 1fr;padding:6px}}.pill{{padding:6px 7px}}#chart{{height:370px}}.brainReason{{font-size:11px}}.legend{{gap:7px}}}}
</style>
</head>
<body>
<div class="wrap" id="wrap">
  <div class="head">
    <div class="left"><span class="title">🧠 NIFTY · ONE BRAIN LIVE CHART</span><span class="spot" id="spot"></span><span class="live" id="liveBadge">SNAPSHOT</span></div>
    <div class="buttons">
      <button data-tf="1m">1m</button><button data-tf="3m">3m</button><button data-tf="5m">5m</button><button data-tf="15m">15m</button>
      <button id="modeBtn" class="active" title="Simple/Advanced chart mode">Mode: Simple</button>
      <button id="emaBtn" class="active" title="EMA20/50 show-hide">EMA</button>
      <button id="fullBtn" title="Full screen / focus mode">⛶</button>
    </div>
  </div>
  <div class="statusRow">
    <div class="pill moneyCE"><b>🔥 CE Heavy Money / OI</b><strong id="ceWall">—</strong><small id="ceFlow"></small></div>
    <div class="pill moneyPE"><b>💰 PE Heavy Money / OI</b><strong id="peWall">—</strong><small id="peFlow"></small></div>
    <div class="pill big"><b>⚡ Big Player Activity</b><strong id="bigPlayer">—</strong><small id="bigDetail"></small></div>
  </div>
  <div class="chartWrap">
    <div id="chart"></div><div id="bands"></div>
    <div class="brain" id="brain">
      <div class="brainTop"><span class="brainTitle">✨ MINI AI BRAIN</span><span class="brainAction" id="brainAction">WAIT</span></div>
      <div class="brainMeta" id="brainMeta">MIXED</div>
      <div class="brainReason" id="brainReason">Market evidence loading...</div>
      <div class="brainTrigger" id="brainTrigger">Next: confirmation</div>
    </div>
  </div>
  <div class="foot">
    <div class="legend"><span><i class="dot r1"></i>Resistance</span><span><i class="dot s1"></i>Support</span><span><i class="dot ce"></i>CE Wall</span><span><i class="dot pe"></i>PE Wall</span><span><i class="dot bp"></i>Big Player</span><span><i class="dot e20"></i>EMA20</span><span><i class="dot e50"></i>EMA50</span></div>
    <div class="review" id="reviewInfo"></div>
  </div>
</div>
<script>
const P = {safe_json};
const container = document.getElementById('chart');
const wrap = document.getElementById('wrap');
const bands = document.getElementById('bands');
const chart = LightweightCharts.createChart(container, {{
  autoSize: true,
  layout: {{background: {{color:'#07101d'}}, textColor:'#b9c8dc'}},
  grid: {{vertLines: {{color:'rgba(93,125,165,.10)'}}, horzLines: {{color:'rgba(93,125,165,.10)'}}}},
  crosshair: {{mode: LightweightCharts.CrosshairMode.Normal}},
  rightPriceScale: {{borderColor:'rgba(114,151,190,.23)', scaleMargins: {{top:0.08,bottom:0.08}}}},
  timeScale: {{borderColor:'rgba(114,151,190,.23)', timeVisible:true, secondsVisible:false, rightOffset:4, barSpacing:7}},
  localization: {{locale:'en-IN'}},
  handleScroll: {{mouseWheel:true, pressedMouseMove:true, horzTouchDrag:true, vertTouchDrag:false}},
  handleScale: {{axisPressedMouseMove:true, mouseWheel:true, pinch:true}}
}});
const candles = chart.addCandlestickSeries({{
  upColor:'#25d98a', downColor:'#ff4d6d', borderVisible:false,
  wickUpColor:'#25d98a', wickDownColor:'#ff4d6d', priceLineVisible:false,
  lastValueVisible:true
}});
const ema20 = chart.addLineSeries({{color:'#ffd166',lineWidth:2,lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false}});
const ema50 = chart.addLineSeries({{color:'#a86cff',lineWidth:2,lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false}});

function build5m(src) {{
  const buckets = new Map();
  (src || []).forEach(x => {{
    const t = Math.floor(x.time / 300) * 300;
    const old = buckets.get(t);
    if (!old) buckets.set(t, {{time:t,open:x.open,high:x.high,low:x.low,close:x.close}});
    else {{ old.high=Math.max(old.high,x.high); old.low=Math.min(old.low,x.low); old.close=x.close; }}
  }});
  return Array.from(buckets.values()).sort((a,b)=>a.time-b.time);
}}
function buildEma(src, span) {{
  const data=(src||[]).slice().sort((a,b)=>a.time-b.time); if(!data.length) return [];
  const alpha=2/(span+1); let value=Number(data[0].close); const out=[];
  data.forEach((x,i)=>{{ const close=Number(x.close); value=i===0?close:(close*alpha+value*(1-alpha)); out.push({{time:x.time,value:Number(value.toFixed(2))}}); }});
  return out;
}}
P.candles['5m'] = build5m(P.candles['1m']);
P.ema['5m'] = {{'20':buildEma(P.candles['5m'],20),'50':buildEma(P.candles['5m'],50)}};

function compactOi(v) {{
  if (v == null || !Number.isFinite(Number(v))) return 'OI —';
  const n = Number(v);
  if (Math.abs(n) >= 10000000) return `OI ${{(n/10000000).toFixed(2)}}Cr`;
  if (Math.abs(n) >= 100000) return `OI ${{(n/100000).toFixed(1)}}L`;
  if (Math.abs(n) >= 1000) return `OI ${{(n/1000).toFixed(1)}}K`;
  return `OI ${{n.toFixed(0)}}`;
}}
function fmt(v, digits=0) {{ return v == null ? '—' : Number(v).toLocaleString('en-IN',{{minimumFractionDigits:digits,maximumFractionDigits:digits}}); }}
function lineColor(label, side) {{
  const l=String(label||'').toUpperCase();
  if(l==='R1') return '#ff9f43'; if(l==='R2') return '#ff4d6d'; if(l==='S1') return '#25d98a'; if(l==='S2') return '#00c9a7';
  return String(side||'').toUpperCase().includes('RESIST') ? '#ff6b6b' : '#33d69f';
}}
function rgba(hex,alpha) {{
  const h=hex.replace('#',''); const r=parseInt(h.slice(0,2),16),g=parseInt(h.slice(2,4),16),b=parseInt(h.slice(4,6),16);
  return `rgba(${{r}},${{g}},${{b}},${{alpha}})`;
}}
function actionStyle(action) {{
  const a=String(action||'').toUpperCase();
  if(a.includes('CE SELL') || a.includes('DOWN')) return ['#ff4d6d','rgba(255,77,109,.16)'];
  if(a.includes('PE SELL') || a.includes('UP')) return ['#25d98a','rgba(37,217,138,.16)'];
  if(a.includes('BUY')) return ['#18d3ff','rgba(24,211,255,.16)'];
  return ['#ffd166','rgba(255,209,102,.14)'];
}}
let advancedMode=false;
const createdPriceLines=[];
function addLine(opts) {{
  try {{ const line=candles.createPriceLine(opts); createdPriceLines.push(line); return line; }} catch(e) {{ return null; }}
}}
function clearLines() {{
  while(createdPriceLines.length) {{
    const line=createdPriceLines.pop();
    try {{ candles.removePriceLine(line); }} catch(e) {{}}
  }}
}}
function visibleBarriers() {{
  const all=P.barriers||[];
  if(advancedMode) return all;
  return all.filter(b => ['R1','S1'].includes(String(b.label||'').toUpperCase()));
}}
function renderLevels() {{
  clearLines();
  visibleBarriers().forEach(b => {{
    const color=lineColor(b.label,b.side);
    const side=String(b.side||'').toUpperCase().includes('RESIST')?'RESISTANCE':'SUPPORT';
    if(advancedMode) {{
      const money=b.moneyAligned && b.moneyScore!=null ? ` · ${{b.moneySide}} ${{Number(b.moneyScore).toFixed(0)}}` : '';
      addLine({{price:b.lower,color,lineWidth:1,lineStyle:LightweightCharts.LineStyle.Dashed,axisLabelVisible:false,title:''}});
      addLine({{price:b.upper,color,lineWidth:1,lineStyle:LightweightCharts.LineStyle.Dashed,axisLabelVisible:false,title:''}});
      addLine({{price:b.midpoint,color,lineWidth:2,lineStyle:LightweightCharts.LineStyle.Solid,axisLabelVisible:true,
        title:`${{b.label}} · STR ${{b.strength}} · BRK ${{b.pressure}}${{money}}`}});
    }} else {{
      addLine({{price:b.midpoint,color,lineWidth:2,lineStyle:LightweightCharts.LineStyle.Solid,axisLabelVisible:true,title:side}});
    }}
  }});
  (P.moneyWalls||[]).forEach(w => {{
    const ce=String(w.side).toUpperCase()==='CE';
    const color=ce?'#ffbf69':'#51f0ba';
    const title=advancedMode
      ? `${{w.side}} WALL · M ${{w.moneyScore==null?'—':Number(w.moneyScore).toFixed(0)}} · ${{w.behavior||'ACTIVITY'}}`
      : `${{w.side}} WALL`;
    addLine({{price:w.strike,color,lineWidth:2,lineStyle:LightweightCharts.LineStyle.Dotted,axisLabelVisible:true,title}});
    if(advancedMode && w.cluster != null && Math.abs(Number(w.cluster)-Number(w.strike)) >= 1) {{
      addLine({{price:w.cluster,color,lineWidth:1,lineStyle:LightweightCharts.LineStyle.Dashed,axisLabelVisible:false,title:`${{w.side}} CLUSTER`}});
    }}
  }});
  if(P.spot!=null) {{
    document.getElementById('spot').textContent=Number(P.spot).toLocaleString('en-IN',{{minimumFractionDigits:2,maximumFractionDigits:2}});
    addLine({{price:P.spot,color:'#e2e8f0',lineWidth:1,lineStyle:LightweightCharts.LineStyle.Dotted,axisLabelVisible:true,title:'NIFTY'}});
  }}
}}

const session=P.session||{{}}; const badge=document.getElementById('liveBadge');
badge.textContent=session.isLive?'LIVE':'REFERENCE'; if(!session.isLive) badge.classList.add('ref');
const walls = Object.fromEntries((P.moneyWalls||[]).map(w=>[String(w.side).toUpperCase(),w]));
function migrationText(w) {{ if(w?.migrationPoints==null) return ''; const n=Number(w.migrationPoints); return ` · Wall Δ ${{n>0?'+':''}}${{fmt(n)}}`; }}
function wallText(side) {{
  const w=walls[side]; if(!w) return 'Unavailable';
  return `${{fmt(w.strike)}} · Money ${{w.moneyScore==null?'—':Number(w.moneyScore).toFixed(0)}}/100 · ${{w.moneyTag||w.tag}}`;
}}
function flowText(side) {{
  const w=walls[side]; if(!w) return '';
  return `${{w.behavior||'ACTIVITY'}} · ${{compactOi(w.oi)}}${{migrationText(w)}}`;
}}
document.getElementById('ceWall').textContent = wallText('CE'); document.getElementById('ceFlow').textContent=flowText('CE');
document.getElementById('peWall').textContent = wallText('PE'); document.getElementById('peFlow').textContent=flowText('PE');
const bp=P.bigPlayer||{{}};
const bpText = bp.status==='UNAVAILABLE' ? 'Unavailable' : `${{bp.direction||'MIXED'}} ${{Number(bp.score||0).toFixed(0)}}/100 · Confirm ${{bp.confirm||0}}/${{bp.confirmTotal||0}}`;
document.getElementById('bigPlayer').textContent=bpText;
document.getElementById('bigDetail').textContent=bp.status==='UNAVAILABLE'?'':`${{bp.setup||bp.activityType||'ACTIVITY'}}${{bp.volumeRatio!=null?` · Vol ${{Number(bp.volumeRatio).toFixed(2)}}x`:''}}${{bp.levelReaction?` · ${{bp.levelReaction}}`:''}}`;

const ai=P.aiBrain||{{}};
const [ac,abg]=actionStyle(ai.action);
const actionEl=document.getElementById('brainAction'); actionEl.textContent=ai.action||'WAIT'; actionEl.style.color=ac; actionEl.style.background=abg; actionEl.style.border=`1px solid ${{ac}}66`;
document.getElementById('brainMeta').textContent=`${{ai.direction||'MIXED'}}${{ai.readiness!=null?` · Readiness ${{Number(ai.readiness).toFixed(0)}}/100`:''}} · Flow ${{P.optionFlow?.bias||'—'}} ${{Number(P.optionFlow?.confidence||0).toFixed(0)}}/100`;
document.getElementById('brainReason').textContent=ai.reason||'Market evidence ko confirm hone do';
document.getElementById('brainTrigger').textContent=`Next: ${{ai.trigger||'confirmation ka wait'}}`;

let currentTf=P.defaultTf||'15m'; let emaEnabled=true;
function applyBigPlayerMarker(data) {{
  if(!data.length || !bp || bp.status==='UNAVAILABLE') {{ try{{candles.setMarkers([])}}catch(e){{}}; return; }}
  const d=String(bp.direction||'').toUpperCase();
  if(!d.includes('BUY') && !d.includes('SELL')) {{ try{{candles.setMarkers([])}}catch(e){{}}; return; }}
  const last=data[data.length-1]; const buy=d.includes('BUY');
  try {{ candles.setMarkers([{{time:last.time,position:buy?'belowBar':'aboveBar',color:buy?'#18d3ff':'#ff6bb5',shape:buy?'arrowUp':'arrowDown',text:`BIG ${{buy?'BUY':'SELL'}} ${{Number(bp.score||0).toFixed(0)}}`}}]); }} catch(e) {{}}
}}
function renderBands() {{
  bands.innerHTML='';
  visibleBarriers().forEach(b=>{{
    const y1=candles.priceToCoordinate(Number(b.upper)); const y2=candles.priceToCoordinate(Number(b.lower));
    if(y1==null || y2==null) return;
    const top=Math.min(y1,y2), height=Math.max(3,Math.abs(y2-y1)), color=lineColor(b.label,b.side);
    const el=document.createElement('div'); el.className='barrierBand'; el.style.top=`${{top}}px`; el.style.height=`${{height}}px`;
    el.style.background=rgba(color,advancedMode ? 0.10 : 0.075); el.style.borderColor=rgba(color,advancedMode ? 0.38 : 0.28);
    const side=String(b.side||'').toUpperCase().includes('RESIST')?'Resistance':'Support';
    const label=advancedMode
      ? `${{b.label}} · STR ${{b.strength}} · BRK ${{b.pressure}}`
      : `${{side}} ${{fmt(b.lower)}}–${{fmt(b.upper)}}`;
    el.innerHTML=`<span style="color:${{color}}">${{label}}</span>`; bands.appendChild(el);
  }});
}}
function setTf(tf) {{
  let data = P.candles[tf] || [];
  if (!data.length) {{ const fallback = ['15m','3m','1m'].find(k => (P.candles[k]||[]).length) || '1m'; tf=fallback; data=P.candles[fallback]||[]; }}
  currentTf=tf; candles.setData(data); applyBigPlayerMarker(data);
  const ema=P.ema?.[tf]||{{}}; ema20.setData(emaEnabled?(ema['20']||[]):[]); ema50.setData(emaEnabled?(ema['50']||[]):[]);
  document.querySelectorAll('button[data-tf]').forEach(btn => btn.classList.toggle('active', btn.dataset.tf===tf));
  chart.timeScale().fitContent(); renderLevels(); setTimeout(renderBands,40);
}}
document.querySelectorAll('button[data-tf]').forEach(btn => btn.addEventListener('click',()=>setTf(btn.dataset.tf)));
const modeBtn=document.getElementById('modeBtn');
modeBtn.addEventListener('click',()=>{{
  advancedMode=!advancedMode;
  modeBtn.textContent=advancedMode?'Mode: Advanced':'Mode: Simple';
  modeBtn.classList.toggle('active',!advancedMode);
  renderLevels(); renderBands();
}});
const emaBtn=document.getElementById('emaBtn'); emaBtn.addEventListener('click',()=>{{emaEnabled=!emaEnabled; emaBtn.classList.toggle('active',emaEnabled); setTf(currentTf);}});

function setFrameHeight(height) {{ try {{ window.parent.postMessage({{isStreamlitMessage:true,type:'streamlit:setFrameHeight',height:height}},'*'); }} catch(e) {{}} }}
let focusMode=false; const fullBtn=document.getElementById('fullBtn');
fullBtn.addEventListener('click', async()=>{{
  try {{
    if(document.fullscreenEnabled) {{ if(!document.fullscreenElement) await wrap.requestFullscreen(); else await document.exitFullscreen(); return; }}
  }} catch(e) {{}}
  focusMode=!focusMode; wrap.classList.toggle('focusMode',focusMode); fullBtn.textContent=focusMode?'✕':'⛶'; setFrameHeight(focusMode?Math.max(760,window.screen.height-80):650); setTimeout(()=>{{chart.timeScale().fitContent();renderBands();}},120);
}});
document.addEventListener('fullscreenchange',()=>{{ fullBtn.textContent=document.fullscreenElement?'✕':'⛶'; setTimeout(()=>{{chart.timeScale().fitContent();renderBands();}},80); }});
window.addEventListener('resize',()=>setTimeout(renderBands,50));
try {{ chart.timeScale().subscribeVisibleLogicalRangeChange(()=>setTimeout(renderBands,0)); }} catch(e) {{}}
const stamp = P.createdAt ? new Date(P.createdAt).toLocaleTimeString('en-IN',{{hour:'2-digit',minute:'2-digit'}}) : '';
document.getElementById('reviewInfo').textContent=`Display-only · ${{stamp}} · ${{P.snapshotId||''}}`;
setTf(P.defaultTf || '15m');
</script>
</body>
</html>
"""
    with st.container(border=False):
        components.html(html, height=650, scrolling=False)
