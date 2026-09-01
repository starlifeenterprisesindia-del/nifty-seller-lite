from contextlib import nullcontext
import pandas as pd
from analysis.option_chain import validate_greeks
from test_budget_greeks import pair
from ui.components import render_greeks_health


def test_bad_opposite_contract_does_not_poison_good_contract():
    source = pair()
    source.loc[1, "delta"] = .45
    checked = validate_greeks(source)
    assert checked.loc[0, "greeks_quality"] == "READY"
    assert checked.loc[1, "greeks_quality"] == "UNAVAILABLE"
    assert checked.loc[0, "delta"] == .55
    pd.testing.assert_frame_equal(checked[["oi", "last_price"]], source[["oi", "last_price"]])


def test_different_expiries_not_compared():
    source = pair()
    source["expiry"] = ["2026-09-01", "2026-09-08"]
    source.loc[1, "delta"] = -.9
    assert validate_greeks(source).greeks_quality.eq("READY").all()


def test_diagnostics_collapsed_and_nonmutating(monkeypatch):
    from ui import components
    source = validate_greeks(pair())
    before = source.copy(deep=True)
    events = []
    monkeypatch.setattr(components.st, "caption", lambda text: events.append(("caption", text)))
    monkeypatch.setattr(components.st, "expander", lambda title, expanded: (events.append(("expanded", expanded)) or nullcontext()))
    for method in ("write", "warning", "info", "dataframe"):
        monkeypatch.setattr(components.st, method, lambda *a, **kw: None)
    render_greeks_health(source)
    assert events[0][0] == "caption"
    assert events[1] == ("expanded", False)
    pd.testing.assert_frame_equal(source, before)


def test_oi_direction_survives_unavailable_greeks(tmp_path):
    from datetime import datetime
    from test_option_intelligence import chain, IST
    from services.option_state_store import OptionStateStore
    from analysis.option_intelligence import calculate_option_intelligence
    now = datetime(2026, 7, 20, 10, 0, tzinfo=IST)
    original = chain()
    checked = validate_greeks(original)
    assert checked.greeks_quality.eq("UNAVAILABLE").all()
    state = OptionStateStore(tmp_path / "state.json").make_snapshot(
        captured_at=now, expiry="2026-07-21", spot=24340, frame=original)
    kwargs = dict(spot=24340, expiry="2026-07-21", captured_at=now,
                  history=[], current_snapshot=state, is_live=True)
    before = calculate_option_intelligence(current_frame=original, **kwargs)
    after = calculate_option_intelligence(current_frame=checked, **kwargs)
    assert (after.bullish_score, after.bearish_score, after.range_score) == (
        before.bullish_score, before.bearish_score, before.range_score)
    assert after.status == before.status
