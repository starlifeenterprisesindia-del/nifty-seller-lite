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
        self.last_checked = snapshot.created_at.isoformat()
        self.last_blocker = reason
        path = self.path.with_suffix(".signals.json")
        try:
            history = json.loads(path.read_text()) if path.exists() else []
            if not isinstance(history, list):
                history = []
            record = {"at": self.last_checked, "action": snapshot.decision.final_action,
                      "candidate": snapshot.trade_plan.selected_setup, "reason": reason,
                      "score": _strategy_score(snapshot, snapshot.trade_plan.selected_setup),
                      "confidence": snapshot.decision.decision_confidence}
            signature = (record["action"], record["candidate"], record["reason"], int(record["score"] // 5))
            previous = history[-1] if history else {}
            old = (previous.get("action"), previous.get("candidate"), previous.get("reason"), int(previous.get("score", 0) // 5))
            if signature != old:
                history.append(record)
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(history[-2000:]))
                os.replace(temporary, path)
        except (OSError, ValueError, TypeError) as exc:
            self.last_error = f"Signal log failed: {type(exc).__name__}"


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
            entry["status"] = "CLOSED"
            entry["outcome"] = guardian.instruction
            entry["closed_at"] = snapshot.created_at.isoformat()
            entry["fill_basis"] = "First observed executable quote, not a guaranteed deadline fill"
            entry["exit_debit_points"] = guardian.current_debit_points
            entry["gross_pnl_rupees"] = round(gross, 2)
            entry["estimated_charges_rupees"] = round(charges, 2)
            entry["net_pnl_rupees"] = round(gross - charges, 2)
            changed = True
            cloud_changed = True
    return changed, cloud_changed


def _eligible(entries: list[dict[str, Any]], snapshot: MarketSnapshot) -> tuple[bool, str]:
    action = snapshot.trade_plan.selected_setup
    today = snapshot.created_at.date().isoformat()
    today_entries = [item for item in entries if str(item.get("session_date")) == today]
    if len(today_entries) >= CONFIG.shadow_journal_max_trades_per_day:
        return False, "Daily 5-trade paper cap reached"
    if not snapshot.market_session.is_live:
        return False, "Market is not live"
    if action not in {"CE BUY", "PE BUY", "CE SELL", "PE SELL", "IRON CONDOR"}:
        return False, "No concrete One-Brain strategy"
    if snapshot.decision.decision_confidence < CONFIG.shadow_journal_min_confidence:
        return False, "Entry confidence below threshold"
    if _strategy_score(snapshot, action) < CONFIG.shadow_journal_min_strategy_score:
        return False, "Strategy score below threshold"
    selected_plan = {
        "CE BUY": snapshot.trade_plan.ce_buy,
        "PE BUY": snapshot.trade_plan.pe_buy,
        "CE SELL": snapshot.trade_plan.ce_sell,
        "PE SELL": snapshot.trade_plan.pe_sell,
        "IRON CONDOR": snapshot.trade_plan.iron_condor,
    }.get(action)
    if selected_plan is None or not selected_plan.available:
        return False, "Protected paper plan is unavailable"
    if snapshot.execution_guard.allowed_lots < 1:
        return False, "One-lot defined risk exceeds configured paper risk budget"
    now_time = snapshot.created_at.timetz().replace(tzinfo=None)
    if not snapshot.risk_profile.entry_start <= now_time <= snapshot.risk_profile.entry_end:
        return False, "Outside configured paper entry window"
    for feed_name in ("quotes", "candles", "option_chain"):
        feed = snapshot.feed_status.get(feed_name)
        if feed is None or feed.use_state != "LIVE":
            return False, f"{feed_name} is not confirmed live"
    ready_windows = sum(
        item.status == "READY" for item in snapshot.option_intelligence.windows
    )
    if snapshot.option_intelligence.status != "READY" or ready_windows < 2:
        return False, "Option flow has fewer than two ready windows"
    if snapshot.option_intelligence.confidence < CONFIG.shadow_journal_min_option_confidence:
        return False, "Option-flow confidence below paper threshold"
    if any(
        str(item.get("status") or "").upper() == "OPEN"
        and str(item.get("setup") or "").upper() == action
        for item in today_entries
    ):
        return False, "Same strategy already open"
    if today_entries:
        try:
            latest = max(datetime.fromisoformat(str(item["opened_at"])) for item in today_entries)
            if snapshot.created_at - latest < timedelta(minutes=CONFIG.shadow_journal_cooldown_minutes):
                return False, "Shadow cooldown active"
        except (KeyError, TypeError, ValueError):
            pass
    return True, "READY"


def _paper_snapshot(snapshot):
    """Select a test candidate on a copy; never mutate the real AI/position."""
    action = snapshot.trade_plan.selected_setup
    if action == "WAIT":
        action = snapshot.trade_plan.candidate_setup
    if action not in {"CE SELL", "PE SELL", "IRON CONDOR"}:
        return snapshot
    plan = replace(snapshot.trade_plan, selected_setup=action)
    guard = calculate_execution_guard(
        decision=snapshot.decision, trade_plan=plan, market_session=snapshot.market_session,
        option_intelligence=snapshot.option_intelligence, price_action=snapshot.price_action,
        risk_profile=snapshot.risk_profile, discipline_state=snapshot.discipline_state,
        feed_status=snapshot.feed_status, as_of=snapshot.created_at,
        big_player=snapshot.big_player_activity,
    )
    return replace(snapshot, trade_plan=plan, execution_guard=guard)


def process_auto_shadow_journal(
    snapshot: MarketSnapshot,
    store: ShadowJournalStore,
    *,
    enabled: bool,
) -> list[dict[str, Any]]:
    entries = store.load(refresh_cloud=False)
    if store.local_read_failed:
        return entries
    changed, cloud_changed = _close_open_entries(entries, snapshot)
    snapshot = _paper_snapshot(snapshot)
    eligible, reason = _eligible(entries, snapshot)
    store.record_check(snapshot, reason if enabled else "Auto paper journal OFF")
    if enabled and eligible:
        record = create_trade_record(
            captured_at=snapshot.created_at,
            decision=snapshot.decision,
            trade_plan=snapshot.trade_plan,
            execution_guard=snapshot.execution_guard,
            lots=1,
            lot_size=snapshot.risk_profile.lot_size,
            spot=snapshot.levels.current_price,
            allow_paper_candidate=(
                snapshot.execution_guard.readiness != "ENTRY READY"
            ),
        )
        action = snapshot.trade_plan.selected_setup
        record.update(
            {
                "journal_type": "AUTO SHADOW",
                "real_ai_action": snapshot.decision.final_action,
                "qualification": (
                    "ENTRY READY SHADOW"
                    if snapshot.execution_guard.readiness == "ENTRY READY"
                    else f"{CONFIG.shadow_journal_min_confidence:.0f}+ TEST CANDIDATE"
                ),
                "trade_id": f"SH-{snapshot.created_at:%Y%m%d-%H%M%S}-{len(entries)+1}",
                "session_date": snapshot.created_at.date().isoformat(),
                "setup": action,
                "action": action,
                "decision_confidence": snapshot.decision.decision_confidence,
                "strategy_score": _strategy_score(snapshot, action),
                "score_band": "45–49" if _strategy_score(snapshot, action) < 50 else "50–54" if _strategy_score(snapshot, action) < 55 else "55+",
                "big_player_direction": snapshot.big_player_activity.direction,
                "big_player_score": snapshot.big_player_activity.score,
                "big_player_confirmations": snapshot.big_player_activity.confirmation_count,
                "oi_basis": snapshot.option_intelligence.basis,
                "oi_bias": snapshot.option_intelligence.market_bias,
                "oi_confidence": snapshot.option_intelligence.confidence,
                "oi_persistence": snapshot.option_intelligence.persistence,
                "entry_reasons": list(snapshot.decision.reasons)
                + list(snapshot.big_player_activity.reasons)
                + list(snapshot.option_intelligence.reasons),
                "mfe_rupees": 0.0,
                "mae_rupees": 0.0,
                "last_pnl_rupees": 0.0,
            }
        )
        entries.append(record)
        changed = True
        cloud_changed = True
    if changed:
        # Intraday mark-to-market remains local to avoid a GitHub commit every
        # refresh. New and closed paper trades are mirrored to cloud.
        store.save(entries, sync_cloud=cloud_changed)
    return entries
