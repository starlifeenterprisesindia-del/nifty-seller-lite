from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from analysis.position_guardian import create_trade_record, calculate_position_guardian
from analysis.execution_guard import calculate_execution_guard
from config import CONFIG
from models import DisciplineState, MarketSnapshot
from services.github_journal import GitHubJsonJournal
from services.journal_research import (
    classify_alignment,
    diagnose_closed_trade,
    freeze_market_context,
    market_intelligence_candidate,
    normalize_direction,
    setup_direction,
)

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


class ShadowJournalStore:
    """Atomic read-only-market paper journal; never places a broker order."""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        path: str | Path | None = None,
        cloud_backend: GitHubJsonJournal | None = None,
    ) -> None:
        self.path = Path(path or CONFIG.shadow_journal_path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.cloud = cloud_backend
        self.last_error = ""
        self.last_blocker = "Not checked"
        self.last_checked = ""
        self.last_saved = ""
        self.local_read_failed = False

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _decision_locked(self) -> Iterator[None]:
        path = self.path.with_suffix(".decisions.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _cycle_locked(self) -> Iterator[None]:
        path = self.path.with_suffix(".cycles.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @classmethod
    def _empty(cls) -> dict[str, Any]:
        return {"schema_version": cls.SCHEMA_VERSION, "entries": []}

    def _read_local(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.local_read_failed = True
            self.last_error = f"Journal read failed: {type(exc).__name__}; original file preserved"
            return self._empty()
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            self.local_read_failed = True
            self.last_error = "Journal format invalid; original file preserved"
            return self._empty()
        return data

    def _write_local(self, data: dict[str, Any]) -> None:
        if self.local_read_failed:
            raise ValueError("Refusing to overwrite unreadable journal")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def load(self, *, refresh_cloud: bool = False) -> list[dict[str, Any]]:
        with self._locked():
            if refresh_cloud and self.cloud is not None and self.cloud.enabled:
                try:
                    remote = self.cloud.read().data
                    if isinstance(remote.get("entries"), list):
                        local = self._read_local()
                        merged = {str(x.get("trade_id")): x for x in remote["entries"] if isinstance(x, dict)}
                        merged.update({str(x.get("trade_id")): x for x in local["entries"] if isinstance(x, dict)})
                        self._write_local({"schema_version": self.SCHEMA_VERSION, "entries": list(merged.values())})
                except Exception as exc:
                    self.last_error = f"Cloud read failed: {type(exc).__name__}; local history retained"
            data = self._read_local()
            return [dict(item) for item in data["entries"] if isinstance(item, dict)]

    def _read_decisions_unlocked(self) -> list[dict[str, Any]]:
        path = self.path.with_suffix(".decisions.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except (OSError, json.JSONDecodeError):
            return []
        return [dict(item) for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def load_decisions(self) -> list[dict[str, Any]]:
        with self._decision_locked():
            return self._read_decisions_unlocked()

    def _save_decisions_unlocked(self, rows: list[dict[str, Any]]) -> None:
        path = self.path.with_suffix(".decisions.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows[-2500:], sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)

    def _save_decisions(self, rows: list[dict[str, Any]]) -> None:
        with self._decision_locked():
            self._save_decisions_unlocked(rows)

    def merge_decisions(self, rows: list[dict[str, Any]]) -> int:
        """Restore/merge durable Railway decision rows after a fresh UI deploy."""
        if not rows:
            return 0
        with self._decision_locked():
            local = self._read_decisions_unlocked()
            merged: dict[str, dict[str, Any]] = {}
            for item in (*local, *rows):
                if not isinstance(item, dict):
                    continue
                key = str(item.get("at") or "")
                if not key:
                    continue
                merged[key] = dict(item)
            ordered = sorted(merged.values(), key=lambda item: str(item.get("at") or ""))
            if len(ordered) != len(local) or any(a != b for a, b in zip(ordered, local)):
                self._save_decisions_unlocked(ordered)
            return len(ordered)

    def _read_cycle_state_unlocked(self) -> dict[str, Any]:
        path = self.path.with_suffix(".cycles.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        return data if isinstance(data, dict) else {}

    def _save_cycle_state_unlocked(self, data: dict[str, Any]) -> None:
        path = self.path.with_suffix(".cycles.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _research_signature(lane: str, snapshot: MarketSnapshot, candidate: dict[str, Any]) -> str:
        direction = normalize_direction(candidate.get("direction") or setup_direction(candidate.get("setup")))
        setup = str(candidate.get("setup") or "").upper()
        barrier = (
            snapshot.barrier_map.nearest_resistance
            if direction == "BULLISH"
            else snapshot.barrier_map.nearest_support
            if direction == "BEARISH"
            else None
        )
        if barrier is None:
            zone = "NO-BARRIER"
        else:
            # Five-point bins prevent tiny zone drift from manufacturing a new trial.
            lo = round(float(barrier.lower) / 5.0) * 5.0
            hi = round(float(barrier.upper) / 5.0) * 5.0
            zone = f"{barrier.label}:{lo:.0f}-{hi:.0f}"
        return f"{lane}|{direction}|{setup}|{zone}"

    def identify_research_cycle(
        self, lane: str, snapshot: MarketSnapshot, candidate: dict[str, Any] | None
    ) -> tuple[str | None, bool, str]:
        """Track one statistical sample per continuous setup cycle.

        A cycle re-arms only after the candidate disappears, direction/setup changes,
        or the relevant barrier materially changes. A cooldown alone never creates a
        new statistical trial.
        """
        session = snapshot.created_at.date().isoformat()
        lane_key = lane.upper()
        with self._cycle_locked():
            state = self._read_cycle_state_unlocked()
            if str(state.get("session_date") or "") != session:
                state = {"session_date": session, "lanes": {}}
            lanes = state.setdefault("lanes", {})
            lane_state = lanes.get(lane_key) if isinstance(lanes.get(lane_key), dict) else {}
            if candidate is None:
                if lane_state.get("active"):
                    lane_state["active"] = False
                    lane_state["signature"] = ""
                    lane_state["sampled"] = False
                    lanes[lane_key] = lane_state
                    self._save_cycle_state_unlocked(state)
                return None, False, "No active setup cycle"

            signature = self._research_signature(lane, snapshot, candidate)
            if not lane_state.get("active") or lane_state.get("signature") != signature:
                counter = int(lane_state.get("counter") or 0) + 1
                lane_state = {
                    "counter": counter,
                    "active": True,
                    "signature": signature,
                    "cycle_id": f"{lane_key.replace(' ', '-')}-{session}-{counter:03d}",
                    "sampled": False,
                    "started_at": snapshot.created_at.isoformat(),
                }
                lanes[lane_key] = lane_state
                self._save_cycle_state_unlocked(state)
            return (
                str(lane_state.get("cycle_id") or ""),
                bool(lane_state.get("sampled")),
                signature,
            )

    def mark_research_cycle_sampled(self, lane: str, cycle_id: str) -> None:
        if not cycle_id:
            return
        with self._cycle_locked():
            state = self._read_cycle_state_unlocked()
            lanes = state.get("lanes") if isinstance(state.get("lanes"), dict) else {}
            lane_state = lanes.get(lane.upper()) if isinstance(lanes.get(lane.upper()), dict) else None
            if lane_state and str(lane_state.get("cycle_id") or "") == cycle_id:
                lane_state["sampled"] = True
                lane_state["sampled_at"] = datetime.now().isoformat()
                self._save_cycle_state_unlocked(state)

    def save(self, entries: list[dict[str, Any]], *, sync_cloud: bool = True) -> None:
        data = {"schema_version": self.SCHEMA_VERSION, "entries": entries[-500:]}
        with self._locked():
            self._write_local(data)
            self.last_saved = datetime.now().isoformat()
            if sync_cloud and self.cloud is not None and self.cloud.enabled:
                try:
                    remote = self.cloud.read()
                    self.cloud.write(data, sha=remote.sha)
                except Exception as exc:
                    # Local journal remains authoritative until cloud recovers.
                    self.last_error = f"Cloud save failed: {type(exc).__name__}; saved locally"

    def record_check(self, snapshot, reason):
        """Record One-Brain decisions plus shadow-intelligence context atomically.

        The Decision Journal is independent from Paper Trades.  It records live
        WAIT/READY/ENTRY observations from 09:15 and backfills 5m/15m/30m spot
        outcomes.  The full read-update-write is held under one process lock so a
        second Streamlit/server writer cannot silently overwrite fresh rows.
        """
        self.last_checked = snapshot.created_at.isoformat()
        self.last_blocker = reason
        simple = snapshot.metadata.get("simple_brain") or {}
        common = snapshot.metadata.get("common_decision") or {}
        context = freeze_market_context(snapshot)
        spot = snapshot.levels.current_price
        if spot is None:
            spot = snapshot.nifty_quote.get("last_price")
        try:
            spot = float(spot) if spot is not None else None
        except (TypeError, ValueError):
            spot = None
        local_clock = snapshot.created_at.timetz().replace(tzinfo=None)
        journal_open = bool(
            snapshot.created_at.weekday() < 5
            and CONFIG.simple_decision_journal_start <= local_clock <= CONFIG.simple_decision_journal_end
        )

        try:
            with self._decision_locked():
                decisions = self._read_decisions_unlocked()
                now = snapshot.created_at
                market_session = getattr(snapshot, "market_session", None)
                session_live = bool(getattr(market_session, "is_live", True))
                changed_rows = False
                if spot is not None and session_live:
                    for row in decisions:
                        try:
                            opened = datetime.fromisoformat(str(row.get("at")))
                            if opened.tzinfo is None and now.tzinfo is not None:
                                opened = opened.replace(tzinfo=now.tzinfo)
                            age_min = (now - opened).total_seconds() / 60.0
                            base = float(row.get("spot"))
                        except (TypeError, ValueError):
                            continue
                        for horizon in (5, 15, 30):
                            key = f"outcome_{horizon}m_points"
                            if row.get(key) is None and age_min >= horizon:
                                move = spot - base
                                row[key] = round(move, 2)
                                row[f"outcome_{horizon}m_label"] = (
                                    "UP" if move >= 5 else "DOWN" if move <= -5 else "RANGE"
                                )
                                changed_rows = True

                current = {
                    "at": now.isoformat(),
                    "session_date": now.date().isoformat(),
                    "session_live": session_live,
                    "spot": spot,
                    "regime": str(simple.get("regime") or ""),
                    "direction": str(simple.get("direction") or snapshot.decision.market_direction),
                    "direction_strength": round(float(simple.get("direction_strength") or 0.0), 1),
                    "entry_readiness": round(float(simple.get("entry_readiness") or 0.0), 1),
                    "entry_state": str(simple.get("entry_state") or ""),
                    "candidate_action": str(simple.get("candidate_action") or snapshot.trade_plan.selected_setup),
                    "final_action": str(common.get("final_action") or simple.get("final_action") or "WAIT"),
                    "trigger": str(simple.get("trigger") or ""),
                    "reason": reason,
                    "option_bias": snapshot.option_intelligence.market_bias,
                    "option_confidence": snapshot.option_intelligence.confidence,
                    "big_player": f"{snapshot.big_player_activity.direction} {snapshot.big_player_activity.score:.0f}",
                    "barrier_state": str(((simple.get("blocks") or {}).get("barrier_entry") or {}).get("state") or ""),
                    "mi_window_state": context.get("institutional_window_state"),
                    "mi_window_score": context.get("institutional_window_score"),
                    "pressure_quality_state": context.get("pressure_quality_state"),
                    "pressure_quality_score": context.get("pressure_quality_score"),
                    "liquidity_magnet_bias": context.get("liquidity_magnet_bias"),
                    "liquidity_magnet_confidence": context.get("liquidity_magnet_confidence"),
                    "mi_direction": context.get("mi_direction"),
                    "ob_mi_alignment": classify_alignment(
                        normalize_direction(simple.get("direction")), context.get("mi_direction")
                    ),
                    "outcome_5m_points": None,
                    "outcome_15m_points": None,
                    "outcome_30m_points": None,
                }
                last = decisions[-1] if decisions else None
                append = last is None
                if last is not None:
                    try:
                        previous_at = datetime.fromisoformat(str(last.get("at")))
                        if previous_at.tzinfo is None and now.tzinfo is not None:
                            previous_at = previous_at.replace(tzinfo=now.tzinfo)
                        elapsed = (now - previous_at).total_seconds()
                    except (TypeError, ValueError):
                        elapsed = CONFIG.simple_decision_journal_interval_seconds
                    state_changed = any(
                        str(last.get(key) or "") != str(current.get(key) or "")
                        for key in (
                            "regime", "direction", "entry_state", "candidate_action",
                            "final_action", "barrier_state", "mi_window_state",
                            "pressure_quality_state", "liquidity_magnet_bias",
                        )
                    )
                    append = state_changed or elapsed >= CONFIG.simple_decision_journal_interval_seconds
                if journal_open and session_live and append:
                    decisions.append(current)
                    changed_rows = True
                if changed_rows:
                    self._save_decisions_unlocked(decisions)
        except (OSError, ValueError, TypeError) as exc:
            self.last_error = f"Decision journal failed: {type(exc).__name__}"


def _strategy_score(snapshot: MarketSnapshot, action: str) -> float:
    evaluation = {
        "CE BUY": snapshot.decision.ce_buy,
        "PE BUY": snapshot.decision.pe_buy,
        "CE SELL": snapshot.decision.ce_sell,
        "PE SELL": snapshot.decision.pe_sell,
        "IRON CONDOR": snapshot.decision.iron_condor,
    }.get(action)
    return float(evaluation.score) if evaluation is not None else 0.0


def _close_open_entries(
    entries: list[dict[str, Any]], snapshot: MarketSnapshot
) -> tuple[bool, bool]:
    changed = False
    cloud_changed = False
    for entry in entries:
        if str(entry.get("status") or "").upper() != "OPEN":
            continue
        state = DisciplineState(
            session_date=snapshot.created_at.date().isoformat(),
            trades_taken=1,
            day_locked=False,
            last_outcome="OPEN",
            last_action=str(entry.get("action") or ""),
            signal_history=(),
            status="READY",
            trade_record=entry,
        )
        guardian = calculate_position_guardian(
            discipline_state=state,
            option_chain=snapshot.option_chain,
            current_expiry=snapshot.expiry,
            current_spot=float(snapshot.levels.current_price)
            if snapshot.levels.current_price is not None
            else None,
            market_session=snapshot.market_session,
            option_chain_live=(
                snapshot.feed_status.get("option_chain") is not None
                and snapshot.feed_status["option_chain"].use_state == "LIVE"
            ),
            as_of=snapshot.created_at,
        )
        pnl = guardian.unrealized_pnl_rupees
        entry["last_guardian_check_at"] = snapshot.created_at.isoformat()
        entry["guardian_status"] = guardian.status
        changed = True
        if guardian.status == "EXIT DUE":
            entry["exit_due_at"] = entry.get("exit_due_at") or snapshot.created_at.isoformat()
        if pnl is not None:
            next_mfe = round(max(float(entry.get("mfe_rupees") or 0.0), pnl), 2)
            next_mae = round(min(float(entry.get("mae_rupees") or 0.0), pnl), 2)
            next_pnl = round(pnl, 2)
            if (
                next_mfe != entry.get("mfe_rupees")
                or next_mae != entry.get("mae_rupees")
                or next_pnl != entry.get("last_pnl_rupees")
            ):
                entry["mfe_rupees"] = next_mfe
                entry["mae_rupees"] = next_mae
                entry["last_pnl_rupees"] = next_pnl
                changed = True
        if guardian.status in {"TARGET ALERT", "EXIT ALERT"} and pnl is not None:
            gross = float(pnl)
            charges = float(CONFIG.shadow_journal_estimated_charges_per_trade)
            net = gross - charges
            entry["status"] = "CLOSED"
            entry["outcome"] = guardian.instruction
            entry["exit_reason"] = guardian.instruction
            entry["closed_at"] = snapshot.created_at.isoformat()
            entry["fill_basis"] = "First observed executable quote, not a guaranteed deadline fill"
            entry["exit_price_basis"] = "First observed executable protected-spread quote at alert time"
            entry["exit_debit_points"] = guardian.current_debit_points
            entry["gross_pnl_rupees"] = round(gross, 2)
            entry["estimated_charges_rupees"] = round(charges, 2)
            entry["net_pnl_rupees"] = round(net, 2)
            entry["estimated_net_pnl_rupees"] = round(net, 2)
            current_spot = (
                float(snapshot.levels.current_price)
                if snapshot.levels.current_price is not None
                else None
            )
            diagnosis = diagnose_closed_trade(
                entry,
                exit_context=freeze_market_context(snapshot),
                current_spot=current_spot,
                gross_pnl=gross,
                net_pnl=net,
                outcome=guardian.instruction,
                closed_at=snapshot.created_at,
            )
            entry.update(diagnosis)
            changed = True
            cloud_changed = True
    return changed, cloud_changed


def _backfill_trade_spot_outcomes(entries: list[dict[str, Any]], snapshot: MarketSnapshot) -> bool:
    """Attach 5m/15m/30m spot outcomes to every research paper entry.

    This uses only later authoritative snapshots and never feeds back into a live score.
    """
    if not snapshot.market_session.is_live or snapshot.levels.current_price is None:
        return False
    changed = False
    now = snapshot.created_at
    spot = float(snapshot.levels.current_price)
    for entry in entries:
        try:
            opened = datetime.fromisoformat(str(entry.get("opened_at") or ""))
            if opened.tzinfo is None and now.tzinfo is not None:
                opened = opened.replace(tzinfo=now.tzinfo)
            base = float(entry.get("entry_spot"))
            age_min = (now - opened).total_seconds() / 60.0
        except (TypeError, ValueError):
            continue
        direction = normalize_direction(entry.get("signal_direction") or setup_direction(entry.get("setup")))
        for horizon in (5, 15, 30):
            key = f"spot_outcome_{horizon}m_points"
            if entry.get(key) is not None or age_min < horizon:
                continue
            raw_move = round(spot - base, 2)
            directional = raw_move if direction == "BULLISH" else -raw_move if direction == "BEARISH" else abs(raw_move)
            entry[key] = raw_move
            entry[f"directional_outcome_{horizon}m_points"] = round(directional, 2)
            entry[f"directional_outcome_{horizon}m_label"] = (
                "FAVORED" if directional >= 5 else "ADVERSE" if directional <= -5 else "RANGE"
            )
            changed = True
    return changed


_CONCRETE_SETUPS = {"CE BUY", "PE BUY", "CE SELL", "PE SELL", "IRON CONDOR"}


def _selected_plan(snapshot: MarketSnapshot, action: str):
    return {
        "CE BUY": snapshot.trade_plan.ce_buy,
        "PE BUY": snapshot.trade_plan.pe_buy,
        "CE SELL": snapshot.trade_plan.ce_sell,
        "PE SELL": snapshot.trade_plan.pe_sell,
        "IRON CONDOR": snapshot.trade_plan.iron_condor,
    }.get(str(action or "").upper())


def _entry_lane(item: dict[str, Any]) -> str:
    lane = str(item.get("validation_lane") or "").upper().strip()
    if lane:
        return lane
    # Legacy shadow rows were One-Brain paper samples.
    return "ONE BRAIN"


def _lane_cap(lane: str) -> int:
    return (
        int(CONFIG.shadow_journal_max_mi_trades_per_day)
        if lane == "MARKET INTELLIGENCE"
        else int(CONFIG.shadow_journal_max_ob_trades_per_day)
    )


def _lane_rows(entries: list[dict[str, Any]], snapshot: MarketSnapshot, lane: str) -> list[dict[str, Any]]:
    today = snapshot.created_at.date().isoformat()
    return [
        item for item in entries
        if str(item.get("session_date") or "") == today and _entry_lane(item) == lane
    ]


def _cooldown_ready(rows: list[dict[str, Any]], snapshot: MarketSnapshot) -> tuple[bool, str]:
    if not rows:
        return True, "READY"
    timestamps: list[datetime] = []
    for item in rows:
        try:
            opened = datetime.fromisoformat(str(item.get("opened_at") or ""))
            if opened.tzinfo is None and snapshot.created_at.tzinfo is not None:
                opened = opened.replace(tzinfo=snapshot.created_at.tzinfo)
            timestamps.append(opened)
        except (TypeError, ValueError):
            continue
    if not timestamps:
        return True, "READY"
    elapsed = snapshot.created_at - max(timestamps)
    minimum = timedelta(minutes=CONFIG.shadow_journal_research_cooldown_minutes)
    if elapsed < minimum:
        wait = max(0.0, (minimum - elapsed).total_seconds() / 60.0)
        return False, f"Research cooldown active ({wait:.1f}m remaining)"
    return True, "READY"


def _one_brain_candidate(snapshot: MarketSnapshot) -> dict[str, Any] | None:
    simple = snapshot.metadata.get("simple_brain") or {}
    common = snapshot.metadata.get("common_decision") or {}
    if simple:
        # Research lane intentionally samples a developing protected candidate even
        # when the live One-Brain final action remains WAIT for confirmation. This
        # does NOT alter live execution; the paper execution guard below must still
        # independently pass.
        action = str(simple.get("candidate_action") or simple.get("final_action") or "WAIT").upper()
        direction_strength = float(simple.get("direction_strength") or 0.0)
        entry_readiness = float(simple.get("entry_readiness") or 0.0)
        if action not in _CONCRETE_SETUPS:
            return None
        if direction_strength < CONFIG.simple_direction_min_strength:
            return None
        if entry_readiness < CONFIG.simple_entry_ready_score:
            return None
        return {
            "source": "ONE BRAIN",
            "trigger_type": "ONE BRAIN",
            "trigger_state": str(simple.get("entry_state") or "ENTRY READY"),
            "direction": setup_direction(action),
            "setup": action,
            "trigger_score": round(entry_readiness, 1),
            "direction_strength": round(direction_strength, 1),
            "reason": (
                f"One Brain {action} · direction {direction_strength:.0f}/100 "
                f"(floor {CONFIG.simple_direction_min_strength:.0f}) · entry {entry_readiness:.0f}/100 "
                f"(floor {CONFIG.simple_entry_ready_score:.0f})"
            ),
        }

    action = str(common.get("final_action") or "WAIT").upper()
    if action not in _CONCRETE_SETUPS or not common.get("entry_allowed"):
        return None
    score = float(common.get("trade_confidence") or snapshot.decision.decision_confidence or 0.0)
    return {
        "source": "ONE BRAIN",
        "trigger_type": "LEGACY COMMON",
        "trigger_state": "ENTRY READY",
        "direction": setup_direction(action),
        "setup": action,
        "trigger_score": round(score, 1),
        "direction_strength": round(score, 1),
        "reason": f"Common One-Brain gate allowed {action} · confidence {score:.0f}/100",
    }


def _research_paper_snapshot(snapshot: MarketSnapshot, candidate: dict[str, Any]) -> MarketSnapshot:
    """Build a paper-only execution guard on a copy, independent of real day lock."""
    action = str(candidate["setup"]).upper()
    plan = replace(snapshot.trade_plan, selected_setup=action)
    paper_discipline = DisciplineState(
        session_date=snapshot.created_at.date().isoformat(),
        trades_taken=0,
        day_locked=False,
        last_outcome="RESEARCH",
        last_action="",
        signal_history=(),
        status="READY",
        trade_record=None,
    )
    research_simple = {
        "candidate_action": action,
        "final_action": action,
        "entry_readiness": float(candidate.get("trigger_score") or 100.0),
        "instruction": str(candidate.get("reason") or "Research paper trigger ready"),
    }
    guard = calculate_execution_guard(
        decision=snapshot.decision,
        trade_plan=plan,
        market_session=snapshot.market_session,
        option_intelligence=snapshot.option_intelligence,
        price_action=snapshot.price_action,
        risk_profile=snapshot.risk_profile,
        discipline_state=paper_discipline,
        feed_status=snapshot.feed_status,
        as_of=snapshot.created_at,
        big_player=snapshot.big_player_activity,
        selected_setup_override=action,
        final_action_override=action,
        simple_brain=research_simple,
    )
    return replace(snapshot, trade_plan=plan, execution_guard=guard)


def _research_eligible(
    entries: list[dict[str, Any]],
    snapshot: MarketSnapshot,
    candidate: dict[str, Any] | None,
    lane: str,
) -> tuple[bool, str, MarketSnapshot | None]:
    if candidate is None:
        if lane == "MARKET INTELLIGENCE":
            return False, "No MI Window/verified-pressure paper trigger", None
        return False, "One Brain candidate / research thresholds not ready", None
    if bool(candidate.get("_cycle_sampled")):
        return False, "Same setup cycle already sampled; waiting for genuine reset/re-arm", None
    if not snapshot.market_session.is_live:
        return False, "Market is not live", None
    rows = _lane_rows(entries, snapshot, lane)
    cap = _lane_cap(lane)
    if len(rows) >= cap:
        return False, f"{lane} daily paper cap {cap} reached", None
    cooldown_ok, cooldown_reason = _cooldown_ready(rows, snapshot)
    if not cooldown_ok:
        return False, cooldown_reason, None
    action = str(candidate.get("setup") or "").upper()
    if action not in _CONCRETE_SETUPS:
        return False, "No concrete protected setup", None
    selected_plan = _selected_plan(snapshot, action)
    if selected_plan is None or not selected_plan.available:
        return False, f"{action} protected plan unavailable", None
    paper_snapshot = _research_paper_snapshot(snapshot, candidate)
    if paper_snapshot.execution_guard.readiness != "ENTRY READY":
        blockers = paper_snapshot.execution_guard.blockers or ("Research execution guard not ready",)
        return False, str(blockers[0]), paper_snapshot
    if paper_snapshot.execution_guard.allowed_lots < 1:
        return False, "One-lot defined risk exceeds configured paper budget", paper_snapshot
    return True, "READY", paper_snapshot


def _score_band(value: float) -> str:
    if value < 50:
        return "<50"
    if value < 55:
        return "50–54"
    if value < 60:
        return "55–59"
    if value < 70:
        return "60–69"
    return "70+"


def _make_research_record(
    snapshot: MarketSnapshot,
    paper_snapshot: MarketSnapshot,
    candidate: dict[str, Any],
    *,
    lane: str,
    sequence: int,
    alignment_state: str,
) -> dict[str, Any]:
    action = str(candidate["setup"]).upper()
    record = create_trade_record(
        captured_at=paper_snapshot.created_at,
        decision=paper_snapshot.decision,
        trade_plan=paper_snapshot.trade_plan,
        execution_guard=paper_snapshot.execution_guard,
        lots=1,
        lot_size=paper_snapshot.risk_profile.lot_size,
        spot=paper_snapshot.levels.current_price,
        # The research lane intentionally tests a signal independently from the real
        # one-trade/day discipline, but the paper guard above must still be ENTRY READY.
        allow_paper_candidate=True,
    )
    context = freeze_market_context(snapshot)
    plan = _selected_plan(paper_snapshot, action)
    trigger_score = float(candidate.get("trigger_score") or 0.0)
    prefix = "OB" if lane == "ONE BRAIN" else "MI"
    entry_reasons = [str(candidate.get("reason") or "Research paper trigger")]
    if plan is not None:
        entry_reasons.extend(str(x) for x in (plan.reasons or ())[:3])
    if lane == "ONE BRAIN":
        entry_reasons.extend(str(x) for x in snapshot.decision.reasons[:2])
    else:
        mie = snapshot.metadata.get("market_intelligence") or {}
        entry_reasons.extend(str(x) for x in (mie.get("reasons") or [])[:2])

    record.update(
        {
            "journal_type": "RESEARCH VALIDATION",
            "validation_lane": lane,
            "signal_source": lane,
            "trigger_type": str(candidate.get("trigger_type") or lane),
            "trigger_state": str(candidate.get("trigger_state") or ""),
            "trigger_score": round(trigger_score, 1),
            "trigger_transition": str(candidate.get("trigger_transition") or ""),
            "setup_cycle_id": str(candidate.get("_setup_cycle_id") or ""),
            "setup_fingerprint": str(candidate.get("_setup_fingerprint") or ""),
            "signal_direction": normalize_direction(candidate.get("direction") or setup_direction(action)),
            "alignment_state": alignment_state,
            "real_ai_action": str((snapshot.metadata.get("simple_brain") or {}).get("final_action") or snapshot.decision.final_action),
            "qualification": (
                "ONE BRAIN VALIDATION"
                if lane == "ONE BRAIN"
                else f"MI {str(candidate.get('trigger_type') or 'RESEARCH')} VALIDATION"
            ),
            "counts_for_ai_accuracy": lane == "ONE BRAIN",
            "candidate_warning": "None",
            "trade_id": f"SH-{prefix}-{snapshot.created_at:%Y%m%d-%H%M%S}-{sequence}",
            "session_date": snapshot.created_at.date().isoformat(),
            "setup": action,
            "action": action,
            "decision_confidence": round(
                float(candidate.get("direction_strength") or trigger_score or snapshot.decision.decision_confidence), 1
            ),
            "strategy_score": round(_strategy_score(snapshot, action), 1),
            "score_band": _score_band(trigger_score),
            "big_player_direction": snapshot.big_player_activity.direction,
            "big_player_score": snapshot.big_player_activity.score,
            "big_player_confirmations": snapshot.big_player_activity.confirmation_count,
            "oi_basis": snapshot.option_intelligence.basis,
            "oi_bias": snapshot.option_intelligence.market_bias,
            "oi_confidence": snapshot.option_intelligence.confidence,
            "oi_persistence": snapshot.option_intelligence.persistence,
            "entry_reasons": list(dict.fromkeys(x for x in entry_reasons if x))[:8],
            "entry_context": context,
            "ob_direction_floor": CONFIG.simple_direction_min_strength,
            "ob_entry_ready_floor": CONFIG.simple_entry_ready_score,
            "paper_trade_cap_lane": _lane_cap(lane),
            "paper_trade_cooldown_minutes": CONFIG.shadow_journal_research_cooldown_minutes,
            "sample_note": "Paper validation sample; overlapping signals are correlated and are not independent statistical trials",
            "mfe_rupees": 0.0,
            "mae_rupees": 0.0,
            "last_pnl_rupees": 0.0,
            "spot_outcome_5m_points": None,
            "spot_outcome_15m_points": None,
            "spot_outcome_30m_points": None,
        }
    )
    return record


def process_auto_shadow_journal(
    snapshot: MarketSnapshot,
    store: ShadowJournalStore,
    *,
    enabled: bool,
) -> list[dict[str, Any]]:
    """Observe/score two independent paper lanes without touching core decisions.

    ONE BRAIN lane: concrete Simple-Brain candidate action, direction >=54 and entry
    readiness >=62 (current config values), then the existing protected-plan/risk/data
    guard must be ENTRY READY.

    MARKET INTELLIGENCE lane: Institutional Window OPEN/STRONG 6/6 with live-data
    safety, or VERIFIED/REALIZED pressure with barrier/attack support.  The lane maps
    BULLISH -> PE SELL and BEARISH -> CE SELL, reusing the already-built protected
    plans. Liquidity Magnet is context only and never creates a trade by itself.
    """
    entries = store.load(refresh_cloud=False)
    if store.local_read_failed:
        return entries

    changed, cloud_changed = _close_open_entries(entries, snapshot)
    if _backfill_trade_spot_outcomes(entries, snapshot):
        changed = True

    ob_candidate = _one_brain_candidate(snapshot)
    mi_candidate = market_intelligence_candidate(snapshot)

    # Unique-cycle sampling: cooldown is retained as a safety throttle, but it can no
    # longer manufacture repeated statistical trials from one unchanged opportunity.
    for lane_name, candidate in (("ONE BRAIN", ob_candidate), ("MARKET INTELLIGENCE", mi_candidate)):
        cycle_id, already_sampled, fingerprint = store.identify_research_cycle(
            lane_name, snapshot, candidate
        )
        if candidate is not None:
            candidate["_setup_cycle_id"] = cycle_id or ""
            candidate["_setup_fingerprint"] = fingerprint
            candidate["_cycle_sampled"] = already_sampled

    ob_ok, ob_reason, ob_snapshot = _research_eligible(
        entries, snapshot, ob_candidate, "ONE BRAIN"
    )
    mi_ok, mi_reason, mi_snapshot = _research_eligible(
        entries, snapshot, mi_candidate, "MARKET INTELLIGENCE"
    )

    active_ob_direction = ob_candidate.get("direction") if ob_candidate else "MIXED"
    active_mi_direction = mi_candidate.get("direction") if mi_candidate else "MIXED"
    alignment_state = classify_alignment(active_ob_direction, active_mi_direction)
    status_reason = f"OB: {ob_reason} | MI: {mi_reason}"
    store.record_check(snapshot, status_reason if enabled else f"Auto paper trades OFF | {status_reason}")

    if enabled:
        candidates = (
            ("ONE BRAIN", ob_candidate, ob_ok, ob_snapshot),
            ("MARKET INTELLIGENCE", mi_candidate, mi_ok, mi_snapshot),
        )
        for lane, candidate, eligible, paper_snapshot in candidates:
            if not eligible or candidate is None or paper_snapshot is None:
                continue
            try:
                record = _make_research_record(
                    snapshot,
                    paper_snapshot,
                    candidate,
                    lane=lane,
                    sequence=len(entries) + 1,
                    alignment_state=alignment_state,
                )
                entries.append(record)
                store.mark_research_cycle_sampled(
                    lane, str(candidate.get("_setup_cycle_id") or "")
                )
                changed = True
                cloud_changed = True
            except (ValueError, TypeError) as exc:
                store.last_error = f"{lane} paper record failed: {type(exc).__name__}: {exc}"[:240]

    if changed:
        # Mark-to-market/outcome backfills remain local between actual trade state
        # changes. New/closed paper rows keep the existing durability sync behavior.
        store.save(entries, sync_cloud=cloud_changed)
    return entries
