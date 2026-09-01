from datetime import datetime
import json

import pandas as pd
import pytest

from analysis.option_chain import validate_greeks
from analysis.spot_premium_calculator import calculate_spot_premium_range
from services.day_memory import compact, DayMemory, IST
from test_day_memory import snapshot


def source_pair():
    return pd.DataFrame([
        dict(strike=24100., side="CE", last_price=135.45, implied_volatility=10.783109,
             delta=.56401, gamma=.0014, theta=-16.64196, vega=10.28477),
        dict(strike=24100., side="PE", last_price=61., implied_volatility=7.779331,
             delta=-.41313, gamma=.00192, theta=-6.49507, vega=10.1712),
    ])


def test_provider_pair_warning_is_auditable_and_does_not_change_source():
    original = source_pair()
    checked = validate_greeks(original)
    assert checked.greeks_quality.eq("IV WARNING").all()
    assert checked.iv_pair_ratio.iloc[0] == pytest.approx(10.783109 / 7.779331)
    assert checked.delta_pair_gap.iloc[0] == pytest.approx(abs(.56401 + .41313 - 1))
    assert checked.source_implied_volatility.tolist() == original.implied_volatility.tolist()
    assert checked.delta.tolist() == original.delta.tolist()
    assert "not an API-versus-screen mismatch" in checked.greeks_reason.iloc[0]


def calculate(chain):
    return calculate_spot_premium_range(option_chain=chain, side="CE", position="SELL",
        strike=24100, current_spot=24115.05, current_premium=135.45, entry_premium=135.45,
        lower_spot=24090, upper_spot=24130, target_minutes=3, lot_size=65, lots=1,
        feed_state="LIVE", minutes_to_expiry=5000)


def test_manual_calculator_honors_pair_warning_and_invalid_block():
    checked = validate_greeks(source_pair())
    result = calculate(checked)
    assert result.status == "CONDITIONAL SCENARIO"
    assert result.overall_reliability <= 35
    assert all(x.reliability <= 35 for x in (result.lower, result.upper, *result.decay_scenarios))
    checked.loc[0, "greeks_quality"] = "MODEL MISMATCH"
    with pytest.raises(ValueError, match="projection blocked"):
        calculate(checked)


def test_record_preserves_raw_fields_and_module_evidence(tmp_path):
    item = snapshot(datetime(2026, 8, 28, 14, 25, tzinfo=IST))
    item.option_chain = validate_greeks(source_pair())
    data = item.public_summary()
    data["core_evidence"] = {"bearish_score": 59.3}
    data["option_intelligence"] = {"windows": [{"status": "READY"}]}
    item.public_summary = lambda: data
    body = compact(item)
    assert body["record_schema"] == 2
    assert body["evidence"]["core_evidence"]["bearish_score"] == 59.3
    assert body["options"][0]["source_implied_volatility"] == pytest.approx(10.783109)
    path = tmp_path / "session.sqlite3"
    store = DayMemory(path)
    assert store.record(item)
    reopened = DayMemory(path)
    report = reopened.report()
    assert report["recording_coverage"]["raw_greeks_rows"] == 2
    assert "option_intelligence" in report["recording_coverage"]["evidence_fields_saved"]
    with reopened.connect() as db:
        saved = json.loads(db.execute("SELECT body FROM samples").fetchone()[0])
    assert saved["options"][0]["source_delta"] == .56401


def test_empty_record_does_not_claim_saved_parameters(tmp_path):
    coverage = DayMemory(tmp_path / "empty.sqlite3").report()["recording_coverage"]
    assert coverage["sample_at"] is None
    assert coverage["evidence_fields_saved"] == []
    assert coverage["raw_greeks_rows"] == 0


def test_main_card_shows_reference_fit_and_strike_without_changing_wait(monkeypatch):
    from types import SimpleNamespace as NS
    from ui import components

    rendered = []
    monkeypatch.setattr(components.st, "html", rendered.append)
    monkeypatch.setattr(components, "best_existing_candidate", lambda _: ("CE SELL", 59., NS(available=True), False))
    monkeypatch.setattr(components, "_plan_structure_text", lambda _: "SELL 24,350 CE · HEDGE 24,450 CE")
    item = NS(decision=NS(final_action="WAIT", blocker="Score threshold pending"), market_session=NS(is_live=True))
    components._render_final_action_hero(item, True)
    assert "Fit 59.0%" in rendered[0]
    assert "SELL 24,350 CE" in rendered[0]
    assert "entry confirmed nahi" in rendered[0]
    assert item.decision.final_action == "WAIT"


def test_absent_candidate_does_not_invent_a_strike(monkeypatch):
    from types import SimpleNamespace as NS
    from ui import components

    rendered = []
    monkeypatch.setattr(components.st, "html", rendered.append)
    monkeypatch.setattr(components, "best_existing_candidate", lambda _: ("CE SELL", 0., None, False))
    item = NS(decision=NS(final_action="WAIT", blocker="Unavailable"), market_session=NS(is_live=True))
    components._render_final_action_hero(item, True)
    assert "Koi usable strike setup nahi" in rendered[0]
