from bottle import Bottle, response

from cron_runner.api.routes.health import SERVICE_NAME, register_health_routes
from cron_runner.api.routes.jobs import register_job_routes
from cron_runner.api.routes.runs import register_run_routes


def create_app() -> Bottle:
    app = Bottle()
    app.title = SERVICE_NAME

    register_health_routes(app)
    register_job_routes(app)
    register_run_routes(app)

    @app.error(404)
    def not_found(_err: object) -> dict:
        response.content_type = "application/json"
        return {"status": "error", "code": "not_found", "error": "not found"}

    return app


app = create_app()
