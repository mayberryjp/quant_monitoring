from webtest import TestApp

from cron_runner.api.app import app
from cron_runner.api.routes import jobs as jobs_route
from cron_runner.scheduling.models import JobDefinition, ScheduleFile


def _fake_schedule() -> ScheduleFile:
    return ScheduleFile(
        timezone="UTC",
        jobs=[
            JobDefinition(
                name="daily_ingestion_report",
                script="scripts/daily_ingestion_report.py",
                schedule="0 16 * * *",
                timezone="Asia/Tokyo",
                timeout_seconds=300,
                max_retries=1,
            )
        ],
    )


def test_list_jobs_returns_loaded_schedule(monkeypatch):
    monkeypatch.setattr(jobs_route, "load_schedule_file", lambda path: _fake_schedule())

    client = TestApp(app)
    resp = client.get("/jobs")

    assert resp.status_code == 200
    assert resp.json["count"] == 1
    job = resp.json["jobs"][0]
    assert job["name"] == "daily_ingestion_report"
    assert job["timezone"] == "Asia/Tokyo"
    assert job["schedule"] == "0 16 * * *"


def test_list_jobs_returns_500_on_load_error(monkeypatch):
    from cron_runner.scheduling.loader import ScheduleLoadError

    def raise_error(path):
        raise ScheduleLoadError("boom")

    monkeypatch.setattr(jobs_route, "load_schedule_file", raise_error)

    client = TestApp(app)
    resp = client.get("/jobs", expect_errors=True)

    assert resp.status_code == 500
    assert resp.json["status"] == "error"
