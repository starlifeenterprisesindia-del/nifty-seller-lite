from analysis.canonical_forecast import compatible_strategies
from analysis.entry_guidance import build_entry_guidance


def test_direction_strategy_compatibility_contract():
    assert compatible_strategies("UP") == ("CE BUY", "PE SELL")
    assert compatible_strategies("DOWN") == ("PE BUY", "CE SELL")
    assert compatible_strategies("RANGE") == ("IRON CONDOR",)
    assert compatible_strategies("MIXED") == ()


def test_entry_guidance_never_suggests_naked_short():
    from test_position_guardian import bundle

    guide = build_entry_guidance(bundle().pe_sell, entry_ready=True, live=True)
    assert guide.status == "TAKE NOW (LIMIT)"
    assert "hedge" in guide.instruction.lower()
    assert "naked short kabhi nahi" in guide.instruction.lower()
