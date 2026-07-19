from pathlib import Path


def test_release_has_no_old_release_documents_or_backup_code():
    root = Path(__file__).resolve().parents[1]
    names = {path.name for path in root.iterdir()}
    forbidden = {
        "DEPLOY_MILESTONE_1.txt",
        "MILESTONE_1_TEST_REPORT.txt",
        "DEPLOY_MILESTONE_2.txt",
        "MILESTONE_2_CHANGELOG.txt",
        "MILESTONE_2_TEST_REPORT.txt",
        "DEPLOY_V0_2_1.txt",
        "V0_2_1_CHANGELOG.txt",
        "V0_2_1_TEST_REPORT.txt",
        "DEPLOY_V0_5.txt",
        "DELETE_OLD_FILES_V0_5.txt",
        "V0_5_CHANGELOG.txt",
        "V0_5_TEST_REPORT.txt",
        "DEPLOY_V0_8.txt",
        "DELETE_OLD_FILES_V0_8.txt",
        "V0_8_CHANGELOG.txt",
        "V0_8_TEST_REPORT.txt",
        "V1_0_UPLOAD_LIST.txt",
        "V1_0_DELETE_LIST.txt",
        "V1_0_CHANGELOG.txt",
        "V1_0_TEST_REPORT.txt",
        "DEPLOY_V1_2.txt",
        "V1_2_UPLOAD_LIST.txt",
        "V1_2_DELETE_LIST.txt",
        "V1_2_CHANGELOG.txt",
        "V1_2_TEST_REPORT.txt",
        "DEPLOY_V1_5.txt",
        "V1_5_UPLOAD_LIST.txt",
        "V1_5_DELETE_LIST.txt",
        "V1_5_CHANGELOG.txt",
        "V1_5_TEST_REPORT.txt",
        "DEPLOY_V1_8.txt",
        "V1_8_UPLOAD_LIST.txt",
        "V1_8_DELETE_LIST.txt",
        "V1_8_CHANGELOG.txt",
        "V1_8_TEST_REPORT.txt",
        "app_old.py",
        "backup.py",
    }
    assert not names.intersection(forbidden)


def test_only_one_snapshot_service_and_one_strategy_brain():
    root = Path(__file__).resolve().parents[1]
    assert len(list(root.glob("services/snapshot_service.py"))) == 1
    assert len(list(root.glob("analysis/decision.py"))) == 1
    assert not (root / "decision_engine.py").exists()
    assert not (root / "decision").exists()


def test_runtime_state_and_cache_files_are_gitignored():
    root = Path(__file__).resolve().parents[1]
    text = (root / ".gitignore").read_text(encoding="utf-8")
    required = {
        "data/instrument_master.csv",
        "data/option_state.json",
        "data/option_state.json.lock",
        "data/market_context.json",
        "data/market_context.json.lock",
        "data/discipline_state.json",
        "data/discipline_state.json.lock",
        ".streamlit/secrets.toml",
        "__pycache__/",
    }
    assert required.issubset(set(text.splitlines()))
