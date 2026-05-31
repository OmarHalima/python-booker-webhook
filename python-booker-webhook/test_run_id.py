"""Sequential artifact runId allocation."""
from pathlib import Path

import booker_legacy as app


def test_resolve_explicit_run_id():
    assert app.resolve_run_id({"runId": "curl-test-001"}) == "curl-test-001"


def test_sequential_run_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(app, "RUN_ID_COUNTER_FILE", tmp_path / ".sequential_run_id")
    monkeypatch.setenv("RUN_ID_MODE", "sequential")

    assert app.resolve_run_id({}) == "run-000001"
    assert app.resolve_run_id({}) == "run-000002"
    assert app.resolve_run_id({"runId": "custom"}) == "custom"
    assert app.resolve_run_id({}) == "run-000003"


def test_bootstrap_counter_from_existing_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(app, "RUN_ID_COUNTER_FILE", tmp_path / ".sequential_run_id")
    (tmp_path / "run-000042").mkdir()
    monkeypatch.setenv("RUN_ID_MODE", "sequential")

    assert app.resolve_run_id({}) == "run-000043"


def test_uuid_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ARTIFACTS_DIR", tmp_path)
    monkeypatch.setenv("RUN_ID_MODE", "uuid")
    a = app.resolve_run_id({})
    b = app.resolve_run_id({})
    assert a != b
    assert len(a) == 32
