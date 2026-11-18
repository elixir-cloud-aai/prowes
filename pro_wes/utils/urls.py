"""Credentials (user information) in the URLs of run requests.

As in proTES (`pro_tes.utils.misc`): URLs with user names or passwords
(e.g., `https://user:password@host/path`) are refused or stripped, so that
credentials are neither stored nor forwarded nor returned in run logs (cf.
`custom.post_runs.url_credentials`).
"""

import re
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pro_wes.exceptions import MalformedUrl, UrlCredentials

UrlCredentialsMode = Literal["reject", "strip"]

# User information after a scheme and at least one slash or backslash,
# anywhere in a string: also forms that `urlsplit()` doesn't take as an
# authority, but lenient parsers (e.g., WHATWG URL parsers of browsers) do,
# such as `https:/user:password@host`, `https:\\user:password@host` and
# URLs inside strings (e.g., `--url=https://user:password@host`); strings
# such as `mailto:user@example.org` (no slash) don't match
USERINFO = re.compile(r"(?P<prefix>[A-Za-z][A-Za-z0-9+.\-]*:[/\\]+)[^\s/\\?#@]*@")


def has_credentials(url: str) -> bool:
    """Check whether a URL has credentials (user information).

    User information is found in the authority of a URL (after `//`), and
    after any scheme followed by slashes or backslashes anywhere in the
    string (cf. `USERINFO`). Strings such as `user@example.org` or
    `mailto:user@example.org` have none.

    Args:
        url: URL, or any string.

    Returns:
        Whether the URL has a user name or password (also empty ones, as in
        `https://@host/path`).

    Raises:
        ValueError: The URL is malformed (e.g., an unclosed `[` of an IPv6
            address).
    """
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None:
        return True
    return USERINFO.search(url) is not None


def strip_auth(url: str) -> str:
    """Remove the user information from a URL, if present.

    `scheme://user:password@host:port/path?query#fragment` becomes
    `scheme://host:port/path?query#fragment`; user information that
    `USERINFO` finds elsewhere in the string is removed, too.

    Args:
        url: URL, or any string.

    Returns:
        URL without user information.
    """
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None:
        elements = list(parts)
        elements[1] = elements[1][elements[1].rfind("@") + 1 :]
        url = urlunsplit(elements)
    # until none is left, e.g., of `https://a@b@host` (each pass removes one
    # `@`, so this ends)
    while True:
        stripped = USERINFO.sub(r"\g<prefix>", url)
        if stripped == url:
            return url
        url = stripped


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
    except ValueError:
        # without the cause, whose message (logged) may hold credentials
        raise MalformedUrl(f"Malformed URL in `{field}`") from None
    if not found:
        return None
    if mode == "reject":
        raise UrlCredentials(f"URL with credentials in `{field}`")
    return strip_auth(url)


def sanitize_json(value: Any, field: str, mode: UrlCredentialsMode) -> bool:
    """Refuse or strip the credentials of the URLs in decoded JSON, in place.

    Checks the strings of all objects (keys and values) and arrays, at any
    depth (iteratively, so that deeply nested values can't exhaust the
    stack); other values are kept.

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
            for key in list(container):
                stripped_key = sanitize_url(key, field, mode)
                if stripped_key is not None:
                    container[stripped_key] = container.pop(key)
                    changed = True
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
