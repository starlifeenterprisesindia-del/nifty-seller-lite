from pathlib import Path


def test_release_has_no_old_milestone_documents_or_backup_code():
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
        "app_old.py",
        "backup.py",
    }
    assert not names.intersection(forbidden)


def test_only_one_snapshot_service_and_no_strategy_engine():
    root = Path(__file__).resolve().parents[1]
    assert len(list(root.glob("services/snapshot_service.py"))) == 1
    assert not (root / "decision_engine.py").exists()
    assert not (root / "decision").exists()
