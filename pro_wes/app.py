"""App factory, and entry point for development.

The image serves the app with Gunicorn (`pro_wes.wsgi:app`, with proWES's
settings, `pro_wes.gunicorn`); `python -m pro_wes.app` runs it with Flask's
development server, on the host and port of the config (section `server`).
"""

import os
from pathlib import Path

from connexion import App
from foca import Foca, get_custom_config
from foca.api.service_info import get_service_info

from pro_wes.config_models import CustomConfig
from pro_wes.ga4gh.wes.models import ServiceInfo


def init_app() -> App:
    """Initialize FOCA application.

    Creates the storage directory of the runs, checks that it is writable,
    and checks that the service info (config section `service_info`) has the
    fields of the WES specification.

    Returns:
        FOCA application.

    Raises:
        PermissionError: The storage directory of the runs is not writable.
        pydantic.ValidationError: The service info lacks WES fields or has
            invalid ones.
    """
    foca = Foca(
        config_file=Path(__file__).resolve().parent / "config.yaml",
        custom_config_model="pro_wes.config_models.CustomConfig",
    )
    app = foca.create_app()
    _setup_first_start(app=app)
    return app


def _setup_first_start(app: App) -> None:
    """Set up application for first start.

    Args:
        app: FOCA application.

    Raises:
        PermissionError: The storage directory of the runs is not writable.
        pydantic.ValidationError: The service info lacks WES fields or has
            invalid ones.
    """
    with app.app.app_context():
        custom_config = get_custom_config(CustomConfig)
        # create storage directory
        work_dir = Path(custom_config.post_runs.storage_path).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        # Fail on startup rather than on the first `POST /runs` (e.g., on a
        # volume that the image's non-root user can't write to)
        if not os.access(work_dir, os.W_OK | os.X_OK):
            raise PermissionError(
                f"Storage directory of the runs is not writable: {work_dir}"
            )
        # FOCA checks the fields of the GA4GH service info, but not those of
        # the WES specification (e.g., `workflow_type_versions`)
        ServiceInfo(**get_service_info())


def main() -> None:
    """Run the app with Flask's development server.

    On the host and port of the config (`server.host`, `server.port`), with
    its debug mode (`server.debug`). For development; not for production.
    """
    app = init_app()
    app.run()


if __name__ == "__main__":
    main()
