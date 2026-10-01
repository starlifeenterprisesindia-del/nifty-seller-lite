from __future__ import annotations

import json
from typing import Any

import pandas as pd


_MAX_POINTS = {"1m": 240, "3m": 180, "15m": 120}


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


def _money_tag(relative_pct: float | None) -> str:
    if relative_pct is None:
        return "ACTIVE"
    if relative_pct >= 80:
        return "VERY HIGH"
    if relative_pct >= 60:
        return "HIGH"
    if relative_pct >= 35:
        return "MEDIUM"
    return "LOW"


def _money_wall_payload(snapshot: Any) -> list[dict[str, Any]]:
    """Display-only OI wall context from already-computed option intelligence.

    No chain scan or market request is made here. Relative strength uses the
    full-chain max OI values already stored in snapshot.metadata when available.
    """

    options = getattr(snapshot, "option_intelligence", None)
    if options is None:
        return []
    global_walls = (getattr(snapshot, "metadata", {}) or {}).get("global_oi_walls") or {}
    output: list[dict[str, Any]] = []
    for side, wall in (("CE", getattr(options, "ce_wall", None)), ("PE", getattr(options, "pe_wall", None))):
        if wall is None:
            continue
        strike = _num(getattr(wall, "strike", None))
        oi = _num(getattr(wall, "oi", None))
        cluster = _num(getattr(wall, "cluster_center", None))
        cluster_oi = _num(getattr(wall, "cluster_oi", None))
        if strike is None:
            continue
        global_info = global_walls.get(side) or global_walls.get(side.lower()) or {}
        global_oi = _num(global_info.get("oi"))
        relative = None
        if oi is not None and global_oi not in (None, 0):
            relative = max(0.0, min(100.0, oi / global_oi * 100.0))
        output.append(
            {
                "side": side,
                "strike": round(strike, 2),
                "oi": round(oi, 2) if oi is not None else None,
                "cluster": round(cluster, 2) if cluster is not None else None,
                "clusterOi": round(cluster_oi, 2) if cluster_oi is not None else None,
                "relativePct": round(relative, 1) if relative is not None else None,
                "tag": _money_tag(relative),
                "status": str(getattr(wall, "status", "")),
            }
        )
    return output


def _big_player_payload(snapshot: Any) -> dict[str, Any]:
    item = getattr(snapshot, "big_player_activity", None)
    if item is None:
        return {"status": "UNAVAILABLE"}
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
    # Keep the floating brain deliberately compact.
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
    return {
        "snapshotId": str(getattr(snapshot, "snapshot_id", "")),
        "createdAt": created_at.isoformat() if created_at is not None else "",
        "spot": round(spot, 2) if spot is not None else None,
        "defaultTf": "15m",
        "candles": {
            "1m": _candle_records(getattr(snapshot, "candles_1m", pd.DataFrame()), session_date=session_date, limit=_MAX_POINTS["1m"]),
            "3m": _candle_records(getattr(snapshot, "candles_3m", pd.DataFrame()), session_date=session_date, limit=_MAX_POINTS["3m"]),
            "15m": _candle_records(getattr(snapshot, "candles_15m", pd.DataFrame()), session_date=session_date, limit=_MAX_POINTS["15m"]),
        },
        "barriers": _barrier_payload(snapshot),
        "moneyWalls": _money_wall_payload(snapshot),
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

    Golden Rule: this is display-only. It makes no Dhan/Railway/API call, reads no
    journal/history, performs no One-Brain calculation, and mutates no canonical
    object. It only renders already-computed snapshot evidence in the browser.
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
  .buttons{{display:flex;gap:5px;align-items:center;flex-wrap:wrap}}
  button{{border:1px solid rgba(148,163,184,.30);background:#101c2e;color:#c9d6e7;padding:5px 9px;border-radius:8px;font-size:12px;cursor:pointer;transition:.15s}}
  button:hover{{transform:translateY(-1px);border-color:rgba(24,211,255,.55)}}
  button.active{{background:linear-gradient(135deg,#155e75,#5037a7);color:#fff;border-color:#43d9ff}}
  #fullBtn{{font-weight:900;min-width:36px;background:linear-gradient(135deg,rgba(24,211,255,.16),rgba(168,108,255,.18))}}
  .statusRow{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;padding:8px 10px;background:rgba(6,12,23,.60);border-bottom:1px solid var(--line)}}
  .pill{{min-width:0;padding:7px 9px;border-radius:10px;border:1px solid rgba(148,163,184,.16);background:rgba(255,255,255,.035);font-size:11px;line-height:1.3}}
  .pill b{{display:block;font-size:10px;color:var(--muted);margin-bottom:2px;text-transform:uppercase;letter-spacing:.35px}}
  .pill strong{{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block}}
  .moneyCE{{border-color:rgba(255,159,67,.34);background:linear-gradient(135deg,rgba(255,159,67,.10),rgba(255,77,109,.05))}}
  .moneyPE{{border-color:rgba(37,217,138,.34);background:linear-gradient(135deg,rgba(37,217,138,.10),rgba(0,201,167,.05))}}
  .big{{border-color:rgba(24,211,255,.32);background:linear-gradient(135deg,rgba(24,211,255,.09),rgba(168,108,255,.08))}}
  .chartWrap{{position:relative}}
  #chart{{width:100%;height:455px}}
  .brain{{position:absolute;left:12px;top:11px;z-index:5;width:min(310px,calc(100% - 24px));border:1px solid rgba(168,108,255,.48);border-radius:12px;padding:8px 10px;background:linear-gradient(135deg,rgba(11,18,35,.91),rgba(39,20,67,.88));backdrop-filter:blur(7px);box-shadow:0 8px 25px rgba(0,0,0,.28);pointer-events:none}}
  .brainTop{{display:flex;align-items:center;justify-content:space-between;gap:7px;margin-bottom:3px}}
  .brainTitle{{font-size:10px;font-weight:900;color:#d5bdff;letter-spacing:.55px}}
  .brainAction{{font-size:13px;font-weight:950;padding:2px 7px;border-radius:999px}}
  .brainMeta{{font-size:10.5px;color:#b8c7da;margin-bottom:3px}}
  .brainReason{{font-size:11.5px;font-weight:750;color:#f6f9ff;line-height:1.32}}
  .brainTrigger{{font-size:10.5px;color:#8ee8ff;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .foot{{display:flex;justify-content:space-between;gap:8px;align-items:center;padding:6px 10px 8px;font-size:10.5px;color:#8fa2ba;border-top:1px solid var(--line);flex-wrap:wrap;background:rgba(5,10,19,.62)}}
  .legend{{display:flex;gap:10px;flex-wrap:wrap}}
  .dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;box-shadow:0 0 8px currentColor}}
  .r1{{background:var(--orange)}} .r2{{background:var(--red)}} .s1{{background:var(--green)}} .s2{{background:var(--teal)}} .ce{{background:#ffbf69}} .pe{{background:#51f0ba}} .bp{{background:var(--cyan)}}
  .review{{color:#aebbd0}}
  .wrap:fullscreen{{border-radius:0;border:none;background:#050b14;width:100vw;height:100vh}}
  .wrap:fullscreen #chart{{height:calc(100vh - 154px)}}
  .wrap:fullscreen .brain{{top:14px;left:14px}}
  @media(max-width:760px){{#chart{{height:390px}}.statusRow{{grid-template-columns:1fr 1fr}}.big{{grid-column:1/-1}}.head{{padding:7px 8px}}.title{{font-size:13px}}button{{padding:5px 7px}}.brain{{width:min(280px,calc(100% - 18px));left:9px;top:9px}}}}
  @media(max-width:430px){{.statusRow{{grid-template-columns:1fr 1fr;padding:6px}}.pill{{padding:6px 7px}}#chart{{height:370px}}.brainReason{{font-size:11px}}.legend{{gap:7px}}}}
</style>
</head>
<body>
<div class="wrap" id="wrap">
  <div class="head">
    <div class="left"><span class="title">🧠 NIFTY · ONE BRAIN LIVE CHART</span><span class="spot" id="spot"></span><span class="live">LIVE SNAPSHOT</span></div>
    <div class="buttons">
      <button data-tf="1m">1m</button><button data-tf="3m">3m</button><button data-tf="5m">5m</button><button data-tf="15m">15m</button>
      <button id="fullBtn" title="Full screen">⛶</button>
    </div>
  </div>
  <div class="statusRow">
    <div class="pill moneyCE"><b>🔥 CE Money Wall</b><strong id="ceWall">—</strong></div>
    <div class="pill moneyPE"><b>💰 PE Money Wall</b><strong id="peWall">—</strong></div>
    <div class="pill big"><b>⚡ Big Player Activity</b><strong id="bigPlayer">—</strong></div>
  </div>
  <div class="chartWrap">
    <div id="chart"></div>
    <div class="brain" id="brain">
      <div class="brainTop"><span class="brainTitle">✨ MINI AI BRAIN</span><span class="brainAction" id="brainAction">WAIT</span></div>
      <div class="brainMeta" id="brainMeta">MIXED</div>
      <div class="brainReason" id="brainReason">Market evidence loading...</div>
      <div class="brainTrigger" id="brainTrigger">Next: confirmation</div>
    </div>
  </div>
  <div class="foot">
    <div class="legend"><span><i class="dot r1"></i>R1</span><span><i class="dot r2"></i>R2</span><span><i class="dot s1"></i>S1</span><span><i class="dot s2"></i>S2</span><span><i class="dot ce"></i>CE Wall</span><span><i class="dot pe"></i>PE Wall</span><span><i class="dot bp"></i>Big Player</span></div>
    <div class="review" id="reviewInfo"></div>
  </div>
</div>
<script>
const P = {safe_json};
const container = document.getElementById('chart');
const wrap = document.getElementById('wrap');
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
P.candles['5m'] = build5m(P.candles['1m']);

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
function actionStyle(action) {{
  const a=String(action||'').toUpperCase();
  if(a.includes('CE SELL') || a.includes('DOWN')) return ['#ff4d6d','rgba(255,77,109,.16)'];
  if(a.includes('PE SELL') || a.includes('UP')) return ['#25d98a','rgba(37,217,138,.16)'];
  if(a.includes('BUY')) return ['#18d3ff','rgba(24,211,255,.16)'];
  return ['#ffd166','rgba(255,209,102,.14)'];
}}

function addLine(opts) {{ try {{ candles.createPriceLine(opts); }} catch(e) {{}} }}
(P.barriers || []).forEach(b => {{
  const color = lineColor(b.label,b.side);
  addLine({{price:b.lower,color,lineWidth:1,lineStyle:LightweightCharts.LineStyle.Dashed,axisLabelVisible:false,title:''}});
  addLine({{price:b.upper,color,lineWidth:1,lineStyle:LightweightCharts.LineStyle.Dashed,axisLabelVisible:false,title:''}});
  addLine({{price:b.midpoint,color,lineWidth:2,lineStyle:LightweightCharts.LineStyle.Solid,axisLabelVisible:true,
    title:`${{b.label}} ${{fmt(b.lower)}}–${{fmt(b.upper)}} · STR ${{b.strength}} · BRK ${{b.pressure}}`}});
}});
(P.moneyWalls || []).forEach(w => {{
  const ce = String(w.side).toUpperCase()==='CE';
  const color = ce ? '#ffbf69' : '#51f0ba';
  addLine({{price:w.strike,color,lineWidth:2,lineStyle:LightweightCharts.LineStyle.Dotted,axisLabelVisible:true,
    title:`${{w.side}} WALL · ${{w.tag}} · ${{compactOi(w.oi)}}`}});
  if(w.cluster != null && Math.abs(Number(w.cluster)-Number(w.strike)) >= 1) {{
    addLine({{price:w.cluster,color,lineWidth:1,lineStyle:LightweightCharts.LineStyle.Dashed,axisLabelVisible:false,title:`${{w.side}} CLUSTER`}});
  }}
}});
if (P.spot != null) {{
  document.getElementById('spot').textContent = Number(P.spot).toLocaleString('en-IN',{{minimumFractionDigits:2,maximumFractionDigits:2}});
  addLine({{price:P.spot,color:'#cbd5e1',lineWidth:1,lineStyle:LightweightCharts.LineStyle.Dotted,axisLabelVisible:true,title:'NIFTY'}});
}}

const walls = Object.fromEntries((P.moneyWalls||[]).map(w=>[String(w.side).toUpperCase(),w]));
function wallText(side) {{
  const w=walls[side]; if(!w) return 'Unavailable';
  const rel=w.relativePct==null?'':` · ${{w.relativePct.toFixed(0)}}% max`;
  return `${{fmt(w.strike)}} · ${{w.tag}} · ${{compactOi(w.oi)}}${{rel}}`;
}}
document.getElementById('ceWall').textContent = wallText('CE');
document.getElementById('peWall').textContent = wallText('PE');
const bp=P.bigPlayer||{{}};
const bpText = bp.status==='UNAVAILABLE' ? 'Unavailable' : `${{bp.direction||'MIXED'}} ${{Number(bp.score||0).toFixed(0)}}/100 · Confirm ${{bp.confirm||0}}/${{bp.confirmTotal||0}}${{bp.volumeRatio!=null?` · Vol ${{Number(bp.volumeRatio).toFixed(2)}}x`:''}}`;
document.getElementById('bigPlayer').textContent=bpText;

const ai=P.aiBrain||{{}};
const [ac,abg]=actionStyle(ai.action);
const actionEl=document.getElementById('brainAction'); actionEl.textContent=ai.action||'WAIT'; actionEl.style.color=ac; actionEl.style.background=abg; actionEl.style.border=`1px solid ${{ac}}66`;
document.getElementById('brainMeta').textContent=`${{ai.direction||'MIXED'}}${{ai.readiness!=null?` · Readiness ${{Number(ai.readiness).toFixed(0)}}/100`:''}} · Flow ${{P.optionFlow?.bias||'—'}} ${{Number(P.optionFlow?.confidence||0).toFixed(0)}}/100`;
document.getElementById('brainReason').textContent=ai.reason||'Market evidence ko confirm hone do';
document.getElementById('brainTrigger').textContent=`Next: ${{ai.trigger||'confirmation ka wait'}}`;

function applyBigPlayerMarker(data) {{
  if(!data.length || !bp || bp.status==='UNAVAILABLE') {{ try{{candles.setMarkers([])}}catch(e){{}}; return; }}
  const d=String(bp.direction||'').toUpperCase();
  if(!d.includes('BUY') && !d.includes('SELL')) {{ try{{candles.setMarkers([])}}catch(e){{}}; return; }}
  const last=data[data.length-1]; const buy=d.includes('BUY');
  try {{ candles.setMarkers([{{time:last.time,position:buy?'belowBar':'aboveBar',color:buy?'#18d3ff':'#ff6bb5',shape:buy?'arrowUp':'arrowDown',text:`BIG ${{buy?'BUY':'SELL'}} ${{Number(bp.score||0).toFixed(0)}}`}}]); }} catch(e) {{}}
}}
function setTf(tf) {{
  let data = P.candles[tf] || [];
  if (!data.length) {{ const fallback = ['15m','3m','1m'].find(k => (P.candles[k]||[]).length) || '1m'; tf=fallback; data=P.candles[fallback]||[]; }}
  candles.setData(data); applyBigPlayerMarker(data);
  document.querySelectorAll('button[data-tf]').forEach(btn => btn.classList.toggle('active', btn.dataset.tf===tf));
  chart.timeScale().fitContent();
}}
document.querySelectorAll('button[data-tf]').forEach(btn => btn.addEventListener('click',()=>setTf(btn.dataset.tf)));

const fullBtn=document.getElementById('fullBtn');
fullBtn.addEventListener('click', async()=>{{
  try {{
    if(!document.fullscreenElement) {{ await wrap.requestFullscreen(); }} else {{ await document.exitFullscreen(); }}
  }} catch(e) {{
    // Browser/iframe may deny fullscreen; keep the chart fully usable in normal mode.
    fullBtn.textContent='⛶';
  }}
}});
document.addEventListener('fullscreenchange',()=>{{ fullBtn.textContent=document.fullscreenElement?'✕':'⛶'; setTimeout(()=>{{chart.timeScale().fitContent();}},80); }});
const stamp = P.createdAt ? new Date(P.createdAt).toLocaleTimeString('en-IN',{{hour:'2-digit',minute:'2-digit'}}) : '';
document.getElementById('reviewInfo').textContent=`Review-ready · ${{stamp}} · ${{P.snapshotId||''}}`;
setTf(P.defaultTf || '15m');
</script>
</body>
</html>
"""
    with st.container(border=False):
        components.html(html, height=590, scrolling=False)
