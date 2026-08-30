"""Pydantic models for validating config/schedule.yaml."""
from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, Field, field_validator


class JobDefinition(BaseModel):
    name: str
    script: str
    schedule: str
    args: list[str] = Field(default_factory=list)
    timeout_seconds: int
    enabled: bool = True
    run_weekend: bool = False
    allow_concurrent: bool = False
    max_retries: int = 0
    retry_delay_seconds: int = 0
    env: dict[str, str] = Field(default_factory=dict)
    timezone: str | None = None

    @field_validator("schedule")
    @classmethod
    def validate_cron_expression(cls, value: str) -> str:
        if not croniter.is_valid(value):
            raise ValueError(f"invalid cron expression: {value!r}")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"invalid IANA timezone: {value!r}") from exc
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("timeout_seconds must be > 0")
        return value


class ScheduleFile(BaseModel):
    timezone: str = "UTC"
    jobs: list[JobDefinition] = Field(default_factory=list)

    @field_validator("timezone")
    @classmethod
    def validate_default_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"invalid IANA timezone: {value!r}") from exc
        return value

    @field_validator("jobs")
    @classmethod
    def validate_unique_names(cls, jobs: list[JobDefinition]) -> list[JobDefinition]:
        names = [job.name for job in jobs]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValueError(f"duplicate job names: {sorted(duplicates)}")
        return jobs
