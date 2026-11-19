"""Shared fixtures.

The tests create the app from `pro_wes/config.yaml` and a test overlay that
moves the storage of the runs into a temporary directory, polls without
waiting, configures one WES instance (`BACKEND_URL`) and trusts the local
identity provider of FOCA's fixture `issuer` (for tokens of `AUDIENCE`). They
use FOCA's pytest plugin (`foca.testing.pytest_plugin`): an in-memory
database (mongomock) for each test, or the MongoDB at the URI of the
environment variable `MONGODB_TEST_URI`, and no network access to other
hosts. Remote WES services are replaced by fakes in the tests. The client of
the fixture `foca_client` (and `TokenClient`) sends the access token of
`USER` with every request, unless a request has an `Authorization` header.
"""

from collections.abc import Callable, Iterator, Mapping
import os
from pathlib import Path
from typing import Any

from connexion import App
from foca.database import register_mongodb
from foca.testing import Client
from foca.testing.client import HeaderTypes, Response
from foca.testing.issuer import Issuer
import mongomock
from pymongo import MongoClient
import pytest

pytest_plugins = ["foca.testing.pytest_plugin"]

# The Celery app (`pro_wes.celery_worker`) is created, with its MongoDB
# client, when its module is imported (e.g., with the controllers), so the
# client is replaced by an in-memory one before the test modules are
# imported; the apps of the tests get the client of FOCA's fixture `mongodb`
_IMPORT_CLIENT: MongoClient[dict[str, Any]] = mongomock.MongoClient()


def _import_client(*args: Any, **kwargs: Any) -> MongoClient[dict[str, Any]]:
    """Get the in-memory client used while the test modules are imported.

    Args:
        *args: Positional arguments of `MongoClient`; ignored.
        **kwargs: Keyword arguments of `MongoClient`; ignored.

    Returns:
        In-memory MongoDB client.
    """
    return _IMPORT_CLIENT


register_mongodb.MongoClient = _import_client  # type: ignore[misc, assignment]

# Identifier of the service info of the test overlay
SERVICE_INFO_ID = "org.example.prowes.test"

# WES instance of the test overlay (`custom.wes.backends`), replaced by fakes
BACKEND_URL = "https://wes.example.org"

# Audience of the test overlay (`security.auth.audience`)
AUDIENCE = "prowes-test"

# Subject of the token of `foca_client`
USER = "alice"

# Namespace of the subjects of the identity provider (`security.auth.issuers`)
NAMESPACE = "test"

# Owner of the runs that `USER` creates (cf. `pro_wes.security.owner_of`)
OWNER = f"{NAMESPACE}|{USER}"


class TokenClient(Client):
    """Test client sending an access token with every request.

    Attributes:
        token: Access token, sent as bearer token unless a request has an
            `Authorization` header (e.g., an empty one, for none).
    """

    def __init__(self, app: App, token: str) -> None:
        """Create the client.

        Args:
            app: App under test.
            token: Access token.
        """
        super().__init__(app)
        self.token = token

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: HeaderTypes | None = None,
        **kwargs: Any,
    ) -> Response:
        """Send a request, with the access token unless it has credentials.

        Args:
            method: HTTP method.
            url: Path of the request.
            headers: Headers; an `Authorization` header replaces the token,
                and an empty one is not sent.
            **kwargs: Other arguments of :py:meth:`foca.testing.Client.request`.

        Returns:
            Response.
        """
        items = list(headers.items() if isinstance(headers, Mapping) else headers or [])
        names = {name.lower() for name, _ in items}
        if "authorization" not in names:
            items.append(("Authorization", f"Bearer {self.token}"))
        items = [
            (name, value)
            for name, value in items
            if name.lower() != "authorization" or value
        ]
        return super().request(method, url, headers=items, **kwargs)


@pytest.fixture
def storage_path(tmp_path: Path) -> Path:
    """Get the storage directory of the runs of the test app.

    Args:
        tmp_path: Temporary directory of the test.

    Returns:
        Storage directory; not created yet.
    """
    return tmp_path / "runs"


@pytest.fixture
def app_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    storage_path: Path,
    mongodb: MongoClient[dict[str, Any]],
    issuer: Issuer,
) -> Iterator[None]:
    """Set the environment of the test app: the test overlay and database.

    Args:
        monkeypatch: Monkeypatch fixture of the test.
        tmp_path: Temporary directory of the test.
        storage_path: Storage directory of the runs.
        mongodb: Database of the test (FOCA's fixture).
        issuer: Identity provider trusted by the app (FOCA's fixture).

    Yields:
        Nothing; the environment is restored afterwards.
    """
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "custom:\n"
        "  post_runs:\n"
        f"    storage_path: {storage_path}\n"
        "    polling_wait: 0\n"
        "    polling_attempts: 2\n"
        "  wes:\n"
        "    backends:\n"
        "      - type: ga4gh-wes\n"
        f"        url: {BACKEND_URL}\n"
        "service_info:\n"
        "  service:\n"
        f"    id: {SERVICE_INFO_ID}\n"
        "security:\n"
        "  auth:\n"
        "    issuers:\n"
        f"      - issuer: {issuer.url}\n"
        f"        namespace: {NAMESPACE}\n"
        "    audience:\n"
        f"      - {AUDIENCE}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FOCA_CONFIG_OVERLAY", str(overlay))
    yield


@pytest.fixture
def add_overlay(
    app_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Callable[[str], None]:
    """Get a function that merges further config over the test overlay.

    Args:
        app_env: Environment of the test app, with the test overlay.
        monkeypatch: Monkeypatch fixture of the test.
        tmp_path: Temporary directory of the test.

    Returns:
        Function taking the YAML text of a further overlay, which apps
        created afterwards merge over the test overlay.
    """
    count = 0

    def _add(text: str) -> None:
        """Merge further config over the overlays set so far.

        Args:
            text: YAML text of the overlay.
        """
        nonlocal count
        count += 1
        path = tmp_path / f"overlay-{count}.yaml"
        path.write_text(text, encoding="utf-8")
        overlays = os.environ["FOCA_CONFIG_OVERLAY"]
        monkeypatch.setenv("FOCA_CONFIG_OVERLAY", f"{overlays}:{path}")

    return _add


@pytest.fixture
def foca_app(app_env: None) -> App:
    """Get the app, created as on startup (FOCA's fixture `foca_client`).

    Args:
        app_env: Environment of the test app.

    Returns:
        The app.
    """
    from pro_wes.app import init_app  # noqa: PLC0415 (after the client patch)

    return init_app()


@pytest.fixture
def token(issuer: Issuer) -> str:
    """Get an access token of `USER` for the test app.

    Args:
        issuer: Identity provider trusted by the app.

    Returns:
        The token.
    """
    return issuer.token(sub=USER, aud=AUDIENCE)


@pytest.fixture
def foca_client(foca_app: App, token: str) -> Iterator[TokenClient]:
    """Get a client of the app that sends the access token of `USER`.

    Replaces FOCA's fixture of the same name.

    Args:
        foca_app: The app.
        token: Access token of `USER`.

    Yields:
        The client; closed afterwards.
    """
    with TokenClient(foca_app, token) as client:
        yield client
