"""Fake WES backend for the Compose smoke test.

Serves the parts of the WES API that proWES uses (service info, run
creation, run logs, run status and cancellation) from memory. Created runs
are `QUEUED`, then `RUNNING` and `COMPLETE` (`RUN_SECONDS` after their
creation), unless their tags hold `fake.hold`: those run until they are
canceled, then are `CANCELING` for `CANCEL_SECONDS` and finally `CANCELED`.
The run log has the run request as received, outputs with the received
workflow URL and workflow attachments (file names and contents, in
order), and, in
final states only, a standard output of its own
(`FINAL_STDOUT`) and the exit code once complete, so that the smoke test
sees what proWES forwarded and that it fetched the final run log.
`GET /runs` lists all runs it received.

It implements WES 1.0.1, or the version of the environment variable
`FAKE_WES_VERSION` (`1.1.0`), in its service info (`service_info()`). As
WES 1.0.1, it rejects run requests with fields that WES 1.0.1 doesn't have
(`400`), as some WES instances do, so that the smoke test sees that proWES
forwards runs in the version of each WES instance.

proWES sends runs to the WES instance of the smoke test's config
(`custom.wes.backends` in `config.yaml` of this directory): the fake WES,
at its service name in Docker Compose. It serves HTTPS on port 443, with a
certificate for that host (`HOSTS`) issued by a certificate authority of
its own, which proWES trusts instead of the public ones
(`REQUESTS_CA_BUNDLE`, `compose.yaml` of this directory), so that the
smoke test forwards runs with TLS verification on, as deployments do. It
also serves plain HTTP on port 8080, for the health check and the checks of
the smoke test inside the container.

It is also the identity provider of the smoke test (`ISSUER`, trusted by
proWES in `config.yaml` of this directory): it serves its OpenID Connect
discovery document and keys below `/oidc/`, and access tokens of any user,
for the audience `AUDIENCE`, at `GET /oidc/token?sub=<user>` (no login; for
the smoke test only). Its WES API needs no token (proWES has no credentials
for WES instances yet, #182).

Run it with `python fake_wes.py certs <dir>` to create the certificates in
`<dir>` (`ca.pem`, `server.pem`, `server-key.pem`), then with
`python fake_wes.py serve <dir>` to serve; `python fake_wes.py verify
<count> <key>=<value>...` checks the runs that a running fake WES got
(see `verify()`), and `python fake_wes.py token <user>` prints an access
token of a user. It depends on the standard library, cryptography and PyJWT
(dependencies of FOCA).
"""

import datetime
import email.parser
import email.policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import ssl
import sys
import threading
import time
from typing import Any
from urllib.parse import parse_qs
import urllib.request
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
import jwt

BASE_PATH = "/ga4gh/wes/v1"
# Host names of the certificate: the service names in Docker Compose (the
# WES instances of the smoke test's config, `config.yaml`: of WES 1.0.1 and
# 1.1.0) and the loopback host
HOSTS = ["fake-wes", "fake-wes-11", "localhost"]
HTTP_PORT = 8080
HTTPS_PORT = 443
RUN_SECONDS = 2.0
# Identity provider: issuer (over HTTPS, at the service name), audience of
# its tokens (both as trusted by proWES, `security.auth` in `config.yaml`),
# lifetime of tokens in seconds, and its signing key (new for each start, so
# tokens of an earlier start are rejected). Its token endpoint issues tokens
# to anyone who reaches it: never publish the fake WES outside the Compose
# network of the smoke test
ISSUER = "https://fake-wes/oidc/"
IDP_PATH = "/oidc/"
AUDIENCE = "prowes-smoke"
TOKEN_SECONDS = 3600
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
# New for each start too, so that proWES fetches the new key after a restart
KEY_ID = uuid.uuid4().hex
CANCEL_SECONDS = 1.0
SERVICE_INFO: dict[str, Any] = {
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

# Form fields of run requests of WES 1.0.1 (WES 1.1.0 adds the workflow
# engine and its version)
FIELDS_1_0 = frozenset(
    {
        "workflow_params",
        "workflow_type",
        "workflow_type_version",
        "tags",
        "workflow_engine_parameters",
        "workflow_url",
        "workflow_attachment",
    }
)


def version() -> str:
    """Get the WES version of the fake WES.

    Returns:
        The version: `1.0.1`, unless `FAKE_WES_VERSION` sets `1.1.0`.
    """
    return "1.1.0" if os.environ.get("FAKE_WES_VERSION") == "1.1.0" else "1.0.1"


def service_info() -> dict[str, Any]:
    """Get the service info, in the WES version of the fake WES.

    Returns:
        The service info (WES 1.1.0: the versions of each engine as objects).
    """
    if version() == "1.0.1":
        return SERVICE_INFO
    return {
        **SERVICE_INFO,
        "type": {**SERVICE_INFO["type"], "version": "1.1.0"},
        "supported_wes_versions": ["1.1.0"],
        "workflow_engine_versions": {"fake": {"workflow_engine_version": ["1.0.0"]}},
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
        "outputs": {
            "workflow_url": run["request"].get("workflow_url"),
            "attachments": run["attachments"],
        },
    }


def parse_form(
    content_type: str, body: bytes
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Parse a form of a run request.

    Args:
        content_type: Value of the `Content-Type` header.
        body: Request body.

    Returns:
        The form fields (the last value of each), without files, and the
        workflow attachments: file name and contents (as text) of each, in
        order.
    """
    if content_type.startswith("multipart/form-data"):
        message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            f"Content-Type: {content_type}\r\n\r\n".encode() + body
        )
        fields: dict[str, str] = {}
        attachments: list[tuple[str, str]] = []
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            payload = part.get_payload(decode=True)
            if not isinstance(name, str) or not isinstance(payload, bytes):
                continue
            if filename is None:
                fields[name] = payload.decode()
            elif name == "workflow_attachment":
                attachments.append((filename, payload.decode(errors="replace")))
        return fields, attachments
    return {
        key: values[-1] for key, values in parse_qs(body.decode()).items() if values
    }, []


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

    def _serve_idp(self) -> bool:
        """Serve a request to the identity provider, if it is one.

        Returns:
            Whether the request was one to the identity provider.
        """
        path, _, query = self.path.partition("?")
        if not path.startswith(IDP_PATH):
            return False
        name = path[len(IDP_PATH) :]
        if name == ".well-known/openid-configuration":
            self._send(
                HTTPStatus.OK,
                {
                    "issuer": ISSUER,
                    "jwks_uri": f"{ISSUER}jwks",
                    "token_endpoint": f"{ISSUER}token",
                },
            )
        elif name == "jwks":
            key = jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True)
            self._send(HTTPStatus.OK, {"keys": [{**key, "kid": KEY_ID, "use": "sig"}]})
        elif name == "token" and parse_qs(query).get("sub"):
            now = int(time.time())
            claims = {
                "iss": ISSUER,
                "sub": parse_qs(query)["sub"][-1],
                "aud": AUDIENCE,
                "iat": now,
                "exp": now + TOKEN_SECONDS,
            }
            token = jwt.encode(
                claims, KEY, algorithm="RS256", headers={"kid": KEY_ID, "typ": "at+jwt"}
            )
            self._send(HTTPStatus.OK, {"access_token": token})
        else:
            self._error(HTTPStatus.NOT_FOUND, "not found")
        return True

    def do_GET(self) -> None:
        """Serve the identity provider, service info, runs, run log or status."""
        if self._serve_idp():
            return
        parts = self._parts()
        now = time.time()
        with LOCK:
            if parts == ["service-info"]:
                self._send(HTTPStatus.OK, service_info())
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
                form, attachments = parse_form(
                    self.headers.get("Content-Type", ""), body
                )
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
                unknown = sorted(set(form) - FIELDS_1_0)
                if unknown and version() == "1.0.1":
                    self._error(HTTPStatus.BAD_REQUEST, f"unknown fields: {unknown}")
                    return
                try:
                    tags = json.loads(form.get("tags") or "{}")
                except ValueError:
                    self._error(HTTPStatus.BAD_REQUEST, "tags are not JSON")
                    return
                # Unique also across restarts, as tasks of the Celery worker
                # may still track runs of an earlier instance
                run_id = f"FAKE{uuid.uuid4().hex[:12].upper()}"
                RUNS[run_id] = {
                    "request": form,
                    "attachments": attachments,
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

    Existing certificates are kept while they are valid for another day, as
    `docker compose up` runs this again for a deployment whose fake WES
    keeps running with them (a fake WES that runs for close to a year
    needs to be restarted to load the new ones). The files are
    readable by all, as the fake WES runs as another user than the one
    creating them; the keys are fictitious and only valid within the smoke
    test.

    Args:
        directory: Directory to write `ca.pem`, `server.pem` and
            `server-key.pem` to.
    """
    file_names = ("ca.pem", "server.pem", "server-key.pem")
    now = datetime.datetime.now(datetime.UTC)
    if all((directory / name).is_file() for name in file_names):
        expiry = min(
            x509.load_pem_x509_certificate(
                (directory / name).read_bytes()
            ).not_valid_after_utc
            for name in ("ca.pem", "server.pem")
        )
        if expiry > now + datetime.timedelta(days=1):
            return
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
    with urllib.request.urlopen(base, timeout=10) as response:
        runs = json.load(response)["runs"]
    matching = []
    for run in runs:
        url = f"{base}/{run['run_id']}"
        # The URL of the fake WES itself, with the run identifier it issued
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
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


def print_token(user: str) -> None:
    """Print an access token of a user, from a running fake WES, via plain HTTP.

    Args:
        user: Subject of the token.
    """
    url = f"http://localhost:{HTTP_PORT}{IDP_PATH}token?sub={user}"
    with urllib.request.urlopen(url, timeout=10) as response:
        print(json.load(response)["access_token"])


def main(args: list[str]) -> None:
    """Create the certificates, serve or verify, as the arguments say.

    Args:
        args: Command-line arguments: `certs <dir>`, `serve <dir>` or
            `verify <count> <key>=<value>...` (the number of runs and form
            fields of their requests), or `token <user>`.

    Raises:
        SystemExit: The arguments are invalid.
    """
    if (
        len(args) >= 3
        and args[0] == "verify"
        and args[1].isdigit()
        and all("=" in arg for arg in args[2:])
    ):
        verify(dict(arg.split("=", 1) for arg in args[2:]), int(args[1]))
    elif len(args) == 2 and args[0] == "certs":
        create_certificates(Path(args[1]))
    elif len(args) == 2 and args[0] == "serve":
        serve(Path(args[1]))
    elif len(args) == 2 and args[0] == "token" and args[1].isalnum():
        print_token(args[1])
    else:
        raise SystemExit(
            "usage: fake_wes.py certs|serve <dir> | verify <count> <key>=<value>..."
            " | token <user>"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
