import pytest

from cron_runner.scheduling.loader import ScheduleLoadError, load_schedule_file


def test_load_valid_schedule(tmp_path):
    path = tmp_path / "schedule.yaml"
    path.write_text(
        """
timezone: "UTC"
jobs:
  - name: "job_a"
    script: "scripts/job_a.py"
    schedule: "*/5 * * * *"
    timeout_seconds: 30
"""
    )
    schedule = load_schedule_file(str(path))
    assert schedule.timezone == "UTC"
    assert len(schedule.jobs) == 1
    assert schedule.jobs[0].name == "job_a"
    assert schedule.jobs[0].allow_concurrent is False


def test_missing_file_raises(tmp_path):
    with pytest.raises(ScheduleLoadError):
        load_schedule_file(str(tmp_path / "does_not_exist.yaml"))


def test_invalid_cron_expression_raises(tmp_path):
    path = tmp_path / "schedule.yaml"
    path.write_text(
        """
jobs:
  - name: "job_a"
    script: "scripts/job_a.py"
    schedule: "not a cron expression"
    timeout_seconds: 30
"""
    )
    with pytest.raises(ScheduleLoadError):
        load_schedule_file(str(path))


def test_duplicate_job_names_raise(tmp_path):
    path = tmp_path / "schedule.yaml"
    path.write_text(
        """
jobs:
  - name: "dupe"
    script: "scripts/a.py"
    schedule: "* * * * *"
    timeout_seconds: 30
  - name: "dupe"
    script: "scripts/b.py"
    schedule: "* * * * *"
    timeout_seconds: 30
"""
    )
    with pytest.raises(ScheduleLoadError):
        load_schedule_file(str(path))


def test_missing_required_field_raises(tmp_path):
    path = tmp_path / "schedule.yaml"
    path.write_text(
        """
jobs:
  - name: "job_a"
    script: "scripts/job_a.py"
    schedule: "* * * * *"
"""
    )
    with pytest.raises(ScheduleLoadError):
        load_schedule_file(str(path))
