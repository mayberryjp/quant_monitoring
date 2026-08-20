import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("CRON_SCHEDULE_FILE", "config/schedule.yaml")
os.environ.setdefault("CRON_HEARTBEAT_FILE", str(Path(__file__).parent / "tmp_heartbeat.json"))

import pytest


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    return tmp_path
