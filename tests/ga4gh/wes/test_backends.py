"""Tests of the WES backends (`custom.wes.backends`, `pro_wes.ga4gh.wes.backends`).

Their configuration (`pro_wes.config_models.Wes`), the startup check, the
discovery in service registries (FOCA's, with fake registry responses) and
the selection of the WES instance of new runs.
"""

from collections.abc import Callable, Iterator
from typing import Any

from foca.plugins.backends import API_TYPES, clear_caches, registry
from foca.testing import Client
from pydantic import ValidationError
from pymongo import MongoClient
import pytest

from pro_wes.app import init_app
from pro_wes.config_models import DEFAULT_API_PATH, Wes, WesServiceEntry
from pro_wes.exceptions import NoBackendAvailable
from pro_wes.ga4gh.wes.backends import WES_API, select_backend
from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.ga4gh.wes.models import RunId
from pro_wes.tasks.track_run_progress import task__track_run_progress
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB
from tests.conftest import BACKEND_URL

BASE_PATH = "/ga4gh/wes/v1"
REGISTRY_URL = "https://registry.example.org/ga4gh/registry/v1"
DISCOVERED_URL = "https://found.example.org"
RUN_REQUEST: dict[str, str] = {
    "workflow_params": "{}",
    "workflow_type": "CWL",
    "workflow_type_version": "v1.0",
    "workflow_url": "https://example.org/workflow.cwl",
}


@pytest.fixture(autouse=True)
def _no_registry_caches() -> Iterator[None]:
    """Start and end each test without backends discovered earlier.

    Yields:
        Nothing.
    """
    clear_caches()
    yield
    clear_caches()


@pytest.fixture
def registry_documents(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake the service registry: it lists two verified WES and one TES.

    One of the WES instances is proWES itself (cf. `_registry_overlay()`),
    which is left out.

    Args:
        monkeypatch: Monkeypatch fixture of the test.

    Returns:
        URLs that were read, in order.
    """
    wes_type = {"group": "org.ga4gh", "artifact": "wes", "version": "1.1.0"}
    tes_type = {"group": "org.ga4gh", "artifact": "tes", "version": "1.1.0"}
    services = [
        {
            "id": "self",
            "url": "https://prowes.example.org/ga4gh/wes/v1",
            "type": wes_type,
        },
        {"id": "wes", "url": f"{DISCOVERED_URL}/ga4gh/wes/v1", "type": wes_type},
        {"id": "tes", "url": "https://tes.example.org", "type": tes_type},
    ]
    verifications = [
        {"serviceId": service["id"], "url": service["url"], "type": service["type"]}
        for service in services
    ]
    read: list[str] = []

    def _fetch_json(url: str, *_args: Any, **_kwargs: Any) -> Any:
        """Answer a read of the registry.

        Args:
            url: URL read.
            *_args: Further arguments; ignored.
            **_kwargs: Further arguments; ignored.

        Returns:
            The verifications or the services.
        """
        read.append(url)
        return verifications if url.endswith("/verifications") else services

    monkeypatch.setattr(registry, "fetch_json", _fetch_json)
    return read


def _registry_overlay(first: bool = True) -> str:
    """Get a config overlay with a registry entry and a configured WES.

    Args:
        first: Whether the registry entry comes first.

    Returns:
        YAML text of the overlay.
    """
    entries = [
        "      - type: ga4gh-service-registry\n"
        f"        url: {REGISTRY_URL}\n"
        "        allow_private_addresses: true\n",
        f"      - type: ga4gh-wes\n        url: {BACKEND_URL}\n",
    ]
    if not first:
        entries.reverse()
    return (
        "server:\n  public_url: https://prowes.example.org\n"
        "custom:\n  wes:\n    backends:\n" + "".join(entries)
    )


def test_wes_api_type_registered() -> None:
    """The API type of WES is known to FOCA's backend lists."""
    api_type = API_TYPES[WES_API]
    assert (api_type.group, api_type.artifact) == ("org.ga4gh", "wes")
    assert api_type.path == DEFAULT_API_PATH


def test_config_defaults() -> None:
    """No backend by default; entries default to the API path of WES."""
    assert Wes().backends == []
    entry = WesServiceEntry(type="ga4gh-wes", url="https://WES.example.org/")
    assert entry.url == "https://wes.example.org"
    assert entry.id == entry.url
    assert entry.api_path == DEFAULT_API_PATH
    assert entry.auth == "none"


@pytest.mark.parametrize(
    ("api_path", "expected"),
    [("/wes/v1/", "/wes/v1"), ("/", ""), ("/a.b~c_d-e", "/a.b~c_d-e")],
)
def test_config_api_path(api_path: str, expected: str) -> None:
    """API paths lose their trailing slash; `/` is the URL itself."""
    entry = WesServiceEntry(
        type="ga4gh-wes", url="https://wes.example.org", api_path=api_path
    )
    assert entry.api_path == expected


@pytest.mark.parametrize(
    "api_path", ["wes", "/wes?x=1", "/wes/../admin", "/./wes", "/wes//v1"]
)
def test_config_api_path_invalid(api_path: str) -> None:
    """API paths must be absolute, without queries or dot segments."""
    with pytest.raises(ValidationError, match="API path"):
        WesServiceEntry(
            type="ga4gh-wes", url="https://wes.example.org", api_path=api_path
        )


@pytest.mark.parametrize(
    ("backends", "message"),
    [
        # credentials come with authentication (#120)
        (
            [{"type": "ga4gh-wes", "url": "https://wes.example.org", "auth": "basic"}],
            "auth\n  Input should be 'none'",
        ),
        (
            [
                {
                    "type": "ga4gh-wes",
                    "url": "https://wes.example.org",
                    "auth": "client_credentials",
                }
            ],
            "auth\n  Input should be 'none'",
        ),
        (
            [
                {
                    "type": "ga4gh-service-registry",
                    "url": REGISTRY_URL,
                    "auth": "client_credentials",
                }
            ],
            "auth\n  Input should be 'none'",
        ),
        # TES instances
        (
            [{"type": "ga4gh-tes", "url": "https://tes.example.org"}],
            "does not match any of the expected tags",
        ),
        # user info, other schemes
        (
            [{"type": "ga4gh-wes", "url": "https://user:pw@wes.example.org"}],
            "user info",
        ),
        ([{"type": "ga4gh-wes", "url": "http://wes.example.org"}], "scheme"),
        # the same identifier twice
        (
            [
                {"type": "ga4gh-wes", "url": "https://wes.example.org"},
                {"type": "ga4gh-wes", "url": "https://WES.example.org/"},
            ],
            "same id",
        ),
        # unknown keys
        (
            [{"type": "ga4gh-wes", "url": "https://wes.example.org", "host": "x"}],
            "Extra inputs are not permitted",
        ),
    ],
)
def test_config_invalid(backends: list[dict[str, Any]], message: str) -> None:
    """Invalid entries are refused, for the reason of each case."""
    with pytest.raises(ValidationError, match=message):
        Wes.model_validate({"backends": backends})


def test_config_unknown_key() -> None:
    """Unknown keys of `custom.wes` are refused, e.g., misspelt ones."""
    with pytest.raises(ValidationError):
        Wes.model_validate({"backend": []})


def test_config_http_allowed() -> None:
    """`http` URLs are allowed where an entry allows them (e.g., tests)."""
    config = Wes.model_validate(
        {
            "backends": [
                {
                    "type": "ga4gh-wes",
                    "url": "http://fake-wes:8080",
                    "url_schemes": ["http"],
                }
            ]
        }
    )
    assert config.backends[0].url == "http://fake-wes:8080"


def test_init_app_refuses_own_url(add_overlay: Callable[[str], None]) -> None:
    """The app refuses to start if a configured WES instance is proWES itself."""
    add_overlay(
        "server:\n"
        f"  public_url: {BACKEND_URL}\n"
        "custom:\n  wes:\n    backends:\n"
        f"      - type: ga4gh-wes\n        url: {BACKEND_URL}/\n"
    )
    with pytest.raises(ValueError, match="at or below the public URL"):
        init_app()


def test_init_app_refuses_own_api_path(add_overlay: Callable[[str], None]) -> None:
    """The app refuses to start if a WES API (URL and API path) is its own."""
    add_overlay(
        "server:\n"
        f"  public_url: {BACKEND_URL}/prowes\n"
        "custom:\n  wes:\n    backends:\n"
        f"      - type: ga4gh-wes\n        url: {BACKEND_URL}\n"
        "        api_path: /prowes/ga4gh/wes/v1\n"
    )
    with pytest.raises(ValueError, match="public URL"):
        init_app()


def test_init_app_refuses_registry_without_public_url(
    add_overlay: Callable[[str], None],
) -> None:
    """Registry entries need the public URL of proWES, to leave it out."""
    add_overlay(
        "custom:\n  wes:\n    backends:\n"
        f"      - type: ga4gh-service-registry\n        url: {REGISTRY_URL}\n"
    )
    with pytest.raises(ValueError, match="need the public URL"):
        init_app()


def test_init_app_reads_no_registry(
    add_overlay: Callable[[str], None], registry_documents: list[str]
) -> None:
    """The startup check reads no registry."""
    add_overlay(_registry_overlay())
    init_app()
    assert registry_documents == []


def test_init_app_allows_parent_of_own_url(
    add_overlay: Callable[[str], None],
) -> None:
    """WES instances above the public URL of proWES (e.g., its host) are used."""
    add_overlay(f"server:\n  public_url: {BACKEND_URL}/prowes\n")
    app = init_app()
    with app.app.app_context():
        assert select_backend() == (BACKEND_URL, BASE_PATH)


def test_select_backend_first(add_overlay: Callable[[str], None]) -> None:
    """New runs go to the first WES instance, at its API path."""
    add_overlay(
        "custom:\n  wes:\n    backends:\n"
        "      - type: ga4gh-wes\n        url: https://first.example.org\n"
        "        api_path: /api/\n"
        f"      - type: ga4gh-wes\n        url: {BACKEND_URL}\n"
    )
    app = init_app()
    with app.app.app_context():
        assert select_backend() == ("https://first.example.org", "/api")


def test_select_backend_none(add_overlay: Callable[[str], None]) -> None:
    """Without backends, no WES instance is chosen."""
    add_overlay("custom:\n  wes:\n    backends: []\n")
    app = init_app()
    with app.app.app_context(), pytest.raises(NoBackendAvailable):
        select_backend()


@pytest.mark.parametrize(
    ("first", "expected"),
    [(True, (DISCOVERED_URL, DEFAULT_API_PATH)), (False, (BACKEND_URL, BASE_PATH))],
)
def test_select_backend_discovered(
    add_overlay: Callable[[str], None],
    registry_documents: list[str],
    first: bool,
    expected: tuple[str, str],
) -> None:
    """Registry entries yield their verified WES instances, at their place."""
    add_overlay(_registry_overlay(first=first))
    app = init_app()
    with app.app.app_context():
        assert select_backend() == expected
    assert registry_documents == [
        f"{REGISTRY_URL}/verifications",
        f"{REGISTRY_URL}/services",
    ]


def test_select_backend_registry_empty(
    add_overlay: Callable[[str], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registry that can't be read yields no WES instance."""
    add_overlay(
        "server:\n  public_url: https://prowes.example.org\n"
        "custom:\n  wes:\n    backends:\n"
        f"      - type: ga4gh-service-registry\n        url: {REGISTRY_URL}\n"
    )

    def _unreachable(*_args: Any, **_kwargs: Any) -> Any:
        """Fail to read the registry.

        Args:
            *_args: Arguments; ignored.
            **_kwargs: Arguments; ignored.

        Raises:
            RegistryError: Always.
        """
        raise registry.RegistryError("unreachable")

    monkeypatch.setattr(registry, "fetch_json", _unreachable)
    app = init_app()
    with app.app.app_context(), pytest.raises(NoBackendAvailable):
        select_backend()


def test_post_run_without_backend(
    add_overlay: Callable[[str], None],
    mongodb: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without backends, runs are refused (`503`), and nothing is stored."""
    add_overlay("custom:\n  wes:\n    backends: []\n")
    sent: list[Any] = []
    monkeypatch.setattr(WesClient, "post_run", lambda *args, **_: sent.append(args))
    with Client(init_app()) as client:
        response = client.post(f"{BASE_PATH}/runs", data=RUN_REQUEST)
    assert response.status_code == 503
    assert response.json()["detail"] == "No WES instance is available for new runs."
    assert not sent
    assert mongodb[RUN_DB][RUN_COLLECTION].count_documents({}) == 0


def test_post_run_to_configured_backend(
    add_overlay: Callable[[str], None],
    mongodb: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runs are sent to, stored with and tracked at the chosen WES instance."""
    add_overlay(
        "custom:\n  wes:\n    backends:\n"
        "      - type: ga4gh-wes\n        url: https://wes.example.com\n"
        "        api_path: /\n"
    )
    urls: list[str] = []

    def _post_run(self: WesClient, **_kwargs: Any) -> RunId:
        """Accept a run.

        Args:
            self: WES client.
            **_kwargs: Arguments; ignored.

        Returns:
            Identifier of the run.
        """
        urls.append(self.url)
        return RunId(run_id="REMOTE1")

    tracked: list[dict[str, Any]] = []
    monkeypatch.setattr(WesClient, "post_run", _post_run)
    monkeypatch.setattr(
        task__track_run_progress,
        "apply_async",
        lambda _args, kwargs, **_options: tracked.append(kwargs),
    )
    with Client(init_app()) as client:
        response = client.post(f"{BASE_PATH}/runs", data=RUN_REQUEST)
    assert response.status_code == 200
    assert urls == ["https://wes.example.com"]
    stored = mongodb[RUN_DB][RUN_COLLECTION].find_one({})
    assert stored is not None
    assert stored["wes_endpoint"]["host"] == "https://wes.example.com"
    assert stored["wes_endpoint"]["base_path"] == ""
    assert tracked[0]["remote_host"] == "https://wes.example.com"
    assert tracked[0]["remote_base_path"] == ""
