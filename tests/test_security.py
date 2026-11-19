"""Unit tests of the owners of runs and the checks of authentication."""

import logging
from typing import Any

from connexion import App
from foca import get_config
from foca.models.config import AuthConfig, IssuerConfig
from foca.testing import request_context
import pytest

from pro_wes.exceptions import Forbidden
from pro_wes.security import check_auth, current_owner, owner_of
from tests.conftest import OWNER, USER

ISSUER = "https://login.example.org/"
OTHER_ISSUER = "https://other.example.org/"


def _conf(*issuers: str | IssuerConfig) -> AuthConfig:
    """Get authentication settings trusting issuers.

    Args:
        *issuers: Trusted issuers.

    Returns:
        The settings.
    """
    return AuthConfig(issuers=list(issuers), audience=["prowes"])


@pytest.mark.parametrize(
    ("issuers", "expected"),
    [
        # namespaced by the issuer, also if it is the only one
        ((ISSUER,), f"{ISSUER}|alice"),
        ((ISSUER, OTHER_ISSUER), f"{ISSUER}|alice"),
        # by the namespace of the issuer, if set
        ((IssuerConfig(issuer=ISSUER, namespace="ls"),), "ls|alice"),
        # bare, with an empty namespace
        ((IssuerConfig(issuer=ISSUER, namespace=""),), "alice"),
        # none for untrusted issuers
        ((OTHER_ISSUER,), None),
    ],
)
def test_owner_of(
    issuers: tuple[str | IssuerConfig, ...], expected: str | None
) -> None:
    """Subjects are namespaced by their issuer, or its namespace."""
    assert owner_of(subject="alice", issuer=ISSUER, conf=_conf(*issuers)) == expected


def test_owner_of_bare_subject_with_separator() -> None:
    """Bare subjects with `|` could collide with namespaced ones: no owner."""
    conf = _conf(IssuerConfig(issuer=ISSUER, namespace=""))
    assert owner_of(subject="ls|alice", issuer=ISSUER, conf=conf) is None


def test_owner_of_namespaced_subject_with_separator() -> None:
    """Namespaced subjects may contain `|`: the namespace tells them apart."""
    owner = owner_of(subject="a|b", issuer=ISSUER, conf=_conf(ISSUER))
    assert owner == f"{ISSUER}|a|b"


def test_current_owner(foca_app: App, issuer: Any) -> None:
    """The owner is the namespaced subject of the request's token."""
    claims = {"iss": issuer.url, "sub": USER}
    with request_context(token_info={"claims": claims}, app=foca_app):
        assert current_owner() == OWNER


def test_current_owner_without_token(foca_app: App) -> None:
    """Without validated token (authentication disabled), runs have no owner."""
    with request_context(app=foca_app):
        assert current_owner() is None


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "alice"},
        {"iss": "https://untrusted.example.org/", "sub": "alice"},
        {"iss": "ISSUER", "sub": ""},
        {"iss": "ISSUER", "sub": 42},
        {"iss": "ISSUER"},
    ],
)
def test_current_owner_refused(
    foca_app: App, issuer: Any, claims: dict[str, Any]
) -> None:
    """Tokens without a subject that can be namespaced are refused (`403`)."""
    claims = {
        key: issuer.url if value == "ISSUER" else value for key, value in claims.items()
    }
    with (
        request_context(token_info={"claims": claims}, app=foca_app),
        pytest.raises(Forbidden),
    ):
        current_owner()


def test_check_auth_without_issuers(
    foca_app: App, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Without trusted issuers, a warning is logged."""
    with foca_app.app.app_context():
        monkeypatch.setattr(get_config().security.auth, "issuers", [])
        with caplog.at_level(logging.WARNING, logger="pro_wes.security"):
            check_auth()
    assert "No trusted token issuers" in caplog.text


def test_check_auth_with_issuers(
    foca_app: App, caplog: pytest.LogCaptureFixture
) -> None:
    """With trusted issuers, nothing is logged."""
    with foca_app.app.app_context(), caplog.at_level(logging.WARNING):
        check_auth()
    assert not caplog.records
