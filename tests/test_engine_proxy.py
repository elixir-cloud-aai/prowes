"""Tests of the integration test's proxy that adds the workflow engine.

Cf. `tests/integration/engine_proxy.py`.
"""

import pytest

from tests.integration.engine_proxy import ENGINE, add_engine

BOUNDARY = "b0und4ry"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"
BODY = (
    f"--{BOUNDARY}\r\n"
    'Content-Disposition: form-data; name="workflow_url"\r\n\r\n'
    "http://workflows:8000/hello.cwl\r\n"
    f"--{BOUNDARY}--\r\n"
).encode()
PART = (
    f"--{BOUNDARY}\r\n"
    'Content-Disposition: form-data; name="workflow_engine"\r\n\r\n'
    f"{ENGINE}\r\n"
).encode()


@pytest.mark.parametrize(
    "content_type",
    [CONTENT_TYPE, f'multipart/form-data; boundary="{BOUNDARY}"'],
)
def test_add_engine(content_type: str) -> None:
    """The engine is added as the last part, with plain or quoted boundary.

    Args:
        content_type: Content type of the request.
    """
    end = BODY.rfind(f"--{BOUNDARY}--".encode())
    assert add_engine(content_type, BODY) == BODY[:end] + PART + BODY[end:]


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        # the field is there already
        (CONTENT_TYPE, PART + BODY),
        # not a multipart form
        ("application/x-www-form-urlencoded", b"workflow_url=x"),
        # no boundary
        ("multipart/form-data", BODY),
        # no closing delimiter
        (CONTENT_TYPE, BODY.replace(f"--{BOUNDARY}--".encode(), b"")),
    ],
)
def test_add_engine_unchanged(content_type: str, body: bytes) -> None:
    """Bodies that the engine can't or needn't be added to stay as they are.

    Args:
        content_type: Content type of the request.
        body: Request body.
    """
    assert add_engine(content_type, body) == body
