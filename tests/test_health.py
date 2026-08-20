from webtest import TestApp

from cron_runner.api.app import app


def test_health_returns_ok():
    client = TestApp(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json == {"status": "ok", "service": "cron-runner-api"}
