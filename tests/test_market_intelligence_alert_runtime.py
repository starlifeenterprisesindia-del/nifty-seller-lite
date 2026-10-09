from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import sys
import types

_streamlit_stub = types.ModuleType("streamlit")
_streamlit_stub.session_state = {}
sys.modules.setdefault("streamlit", _streamlit_stub)

import ui.market_intelligence as mi_ui


class _SessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class _FakeStreamlit:
    def __init__(self):
        self.session_state = _SessionState(market_intelligence_alerts_enabled=True)


def _snapshot(*, include_institutional: bool = True):
    item = {
        "alerts": [{
            "kind": "INSTITUTIONAL_WINDOW_OPEN",
            "title": "Institutional Window Open",
            "direction": "BEARISH",
            "score": 78.0,
            "nifty_ltp": 22220.0,
        }],
        "expansion_pressure": 64.0,
        "evidence_coverage": 82.0,
        "one_brain_alignment": "DIRECTION ALIGNED",
        "system_status": "WATCH",
        "pressure_integrity": {
            "quality_state": "VERIFIED",
            "quality_score": 74.0,
            "supportive_signals": [],
        },
        "liquidity": {
            "hunt_bias": "DOWNSIDE",
            "hunt_strength": "STRONG",
            "sweep_state": "NONE",
            "sweep_outcome": "UNCLEAR",
        },
    }
    if include_institutional:
        item["institutional_window"] = {
            "state": "OPEN",
            "opportunity_score": 78.0,
            "gates_ready": 6,
        }
    return SimpleNamespace(
        metadata={"market_intelligence": item},
        created_at=datetime(2026, 10, 9, 9, 24, tzinfo=ZoneInfo("Asia/Kolkata")),
    )


def test_live_mi_alert_with_institutional_window_does_not_raise_nameerror(monkeypatch):
    fake_st = _FakeStreamlit()
    monkeypatch.setattr(mi_ui, "st", fake_st)
    monkeypatch.setattr(mi_ui, "combined_signal_alert", lambda snapshot: None)

    result = mi_ui.process_market_intelligence_alerts(_snapshot(), "", "")

    assert result == []
    assert fake_st.session_state.market_intelligence_alert_status == "App only — Telegram gateway not configured"
    assert fake_st.session_state.last_market_intelligence_alerts[0]["kind"] == "INSTITUTIONAL_WINDOW_OPEN"


def test_live_mi_alert_without_institutional_window_is_safe(monkeypatch):
    fake_st = _FakeStreamlit()
    monkeypatch.setattr(mi_ui, "st", fake_st)
    monkeypatch.setattr(mi_ui, "combined_signal_alert", lambda snapshot: None)

    result = mi_ui.process_market_intelligence_alerts(_snapshot(include_institutional=False), "", "")

    assert result == []
    assert fake_st.session_state.market_intelligence_alert_status == "App only — Telegram gateway not configured"
