"""Entrypoint for the health/ready API process (run under supervisord)."""
from waitress import serve

from cron_runner.api.app import app
from cron_runner.config import settings
from cron_runner.logging import configure_logging


def main() -> None:
    configure_logging(settings.log_level)
    serve(app, host=settings.api_listen_address, port=settings.api_port)


if __name__ == "__main__":
    main()
