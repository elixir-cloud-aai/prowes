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

# A user name and password after a scheme and at least one slash or
# backslash, anywhere in a string: also forms that `urlsplit()` doesn't take
# as an authority, but lenient parsers (e.g., WHATWG URL parsers of
# browsers) do, such as `https:/user:password@host` and
# `https:\\user:password@host`, and URLs inside strings (e.g.,
# `--url=https://user:password@host`). Only user information with a password
# (`:`) counts here, so that text such as `?q=a:/b@c` or `C:\me@corp` and
# image digests (`--image=docker://ubuntu@sha256:...`) are kept. Schemes start
# at a boundary only (lookbehind) and the password extends to the last `@`
# of its segment (greedy), so that matching takes linear time
USERINFO = re.compile(
    r"(?<![A-Za-z0-9+.\-])(?P<prefix>[A-Za-z][A-Za-z0-9+.\-]*:[/\\]+)"
    r"[^\s/\\?#:]*:[^\s/\\?#]*@"
)

# Schemes whose user names are no secrets (e.g., `ssh://git@github.com/...`,
# `docker://ubuntu@sha256:...`): URLs of these schemes have credentials only
# if they have a password
USER_NAME_SCHEMES = frozenset({"docker", "git", "git+ssh", "ssh", "ssh+git"})

# Characters that URL parsers remove anywhere in URLs (WHATWG: ASCII tab and
# newline), removed before checking, so that they can't hide credentials
_IGNORED = str.maketrans("", "", "\t\r\n")


def _has_authority_credentials(url: str) -> bool:
    """Check whether the authority of a URL has credentials.

    Args:
        url: URL.

    Returns:
        Whether the authority has a password, or a user name for schemes
        other than those of `USER_NAME_SCHEMES`.

    Raises:
        ValueError: The URL is malformed.
    """
    parts = urlsplit(url)
    if parts.password is not None:
        return True
    return parts.username is not None and parts.scheme not in USER_NAME_SCHEMES


def has_credentials(url: str) -> bool:
    """Check whether a URL has credentials (user information).

    Credentials are a user name or password in the authority of a URL (after
    `//`; user names only for schemes other than those of
    `USER_NAME_SCHEMES`), or a user name and password after any scheme
    followed by slashes or backslashes anywhere in the string (cf.
    `USERINFO`). Tabs and newlines are ignored, as URL parsers remove them.
    Strings such as `user@example.org` or `mailto:user@example.org` have
    none.

    Args:
        url: URL, or any string.

    Returns:
        Whether the URL has credentials (also empty ones, as in
        `https://@host/path`).

    Raises:
        ValueError: The URL is malformed (e.g., an unclosed `[` of an IPv6
            address).
    """
    url = url.translate(_IGNORED)
    return _has_authority_credentials(url) or USERINFO.search(url) is not None


def strip_auth(url: str) -> str:
    """Remove the credentials from a URL, if present.

    `scheme://user:password@host:port/path?query#fragment` becomes
    `scheme://host:port/path?query#fragment`; credentials that `USERINFO`
    finds elsewhere in the string are removed, too, in a single pass. Tabs
    and newlines are removed only if they hide credentials (cf.
    `has_credentials`), so that other text is kept as is.

    Args:
        url: URL, or any string, with credentials.

    Returns:
        URL without credentials.
    """
    stripped = _strip(url)
    if has_credentials(stripped):
        stripped = _strip(url.translate(_IGNORED))
    return stripped


def _strip(url: str) -> str:
    """Remove the credentials from a URL as it is, in a single pass.

    Args:
        url: URL, or any string.

    Returns:
        URL without the credentials that are found without removing tabs and
        newlines.
    """
    if _has_authority_credentials(url):
        elements = list(urlsplit(url))
        elements[1] = elements[1][elements[1].rfind("@") + 1 :]
        url = urlunsplit(elements)
    return USERINFO.sub(r"\g<prefix>", url)


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
            is `reject`; or a key without its credentials (`strip`) is the
            same as another key of its object.
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
                    if stripped_key in container:
                        # refused rather than dropping one of the values
                        raise UrlCredentials(
                            f"Key with credentials in `{field}` collides with"
                            " another key without them"
                        )
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
