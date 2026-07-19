from pathlib import Path


def test_analysis_modules_do_not_fetch_data():
    root = Path(__file__).resolve().parents[1]
    for file in (root / "analysis").glob("*.py"):
        text = file.read_text(encoding="utf-8")
        assert "requests." not in text
        assert "DhanClient(" not in text


def test_no_decision_engine_in_foundation():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "decision_engine.py").exists()
    app_text = (root / "app.py").read_text(encoding="utf-8")
    assert "FINAL ACTION" not in app_text
    assert "SELL CE" not in app_text
    assert "SELL PE" not in app_text
