"""Expiry-cycle diary and explain-only context; never changes market scores."""
from datetime import datetime
from dataclasses import asdict
import hashlib

import streamlit as st

from analysis.history_context import history_context
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
        for name in ("day_memory_report", "day_memory_error", "day_memory_fetch_at"):
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
        counts = cached.get("counts",{})
        st.caption(f"Cycle expiry: {cached.get('cycle_expiry') or 'Pending'} · Last session: {cached.get('day') or '—'} · Samples {counts.get('samples',0)} · DB {cached.get('bytes',0)/1048576:.2f} MB")
        st.caption(f"First: {cached.get('first') or '—'} · Last: {cached.get('last') or '—'}")
        if cached.get("last_error"):
            st.warning("Data gap: "+str(cached["last_error"].get("reason","Unknown")))
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
