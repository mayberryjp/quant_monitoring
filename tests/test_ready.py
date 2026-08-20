from webtest import TestApp

from cron_runner.api.app import app
from cron_runner.api.routes import health as health_route


def test_ready_returns_503_when_no_heartbeat(monkeypatch):
    monkeypatch.setattr(health_route, "read_heartbeat", lambda: None)
    monkeypatch.setattr(health_route, "check_database", lambda: (True, "ok"))

    client = TestApp(app)
    resp = client.get("/ready", expect_errors=True)

    assert resp.status_code == 503
    assert resp.json["status"] == "error"


def test_ready_returns_503_when_database_unreachable(monkeypatch):
    monkeypatch.setattr(
        health_route,
        "read_heartbeat",
        lambda: {"schedule_loaded": True, "heartbeat_at": "2026-08-20T02:00:00+00:00", "job_count": 1},
    )
    monkeypatch.setattr(health_route, "heartbeat_age_seconds", lambda payload: 0.1)
    monkeypatch.setattr(health_route, "check_database", lambda: (False, "database check failed: OperationalError"))

    client = TestApp(app)
    resp = client.get("/ready", expect_errors=True)

    assert resp.status_code == 503


def test_ready_returns_200_when_healthy(monkeypatch):
    monkeypatch.setattr(
        health_route,
        "read_heartbeat",
        lambda: {"schedule_loaded": True, "heartbeat_at": "2026-08-20T02:00:00+00:00", "job_count": 2},
    )
    monkeypatch.setattr(health_route, "heartbeat_age_seconds", lambda payload: 0.1)
    monkeypatch.setattr(health_route, "check_database", lambda: (True, "ok"))

    client = TestApp(app)
    resp = client.get("/ready")

    assert resp.status_code == 200
    assert resp.json["status"] == "ok"
    assert resp.json["job_count"] == 2
