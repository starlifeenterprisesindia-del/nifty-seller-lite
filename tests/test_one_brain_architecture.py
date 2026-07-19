from pathlib import Path


def test_analysis_modules_do_not_fetch_data():
    root = Path(__file__).resolve().parents[1]
    for file in (root / "analysis").glob("*.py"):
        text = file.read_text(encoding="utf-8")
        assert "requests." not in text
        assert "DhanClient(" not in text


def test_core_engine_is_evidence_only_and_has_no_strategy_decision():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "decision_engine.py").exists()
    assert not (root / "decision").exists()
    active_files = [root / "app.py", *(root / "analysis").glob("*.py")]
    forbidden = ("FINAL ACTION", "SELL CE", "SELL PE", "IRON CONDOR")
    for file in active_files:
        text = file.read_text(encoding="utf-8").upper()
        for phrase in forbidden:
            assert phrase not in text
