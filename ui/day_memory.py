"""Expiry-cycle diary and explain-only context; never changes market scores."""
from datetime import datetime
from dataclasses import asdict
import hashlib
import base64

import streamlit as st

from analysis.history_context import history_context
from analysis.recent_history import recent_history
from services.day_memory import clean
from services.railway_live_client import RailwayDhanClient


def app_observation(snapshot):
    candidate = snapshot.trade_plan.selected_setup
    if candidate == "WAIT":
        candidate = snapshot.trade_plan.candidate_setup
    field = {"CE SELL": "ce_sell", "PE SELL": "pe_sell", "IRON CONDOR": "iron_condor"}.get(candidate)
    plan = getattr(snapshot.trade_plan, field, None) if field else None
    evaluation = getattr(snapshot.decision, field, None) if field else None
    legs = []
    valid = True
    if plan:
        for role, items in (("SELL", plan.short_legs), ("HEDGE", plan.hedge_legs)):
            for leg in items:
                matches = snapshot.option_chain[(snapshot.option_chain["strike"] == leg.strike) & (snapshot.option_chain["side"] == leg.side)]
                if len(matches) != 1:
                    valid = False
                    continue
                row = matches.iloc[0]
                legs.append({"role": role, "strike": leg.strike, "side": leg.side,
                             "security_id": row.get("security_id"), "top_bid_price": row.get("top_bid_price"),
                             "top_ask_price": row.get("top_ask_price")})
    return clean({"at": snapshot.created_at.isoformat(), "action": snapshot.decision.final_action,
                  "reason": snapshot.decision.execution_status, "version": snapshot.metadata.get("version", ""),
                  "candidate": candidate, "score": getattr(evaluation,"score",0), "expiry": snapshot.expiry,
                  "spot": snapshot.nifty_quote.get("last_price"), "legs": legs if valid else [],
                  "institutional": asdict(snapshot.institutional_context),
                  "fresh": snapshot.market_session.is_live and all(getattr(snapshot.feed_status.get(k),"use_state","")=="LIVE" for k in ("quotes","candles","option_chain"))})


def sync_day_memory(snapshot, url, key):
    """Called before presentation; fresh same-version history only, never a vote."""
    now = datetime.now().timestamp()
    connection = hashlib.sha256(f"{url}|{key}".encode()).hexdigest()
    if st.session_state.get("day_memory_connection") != connection:
        for name in ("day_memory_report", "day_memory_error", "day_memory_fetch_at", "evidence_download"):
            st.session_state.pop(name,None)
        st.session_state.day_memory_connection = connection
    if url and key and now-st.session_state.get("day_memory_fetch_at",0)>=60:
        try:
            client = RailwayDhanClient(url,key,timeout_seconds=3)
            event = app_observation(snapshot) if snapshot.market_session.is_live else None
            st.session_state.day_memory_report = client._post("/day-memory", {"event":event})
            st.session_state.pop("day_memory_error",None)
        except Exception:
            st.session_state.day_memory_error = "History unavailable — Railway update/storage/connection check karo."
        st.session_state.day_memory_fetch_at = now
    report = st.session_state.get("day_memory_report") if not st.session_state.get("day_memory_error") and url and key else None
    snapshot.metadata["history_context"] = history_context(snapshot,report)
    snapshot.metadata["recent_history"] = recent_history(snapshot, report)
    # Explicit allowlist: no connection URL/key or arbitrary metadata exported.
    snapshot.metadata["recording_diagnostics"] = clean({
        "checked_at": datetime.fromtimestamp(now).astimezone().isoformat(),
        "report_fetched_at_epoch": st.session_state.get("day_memory_fetch_at"),
        "available": report is not None,
        "error": st.session_state.get("day_memory_error"),
        **({k: report.get(k) for k in ("recorder_status", "recording_health", "last_sample_age_seconds", "interval_seconds", "counts", "first", "last", "cycle_expiry", "bytes", "last_error", "recording_coverage")} if report else {}),
        "recent_history": snapshot.metadata["recent_history"],
        "history_context": snapshot.metadata["history_context"],
        "usage": "OI/Top9 history supplies rolling calculations via analysis_history feed. Diary supplies context only; no extra vote or automatic training.",
    })


def render_day_memory(snapshot, url, key):
    with st.expander("Expiry-cycle record — Barrier, AI aur spread results", expanded=False):
        st.caption("Current expiry ki detail; last 8 completed cycles ki short summary. History ka extra vote 0.")
        if not url or not key:
            st.info("Railway connection chahiye. Local app se background recording nahi chalti.")
            return
        if st.session_state.get("day_memory_error"):
            st.warning(st.session_state.day_memory_error)
        cached = st.session_state.get("day_memory_report")
        if not cached:
            return
        st.write(cached.get("recorder_status","Status unavailable"))
        st.caption("Recording check: " + str(cached.get("recording_health", "Detailed health unavailable")))
        counts = cached.get("counts",{})
        st.caption(f"Cycle expiry: {cached.get('cycle_expiry') or 'Pending'} · Last session: {cached.get('day') or '—'} · Samples {counts.get('samples',0)} · DB {cached.get('bytes',0)/1048576:.2f} MB")
        st.caption(f"First: {cached.get('first') or '—'} · Last: {cached.get('last') or '—'}")
        coverage = cached.get("recording_coverage") or {}
        if coverage:
            st.caption(f"Record schema {coverage.get('record_schema')} · Option rows {coverage.get('option_rows', 0)} · Raw Greeks rows {coverage.get('raw_greeks_rows', 0)}")
            st.caption("Saved modules: " + ", ".join(coverage.get("evidence_fields_saved", [])))
            st.caption(f"Last AI decision change: {coverage.get('last_app_ai_at') or '—'} · App heartbeat: {coverage.get('last_app_heartbeat_at') or '—'}")
            st.caption(f"Observed-span coverage: {coverage.get('slot_coverage_pct', '—')}% · Missing minute slots: {coverage.get('missing_slots', '—')}. Yeh data coverage hai, accuracy nahi.")
        else:
            st.caption("Detailed recording coverage unavailable — Railway recorder version check karo.")
        history_feed = snapshot.feed_status.get("analysis_history")
        if history_feed:
            st.caption("Calculation history: " + str(history_feed.message))
        if cached.get("last_error"):
            st.warning("Data gap: "+str(cached["last_error"].get("reason","Unknown")))
        if st.button("Prepare full evidence download", key="prepare_evidence_export"):
            try:
                export = RailwayDhanClient(url,key,timeout_seconds=30)._post("/day-memory-export", {})
                st.session_state.evidence_download = base64.b64decode(export["content_base64"], validate=True)
            except Exception:
                st.error("Export nahi mila; Railway par matching version aur volume check karo. Records delete nahi kiye.")
        if st.session_state.get("evidence_download"):
            st.download_button("Download full recorded evidence", st.session_state.evidence_download,
                               file_name="nifty-evidence.jsonl.gz", mime="application/gzip")
        analytics = snapshot.metadata.get("history_analytics", {})
        st.write("**OI history — pehle aur ab**")
        st.caption("Extra vote 0: existing OI engine already uses rolling history. Labels inference hain, trader counts nahi.")
        for window in analytics.get("oi", {}).get("windows", []):
            st.caption(f"{window['minutes']}m: {window['status']} · Nifty change {window.get('spot_change', '—')}")
            if window.get("inferred_pressure"):
                st.caption(f"Pressure: {window['inferred_pressure']} · Price support: {window['price_supports_pressure']} — trader count nahi")
            if window.get("rows"):
                st.dataframe(window["rows"], hide_index=True, use_container_width=True)
        st.write("**Futures VWAP — same instrument**")
        st.json(analytics.get("vwap", {}), expanded=False)
        st.write("**FII/DII — prior reported sessions**")
        st.caption(analytics.get("institutions", {}).get("note", "Pending"))
        st.dataframe(analytics.get("institutions", {}).get("rows", []), hide_index=True, use_container_width=True)
        recent = snapshot.metadata.get("recent_history", {})
        st.markdown("### Recent History — Price, Big Player aur Barrier")
        st.caption(recent.get("message", "Fresh records ka wait"))
        for window in recent.get("windows", []):
            st.write(f"**{window['Window']} · {window.get('Observed', 'PENDING')}**")
            if "Nifty change" in window:
                st.write(f"Nifty change: {window['Nifty change']:+.2f} points")
            st.write(window["Price reaction"])
            st.caption(window["Flow"])
        for barrier in recent.get("barriers", []):
            st.write(f"**{barrier['Level']}** · {barrier['Last recorded price']}")
            st.caption(barrier["Latest recorded reaction"])
        st.caption("Observed history only. 4-point flat band / 10-score flow gap display filters hain, trade rules nahi. Final AI score/action unchanged. Barrier events limited recent log se hain; no event ka matlab no test nahi.")
        for line in snapshot.metadata.get("history_context",{}).get("lines",[]):
            st.write(line)
        for zone in cached.get("zone_history",[]):
            st.caption(f"{zone['side']} {zone['lower']:,.0f}–{zone['upper']:,.0f}: rejection events {zone['rejections']}, break events {zone['breaks']}, retest holds {zone['retest_holds']}. Purana event aaj ki confirmation nahi.")
        st.caption("Exact observed zones only. Nearby shifted zones separate hain. Event count successful trades nahi.")
        for event in cached.get("events",[])[:30]:
            stamp = str(event.get("at",""))[:16].replace("T"," ")
            text = event.get("status") or event.get("action") or event.get("direction") or "—"
            detail = event.get("zone") or event.get("names") or event.get("reason","")
            st.write(f"{stamp} · {event['kind']} · {detail} · {text}")
        if cached.get("outcomes"):
            st.write("Candidate ke baad kya hua — actual trades nahi")
            rows = []
            for row in cached["outcomes"]:
                rows.append({"Signal":row["at"][:16].replace("T"," "),"Setup":row.get("candidate"),
                             "AI action":row.get("action"),"Minutes":row["horizon_minutes"],
                             "Nifty change":row.get("spot_change"),"Spread points":row.get("spread_points"),
                             "Observed loss pts":row.get("observed_max_loss_points"),
                             "Coverage":row.get("coverage",row.get("status")),
                             "Spread path complete":row.get("spread_path_complete",False)})
            st.dataframe(rows,hide_index=True,use_container_width=True)
            st.caption("SELL entry bid / hedge ask; exit short ask / hedge bid. Equal-quantity points, no fees/slippage/fill guarantee. Observed loss minute samples ka hai, true intraminute maximum nahi. Missing result zero profit nahi.")
        if cached.get("cycle_summaries"):
            st.write("Completed expiry cycles")
            st.json(cached["cycle_summaries"],expanded=False)
        st.caption("Latest 30 events shown; diary retains current-cycle detail. Background direction app ke manual context se different ho sakti hai. App AI events sirf app fetch hone par.")
