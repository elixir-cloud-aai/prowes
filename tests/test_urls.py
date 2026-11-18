"""Tests of the handling of credentials in URLs (`pro_wes.utils.urls`)."""

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
        ("https://example.org/path?user=a@b", False),
        ("https://example.org/a@b", False),
        ("user@example.org", False),
        ("mailto:user@example.org", False),
        ("plain text", False),
        ("", False),
    ],
)
def test_has_credentials(url: str, expected: bool) -> None:
    """User names and passwords are found in the authority of URLs only."""
    assert has_credentials(url) is expected


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
    with pytest.raises(MalformedUrl, match="`f`"):
        sanitize_url("https://[u@example.org/", "f", "strip")
    assert issubclass(UrlCredentials, BadRequest)
    assert issubclass(MalformedUrl, BadRequest)


def test_sanitize_json() -> None:
    """String values at any depth are checked; keys and other types are kept."""
    value = {
        "https://u:p@example.org/key": [1, None, True, {"x": "https://u@h/"}],
        "y": "https://p@h/",
    }
    with pytest.raises(UrlCredentials):
        sanitize_json(value, "f", "reject")
    assert sanitize_json(value, "f", "strip") is True
    assert value == {
        "https://u:p@example.org/key": [1, None, True, {"x": "https://h/"}],
        "y": "https://h/",
    }
    assert sanitize_json(value, "f", "reject") is False
    assert sanitize_json("https://u@h/", "f", "strip") is False


def test_sanitize_json_deep() -> None:
    """Deeply nested values are walked without recursion."""
    inner: list[object] = ["https://u@h/"]
    value: list[object] = inner
    for _ in range(5000):
        value = [value]
    assert sanitize_json(value, "f", "strip") is True
    assert inner == ["https://h/"]
