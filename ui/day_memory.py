"""Expiry-cycle diary and explain-only context; never changes market scores."""
from datetime import datetime
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import threading
import time

import streamlit as st

from analysis.history_context import history_context
from analysis.recent_history import recent_history
from services.day_memory import clean, compact
from services.railway_live_client import RailwayDhanClient


# Phase-11: final evidence writes are non-critical to the live decision path. Keep
# one ordered worker so a slow Railway ACK cannot add seconds to every Streamlit
# snapshot. Payloads are fully materialized before submission; the worker never
# touches Streamlit session state or the mutable MarketSnapshot.
_EVIDENCE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nsl-evidence")
_EVIDENCE_LOCK = threading.Lock()
_EVIDENCE_STATUS = {
    "queued": 0, "completed": 0, "failed": 0, "pending": 0,
    "last_seconds": 0.0, "last_error": "",
}

# Full Railway history/report reads are advisory and must never sit on the live
# One-Brain critical path.  Keep one process-wide cache keyed by the protected
# connection fingerprint and refresh it asynchronously.
_REPORT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nsl-history")
_REPORT_LOCK = threading.Lock()
_REPORT_CACHE: dict[str, dict] = {}
_REPORT_STATUS: dict[str, dict] = {}


def _refresh_report_worker(url: str, key: str, connection: str) -> None:
    started = time.perf_counter()
    try:
        report = RailwayDhanClient(url, key, timeout_seconds=8)._post(
            "/day-memory", {"report": True}
        )
        with _REPORT_LOCK:
            _REPORT_CACHE[connection] = report if isinstance(report, dict) else {}
            _REPORT_STATUS[connection] = {
                "pending": False, "fetched_at": time.time(), "error": "",
                "seconds": round(time.perf_counter() - started, 4),
            }
    except Exception as exc:
        with _REPORT_LOCK:
            previous = dict(_REPORT_STATUS.get(connection) or {})
            _REPORT_STATUS[connection] = {
                **previous, "pending": False, "fetched_at": previous.get("fetched_at", 0.0),
                "error": type(exc).__name__,
                "seconds": round(time.perf_counter() - started, 4),
            }


def _queue_report_refresh(url: str, key: str, connection: str, *, ttl_seconds: float = 60.0) -> None:
    now = time.time()
    with _REPORT_LOCK:
        status = dict(_REPORT_STATUS.get(connection) or {})
        if status.get("pending"):
            return
        if now - float(status.get("fetched_at") or 0.0) < ttl_seconds:
            return
        _REPORT_STATUS[connection] = {**status, "pending": True}
    _REPORT_EXECUTOR.submit(_refresh_report_worker, str(url), str(key), connection)


def history_async_status(connection: str) -> dict:
    with _REPORT_LOCK:
        return dict(_REPORT_STATUS.get(connection) or {})


def _post_final_evidence(url: str, key: str, payload: dict):
    started = time.perf_counter()
    with _EVIDENCE_LOCK:
        _EVIDENCE_STATUS["pending"] += 1
    try:
        RailwayDhanClient(url, key, timeout_seconds=3)._post("/day-memory", payload)
        with _EVIDENCE_LOCK:
            _EVIDENCE_STATUS["completed"] += 1
            _EVIDENCE_STATUS["last_error"] = ""
    except Exception as exc:
        with _EVIDENCE_LOCK:
            _EVIDENCE_STATUS["failed"] += 1
            _EVIDENCE_STATUS["last_error"] = type(exc).__name__
    finally:
        with _EVIDENCE_LOCK:
            _EVIDENCE_STATUS["pending"] = max(0, _EVIDENCE_STATUS["pending"] - 1)
            _EVIDENCE_STATUS["last_seconds"] = round(time.perf_counter() - started, 4)


def evidence_async_status() -> dict:
    with _EVIDENCE_LOCK:
        return dict(_EVIDENCE_STATUS)


def app_observation(snapshot):
    common = snapshot.metadata.get("common_decision") or {}
    candidate = str(common.get("best_strategy") or snapshot.trade_plan.candidate_setup or "WAIT")
    field = {"CE SELL": "ce_sell", "PE SELL": "pe_sell", "IRON CONDOR": "iron_condor",
             "CE BUY": "ce_buy", "PE BUY": "pe_buy"}.get(candidate)
    plan = getattr(snapshot.trade_plan, field, None) if field else None
    evaluation = getattr(snapshot.decision, field, None) if field else None
    legs = []
    valid = True
    if plan:
        # Debit spreads contain a BUY long plus a SELL farther-OTM hedge;
        # credit spreads contain SELL shorts plus bought HEDGE legs.
        leg_groups = (
            (("SELL", plan.short_legs), ("BUY", plan.long_legs))
            if getattr(plan, "is_buy", False)
            else (("SELL", plan.short_legs), ("HEDGE", plan.hedge_legs))
        )
        for role, items in leg_groups:
            for leg in items:
                matches = snapshot.option_chain[(snapshot.option_chain["strike"] == leg.strike) & (snapshot.option_chain["side"] == leg.side)]
                if len(matches) != 1:
                    valid = False
                    continue
                row = matches.iloc[0]
                legs.append({"role": role, "strike": leg.strike, "side": leg.side,
                             "security_id": row.get("security_id"), "top_bid_price": row.get("top_bid_price"),
                             "top_ask_price": row.get("top_ask_price")})
    future = snapshot.metadata.get("future_brain") or {}
    simple = snapshot.metadata.get("simple_brain") or {}
    edge = snapshot.metadata.get("research_edge_context") or {}
    simple_reason = str(simple.get("instruction") or "")
    reason = simple_reason or str((common.get("blockers") or [common.get("status", "WAIT")])[0])
    return clean({"at": snapshot.created_at.isoformat(), "action": common.get("final_action", simple.get("final_action", "WAIT")),
                  "reason": reason, "version": snapshot.metadata.get("version", ""),
                  "candidate": simple.get("candidate_action", candidate),
                  "score": simple.get("entry_readiness", common.get("trade_confidence", getattr(evaluation,"score",0))), "expiry": snapshot.expiry,
                  "spot": snapshot.nifty_quote.get("last_price"), "legs": legs if valid else [],
                  "simple_brain": {k: simple.get(k) for k in (
                      "engine", "regime", "direction", "direction_strength", "raw_direction_strength",
                      "display_direction_strength", "entry_readiness", "raw_entry_readiness",
                      "display_entry_readiness", "option_quality", "option_window_fallback", "participation_quality",
                      "evidence_coverage", "confirmation_blocks",
                      "entry_state", "candidate_action", "final_action", "trigger", "next_level",
                      "risk_notes", "reasons")},
                  "future_brain": {k: future.get(k) for k in (
                      "feature_key", "current_direction", "next_direction", "transition",
                      "up_5m", "down_5m", "range_5m", "up_15m", "down_15m", "range_15m",
                      "final_gate", "model_label")},
                  "common_decision": {k: common.get(k) for k in (
                      "status", "final_action", "best_strategy", "entry_allowed", "direction",
                      "trade_confidence", "historical_hit_rate", "historical_matches", "blockers")},
                  "research_edge_context": edge,
                  "institutional": asdict(snapshot.institutional_context),
                  "fresh": snapshot.market_session.is_live and all(getattr(snapshot.feed_status.get(k),"use_state","")=="LIVE" for k in ("quotes","candles","option_chain"))})



def market_history_observation(snapshot):
    """Compact rolling history from the already-built snapshot; zero broker calls."""
    option_snapshot = None
    option_feed = snapshot.feed_status.get("option_chain")
    if (
        snapshot.expiry
        and option_feed is not None
        and getattr(option_feed, "use_state", "") == "LIVE"
    ):
        raw_state = (getattr(snapshot, "metadata", {}) or {}).get("option_state_snapshot")
        if isinstance(raw_state, dict):
            option_snapshot = raw_state

    prices = {}
    for quote in list(getattr(snapshot, "heavyweight_quotes", ()) or ()):
        symbol = str((quote or {}).get("symbol") or "").strip()
        try:
            value = float((quote or {}).get("last_price"))
        except (TypeError, ValueError):
            continue
        if symbol and value > 0:
            prices[symbol] = value
    top9 = None
    if prices:
        try:
            nifty = float(snapshot.nifty_quote.get("last_price"))
        except (TypeError, ValueError):
            nifty = None
        top9 = {
            "at": snapshot.created_at.isoformat(),
            "nifty": nifty,
            "universe": sorted(prices),
            "prices": prices,
        }
    return clean({"option_snapshot": option_snapshot, "top9": top9})

def sync_day_memory(snapshot, url, key, *, record_event: bool = True):
    """Attach cached Railway history without blocking the live decision path.

    A slow SQLite/report read used to add up to three seconds to the critical
    finalize path.  The report now refreshes in a single background worker; the
    current snapshot consumes the newest completed cache only.  History remains
    advisory and unavailable data receives no vote.
    """
    now = datetime.now().timestamp()
    connection = hashlib.sha256(f"{url}|{key}".encode()).hexdigest()
    if st.session_state.get("day_memory_connection") != connection:
        for name in ("day_memory_report", "day_memory_error", "day_memory_fetch_at", "evidence_download"):
            st.session_state.pop(name, None)
        st.session_state.day_memory_connection = connection

    if url and key:
        _queue_report_refresh(url, key, connection, ttl_seconds=60.0)
        with _REPORT_LOCK:
            cached = _REPORT_CACHE.get(connection)
            status = dict(_REPORT_STATUS.get(connection) or {})
        if isinstance(cached, dict) and cached:
            st.session_state.day_memory_report = cached
            st.session_state.day_memory_fetch_at = float(status.get("fetched_at") or now)
            st.session_state.pop("day_memory_error", None)
        elif status.get("error") and not st.session_state.get("day_memory_report"):
            st.session_state.day_memory_error = (
                "History refresh pending — live One Brain unaffected; Railway journal retry active."
            )

    report = (
        st.session_state.get("day_memory_report")
        if url and key and isinstance(st.session_state.get("day_memory_report"), dict)
        else None
    )
    snapshot.metadata["learning_outcomes"] = list((report or {}).get("outcomes") or [])
    snapshot.metadata["history_context"] = history_context(snapshot, report)
    snapshot.metadata["recent_history"] = recent_history(snapshot, report)
    snapshot.metadata["cycle_recorded_days"] = len((report or {}).get("cycle_prices", {}).get("days", []))
    async_state = history_async_status(connection) if url and key else {}
    snapshot.metadata["recording_diagnostics"] = clean({
        "checked_at": datetime.fromtimestamp(now).astimezone().isoformat(),
        "report_fetched_at_epoch": st.session_state.get("day_memory_fetch_at"),
        "available": report is not None,
        "error": st.session_state.get("day_memory_error"),
        "async_refresh": async_state,
        **({k: report.get(k) for k in ("recorder_status", "recording_health", "last_sample_age_seconds", "interval_seconds", "counts", "first", "last", "cycle_expiry", "bytes", "last_error", "recording_coverage")} if report else {}),
        "recent_history": snapshot.metadata["recent_history"],
        "history_context": snapshot.metadata["history_context"],
        "usage": "OI/Top9 history supplies rolling calculations via analysis_history feed. Diary supplies context only; no extra vote or automatic training.",
    })



def _latest_candle_payload(frame) -> dict | None:
    if frame is None or getattr(frame, "empty", True):
        return None
    try:
        row = frame.iloc[-1]
        return clean({
            "timestamp": row.get("timestamp"),
            "open": row.get("open"), "high": row.get("high"),
            "low": row.get("low"), "close": row.get("close"),
            "volume": row.get("volume"), "open_interest": row.get("open_interest"),
        })
    except Exception:
        return None


def _compact_app_sample(snapshot) -> dict:
    body = compact(snapshot, tracked_strikes=())
    body["latest_candles"] = {
        "NIFTY_1M": _latest_candle_payload(getattr(snapshot, "candles_1m", None)),
        "NIFTY_3M": _latest_candle_payload(getattr(snapshot, "candles_3m", None)),
        "FUTURES_1M": _latest_candle_payload(getattr(snapshot, "future_candles_1m", None)),
    }
    return clean(body)


def record_final_day_memory(snapshot, url, key):
    """Queue one fully-finalized app observation without blocking the Brain UI."""
    if not url or not key or not snapshot.market_session.is_live:
        return
    snapshot_key = str(getattr(snapshot, "snapshot_id", "") or snapshot.created_at.isoformat())
    if st.session_state.get("day_memory_final_snapshot") == snapshot_key:
        return
    common = snapshot.metadata.get("common_decision") or {}
    if not snapshot.metadata.get("simple_brain") or not common or not getattr(snapshot, "execution_guard", None):
        snapshot.metadata["recording_skip_reason"] = "FINAL_CALCULATION_INCOMPLETE"
        return

    # Materialize the compact payload synchronously (CPU-only, no I/O). The actual
    # Railway network write is queued after the decision is complete.
    now_ts = datetime.now().timestamp()
    last_history_push = float(st.session_state.get("market_history_push_at", 0.0))
    history = market_history_observation(snapshot) if now_ts - last_history_push >= 25.0 else None
    minute_key = snapshot.created_at.astimezone().strftime("%Y-%m-%dT%H:%M")
    sample = None
    if st.session_state.get("day_memory_sample_minute") != minute_key:
        # CPU-only serialization of the snapshot already in memory.  No broker call.
        sample = _compact_app_sample(snapshot)
    payload = {
        "event": app_observation(snapshot),
        "history": history,
        "sample": sample,
        "report": False,
    }
    try:
        _EVIDENCE_EXECUTOR.submit(_post_final_evidence, str(url), str(key), payload)
        with _EVIDENCE_LOCK:
            _EVIDENCE_STATUS["queued"] += 1
        if history is not None:
            st.session_state.market_history_push_at = now_ts
        if sample is not None:
            st.session_state.day_memory_sample_minute = minute_key
        # As before, one finalized observation is attempted once per immutable snapshot.
        st.session_state.day_memory_final_snapshot = snapshot_key
        snapshot.metadata.setdefault("performance", {})["evidence_write"] = "ASYNC_QUEUED"
        snapshot.metadata["evidence_async_status"] = evidence_async_status()
        st.session_state.pop("day_memory_error", None)
    except Exception:
        st.session_state.day_memory_error = (
            "Final evidence queue pending — calculation safe; Railway connection check karo."
        )


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
        render_cycle_prices(cached.get("cycle_prices", {}))
        st.caption(f"Cycle expiry: {cached.get('cycle_expiry') or 'Pending'} · Last session: {cached.get('day') or '—'} · Samples {counts.get('samples',0)} · DB {cached.get('bytes',0)/1048576:.2f} MB")
        st.caption(f"First: {cached.get('first') or '—'} · Last: {cached.get('last') or '—'}")
        coverage = cached.get("recording_coverage") or {}
        if coverage:
            st.caption(f"Record schema {coverage.get('record_schema')} · Option rows {coverage.get('option_rows', 0)} · Raw Greeks rows {coverage.get('raw_greeks_rows', 0)}")
            st.caption("Saved modules: " + ", ".join(coverage.get("evidence_fields_saved", [])))
            st.caption(f"Last AI decision change: {coverage.get('last_app_ai_at') or '—'} · App heartbeat: {coverage.get('last_app_heartbeat_at') or '—'}")
            st.caption(f"Observed-span coverage: {coverage.get('slot_coverage_pct', '—')}% · Missing minute slots: {coverage.get('missing_slots', '—')}. Yeh data coverage hai, accuracy nahi.")
            if coverage.get("archive_policy"):
                st.caption(
                    "Storage policy: " + str(coverage["archive_policy"])
                    + f" · Full archives {coverage.get('archive_files', 0)}"
                    + f" · {float(coverage.get('archive_bytes', 0))/1048576:.2f} MB"
                )
            top9 = coverage.get("last_valid_top9") or {}
            if top9:
                st.caption(
                    f"Last valid Top-9: {top9.get('state')} · "
                    f"15m move {float(top9.get('move_pct') or 0):+.2f}% · "
                    f"{top9.get('at')} · history reference only"
                )
        else:
            st.caption("Detailed recording coverage unavailable — Railway recorder version check karo.")
        history_feed = snapshot.feed_status.get("analysis_history")
        if history_feed:
            st.caption("Calculation history: " + str(history_feed.message))
            if not snapshot.market_session.is_live and "samples=0" in str(history_feed.message):
                st.caption(
                    "Reference/closed session me live Top-9 history append nahi hoti; "
                    "0 samples ko persistence failure na samjho. Live session me verify karna hai."
                )
        if cached.get("last_error"):
            st.warning("Data gap: "+str(cached["last_error"].get("reason","Unknown")))

        st.markdown("### Post-market Replay Review")
        st.caption(
            "Manual/on-demand diagnostic: saved samples ke observed moves aur Big Player timing ko replay karta hai. "
            "Koi broker call, live score change ya automatic threshold tuning nahi hoti."
        )
        rc1, rc2 = st.columns(2)
        replay_horizon = rc1.selectbox(
            "Replay horizon",
            (5, 10, 15, 30),
            index=2,
            format_func=lambda value: f"{value} minute",
            key="post_market_review_horizon",
        )
        replay_move = rc2.selectbox(
            "Minimum observed move",
            (20, 30, 50, 75),
            index=1,
            format_func=lambda value: f"{value} points",
            key="post_market_review_move_points",
        )
        if st.button("Build Replay Review", key="build_post_market_review"):
            try:
                st.session_state.post_market_review = RailwayDhanClient(
                    url, key, timeout_seconds=10
                )._post(
                    "/day-memory-review",
                    {"horizon_minutes": int(replay_horizon), "move_points": int(replay_move)},
                )
                st.session_state.pop("post_market_review_error", None)
            except Exception as exc:
                st.session_state.post_market_review_error = type(exc).__name__
        if st.session_state.get("post_market_review_error"):
            st.warning("Replay build nahi hua — recorded data safe hai aur One Brain unaffected hai.")
        review = st.session_state.get("post_market_review")
        if review:
            validation = review.get("big_player_validation") or {}
            summary = review.get("episode_summary") or {}
            st.caption(
                f"Replay rule: {review.get('horizon_minutes', replay_horizon)}m / "
                f"{review.get('move_threshold_points', replay_move)} points · "
                f"Samples {review.get('samples', 0)}"
            )
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Observed moves", summary.get("move_episodes", validation.get("move_episodes", 0)))
            c2.metric("WAIT at start", summary.get("wait_at_start", 0))
            c3.metric("Signal at start", summary.get("signal_present_at_start", 0))
            c4.metric("BP aligned start", validation.get("aligned_at_episode_start", 0))
            lag = summary.get("median_same_direction_bp_lag_minutes")
            c5.metric("Median BP lag", "—" if lag is None else f"{float(lag):.1f}m")

            if summary:
                st.caption(
                    f"Move size median {summary.get('median_move_points') if summary.get('median_move_points') is not None else '—'} pts · "
                    f"largest {summary.get('largest_move_points') if summary.get('largest_move_points') is not None else '—'} pts · "
                    f"same-direction BP <=3m {summary.get('same_direction_bp_within_3m', 0)} · "
                    f"<=5m {summary.get('same_direction_bp_within_5m', 0)}"
                )

            rows = []
            for item in review.get("non_overlapping_episodes", []):
                activity = item.get("big_player_at_start") or {}
                rows.append({
                    "Start": str(item.get("start") or "")[:16].replace("T", " "),
                    "End": str(item.get("end") or "")[:16].replace("T", " "),
                    "Move pts": item.get("observed_move"),
                    "Direction": item.get("observed_direction"),
                    "App at start": item.get("background_action_at_start"),
                    "Context": "WAIT at start" if item.get("label") == "MOVE WHILE WAIT" else "Signal present",
                    "BP start": activity.get("direction", "—"),
                    "BP score": activity.get("score"),
                    "BP state": activity.get("state", "—"),
                    "Same-dir BP lag min": item.get("big_player_observation_lag_minutes"),
                })
            episode_tab, timing_tab = st.tabs(["Replay timeline", "Big Player timing"])
            with episode_tab:
                if rows:
                    st.dataframe(rows, hide_index=True, width="stretch")
                else:
                    st.info("Selected replay rule par complete observed move episode nahi mila.")
            with timing_tab:
                if rows:
                    timing_rows = [
                        {
                            "Start": row["Start"],
                            "Direction": row["Direction"],
                            "BP start": row["BP start"],
                            "BP score": row["BP score"],
                            "Same-dir BP lag min": row["Same-dir BP lag min"],
                        }
                        for row in rows
                    ]
                    st.dataframe(timing_rows, hide_index=True, width="stretch")
                else:
                    st.info("Big Player timing compare karne ke liye replay episodes chahiye.")
            st.caption(str(summary.get("note") or ""))
            st.caption(str(review.get("warning") or ""))

        analytics = snapshot.metadata.get("history_analytics", {})
        st.write("**OI history — pehle aur ab**")
        st.caption("Extra vote 0: existing OI engine already uses rolling history. Labels inference hain, trader counts nahi.")
        for window in analytics.get("oi", {}).get("windows", []):
            st.caption(f"{window['minutes']}m: {window['status']} · Nifty change {window.get('spot_change', '—')}")
            if window.get("inferred_pressure"):
                st.caption(f"Pressure: {window['inferred_pressure']} · Price support: {window['price_supports_pressure']} — trader count nahi")
            if window.get("rows"):
                st.dataframe(window["rows"], hide_index=True, width="stretch")
        st.write("**Futures VWAP — same instrument**")
        st.json(analytics.get("vwap", {}), expanded=False)
        st.write("**FII/DII — prior reported sessions**")
        st.caption(analytics.get("institutions", {}).get("note", "Pending"))
        st.dataframe(analytics.get("institutions", {}).get("rows", []), hide_index=True, width="stretch")
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
            st.dataframe(rows,hide_index=True,width="stretch")
            st.caption("SELL entry bid / hedge ask; exit short ask / hedge bid. Equal-quantity points, no fees/slippage/fill guarantee. Observed loss minute samples ka hai, true intraminute maximum nahi. Missing result zero profit nahi.")
        if cached.get("cycle_summaries"):
            st.write("Completed expiry cycles")
            st.json(cached["cycle_summaries"],expanded=False)
        st.caption("Latest 30 events shown; diary retains current-cycle detail. Background direction app ke manual context se different ho sakti hai. App AI events sirf app fetch hone par.")


def render_evidence_download(url, key):
    """Centralised evidence export control for the downloads centre."""
    if not url or not key:
        st.info("Evidence download ke liye Railway connection chahiye.")
        return
    if st.button("Prepare Full Evidence Download", key="prepare_evidence_export"):
        try:
            st.session_state.evidence_download = RailwayDhanClient(
                url, key, timeout_seconds=60
            ).download_bytes("/day-memory-export-file")
            st.session_state.pop("evidence_download_error", None)
        except Exception as exc:
            st.session_state.evidence_download_error = str(exc)[:220]
            st.error(
                "Export nahi mila: "
                + st.session_state.evidence_download_error
                + ". Records delete nahi kiye."
            )
    if st.session_state.get("evidence_download"):
        st.download_button(
            "Download Full Recorded Evidence",
            st.session_state.evidence_download,
            file_name="nifty-evidence.jsonl.gz",
            mime="application/gzip",
            width="stretch",
        )


def render_cycle_prices(view):
    from analysis.cycle_prices import selected_rows
    import pandas as pd
    with st.expander("Expiry-cycle Price History — 9:30 / 3:30 CE + PE", expanded=False):
        st.caption(f"Expiry: {view.get('expiry') or 'Pending'} · Recorded observations only; extra AI vote 0.")
        if not view.get("contracts"):
            st.info("Valid option history abhi nahi. Missing records backfill nahi kiye jayenge.")
            return
        choices = {side: [c for c in view["contracts"] if c["side"] == side] for side in ("CE", "PE")}
        selected = {}
        for side in ("CE", "PE"):
            if not choices[side]:
                selected[side] = None
                continue
            labels = {c["key"]: f"{c['strike']:,.0f} {side} · ID {c['security_id']}" for c in choices[side]}
            selected[side] = st.selectbox(f"{side} contract", list(labels), format_func=labels.get,
                                         key=f"cycle_price_{view.get('expiry')}_{side}")
        rows = selected_rows(view, selected["CE"], selected["PE"])
        st.dataframe(pd.DataFrame(rows).drop(columns="Observed at"), hide_index=True, width="stretch")
        st.caption("Blank = Data missing. Exact target-minute snapshot only; 15:28 ko 15:30 nahi banaya. LTP last trade hai, executable bid/ask ya official settlement nahi.")
        with st.expander("Daily change, OI / IV aur observed high-low"):
            detail = []
            for day in view.get("days", []):
                pair = [r for r in rows if r["Din"] == day["day"]]
                record = {"Din": day["day"], "Nifty observed high": day["observed_high"], "Nifty observed low": day["observed_low"], "Samples": day["samples"]}
                for label in ("Nifty LTP", "CE LTP", "PE LTP"):
                    start, end = pair[0][label], pair[1][label]
                    record[label + " change"] = round(end-start, 2) if start is not None and end is not None else None
                    record[label + " change %"] = round((end-start)/start*100, 2) if start and end is not None else None
                for side in ("CE", "PE"):
                    record[side+" observed high"] = day["contracts"].get(selected[side], {}).get("observed_high")
                    record[side+" observed low"] = day["contracts"].get(selected[side], {}).get("observed_low")
                detail.append(record)
            st.dataframe(detail, hide_index=True, width="stretch")
            for row in view.get("rows", []):
                st.caption(f"{row['day']} {row['time']} · Observed: {row['observed_at'] or 'Data missing'}")
                st.json({side: row["options"].get(selected[side], {}) for side in ("CE", "PE")}, expanded=False)
            st.caption("High/low saved minute LTP ka hai, tick-level daily high/low nahi. Missing days/strikes ka data invent nahi hota. OI/IV change se cause prove nahi hota.")
