"""Tests of the entry points of the Gunicorn server and the Celery worker."""

from collections.abc import Iterator
import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pro_wes.celery_worker import celery
import pro_wes.gunicorn
import pro_wes.tasks.track_run_progress

ROOT = Path(__file__).parents[1]


@pytest.fixture
def reload_gunicorn() -> Iterator[None]:
    """Reload the Gunicorn settings again after the test.

    Yields:
        Nothing; the settings are reloaded with the restored environment.
    """
    yield
    importlib.reload(pro_wes.gunicorn)


@pytest.mark.usefixtures("reload_gunicorn")
@pytest.mark.parametrize(
    ("env", "workers", "threads"),
    [({}, 1, 4), ({"GUNICORN_PROCESSES": "3", "GUNICORN_THREADS": "2"}, 3, 2)],
)
def test_gunicorn_settings_from_foca(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], workers: int, threads: int
) -> None:
    """Gunicorn gets FOCA's settings, with workers and threads from the env."""
    monkeypatch.delenv("GUNICORN_PROCESSES", raising=False)
    monkeypatch.delenv("GUNICORN_THREADS", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    settings = importlib.reload(pro_wes.gunicorn)
    assert settings.bind == ["0.0.0.0:8080"]
    assert settings.worker_class == "gthread"
    assert settings.workers == workers
    assert settings.threads == threads
    assert settings.forwarded_allow_ips == "*"


def test_gunicorn_settings_create_no_app() -> None:
    """Loading the settings doesn't create the app (once per worker only)."""
    code = (
        "import sys, pro_wes.gunicorn; "
        "assert 'pro_wes.app' not in sys.modules; "
        "assert 'pro_wes.wsgi' not in sys.modules"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("MONGO_")}
    subprocess.run(  # noqa: S603 (the interpreter of the tests)
        [sys.executable, "-c", code], check=True, cwd=ROOT, env=env
    )


def test_celery_worker() -> None:
    """The worker logs in the format of the app and has the tracking task."""
    assert celery.conf.worker_hijack_root_logger is False
    assert "tasks.track_run_progress" in celery.tasks
