"""proWES application entry point."""

from contextlib import suppress
from pathlib import Path

from connexion import App
from foca import Foca, get_custom_config
from pymongo.errors import DuplicateKeyError

from pro_wes.config_models import CustomConfig
from pro_wes.exceptions import NotFound
from pro_wes.ga4gh.wes.service_info import ServiceInfo


def init_app() -> App:
    """Initialize FOCA application.

    Creates the storage directory of the runs and stores the service info of
    the configuration (`custom.service_info`) unless the database has one.

    Returns:
        FOCA application.
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
    """
    with app.app.app_context():
        custom_config = get_custom_config(CustomConfig)
        # create storage directory
        work_dir = Path(custom_config.post_runs.storage_path).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        # set service info; JSON values (e.g., URLs as strings), which the
        # database can store
        # (another worker starting at the same time may store it first)
        try:
            ServiceInfo().get_service_info()
        except NotFound:
            with suppress(DuplicateKeyError):
                ServiceInfo().set_service_info(
                    data=custom_config.service_info.model_dump(mode="json")
                )


def run_app(app: App) -> None:
    """Run FOCA application.

    Args:
        app: FOCA application.
    """
    app.run(port=app.port)


if __name__ == "__main__":
    foca_app = init_app()
    run_app(app=foca_app)
