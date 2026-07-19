from pathlib import Path


def test_analysis_modules_do_not_fetch_data():
    root = Path(__file__).resolve().parents[1]
    for file in (root / "analysis").glob("*.py"):
        text = file.read_text(encoding="utf-8")
        assert "requests." not in text
        assert "DhanClient(" not in text


def test_exactly_one_final_decision_brain_exists():
    root = Path(__file__).resolve().parents[1]
    definitions = []
    for file in root.rglob("*.py"):
        if "tests" in file.parts:
            continue
        text = file.read_text(encoding="utf-8")
        if "def calculate_final_decision(" in text:
            definitions.append(file.relative_to(root).as_posix())
    assert definitions == ["analysis/decision.py"]
    assert not (root / "decision_engine.py").exists()
    assert not (root / "decision").exists()


def test_decision_brain_is_read_only():
    root = Path(__file__).resolve().parents[1]
    text = (root / "analysis" / "decision.py").read_text(encoding="utf-8").upper()
    forbidden = ("PLACE_ORDER", "ORDER PLACEMENT ENABLED", "DHANCLIENT(", "REQUESTS.")
    for phrase in forbidden:
        assert phrase not in text


def test_only_snapshot_service_and_app_touch_state_stores():
    root = Path(__file__).resolve().parents[1]
    option_users = []
    context_users = []
    for file in root.rglob("*.py"):
        if "tests" in file.parts or file.name in {
            "option_state_store.py",
            "context_store.py",
        }:
            continue
        text = file.read_text(encoding="utf-8")
        relative = file.relative_to(root).as_posix()
        if "OptionStateStore" in text:
            option_users.append(relative)
        if "MarketContextStore" in text:
            context_users.append(relative)
    assert set(option_users) == {"app.py", "services/snapshot_service.py"}
    assert set(context_users) == {"app.py", "services/snapshot_service.py"}
