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
    assert "safe_brain_hinglish_line(snapshot, previous_snapshot)" in report
    assert "safe_brain_hinglish_line(snapshot, previous_snapshot)" in ui
    assert "def _brain_hinglish_line" not in ui


def test_main_ai_is_top_screen_and_developer_raw_is_hidden_by_default():
    root = Path(__file__).resolve().parents[1]
    app = (root / "app.py").read_text(encoding="utf-8")
    assert app.index("render_main_ai_market_view(view_snapshot, previous_view_snapshot)") < app.index("render_compact_barrier_map(view_snapshot, previous_view_snapshot)")
    assert 'if os.getenv("NSL_SHOW_DEVELOPER_DATA", "").strip() == "1":' in app
    assert "Delete selected date" not in app


def test_simple_view_prioritizes_final_brain_levels_and_one_compact_setup():
    root = Path(__file__).resolve().parents[1]
    app = (root / "app.py").read_text(encoding="utf-8")
    main = app.index("render_main_ai_market_view(view_snapshot, previous_view_snapshot)")
    levels = app.index("render_compact_barrier_map(view_snapshot, previous_view_snapshot)")
    calculator = app.index("render_spot_premium_calculator(view_snapshot)")
    assert main < levels < calculator
    assert 'with st.expander("Risk & one-trade discipline", expanded=False):' not in app
    assert '"Compact Evidence + Next 5–15 Min Outlook"' in app
    assert 'with persistent_panel(' in app


def test_detailed_screen_uses_one_combined_strategy_audit_without_full_planner_duplicate():
    root = Path(__file__).resolve().parents[1]
    app = (root / "app.py").read_text(encoding="utf-8")
    assert "render_decision(view_snapshot, audit_only=True)" in app
    assert "render_trade_plan(view_snapshot, compact=False)" not in app
    assert "render_execution_guard(view_snapshot)" not in app
    assert "render_position_guardian(view_snapshot)" not in app
