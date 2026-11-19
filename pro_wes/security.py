"""Authentication of clients: owners of runs, and checks on startup.

The WES API requires access tokens of the trusted issuers
(`security.auth.issuers`), validated by FOCA
(:py:func:`foca.security.auth.validate_token`); `GET /service-info`, the
specification and the Swagger UI are public (cf. `api/additions.openapi.yaml`).

Each run belongs to the caller that created it (:py:func:`current_owner`),
whose runs only it lists, reads and cancels.
"""

import logging

from foca import get_config
from foca.models.config import NAMESPACE_SEPARATOR, AuthConfig
from foca.security.auth import current_claims

from pro_wes.exceptions import Forbidden

logger = logging.getLogger(__name__)


def owner_of(subject: str, issuer: str, conf: AuthConfig) -> str | None:
    """Get the owner of runs of a subject of an issuer.

    Subjects are namespaced by their issuer, as `<namespace>|<subject>`, so
    that equal subjects of different issuers are different owners. The
    namespace is that of the issuer's entry in `security.auth.issuers`
    (`namespace`), by default the issuer itself, also if only one issuer is
    trusted (as FOCA 0.23 does, so that owners stay the same after upgrading
    FOCA, and when another issuer is trusted); issuers with an empty
    namespace keep their subjects bare (cf.
    :py:class:`foca.models.config.IssuerConfig`).

    Args:
        subject: Subject, as issued (e.g., the `sub` claim).
        issuer: Issuer of the token (`iss` claim).
        conf: Authentication settings.

    Returns:
        The owner; `None` if the issuer is not trusted, its namespace (e.g.,
        the issuer itself) contains `|`, or the subject is bare and contains
        `|`, so that owners of different issuers could collide.
    """
    settings = conf.get_issuer_configs().get(issuer)
    if settings is None:
        return None
    namespace = issuer if settings.namespace is None else settings.namespace
    if NAMESPACE_SEPARATOR in namespace:
        return None
    if not namespace:
        return None if NAMESPACE_SEPARATOR in subject else subject
    return f"{namespace}{NAMESPACE_SEPARATOR}{subject}"


def current_owner() -> str | None:
    """Get the owner of runs that the current request creates or accesses.

    Returns:
        The owner (:py:func:`owner_of`) of the subject of the validated token
        of the current request (claim `security.auth.claim_identity`);
        `None` if the request has no validated token, i.e., if
        authentication is disabled (`disable_auth`), so that runs have no
        owner and every client sees all runs.

    Raises:
        pro_wes.exceptions.Forbidden: The token has no subject or one that
            can't be namespaced (cf. :py:func:`owner_of`).
    """
    claims = current_claims()
    if not claims:
        return None
    conf = get_config().security.auth
    subject = claims.get(conf.claim_identity)
    issuer = claims.get("iss")
    owner = None
    if isinstance(subject, str) and subject and isinstance(issuer, str):
        owner = owner_of(subject=subject, issuer=issuer, conf=conf)
    if owner is None:
        logger.warning("Request refused: no owner for the subject of its token")
        raise Forbidden
    return owner


def check_auth() -> None:
    """Log a warning if no issuer is trusted, on startup.

    Without trusted issuers (`security.auth.issuers`, none by default), all
    tokens are rejected, so that only `GET /service-info` can be used. Must
    be called in an app context.
    """
    if not get_config().security.auth.issuer_urls:
        logger.warning(
            "No trusted token issuers (`security.auth.issuers`): all requests"
            " to the WES API but `GET /service-info` are rejected"
        )
