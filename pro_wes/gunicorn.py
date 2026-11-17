"""Gunicorn settings of proWES.

FOCA's settings for apps in containers (:py:mod:`foca.gunicorn_config`; e.g.,
the address `0.0.0.0:8080`, or the port of the environment variable `PORT`),
e.g.::

    gunicorn --config python:pro_wes.gunicorn pro_wes.wsgi:app

The app is not created here, but by each Gunicorn worker after it forked
(`pro_wes.wsgi`), so that it is initialized once per worker and MongoDB
clients aren't shared across forks. The number of workers and threads can
be set with the environment variables `GUNICORN_PROCESSES` and
`GUNICORN_THREADS`, as before; other settings via `GUNICORN_CMD_ARGS`, which
takes precedence over this module.
"""

import os

from foca.gunicorn_config import *  # noqa: F403

# Workers and threads; FOCA's defaults unless set
workers = int(os.environ.get("GUNICORN_PROCESSES", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

# Trust `X-Forwarded-*` headers of any proxy, as before
forwarded_allow_ips = "*"
