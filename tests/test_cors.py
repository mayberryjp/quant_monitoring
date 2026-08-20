from webtest import TestApp

from cron_runner.api.app import app


def test_cors_header_present_on_get():
    client = TestApp(app)
    resp = client.get("/health")
    assert resp.headers["Access-Control-Allow-Origin"] == "*"


def test_cors_preflight_options_request():
    client = TestApp(app)
    resp = client.options("/health")
    assert resp.status_code == 200
    assert resp.headers["Access-Control-Allow-Origin"] == "*"
    assert "GET" in resp.headers["Access-Control-Allow-Methods"]
