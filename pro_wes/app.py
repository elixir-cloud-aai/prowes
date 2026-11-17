"""proWES application entry point."""

from pathlib import Path

from connexion import App
from foca import Foca, get_custom_config
from foca.api.service_info import get_service_info

from pro_wes.config_models import CustomConfig
from pro_wes.ga4gh.wes.models import ServiceInfo


def init_app() -> App:
    """Initialize FOCA application.

    Creates the storage directory of the runs and checks that the service
    info (config section `service_info`) has the fields of the WES
    specification.

    Returns:
        FOCA application.

    Raises:
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
        pydantic.ValidationError: The service info lacks WES fields or has
            invalid ones.
    """
    with app.app.app_context():
        custom_config = get_custom_config(CustomConfig)
        # create storage directory
        work_dir = Path(custom_config.post_runs.storage_path).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        # FOCA checks the fields of the GA4GH service info, but not those of
        # the WES specification (e.g., `workflow_type_versions`)
        ServiceInfo(**get_service_info())


def run_app(app: App) -> None:
    """Run FOCA application.

    Args:
        app: FOCA application.
    """
    app.run(port=app.port)


if __name__ == "__main__":
    foca_app = init_app()
    run_app(app=foca_app)
