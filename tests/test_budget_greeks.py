from types import SimpleNamespace as NS
from pathlib import Path
import io
import json
import zipfile
import pandas as pd
import pytest
from analysis.option_chain import validate_greeks
from analysis.trade_plan import calculate_trade_plan
from config import CONFIG


def pair():
    return pd.DataFrame([
        dict(strike=24000, side="CE", delta=.55, gamma=.001, theta=-10, vega=8, implied_volatility=12, oi=100, last_price=100),
        dict(strike=24000, side="PE", delta=-.45, gamma=.001, theta=-8, vega=8, implied_volatility=8, oi=100, last_price=80)])


def test_iv_difference_is_warning_not_missing_greeks():
    original = pair()
    checked = validate_greeks(original)
    assert checked.greeks_quality.eq("IV WARNING").all()
    assert checked.delta.tolist() == original.delta.tolist()
    assert checked.source_delta.tolist() == original.delta.tolist()


@pytest.mark.parametrize("column,value", [("delta", .45), ("gamma", 0), ("vega", float("nan")), ("theta", float("nan"))])
def test_invalid_leg_stays_blocked(column, value):
    rows = pair()
    rows.loc[1, column] = value
    checked = validate_greeks(rows)
    assert checked.loc[1, "greeks_quality"] not in {"READY", "IV WARNING"}
    assert pd.isna(checked.loc[1, "delta"])


def test_delta_consistency_still_hard_blocked():
    rows = pair()
    rows.loc[1, "delta"] = -.9
    assert validate_greeks(rows).greeks_quality.eq("MODEL MISMATCH").all()


def test_authorized_capital_only_risk_and_lots_unchanged():
    assert CONFIG.risk_default_capital == 900000
    assert CONFIG.risk_default_pct == .5
    assert CONFIG.risk_default_max_lots == 1
    assert CONFIG.risk_default_capital * CONFIG.risk_default_pct / 100 == 4500


def obj(value):
    return NS(**{k:obj(v) for k,v in value.items()}) if isinstance(value,dict) else [obj(v) for v in value] if isinstance(value,list) else value


def test_actual_support_bundle_budget_replay():
    path = Path("/workspace/scratch/910dd0ccee78/upload/nifty_seller_lite_support_bundle_20260827_190629_30f3139e.zip")
    if not path.exists():
        pytest.skip("Optional user-provided regression fixture")
    with zipfile.ZipFile(path) as z:
        snapshot = json.loads(z.read("current_snapshot.json"))
        rows = pd.read_csv(io.BytesIO(z.read("option_chain.csv")))
    for name in ("delta", "gamma", "theta", "vega", "implied_volatility"):
        rows[name] = rows["source_" + name]
    rows = validate_greeks(rows)
    # Invalid counterpart no longer contaminates the valid contract. The bad
    # leg remains UNAVAILABLE; no pair-based diagnosis is made from that leg.
    assert rows.greeks_quality.value_counts().to_dict() == {"IV WARNING":20, "UNAVAILABLE":5, "READY":5}
    kwargs = dict(frame=rows, spot=snapshot["nifty_last_price"], expiry=snapshot["expiry"],
                  levels=obj(snapshot["levels"]), options=obj(snapshot["option_intelligence"]),
                  decision=obj(snapshot["decision"]), market_session=obj(snapshot["market_session"]))
    plan = calculate_trade_plan(**kwargs, risk_profile=NS(risk_budget_rupees=4500, lot_size=65, max_lots_cap=1))
    for item in (plan.ce_sell, plan.pe_sell, plan.iron_condor):
        assert item.available
        assert item.max_risk_points * 65 <= 4500
        assert item.status == "REFERENCE ONLY"
    # Recovered healthy candidates may change the chosen spread. Verify its
    # actual defined-risk identity rather than freezing the old blocked pick.
    for spread in (plan.ce_sell, plan.pe_sell):
        width = abs(spread.short_legs[0].strike - spread.hedge_legs[0].strike)
        assert spread.max_risk_points == pytest.approx(width - spread.estimated_credit_points)
        for leg in (*spread.short_legs, *spread.hedge_legs):
            candidate = rows[(rows.strike == leg.strike) & (rows.side == leg.side)]
            assert candidate.greeks_quality.isin(["READY", "IV WARNING"]).all()
    assert plan.selected_setup == "WAIT"
    tiny = calculate_trade_plan(**kwargs, risk_profile=NS(risk_budget_rupees=1, lot_size=65, max_lots_cap=1))
    assert not tiny.ce_sell.available and not tiny.pe_sell.available
    assert "budget" in tiny.ce_sell.blocker
    assert all(not p.plan.available for p in tiny.protected_candidates)
