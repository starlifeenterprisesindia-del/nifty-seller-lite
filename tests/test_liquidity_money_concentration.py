from types import SimpleNamespace

import pandas as pd

from analysis.liquidity_intelligence import _money_concentration


def test_money_concentration_prefers_heavier_upside_option_pool():
    chain = pd.DataFrame([
        {"side": "CE", "strike": 22650, "oi": 8_000_000, "day_oi_change": 2_200_000, "volume": 150_000_000},
        {"side": "CE", "strike": 22700, "oi": 6_000_000, "day_oi_change": 1_400_000, "volume": 120_000_000},
        {"side": "CE", "strike": 22750, "oi": 2_000_000, "day_oi_change": 200_000, "volume": 30_000_000},
        {"side": "PE", "strike": 22600, "oi": 3_000_000, "day_oi_change": 300_000, "volume": 40_000_000},
        {"side": "PE", "strike": 22550, "oi": 2_500_000, "day_oi_change": 150_000, "volume": 30_000_000},
        {"side": "PE", "strike": 22500, "oi": 1_000_000, "day_oi_change": 50_000, "volume": 10_000_000},
    ])
    snapshot = SimpleNamespace(option_chain=chain)
    result = _money_concentration(snapshot, spot=22620.0, atr=12.0)

    assert result.bias == "UPSIDE"
    assert result.upside_score > result.downside_score
    assert result.primary_zone is not None
    assert result.primary_zone.side == "UPSIDE"
    assert result.primary_zone.strike in {22650.0, 22700.0}


def test_money_concentration_is_proxy_not_exact_money():
    snapshot = SimpleNamespace(option_chain=pd.DataFrame())
    result = _money_concentration(snapshot, spot=22620.0, atr=12.0)
    assert result.bias == "UNCLEAR"
    assert "proxy" in result.cautions[0].lower()
