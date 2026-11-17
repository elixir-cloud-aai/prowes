"""Celery worker entry point.

Start the worker with `celery -A pro_wes.celery_worker worker`. FOCA replaces
the MongoDB client of the worker before it forks its pool processes, and in
each of them after the fork, so that clients aren't shared across forks
(cf. `foca.Foca.create_celery_app()`); tasks get their collections when
they run (`foca.get_collection()`).
"""

from pathlib import Path

from celery import Celery
from foca import Foca

foca = Foca(
    config_file=Path(__file__).resolve().parent / "config.yaml",
    custom_config_model="pro_wes.config_models.CustomConfig",
)
celery: Celery = foca.create_celery_app()
# Logs of the worker in the format of the app (section `log` of the
# configuration), instead of Celery's own
celery.conf.worker_hijack_root_logger = False
