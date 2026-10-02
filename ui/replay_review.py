from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from services.railway_live_client import RailwayDhanClient


def _fmt(value: Any, digits: int = 1) -> str:
    try:
        if value is None:
            return "—"
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _mid(level: Any) -> float | None:
    if not isinstance(level, dict):
        return None
    try:
        lo = float(level.get("lower"))
        hi = float(level.get("upper"))
        return (lo + hi) / 2.0
    except (TypeError, ValueError):
        return None


def _chart_html(candles: list[dict[str, Any]], timeline: list[dict[str, Any]], selected: dict[str, Any]) -> str:
    selected_at = str(selected.get("at") or "")
    visible = [row for row in candles if str(row.get("at") or "") <= selected_at]
    marker_rows = [row for row in timeline if str(row.get("at") or "") <= selected_at]
    payload = {
        "candles": visible,
        "selected": selected,
        "markers": marker_rows[-140:],
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    return f"""
<!DOCTYPE html><html><head><meta charset="utf-8"/>
<script src="https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
html,body{{margin:0;background:#0b1020;color:#e5e7eb;font-family:Inter,system-ui,sans-serif}}
#wrap{{position:relative;border:1px solid #25304a;border-radius:16px;overflow:hidden;background:#0b1020}}
#chart{{height:560px}}
#brain{{position:absolute;z-index:5;top:12px;left:12px;max-width:420px;padding:10px 12px;border-radius:12px;background:rgba(15,23,42,.90);border:1px solid #334155;box-shadow:0 8px 24px rgba(0,0,0,.25)}}
#brain .t{{font-weight:800;font-size:13px;letter-spacing:.08em;color:#93c5fd}} #brain .a{{font-weight:900;font-size:22px;margin:2px 0}} #brain .s{{font-size:12px;color:#cbd5e1;line-height:1.4}}
#full{{position:absolute;z-index:6;top:12px;right:12px;background:#111827;color:white;border:1px solid #475569;border-radius:9px;padding:7px 10px;cursor:pointer}}
#legend{{position:absolute;z-index:5;bottom:10px;left:12px;background:rgba(15,23,42,.86);border:1px solid #334155;border-radius:9px;padding:6px 9px;font-size:11px;color:#cbd5e1}}
</style></head><body><div id="wrap"><button id="full">⛶ Full screen</button><div id="brain"></div><div id="chart"></div><div id="legend">Replay only · recorded data · no broker call · no Brain write</div></div>
<script>
const p={raw};
const box=document.getElementById('brain'); const s=p.selected||{{}};
const esc=(x)=>String(x??'').replace(/[&<>\"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[c]));
box.innerHTML=`<div class="t">REPLAY AI BRAIN · ${{esc(s.time||'')}}</div><div class="a">${{esc(s.final_action||'WAIT')}} · ${{esc(s.direction||'')}}</div><div class="s">${{esc(s.regime||'')}} · readiness ${{s.entry_readiness==null?'—':Number(s.entry_readiness).toFixed(1)}}/100<br>${{esc(s.trigger||s.reason||'Recorded state')}}</div>`;
const chart=LightweightCharts.createChart(document.getElementById('chart'),{{layout:{{background:{{color:'#0b1020'}},textColor:'#cbd5e1'}},grid:{{vertLines:{{color:'#182238'}},horzLines:{{color:'#182238'}}}},rightPriceScale:{{borderColor:'#334155'}},timeScale:{{borderColor:'#334155',timeVisible:true,secondsVisible:false}},crosshair:{{mode:LightweightCharts.CrosshairMode.Normal}}}});
const cs=chart.addCandlestickSeries({{upColor:'#22c55e',downColor:'#ef4444',borderVisible:false,wickUpColor:'#4ade80',wickDownColor:'#f87171'}});
const toT=(x)=>Math.floor(new Date(x).getTime()/1000);
cs.setData((p.candles||[]).map(x=>({{time:toT(x.at),open:+x.open,high:+x.high,low:+x.low,close:+x.close}})).filter(x=>Number.isFinite(x.time)));
function line(price,title,color,style=0){{if(price==null||!Number.isFinite(+price))return;cs.createPriceLine({{price:+price,color,lineWidth:2,lineStyle:style,axisLabelVisible:true,title}})}}
function mid(x){{if(!x||x.lower==null||x.upper==null)return null;return (+x.lower + +x.upper)/2}}
line(mid(s.r1),'R1','#fb7185'); line(mid(s.r2),'R2','#f43f5e',2); line(mid(s.s1),'S1','#4ade80'); line(mid(s.s2),'S2','#22c55e',2);
line(s.ce_wall&&s.ce_wall.strike,'CE WALL','#f59e0b',1); line(s.pe_wall&&s.pe_wall.strike,'PE WALL','#38bdf8',1);
const markers=[];
for(const x of (p.markers||[])){{const t=toT(x.at); if(!Number.isFinite(t))continue; const a=String(x.final_action||'WAIT'); const bp=Number(x.big_player_score||0); if(a!=='WAIT') markers.push({{time:t,position:a.includes('PE SELL')||a.includes('CE BUY')?'belowBar':'aboveBar',color:a.includes('SELL')?'#fbbf24':'#a78bfa',shape:'arrowUp',text:a}}); else if(bp>=60) markers.push({{time:t,position:String(x.big_player_direction).toUpperCase()==='SELLING'?'aboveBar':'belowBar',color:String(x.big_player_direction).toUpperCase()==='SELLING'?'#fb7185':'#34d399',shape:'circle',text:`BP ${{Math.round(bp)}}`}});}}
try{{cs.setMarkers(markers)}}catch(e){{}}
chart.timeScale().fitContent();
window.addEventListener('resize',()=>chart.applyOptions({{width:document.getElementById('chart').clientWidth}}));
document.getElementById('full').onclick=()=>{{const el=document.getElementById('wrap'); if(!document.fullscreenElement)el.requestFullscreen?.(); else document.exitFullscreen?.();}};
</script></body></html>"""


def _timeline_view(rows: list[dict[str, Any]]) -> pd.DataFrame:
    view = []
    for row in rows:
        r1, s1 = row.get("r1") or {}, row.get("s1") or {}
        ce, pe = row.get("ce_wall") or {}, row.get("pe_wall") or {}
        view.append({
            "Time": row.get("time"), "Spot": row.get("spot"), "Action": row.get("final_action"),
            "Direction": row.get("direction"), "Regime": row.get("regime"), "Readiness": row.get("entry_readiness"),
            "Big Player": row.get("big_player_direction"), "BP Score": row.get("big_player_score"),
            "R1": None if not r1 else f"{_fmt(r1.get('lower'),0)}–{_fmt(r1.get('upper'),0)}",
            "S1": None if not s1 else f"{_fmt(s1.get('lower'),0)}–{_fmt(s1.get('upper'),0)}",
            "CE Wall": ce.get("strike"), "PE Wall": pe.get("strike"), "Option Bias": row.get("option_bias"),
        })
    return pd.DataFrame(view)


def render_phase3_replay(snapshot: Any, url: str, key: str) -> None:
    """Explicit on-demand recorded-data replay; no work until the user requests it."""
    st.caption("🎞️ Phase-3 One Brain Replay · recorded SQLite only · zero broker/API market-data calls")
    if not url or not key:
        st.info("Replay ke liye Railway persistent recorder connection required hai.")
        return

    current_day = str(getattr(snapshot, "created_at", ""))[:10]
    cached = st.session_state.get("phase3_replay_bundle")
    cached_day = (cached or {}).get("session_date") if isinstance(cached, dict) else None
    c1, c2 = st.columns([1, 3])
    with c1:
        load = st.button("Load / Refresh Replay", key="phase3_load_replay", width="stretch")
    with c2:
        st.caption("Load button dabane par sirf saved Railway evidence read hota hai. Live One Brain path unaffected rahega.")
    if load:
        try:
            with st.spinner("Recorded One Brain timeline load ho rahi hai..."):
                st.session_state.phase3_replay_bundle = RailwayDhanClient(
                    url, key, timeout_seconds=10
                )._post("/day-memory-replay", {"max_rows": 390})
            st.session_state.pop("phase3_replay_error", None)
        except Exception as exc:
            st.session_state.phase3_replay_error = type(exc).__name__
    if st.session_state.get("phase3_replay_error"):
        st.warning("Replay load nahi hua; live calculations safe aur unchanged hain.")

    bundle = st.session_state.get("phase3_replay_bundle")
    if not isinstance(bundle, dict):
        return
    if cached_day and current_day and cached_day != current_day:
        st.caption(f"Loaded replay session: {cached_day} · current screen date: {current_day}")

    rows = list(bundle.get("timeline") or [])
    stats = bundle.get("statistics") or {}
    if not rows:
        st.info("Abhi recorded replay samples available nahi hain.")
        return

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Samples", stats.get("sample_rows", len(rows)))
    m2.metric("AI decisions", stats.get("decision_rows", 0))
    m3.metric("Barrier shifts", stats.get("barrier_state_changes", 0))
    m4.metric("CE wall moves", stats.get("ce_wall_changes", 0))
    m5.metric("PE wall moves", stats.get("pe_wall_changes", 0))
    m6.metric("BP 60+", stats.get("big_player_60plus_samples", 0))

    step = st.slider("Replay step", 0, len(rows) - 1, len(rows) - 1, key="phase3_replay_step")
    selected = rows[int(step)]
    st.caption(f"Replay point: {selected.get('at')} · recorded version {selected.get('version') or '—'}")
    components.html(_chart_html(list(bundle.get("candles_1m") or []), rows, selected), height=590, scrolling=False)

    a, b, c, d = st.columns(4)
    a.metric("Action", str(selected.get("final_action") or "WAIT"))
    b.metric("Direction", str(selected.get("direction") or "—"), f"Readiness {_fmt(selected.get('entry_readiness'),1)}")
    bps = selected.get("big_player_score")
    c.metric("Big Player", str(selected.get("big_player_direction") or "—"), "—" if bps is None else f"{float(bps):.0f}/100")
    d.metric("Option Bias", str(selected.get("option_bias") or "—"), f"Conf {_fmt(selected.get('option_confidence'),0)}")

    r1, s1 = selected.get("r1") or {}, selected.get("s1") or {}
    ce, pe = selected.get("ce_wall") or {}, selected.get("pe_wall") or {}
    st.caption(
        f"R1 {_fmt(r1.get('lower'),0)}–{_fmt(r1.get('upper'),0)} · strength {_fmt(r1.get('strength'),0)} · pressure {_fmt(r1.get('pressure'),0)} | "
        f"S1 {_fmt(s1.get('lower'),0)}–{_fmt(s1.get('upper'),0)} · strength {_fmt(s1.get('strength'),0)} · pressure {_fmt(s1.get('pressure'),0)}"
    )
    st.caption(
        f"CE wall {_fmt(ce.get('strike'),0)} · migration {_fmt(ce.get('migration_points'),0)} | "
        f"PE wall {_fmt(pe.get('strike'),0)} · migration {_fmt(pe.get('migration_points'),0)}"
    )

    tab1, tab2, tab3, tab4 = st.tabs(["🧠 One Brain Timeline", "🧱 Barrier + Money", "📊 Outcomes", "🧾 Events"])
    with tab1:
        st.dataframe(_timeline_view(rows), hide_index=True, use_container_width=True)
    with tab2:
        barrier_rows = []
        for row in rows:
            r1, r2, s1, s2 = row.get("r1") or {}, row.get("r2") or {}, row.get("s1") or {}, row.get("s2") or {}
            ce, pe = row.get("ce_wall") or {}, row.get("pe_wall") or {}
            barrier_rows.append({
                "Time": row.get("time"), "Spot": row.get("spot"),
                "R1 low": r1.get("lower"), "R1 high": r1.get("upper"), "R1 strength": r1.get("strength"), "R1 pressure": r1.get("pressure"), "R1 state": r1.get("state"),
                "R2": _mid(r2), "S1 low": s1.get("lower"), "S1 high": s1.get("upper"), "S1 strength": s1.get("strength"), "S1 pressure": s1.get("pressure"), "S1 state": s1.get("state"), "S2": _mid(s2),
                "CE Wall": ce.get("strike"), "CE migration": ce.get("migration_points"), "PE Wall": pe.get("strike"), "PE migration": pe.get("migration_points"),
            })
        st.dataframe(pd.DataFrame(barrier_rows), hide_index=True, use_container_width=True)
        st.caption("Wall migration aur barrier changes historical display hain; inka live One Brain weight yahan se change nahi hota.")
    with tab3:
        outcome = stats.get("outcomes") or {}
        out_rows = []
        for horizon in ("5m", "15m", "30m"):
            item = outcome.get(horizon) or {}
            labels = item.get("labels") or {}
            out_rows.append({
                "Horizon": horizon, "Covered": item.get("covered", 0), "Pending": item.get("pending", 0),
                "UP": labels.get("UP", 0), "DOWN": labels.get("DOWN", 0), "RANGE": labels.get("RANGE", 0),
                "Median move pts": item.get("median_move_points"), "Median abs move pts": item.get("median_abs_move_points"),
            })
        st.dataframe(pd.DataFrame(out_rows), hide_index=True, use_container_width=True)
        st.write("**Action counts**", stats.get("action_counts") or {})
        st.write("**Regime counts**", stats.get("regime_counts") or {})
        st.caption(str(stats.get("note") or ""))
    with tab4:
        events = list(bundle.get("events") or [])
        if events:
            st.dataframe(pd.DataFrame(events), hide_index=True, use_container_width=True)
        else:
            st.caption("Selected session me replay events available nahi hain.")

    st.caption(str(bundle.get("note") or ""))
