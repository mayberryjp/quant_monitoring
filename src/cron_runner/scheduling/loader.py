"""Load and validate the schedule definition file (config/schedule.yaml)."""
from __future__ import annotations

import yaml
from pydantic import ValidationError

from cron_runner.scheduling.models import ScheduleFile


class ScheduleLoadError(Exception):
    """Raised when the schedule file is missing, malformed, or fails validation."""


def load_schedule_file(path: str) -> ScheduleFile:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except OSError as exc:
        raise ScheduleLoadError(f"could not read schedule file {path!r}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ScheduleLoadError(f"invalid YAML in schedule file {path!r}: {exc}") from exc

    try:
        return ScheduleFile.model_validate(raw)
    except ValidationError as exc:
        raise ScheduleLoadError(f"schedule file {path!r} failed validation: {exc}") from exc
