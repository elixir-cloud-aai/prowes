"""Tests of the entry points of the Gunicorn server and the Celery worker."""

import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

import pro_wes.gunicorn
import pro_wes.tasks.track_run_progress  # noqa: F401 (registers the task)
from pro_wes.celery_worker import celery

ROOT = Path(__file__).parents[1]


def test_gunicorn_settings_from_foca(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gunicorn gets FOCA's settings, with workers and threads from the env."""
    monkeypatch.setenv("GUNICORN_PROCESSES", "3")
    monkeypatch.setenv("GUNICORN_THREADS", "2")
    settings = importlib.reload(pro_wes.gunicorn)
    assert settings.bind == ["0.0.0.0:8080"]
    assert settings.worker_class == "gthread"
    assert settings.workers == 3
    assert settings.threads == 2
    assert settings.forwarded_allow_ips == "*"
    monkeypatch.delenv("GUNICORN_PROCESSES")
    monkeypatch.delenv("GUNICORN_THREADS")
    settings = importlib.reload(pro_wes.gunicorn)
    assert settings.workers == 1
    assert settings.threads == 4


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
