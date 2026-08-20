from webtest import TestApp

from cron_runner.api.app import app
from cron_runner.api.routes import runs as runs_route


def test_list_runs_default_params(monkeypatch):
    captured = {}

    def fake_list_runs(**kwargs):
        captured.update(kwargs)
        return [{"execution_id": "abc", "job_name": "job_a", "status": "success"}]

    monkeypatch.setattr(runs_route.run_history, "list_runs", fake_list_runs)

    client = TestApp(app)
    resp = client.get("/runs")

    assert resp.status_code == 200
    assert resp.json["count"] == 1
    assert resp.json["limit"] == 50
    assert resp.json["offset"] == 0
    assert captured["limit"] == 50
    assert captured["job_name"] is None


def test_list_runs_applies_filters(monkeypatch):
    captured = {}

    def fake_list_runs(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(runs_route.run_history, "list_runs", fake_list_runs)

    client = TestApp(app)
    resp = client.get("/runs?job_name=job_a&status=failed&limit=10&offset=5")

    assert resp.status_code == 200
    assert captured["job_name"] == "job_a"
    assert captured["status"] == "failed"
    assert captured["limit"] == 10
    assert captured["offset"] == 5


def test_list_runs_invalid_status_returns_422(monkeypatch):
    client = TestApp(app)
    resp = client.get("/runs?status=bogus", expect_errors=True)

    assert resp.status_code == 422
    assert resp.json["code"] == "validation_error"


def test_list_runs_limit_out_of_range_returns_422():
    client = TestApp(app)
    resp = client.get("/runs?limit=999", expect_errors=True)

    assert resp.status_code == 422


def test_run_detail_found(monkeypatch):
    monkeypatch.setattr(
        runs_route.run_history, "get_run", lambda execution_id: {"execution_id": execution_id}
    )

    client = TestApp(app)
    resp = client.get("/runs/exec-1")

    assert resp.status_code == 200
    assert resp.json["run"]["execution_id"] == "exec-1"


def test_run_detail_not_found(monkeypatch):
    monkeypatch.setattr(runs_route.run_history, "get_run", lambda execution_id: None)

    client = TestApp(app)
    resp = client.get("/runs/missing", expect_errors=True)

    assert resp.status_code == 404
    assert resp.json["code"] == "not_found"


def test_run_detail_internal_error(monkeypatch):
    def raise_error(execution_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(runs_route.run_history, "get_run", raise_error)

    client = TestApp(app)
    resp = client.get("/runs/exec-1", expect_errors=True)

    assert resp.status_code == 500
    assert resp.json["code"] == "internal_error"


def test_latest_run_found(monkeypatch):
    monkeypatch.setattr(
        runs_route.run_history, "get_latest_run", lambda job_name: {"job_name": job_name}
    )

    client = TestApp(app)
    resp = client.get("/jobs/daily_ingestion_report/runs/latest")

    assert resp.status_code == 200
    assert resp.json["run"]["job_name"] == "daily_ingestion_report"


def test_latest_run_not_found(monkeypatch):
    monkeypatch.setattr(runs_route.run_history, "get_latest_run", lambda job_name: None)

    client = TestApp(app)
    resp = client.get("/jobs/no_such_job/runs/latest", expect_errors=True)

    assert resp.status_code == 404
