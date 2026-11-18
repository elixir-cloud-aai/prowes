"""Tests of the handling of credentials in URLs (`pro_wes.utils.urls`)."""

import time

import pytest

from pro_wes.exceptions import BadRequest, MalformedUrl, UrlCredentials
from pro_wes.utils.urls import (
    has_credentials,
    sanitize_json,
    sanitize_url,
    strip_auth,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://user:pass@example.org/path", True),
        ("https://user@example.org/path", True),
        ("https://:pass@example.org/path", True),
        ("https://@example.org/path", True),
        ("s3://key:secret@bucket/object", True),
        # forms that lenient (WHATWG) parsers take as user information
        ("https:/user:pass@example.org/wf.cwl", True),
        ("https:\\\\user:pass@example.org/x", True),
        ("　https://user:pass@example.org/", True),
        # URLs inside strings, with passwords
        ("--url=https://user:pass@example.org/", True),
        ("see https://u:p@a/ and s3://k:s@b/", True),
        # tabs and newlines, which URL parsers remove
        ("--url=https://u:p\t@h/", True),
        ("https:/u:p\n@h/x", True),
        ("see https://u:p\r\n@h/", True),
        # user names that are no secrets
        ("docker://ubuntu@sha256:abcd", False),
        ("ssh://git@github.com/org/repo", False),
        ("git@github.com:org/repo", False),
        ("ssh://git:pw@github.com/org/repo", True),
        # user names without password inside strings are kept (only whole
        # URLs are checked for them)
        ("--image=docker://ubuntu@sha256:abcd", False),
        ("see https://token@example.org/x", False),
        # text that looks like user information
        ("https://example.org/x?q=a:/b@c", False),
        ("C:\\me@corp\\x", False),
        ("s3://bucket/key@v1", False),
        ("https://example.org/path?user=a@b", False),
        ("https://example.org/x?next=https://example.com/a@b", False),
        ("https://example.org/a@b", False),
        ("user@example.org", False),
        ("mailto:user@example.org", False),
        ("plain text", False),
        ("", False),
    ],
)
def test_has_credentials(url: str, expected: bool) -> None:
    """User names and passwords are found after a scheme and slashes only."""
    assert has_credentials(url) is expected


@pytest.mark.parametrize(
    "text",
    [
        "a" * 500_000,
        "x https://" + "a@" * 250_000,
        "x https://a:" + "a@" * 250_000,
        "a:/" * 166_000,
        "a:/b:" * 100_000,
        "x " + "a1+.-" * 100_000,
    ],
    ids=["letters", "ats", "password-ats", "schemes", "colons", "scheme-chars"],
)
def test_linear_time(text: str) -> None:
    """Long adversarial strings (up to Flask's limit of form fields) are fast."""
    start = time.perf_counter()
    if has_credentials(text):
        strip_auth(text)
    assert time.perf_counter() - start < 1


def test_has_credentials_malformed() -> None:
    """Malformed URLs raise `ValueError`."""
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        has_credentials("https://[user@host/x")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://user:pass@example.org:8443/p?q=1#f",
            "https://example.org:8443/p?q=1#f",
        ),
        ("https://a@b:c@example.org/", "https://example.org/"),
        ("https://@example.org/", "https://example.org/"),
        ("https://example.org/", "https://example.org/"),
        ("https:/user:pass@example.org/x", "https:/example.org/x"),
        ("--url=https://u:p@h/ --in=s3://k:s@b/o", "--url=https://h/ --in=s3://b/o"),
        # to the last `@` of the segment, in one pass
        ("x https://a:b@c@d@h/", "x https://h/"),
        ("x https:/u:p\t@h/", "x https:/h/"),
    ],
)
def test_strip_auth(url: str, expected: str) -> None:
    """The user information is removed, the rest of the URL is kept."""
    assert strip_auth(url) == expected
    assert not has_credentials(strip_auth(url))


def test_sanitize_url() -> None:
    """URLs with credentials are refused or stripped; others are kept."""
    assert sanitize_url("https://example.org/", "f", "reject") is None
    assert sanitize_url("https://u:p@example.org/", "f", "strip") == (
        "https://example.org/"
    )
    with pytest.raises(UrlCredentials, match="`f`"):
        sanitize_url("https://u:p@example.org/", "f", "reject")
    with pytest.raises(MalformedUrl, match="`f`") as info:
        sanitize_url("https://[u@example.org/", "f", "strip")
    # no cause, whose logged message may hold credentials
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__
    assert issubclass(UrlCredentials, BadRequest)
    assert issubclass(MalformedUrl, BadRequest)


def test_sanitize_json() -> None:
    """Strings at any depth, also keys, are checked; other types are kept."""
    value = {
        "https://u:p@example.org/key": [1, None, True, {"x": "https://u@h/"}],
        "y": "https://p@h/",
    }
    with pytest.raises(UrlCredentials):
        sanitize_json(value, "f", "reject")
    with pytest.raises(UrlCredentials):
        sanitize_json({"https://u@h/": "v"}, "f", "reject")
    assert sanitize_json(value, "f", "strip") is True
    assert value == {
        "https://example.org/key": [1, None, True, {"x": "https://h/"}],
        "y": "https://h/",
    }
    assert sanitize_json(value, "f", "reject") is False
    assert sanitize_json("https://u@h/", "f", "strip") is False
    # keys that collide without their credentials are refused
    with pytest.raises(UrlCredentials, match="collides"):
        sanitize_json({"https://u@h/": 1, "https://h/": 2}, "f", "strip")


def test_strip_auth_free_form() -> None:
    """Text around URLs in free-form strings is kept when stripping."""
    # `urlsplit()` takes the user name in the authority, the rest as path
    text = "https://a@b/ and more?q=1 x#y"
    assert has_credentials(text)
    assert strip_auth(text) == "https://b/ and more?q=1 x#y"
    # inside a string, with a password
    text = "see https://a:p@b/ and more"
    assert has_credentials(text)
    assert strip_auth(text) == "see https://b/ and more"


def test_sanitize_json_deep() -> None:
    """Deeply nested values are walked without recursion."""
    inner: list[object] = ["https://u@h/"]
    value: list[object] = inner
    for _ in range(5000):
        value = [value]
    assert sanitize_json(value, "f", "strip") is True
    assert inner == ["https://h/"]
