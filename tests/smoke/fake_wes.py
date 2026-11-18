"""Fake WES backend for the Compose smoke test.

Serves the parts of the WES API that proWES uses (service info, run
creation, run logs, run status and cancellation) from memory. Created runs
are `QUEUED`, then `RUNNING` and `COMPLETE` (`RUN_SECONDS` after their
creation), unless their tags hold `fake.hold`: those run until they are
canceled, then are `CANCELING` for `CANCEL_SECONDS` and finally `CANCELED`.
The run log has the run request as received, an output with the received
workflow URL, and, in final states only, a standard output of its own
(`FINAL_STDOUT`) and the exit code once complete, so that the smoke test
sees what proWES forwarded and that it fetched the final run log.
`GET /runs` lists all runs it received.

proWES sends all runs to a hard-coded WES (`run_workflow()` in
`pro_wes/ga4gh/wes/workflow_runs.py`, until #118), so the fake WES takes its
place: it serves HTTPS on port 443, with a certificate for that host
(`HOSTS`) issued by a certificate authority of its own, and Docker Compose
resolves the host to it (`compose.yaml` of this directory). proWES trusts
only that authority (`REQUESTS_CA_BUNDLE`), so that no run reaches the real
WES. It also serves plain HTTP on port 8080, for the health check and the
checks of the smoke test inside the container.

Run it with `python fake_wes.py certs <dir>` to create the certificates in
`<dir>` (`ca.pem`, `server.pem`, `server-key.pem`), then with
`python fake_wes.py serve <dir>` to serve; `python fake_wes.py verify
<count> <key>=<value>...` checks the runs that a running fake WES got
(see `verify()`). It depends on the standard
library and cryptography (a dependency of FOCA).
"""

import datetime
import email.parser
import email.policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import ssl
import sys
import threading
import time
from typing import Any
from urllib.parse import parse_qs
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

BASE_PATH = "/ga4gh/wes/v1"
# Host names of the certificate: the WES that proWES sends all runs to (#118;
# also the network alias of the fake WES in `compose.yaml`), the service name
# in Docker Compose and the loopback host
HOSTS = ["wes-na.cloud.e-infra.cz", "fake-wes", "localhost"]
HTTP_PORT = 8080
HTTPS_PORT = 443
RUN_SECONDS = 2.0
CANCEL_SECONDS = 1.0
SERVICE_INFO = {
    "id": "org.example.fake-wes",
    "name": "Fake WES",
    "type": {"group": "org.ga4gh", "artifact": "wes", "version": "1.0.1"},
    "organization": {"name": "Example Organization", "url": "https://example.org"},
    "version": "1.0.0",
    "workflow_type_versions": {"CWL": {"workflow_type_version": ["v1.0"]}},
    "supported_wes_versions": ["1.0.1"],
    "supported_filesystem_protocols": ["https"],
    "workflow_engine_versions": {"fake": "1.0.0"},
    "default_workflow_engine_parameters": [],
    "system_state_counts": {},
    "auth_instructions_url": "https://example.org/auth",
    "tags": {},
}

# Standard output of runs in final states, which only final run logs have
FINAL_STDOUT = {"COMPLETE": "fake complete", "CANCELED": "fake canceled"}

RUNS: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()


def state_of(run: dict[str, Any], now: float) -> str:
    """Return the state of a run at a time.

    Args:
        run: The run, as stored.
        now: The time, in seconds since the epoch.

    Returns:
        The WES state of the run.
    """
    canceled = run["canceled"]
    if canceled is not None:
        return "CANCELED" if now - canceled >= CANCEL_SECONDS else "CANCELING"
    age = now - run["created"]
    if not run["hold"] and age >= RUN_SECONDS:
        return "COMPLETE"
    return "RUNNING" if age >= RUN_SECONDS / 2 else "QUEUED"


def run_log(run_id: str, run: dict[str, Any], now: float) -> dict[str, Any]:
    """Return the run log of a run.

    Args:
        run_id: Identifier of the run.
        run: The run, as stored.
        now: The time, in seconds since the epoch.

    Returns:
        The run log (WES schema `RunLog`).
    """
    state = state_of(run, now)
    return {
        "run_id": run_id,
        "request": run["request"],
        "state": state,
        "run_log": {
            "name": "fake",
            "cmd": ["fake"],
            "stdout": FINAL_STDOUT.get(state, "fake stdout"),
            "stderr": "fake stderr",
            "exit_code": 0 if state == "COMPLETE" else None,
        },
        "task_logs": [],
        "outputs": {"workflow_url": run["request"].get("workflow_url")},
    }


def parse_form(content_type: str, body: bytes) -> dict[str, str]:
    """Parse a form of a run request.

    Args:
        content_type: Value of the `Content-Type` header.
        body: Request body.

    Returns:
        The form fields (the last value of each), without files.
    """
    if content_type.startswith("multipart/form-data"):
        message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            f"Content-Type: {content_type}\r\n\r\n".encode() + body
        )
        fields: dict[str, str] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if isinstance(name, str) and part.get_filename() is None:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    fields[name] = payload.decode()
        return fields
    return {
        key: values[-1] for key, values in parse_qs(body.decode()).items() if values
    }


class Handler(BaseHTTPRequestHandler):
    """Handler of the WES API requests."""

    def _send(self, status: HTTPStatus, data: Any) -> None:
        """Send a JSON response.

        Args:
            status: HTTP status.
            data: Response body, serialized as JSON.
        """
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, msg: str) -> None:
        """Send an error response (WES schema `ErrorResponse`).

        Args:
            status: HTTP status.
            msg: Error message.
        """
        self._send(status, {"msg": msg, "status_code": status.value})

    def _parts(self) -> list[str] | None:
        """Return the path below the base path, split at slashes.

        Returns:
            The parts of the path, or `None` if it isn't below the base path.
        """
        path = self.path.split("?", 1)[0]
        if path != BASE_PATH and not path.startswith(f"{BASE_PATH}/"):
            return None
        return [part for part in path[len(BASE_PATH) :].split("/") if part]

    def do_GET(self) -> None:
        """Serve the service info, the runs, a run log or a run status."""
        parts = self._parts()
        now = time.time()
        with LOCK:
            if parts == ["service-info"]:
                self._send(HTTPStatus.OK, SERVICE_INFO)
            elif parts == ["runs"]:
                runs = [
                    {"run_id": run_id, "state": state_of(run, now)}
                    for run_id, run in RUNS.items()
                ]
                self._send(HTTPStatus.OK, {"runs": runs, "next_page_token": ""})
            elif parts and len(parts) in (2, 3) and parts[0] == "runs":
                run = RUNS.get(parts[1])
                if run is None:
                    self._error(HTTPStatus.NOT_FOUND, "run not found")
                elif len(parts) == 2:
                    self._send(HTTPStatus.OK, run_log(parts[1], run, now))
                elif parts[2] == "status":
                    self._send(
                        HTTPStatus.OK,
                        {"run_id": parts[1], "state": state_of(run, now)},
                    )
                else:
                    self._error(HTTPStatus.NOT_FOUND, "not found")
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        """Create a run, or cancel one."""
        parts = self._parts()
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        now = time.time()
        with LOCK:
            if parts == ["runs"]:
                form = parse_form(self.headers.get("Content-Type", ""), body)
                missing = [
                    field
                    for field in (
                        "workflow_params",
                        "workflow_type",
                        "workflow_type_version",
                        "workflow_url",
                    )
                    if field not in form
                ]
                if missing:
                    self._error(HTTPStatus.BAD_REQUEST, f"missing fields: {missing}")
                    return
                try:
                    tags = json.loads(form.get("tags") or "{}")
                except ValueError:
                    self._error(HTTPStatus.BAD_REQUEST, "tags are not JSON")
                    return
                run_id = f"FAKE{len(RUNS) + 1}"
                RUNS[run_id] = {
                    "request": form,
                    "created": now,
                    "hold": isinstance(tags, dict) and "fake.hold" in tags,
                    "canceled": None,
                }
                self._send(HTTPStatus.OK, {"run_id": run_id})
            elif parts and len(parts) == 3 and parts[::2] == ["runs", "cancel"]:
                run = RUNS.get(parts[1])
                if run is None:
                    self._error(HTTPStatus.NOT_FOUND, "run not found")
                    return
                if state_of(run, now) not in ("QUEUED", "RUNNING"):
                    self._error(HTTPStatus.BAD_REQUEST, "run not cancelable")
                    return
                run["canceled"] = now
                self._send(HTTPStatus.OK, {"run_id": parts[1]})
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")


def create_certificates(directory: Path) -> None:
    """Create a certificate authority and a server certificate of `HOSTS`.

    Existing certificates are kept, as `docker compose up` runs this again
    for a deployment whose fake WES keeps running with them. The files are
    readable by all, as the fake WES runs as another user than the one
    creating them; the keys are fictitious and only valid within the smoke
    test.

    Args:
        directory: Directory to write `ca.pem`, `server.pem` and
            `server-key.pem` to.
    """
    file_names = ("ca.pem", "server.pem", "server-key.pem")
    if all((directory / name).is_file() for name in file_names):
        return
    now = datetime.datetime.now(datetime.UTC)
    validity = datetime.timedelta(days=365)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fake WES CA")])
    ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + validity)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(ca_ski, critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    names: list[x509.GeneralName] = [x509.DNSName(host) for host in HOSTS]
    names.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTS[0])]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + validity)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        "ca.pem": ca_cert.public_bytes(serialization.Encoding.PEM),
        "server.pem": cert.public_bytes(serialization.Encoding.PEM),
        "server-key.pem": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    }
    for name, content in files.items():
        path = directory / name
        path.write_bytes(content)
        path.chmod(0o644)


def serve(directory: Path) -> None:
    """Serve the fake WES over HTTP and HTTPS until interrupted.

    Args:
        directory: Directory with the certificates of `create_certificates`.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(directory / "server.pem", directory / "server-key.pem")
    https = ThreadingHTTPServer(("", HTTPS_PORT), Handler)
    https.socket = context.wrap_socket(https.socket, server_side=True)
    threading.Thread(target=https.serve_forever, daemon=True).start()
    ThreadingHTTPServer(("", HTTP_PORT), Handler).serve_forever()


def verify(expected: dict[str, str], count: int) -> None:
    """Check the runs that a running fake WES got, via plain HTTP.

    Only the runs whose requests have the expected form fields are checked,
    so that runs of earlier smoke tests on the same deployment are left
    out: there must be `count` of them, as proWES forwards them, and all
    must be finished.

    Args:
        expected: Form fields that the run requests must have.
        count: Number of runs with these fields.

    Raises:
        SystemExit: The check fails.
    """
    base = f"http://localhost:{HTTP_PORT}{BASE_PATH}/runs"
    with urllib.request.urlopen(base) as response:
        runs = json.load(response)["runs"]
    matching = []
    for run in runs:
        url = f"{base}/{run['run_id']}"
        # The URL of the fake WES itself, with the run identifier it issued
        with urllib.request.urlopen(url) as response:  # noqa: S310
            log = json.load(response)
        request = log["request"]
        if all(request.get(key) == value for key, value in expected.items()):
            matching.append(log)
    if len(matching) != count:
        raise SystemExit(
            f"The fake WES got {len(matching)} runs with {expected}, not {count}"
        )
    unfinished = [
        log["run_id"]
        for log in matching
        if log["state"] not in ("COMPLETE", "CANCELED")
    ]
    if unfinished:
        raise SystemExit(f"Runs not finished at the fake WES: {unfinished}")


def main(args: list[str]) -> None:
    """Create the certificates, serve or verify, as the arguments say.

    Args:
        args: Command-line arguments: `certs <dir>`, `serve <dir>` or
            `verify <count> <key>=<value>...` (the number of runs and form
            fields of their requests).

    Raises:
        SystemExit: The arguments are invalid.
    """
    if len(args) >= 3 and args[0] == "verify":
        verify(dict(arg.split("=", 1) for arg in args[2:]), int(args[1]))
    elif len(args) == 2 and args[0] == "certs":
        create_certificates(Path(args[1]))
    elif len(args) == 2 and args[0] == "serve":
        serve(Path(args[1]))
    else:
        raise SystemExit(
            "usage: fake_wes.py certs|serve <dir> | verify <count> <key>=<value>..."
        )


if __name__ == "__main__":
    main(sys.argv[1:])
