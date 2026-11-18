"""Credentials (user information) in the URLs of run requests.

As in proTES (`pro_tes.utils.misc`): URLs with user names or passwords
(e.g., `https://user:password@host/path`) are refused or stripped, so that
credentials are neither stored nor forwarded nor returned in run logs (cf.
`custom.post_runs.url_credentials`).
"""

from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pro_wes.exceptions import MalformedUrl, UrlCredentials

UrlCredentialsMode = Literal["reject", "strip"]


def has_credentials(url: str) -> bool:
    """Check whether a URL has credentials (user information).

    Only the authority of a URL (after `//`) has user information: strings
    without it (e.g., `user@example.org` or `mailto:user@example.org`) have
    none.

    Args:
        url: URL.

    Returns:
        Whether the URL has a user name or password (also empty ones, as in
        `https://@host/path`).

    Raises:
        ValueError: The URL is malformed (e.g., an unclosed `[` of an IPv6
            address).
    """
    parts = urlsplit(url)
    return parts.username is not None or parts.password is not None


def strip_auth(url: str) -> str:
    """Remove the user information from a URL, if present.

    `scheme://user:password@host:port/path?query#fragment` becomes
    `scheme://host:port/path?query#fragment`.

    Args:
        url: URL.

    Returns:
        URL without user information.
    """
    elements = list(urlsplit(url))
    elements[1] = elements[1][elements[1].rfind("@") + 1 :]
    return urlunsplit(elements)


def sanitize_url(url: str, field: str, mode: UrlCredentialsMode) -> str | None:
    """Refuse or strip the credentials of a URL.

    Args:
        url: URL, or any string (strings without user information are kept).
        field: Name of the field of the run request, for error messages
            (never the URL, which may hold credentials).
        mode: Whether URLs with credentials are refused (`reject`) or their
            credentials removed (`strip`).

    Returns:
        The URL without credentials, if it had any (`strip`); `None` if it
        had none.

    Raises:
        pro_wes.exceptions.UrlCredentials: The URL has credentials, and
            `mode` is `reject`.
        pro_wes.exceptions.MalformedUrl: The URL is malformed.
    """
    try:
        found = has_credentials(url)
    except ValueError as exc:
        raise MalformedUrl(f"Malformed URL in `{field}`") from exc
    if not found:
        return None
    if mode == "reject":
        raise UrlCredentials(f"URL with credentials in `{field}`")
    return strip_auth(url)


def sanitize_json(value: Any, field: str, mode: UrlCredentialsMode) -> bool:
    """Refuse or strip the credentials of the URLs in decoded JSON, in place.

    Checks the string values of all objects and arrays, at any depth
    (iteratively, so that deeply nested values can't exhaust the stack);
    keys and other values are kept.

    Args:
        value: Decoded JSON (e.g., the workflow parameters); changed in place
            if `mode` is `strip`. A string at the top is not checked, as it
            can't be changed in place.
        field: Name of the field of the run request, for error messages.
        mode: Whether URLs with credentials are refused (`reject`) or their
            credentials removed (`strip`).

    Returns:
        Whether credentials were removed.

    Raises:
        pro_wes.exceptions.UrlCredentials: A URL has credentials, and `mode`
            is `reject`.
        pro_wes.exceptions.MalformedUrl: A URL is malformed.
    """
    changed = False
    stack: list[Any] = [value]
    while stack:
        container = stack.pop()
        if isinstance(container, dict):
            items: Any = container.items()
        elif isinstance(container, list):
            items = enumerate(container)
        else:
            continue
        for key, item in list(items):
            if isinstance(item, str):
                stripped = sanitize_url(item, field, mode)
                if stripped is not None:
                    container[key] = stripped
                    changed = True
            elif isinstance(item, dict | list):
                stack.append(item)
    return changed
