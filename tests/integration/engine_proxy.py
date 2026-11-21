"""Proxy in front of Sapporo that chooses the workflow engine of runs.

Sapporo requires the field `workflow_engine` of WES 1.1.0 in run requests
(e.g., `cwltool`), which WES 1.1.0 makes optional. proWES doesn't forward it
yet (#66): it refuses run requests that set it. Until it does, the
integration test sends runs to Sapporo through this proxy (`compose.yaml` of
this directory), which adds the field to forwarded run requests that don't
have it (`POST /runs` with a `multipart/form-data` body, as proWES sends
them), with the engine `ENGINE`, and passes all other requests on as they
are. `check.sh` checks that Sapporo still rejects runs without the field, so
that the proxy is dropped once it doesn't (or once proWES forwards the
field).

Run it with `python engine_proxy.py`: it serves plain HTTP on port `PORT`
and forwards requests to `UPSTREAM`. It depends on the standard library
only. Never publish it outside the Compose network of the integration test.
"""

from email.message import Message
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import urllib.error
import urllib.parse
import urllib.request

# Sapporo, at its service name and port in Docker Compose
UPSTREAM = "http://sapporo:1122"
PORT = 1122
# The engine of the workflow type of the test (CWL)
ENGINE = "cwltool"
TIMEOUT = 60
# Request and response headers that are passed on
REQUEST_HEADERS = ("Accept", "Authorization", "Content-Type")
RESPONSE_HEADERS = ("Content-Type",)
_BOUNDARY = re.compile(r'boundary="?([^";]+)"?')


def add_engine(content_type: str, body: bytes) -> bytes:
    """Add the field `workflow_engine` to a run request without it.

    Args:
        content_type: Value of the `Content-Type` header of the request.
        body: Request body.

    Returns:
        The body with a `workflow_engine` part (`ENGINE`) before its closing
        delimiter; unchanged if the body isn't `multipart/form-data`, has
        no closing delimiter or has the field already.
    """
    match = _BOUNDARY.search(content_type)
    if not content_type.startswith("multipart/form-data") or match is None:
        return body
    if b'name="workflow_engine"' in body:
        return body
    delimiter = b"--" + match.group(1).encode()
    end = body.rfind(delimiter + b"--")
    if end < 0:
        return body
    part = (
        delimiter
        + b'\r\nContent-Disposition: form-data; name="workflow_engine"\r\n\r\n'
        + ENGINE.encode()
        + b"\r\n"
    )
    return body[:end] + part + body[end:]


class Handler(BaseHTTPRequestHandler):
    """Handler that forwards requests to Sapporo."""

    def _forward(self, method: str) -> None:
        """Forward the request, and send Sapporo's response.

        Request bodies are read by their `Content-Length` (proWES sends no
        chunked bodies).

        Args:
            method: HTTP method of the request.
        """
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        content_type = self.headers.get("Content-Type", "")
        path = urllib.parse.urlsplit(self.path).path.rstrip("/")
        if method == "POST" and path == "/runs" and body:
            body = add_engine(content_type, body)
        request = urllib.request.Request(  # noqa: S310 (fixed http URL)
            UPSTREAM + self.path, data=body, method=method
        )
        for name in REQUEST_HEADERS:
            value = self.headers.get(name)
            if value is not None:
                request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 (fixed http URL)
                self._send(response.status, response.headers, response.read())
        except urllib.error.HTTPError as error:
            self._send(error.code, error.headers, error.read())
        except OSError as error:
            message = f"upstream unavailable: {error}".encode()
            self._send(HTTPStatus.BAD_GATEWAY, Message(), message)

    def _send(self, status: int, headers: Message, body: bytes) -> None:
        """Send a response.

        Args:
            status: HTTP status.
            headers: Response headers of Sapporo.
            body: Response body.
        """
        self.send_response(status)
        for name in RESPONSE_HEADERS:
            value = headers.get(name)
            if value is not None:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        """Forward a `GET` request."""
        self._forward("GET")

    def do_POST(self) -> None:
        """Forward a `POST` request."""
        self._forward("POST")

    def do_DELETE(self) -> None:
        """Forward a `DELETE` request."""
        self._forward("DELETE")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104 (Compose network only)
