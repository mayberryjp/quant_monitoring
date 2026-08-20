from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CRON_", extra="ignore")

    schedule_file: str = Field("config/schedule.yaml", validation_alias="CRON_SCHEDULE_FILE")
    poll_interval_seconds: int = Field(60, validation_alias="CRON_POLL_INTERVAL_SECONDS")
    config_reload_seconds: int = Field(0, validation_alias="CRON_CONFIG_RELOAD_SECONDS")
    api_listen_address: str = Field("0.0.0.0", validation_alias="API_LISTEN_ADDRESS")
    api_port: int = Field(8000, validation_alias="API_PORT")
    log_level: str = Field("INFO", validation_alias="LOG_LEVEL")
    database_url: str = Field(..., validation_alias="DATABASE_URL")
    db_pool_size: int = 5
    db_max_overflow: int = 10
    max_output_bytes: int | None = Field(None, validation_alias="CRON_MAX_OUTPUT_BYTES")
    heartbeat_file: str = Field(
        "/tmp/cron_runner_heartbeat.json", validation_alias="CRON_HEARTBEAT_FILE"
    )


settings = Settings()
