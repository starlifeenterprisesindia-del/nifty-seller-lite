from pathlib import Path


def test_summary_presenter_is_read_only_and_not_a_second_brain():
    root = Path(__file__).resolve().parents[1]
    text = (root / "services" / "summary_presenter.py").read_text(encoding="utf-8")
    assert "calculate_final_decision(" not in text
    assert "DhanClient(" not in text
    assert "requests." not in text
    assert "OptionStateStore" not in text
    assert "MarketContextStore" not in text
    assert "DisciplineStore" not in text


def test_pdf_uses_same_presentation_summary_and_not_a_duplicate_explanation_engine():
    root = Path(__file__).resolve().parents[1]
    report = (root / "services" / "pdf_report.py").read_text(encoding="utf-8")
    ui = (root / "ui" / "components.py").read_text(encoding="utf-8")
    assert "brain_hinglish_line(snapshot)" in report
    assert "brain_hinglish_line(snapshot)" in ui
    assert "def _brain_hinglish_line" not in ui


def test_main_ai_is_top_screen_and_developer_raw_is_hidden_by_default():
    root = Path(__file__).resolve().parents[1]
    app = (root / "app.py").read_text(encoding="utf-8")
    assert app.index("render_main_ai_market_view(snapshot, previous_snapshot)") < app.index("render_barrier_map(snapshot)")
    assert 'if os.getenv("NSL_SHOW_DEVELOPER_DATA", "").strip() == "1":' in app
    assert "Delete selected date" not in app
