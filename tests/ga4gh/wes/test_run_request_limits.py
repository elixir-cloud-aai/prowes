"""Tests of the limits and sanitization of run requests (`POST /runs`).

Request size limit (`server.max_content_length`), limits of workflow
attachments (`custom.post_runs.max_attachments`, `max_attachment_size`),
safe names of attachments, credentials in URLs
(`custom.post_runs.url_credentials`) and validated responses.
"""

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from io import BytesIO
from json import loads
from pathlib import Path
from typing import Any

from foca.testing import Client
from pymongo import MongoClient
import pytest

from pro_wes.ga4gh.wes import workflow_runs
from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.ga4gh.wes.models import RunId
from pro_wes.tasks.track_run_progress import task__track_run_progress
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB
from tests.conftest import TokenClient

BASE_PATH = "/ga4gh/wes/v1"
BOUNDARY = "prowes-limits-boundary"
RUN_REQUEST: dict[str, str] = {
    "workflow_params": '{"input": "value"}',
    "workflow_type": "CWL",
    "workflow_type_version": "v1.0",
    "workflow_url": "https://example.org/workflow.cwl",
}


def _multipart(
    fields: dict[str, str], files: list[tuple[str, bytes]] | None = None
) -> bytes:
    """Encode form fields and workflow attachments as `multipart/form-data`.

    Args:
        fields: Names and values of the form fields.
        files: File names and contents of the workflow attachments
            (`workflow_attachment`), in order.

    Returns:
        Body, with the boundary `BOUNDARY`.
    """
    parts = [
        (
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()
        for name, value in fields.items()
    ]
    for filename, content in files or []:
        parts.append(
            (
                f"--{BOUNDARY}\r\nContent-Disposition: form-data;"
                f' name="workflow_attachment"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
            + content
            + b"\r\n"
        )
    return b"".join(parts) + f"--{BOUNDARY}--\r\n".encode()


def _post_run(
    client: Client,
    fields: dict[str, str],
    files: list[tuple[str, bytes]] | None = None,
) -> Any:
    """Send `POST /runs` with form fields and workflow attachments.

    Args:
        client: Test client of the app.
        fields: Form fields of the run request.
        files: File names and contents of the workflow attachments.

    Returns:
        Response.
    """
    return client.post(
        f"{BASE_PATH}/runs",
        content=_multipart(fields, files),
        headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
    )


@pytest.fixture
def client_with(
    add_overlay: Callable[[str], None], token: str
) -> Iterator[Callable[[str], Client]]:
    """Get a function that creates a test client of the app with an overlay.

    Args:
        add_overlay: Function that merges config over the test overlay.
        token: Access token that the clients send.

    Yields:
        Function taking the YAML text of an overlay, returning a client of
        an app created with it; the clients are closed afterwards.
    """
    from pro_wes.app import init_app  # noqa: PLC0415 (after the client patch)

    with ExitStack() as stack:

        def _client(text: str) -> Client:
            """Create a client of an app with an overlay.

            Args:
                text: YAML text of the overlay.

            Returns:
                Client of the app.
            """
            add_overlay(text)
            return stack.enter_context(TokenClient(init_app(), token))

        yield _client


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record the tracking tasks of runs instead of queuing them.

    Args:
        monkeypatch: Monkeypatch fixture of the test.

    Returns:
        Keyword arguments of the queued tasks, in order.
    """
    calls: list[dict[str, Any]] = []

    def _apply_async(args: Any, kwargs: dict[str, Any], **options: Any) -> None:
        """Record a queued task.

        Args:
            args: Positional arguments of the task.
            kwargs: Keyword arguments of the task.
            **options: Options of the task.
        """
        calls.append(kwargs)

    monkeypatch.setattr(task__track_run_progress, "apply_async", _apply_async)
    return calls


@pytest.fixture
def remote_wes(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Fake the remote WES: runs are accepted.

    Args:
        monkeypatch: Monkeypatch fixture of the test.

    Returns:
        Form data of the run requests sent to the remote WES, in order.
    """
    sent: list[dict[str, str]] = []

    def _post_run(self: WesClient, form_data: dict[str, str], **kwargs: Any) -> RunId:
        """Accept a run.

        Args:
            self: WES client.
            form_data: Form data of the run request.
            **kwargs: Further arguments; ignored.

        Returns:
            Identifier of the run.
        """
        sent.append(form_data)
        return RunId(run_id="REMOTE1")

    monkeypatch.setattr(WesClient, "post_run", _post_run)
    return sent


def _assert_refused(
    response: Any,
    status: int,
    mongodb: MongoClient[dict[str, Any]],
    storage_path: Path,
    remote_wes: list[dict[str, str]],
) -> None:
    """Check that a run was refused, and neither stored nor forwarded.

    Args:
        response: Response to `POST /runs`.
        status: Expected status of the response.
        mongodb: Database of the test.
        storage_path: Storage directory of the runs.
        remote_wes: Run requests sent to the remote WES.
    """
    assert response.status_code == status
    assert response.json()["status"] == status
    assert mongodb[RUN_DB][RUN_COLLECTION].count_documents({}) == 0
    assert not storage_path.exists() or not any(storage_path.iterdir())
    assert not remote_wes


@pytest.fixture
def storage(storage_path: Path) -> Path:
    """Get the storage directory of the runs, created if need be.

    Args:
        storage_path: Storage directory of the runs.

    Returns:
        The storage directory.
    """
    storage_path.mkdir(exist_ok=True)
    return storage_path


# Request size limit


def test_config_limits_request_size() -> None:
    """The base config limits request bodies to 10 MiB."""
    from foca.config.config_parser import ConfigParser  # noqa: PLC0415

    import pro_wes  # noqa: PLC0415

    config = ConfigParser(
        Path(pro_wes.__file__).parent / "config.yaml", format_logs=False
    ).config
    assert config.server.max_content_length == 10 * 1024 * 1024
    assert config.api.specs[0].connexion is not None
    assert config.api.specs[0].connexion.validate_responses is True


def test_request_too_large(
    client_with: Callable[[str], Client],
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Requests larger than `server.max_content_length` get `413`."""
    client = client_with("server:\n  max_content_length: 2048\n")
    fields = {**RUN_REQUEST, "workflow_params": f'{{"input": "{"x" * 4096}"}}'}
    response = _post_run(client, fields)
    _assert_refused(response, 413, mongodb, storage, remote_wes)
    assert response.json()["title"] == "Content Too Large"


def test_chunked_request_too_large(
    client_with: Callable[[str], Client],
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Chunked requests (without declared size) over the limit get `413`."""
    client = client_with("server:\n  max_content_length: 2048\n")
    fields = {**RUN_REQUEST, "workflow_params": f'{{"input": "{"x" * 4096}"}}'}
    # Werkzeug's client (FOCA's sends a declared size), with a stream of
    # undeclared size, as for chunked bodies
    flask_client = client._client
    response = flask_client.post(
        f"{BASE_PATH}/runs",
        input_stream=BytesIO(_multipart(fields)),
        headers={
            "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
            "Transfer-Encoding": "chunked",
        },
        # no declared size (cf. PEP 3333), as servers pass chunked bodies
        environ_overrides={"CONTENT_LENGTH": "", "wsgi.input_terminated": True},
    )
    assert response.status_code == 413
    assert response.get_json()["status"] == 413
    assert mongodb[RUN_DB][RUN_COLLECTION].count_documents({}) == 0
    assert not any(storage.iterdir())
    assert not remote_wes


# Attachments


def test_attachments_at_limits(
    client_with: Callable[[str], Client],
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Runs with as many and as large attachments as allowed are accepted."""
    client = client_with(
        "custom:\n  post_runs:\n    max_attachments: 2\n    max_attachment_size: 16\n"
    )
    files = [("a.txt", b"x" * 16), ("b.txt", b"y" * 16)]
    response = _post_run(client, RUN_REQUEST, files)
    assert response.status_code == 200
    assert len(remote_wes) == 1


def test_no_attachments_allowed(
    client_with: Callable[[str], Client],
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """With `max_attachments: 0`, runs without attachments only are accepted."""
    client = client_with("custom:\n  post_runs:\n    max_attachments: 0\n")
    assert _post_run(client, RUN_REQUEST).status_code == 200
    response = _post_run(client, RUN_REQUEST, [("a.txt", b"1")])
    assert response.status_code == 413


def test_too_many_attachments(
    client_with: Callable[[str], Client],
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Runs with more attachments than `max_attachments` get `413`."""
    client = client_with("custom:\n  post_runs:\n    max_attachments: 2\n")
    files = [(f"file{i}.txt", b"content") for i in range(3)]
    response = _post_run(client, RUN_REQUEST, files)
    _assert_refused(response, 413, mongodb, storage, remote_wes)
    assert "attachments" in response.json()["detail"]


def test_attachment_too_large(
    client_with: Callable[[str], Client],
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Runs with an attachment larger than `max_attachment_size` get `413`."""
    client = client_with("custom:\n  post_runs:\n    max_attachment_size: 16\n")
    files = [("small.txt", b"x" * 16), ("large.txt", b"x" * 17)]
    response = _post_run(client, RUN_REQUEST, files)
    _assert_refused(response, 413, mongodb, storage, remote_wes)
    assert "attachment" in response.json()["detail"]


def test_attachments_saved_safely(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Attachments are written with their contents, inside the run's directory.

    Names with directories (e.g., `../`) are reduced to safe file names.
    """
    files = [
        ("workflow.cwl", b"cwlVersion: v1.0\n"),
        ("../../escape.txt", b"escape"),
        ("/etc/passwd", b"absolute"),
    ]
    response = _post_run(foca_client, RUN_REQUEST, files)
    assert response.status_code == 200
    stored = mongodb[RUN_DB][RUN_COLLECTION].find_one({})
    assert stored is not None
    work_dir = Path(stored["work_dir"])
    assert work_dir.parent == storage.resolve()
    assert sorted(path.name for path in work_dir.iterdir()) == [
        "escape.txt",
        "etc_passwd",
        "workflow.cwl",
    ]
    assert (work_dir / "workflow.cwl").read_bytes() == b"cwlVersion: v1.0\n"
    assert (work_dir / "escape.txt").read_bytes() == b"escape"
    assert (work_dir / "etc_passwd").read_bytes() == b"absolute"
    assert not (storage.parent / "escape.txt").exists()
    # only the run's directory is in the storage
    assert list(storage.iterdir()) == [work_dir]
    # the database has their names and paths, not their contents
    assert stored["attachments"][1] == {
        "filename": "../../escape.txt",
        "path": str(work_dir / "escape.txt"),
    }


@pytest.fixture
def forwarded(monkeypatch: pytest.MonkeyPatch) -> list[list[tuple[str, bytes]]]:
    """Fake the remote WES: runs are accepted, with their attachments.

    Args:
        monkeypatch: Monkeypatch fixture of the test.

    Returns:
        Workflow attachments (file names and contents) of the run requests
        sent to the remote WES, in order.
    """
    sent: list[list[tuple[str, bytes]]] = []

    def _post_run(
        self: WesClient,
        form_data: dict[str, str],
        attachments: list[tuple[str, bytes]] | None = None,
        **kwargs: Any,
    ) -> RunId:
        """Accept a run.

        Args:
            self: WES client.
            form_data: Form data of the run request.
            attachments: Workflow attachments of the run request.
            **kwargs: Further arguments; ignored.

        Returns:
            Identifier of the run.
        """
        sent.append(list(attachments or []))
        return RunId(run_id="REMOTE1")

    monkeypatch.setattr(WesClient, "post_run", _post_run)
    return sent


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("main.cwl", "main.cwl"),
        ("tools/step.cwl", "tools/step.cwl"),
        ("donn\u00e9es/a.csv", "donn\u00e9es/a.csv"),
        ("~/a.txt", "~/a.txt"),
        ("a/" + "b" * 255, "a/" + "b" * 255),
        ("a/" + "b" * 256, "SAFE"),
        ("../a.txt", "SAFE"),
        ("a/../b.txt", "SAFE"),
        ("/etc/passwd", "SAFE"),
        ("./a.txt", "SAFE"),
        ("a//b.txt", "SAFE"),
        ("a/", "SAFE"),
        ("C:\\a.txt", "SAFE"),
        ("a\tb.txt", "SAFE"),
        ("a\u200bb.txt", "SAFE"),
        ("\uff0e\uff0e\uff0f\uff0e\uff0e\uff0fetc\uff0fpasswd", "SAFE"),
        ("\u2025/x", "SAFE"),
        ('a"b.cwl', "SAFE"),
        ("a%22b.cwl", "SAFE"),
        (".hidden/a b.txt", ".hidden/a b.txt"),
        (".. /x", "SAFE"),
        (" ../x", "SAFE"),
        (".../x", "SAFE"),
        ("a./x", "SAFE"),
        # drive-qualified names (Windows), and characters that Windows
        # doesn't allow in file names
        ("C:/outside.txt", "SAFE"),
        ("c:outside.txt", "SAFE"),
        ("a/b:c.txt", "SAFE"),
        ("a<b.txt", "SAFE"),
        ("a>b.txt", "SAFE"),
        ("a|b.txt", "SAFE"),
        ("a?b.txt", "SAFE"),
        ("a*b.txt", "SAFE"),
        # names that Windows reserves for devices, also with extensions
        ("CON", "SAFE"),
        ("con.txt", "SAFE"),
        ("a/Nul", "SAFE"),
        ("aux.tar.gz", "SAFE"),
        ("PRN", "SAFE"),
        ("COM1.log", "SAFE"),
        ("lpt9", "SAFE"),
        ("COM0", "SAFE"),
        ("CONIN$", "SAFE"),
        ("conout$.txt", "SAFE"),
        ("CONSOLE.txt", "CONSOLE.txt"),
        ("COM10", "COM10"),
        ("a.con", "a.con"),
        ("nul/a.txt", "SAFE"),
        # at most `MAX_PATH_BYTES` bytes
        (
            "/".join(["b" * 255] * 3 + ["b" * 254, "b"]),
            "/".join(["b" * 255] * 3 + ["b" * 254, "b"]),
        ),
        ("/".join(["b" * 255] * 4 + ["b"]), "SAFE"),
    ],
)
def test_forward_name(filename: str, expected: str) -> None:
    """Names are forwarded as sent if the WES API allows them (#44).

    Others are forwarded under their safe file names (here `SAFE`).
    """
    assert workflow_runs._forward_name(filename, "SAFE") == expected
    upload = workflow_runs.Upload(filename=filename, name="SAFE", content=b"x")
    assert upload.forwarded == (expected, b"x")


@pytest.mark.usefixtures("storage", "queued")
def test_attachments_forwarded(
    foca_client: Client,
    forwarded: list[list[tuple[str, bytes]]],
) -> None:
    """Attachments are sent on to the WES instance, in order (#44).

    Names that are relative paths without `.` or `..` parts are sent as
    they are, with their directories, so that relative paths in the run
    request (e.g., `workflow_url`) find them; others are sent with the safe
    file name that they are written under.
    """
    files = [
        ("workflow.cwl", b"cwlVersion: v1.0\n"),
        ("tools/step.cwl", b"class: CommandLineTool\n"),
        ("donn\u00e9es.csv", b"a,b\n"),
        ("../../escape.txt", b"escape"),
        ("/etc/passwd", b"absolute"),
        ("tools/./dot.txt", b"dot"),
        ("tools//double.txt", b"double"),
        ("tools/", b"trailing"),
        ("win\\path.txt", b"backslash"),
        ("tab\tname.txt", b"control"),
        ("C:/outside.txt", b"drive"),
        ("con.txt", b"device"),
    ]
    fields = {**RUN_REQUEST, "workflow_url": "workflow.cwl"}
    response = _post_run(foca_client, fields, files)
    assert response.status_code == 200
    assert forwarded == [
        [
            ("workflow.cwl", b"cwlVersion: v1.0\n"),
            ("tools/step.cwl", b"class: CommandLineTool\n"),
            ("donn\u00e9es.csv", b"a,b\n"),
            ("escape.txt", b"escape"),
            ("etc_passwd", b"absolute"),
            ("tools_dot.txt", b"dot"),
            ("tools_double.txt", b"double"),
            ("tools", b"trailing"),
            ("winpath.txt", b"backslash"),
            ("tab_name.txt", b"control"),
            ("C_outside.txt", b"drive"),
            ("_con.txt", b"device"),
        ]
    ]


@pytest.mark.usefixtures("storage", "queued")
def test_no_attachments_forwarded(
    foca_client: Client,
    forwarded: list[list[tuple[str, bytes]]],
) -> None:
    """Runs without attachments are sent on without any."""
    response = _post_run(foca_client, RUN_REQUEST)
    assert response.status_code == 200
    assert forwarded == [[]]


def test_attachment_without_safe_name(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Attachments without any safe character in their name get a random one."""
    response = _post_run(foca_client, RUN_REQUEST, [("..", b"dots")])
    assert response.status_code == 200
    stored = mongodb[RUN_DB][RUN_COLLECTION].find_one({})
    assert stored is not None
    (path,) = Path(stored["work_dir"]).iterdir()
    assert path.name not in {"", ".", ".."}
    assert path.read_bytes() == b"dots"


def test_attachment_not_writable(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Attachments that can't be written fail the run, which isn't forwarded."""

    def _open(*args: Any, **kwargs: Any) -> Any:
        """Fail like a full disk.

        Args:
            *args: Positional arguments of `open()`; ignored.
            **kwargs: Keyword arguments of `open()`; ignored.

        Raises:
            OSError: Always.
        """
        raise OSError("No space left on device")

    monkeypatch.setattr(workflow_runs, "open", _open, raising=False)
    response = _post_run(foca_client, RUN_REQUEST, [("a.txt", b"1")])
    assert response.status_code == 500
    assert response.json()["detail"] == "Storage is not accessible."
    assert not remote_wes
    assert not queued
    stored = mongodb[RUN_DB][RUN_COLLECTION].find_one({})
    assert stored is not None
    assert stored["run_log"]["state"] == "SYSTEM_ERROR"
    assert stored["system_error_by_prowes"] is True


@pytest.mark.parametrize(
    "files",
    [
        # the same name twice
        [("a.txt", b"1"), ("a.txt", b"2")],
        # different names with the same safe name
        [("dir/a.txt", b"1"), ("dir_a.txt", b"2")],
        # a name longer than file systems allow
        [("a" * 252 + ".txt", b"1")],
        # an empty name
        [("", b"1")],
    ],
    ids=["duplicate", "same-safe-name", "too-long", "empty"],
)
def test_attachment_names_refused(
    files: list[tuple[str, bytes]],
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Attachments with clashing or overlong names get `400`."""
    response = _post_run(foca_client, RUN_REQUEST, files)
    _assert_refused(response, 400, mongodb, storage, remote_wes)
    assert "attachment" in response.json()["detail"]


# Size of tags


def test_tags_at_limit(
    client_with: Callable[[str], Client],
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Runs with tags as large as `max_tags_size` (bytes, UTF-8) are accepted."""
    client = client_with("custom:\n  post_runs:\n    max_tags_size: 17\n")
    tags = '{"k": "' + "é" * 4 + '"}'
    assert len(tags.encode()) == 17
    response = _post_run(client, {**RUN_REQUEST, "tags": tags})
    assert response.status_code == 200
    assert remote_wes[0]["tags"] == tags


def test_tags_too_large(
    client_with: Callable[[str], Client],
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Runs with tags larger than `max_tags_size` get `400`, naming `tags`.

    The size is that of the JSON text in bytes (UTF-8), not in characters.
    """
    client = client_with("custom:\n  post_runs:\n    max_tags_size: 17\n")
    tags = '{"k": "' + "é" * 5 + '"}'
    assert len(tags) <= 17 < len(tags.encode())
    response = _post_run(client, {**RUN_REQUEST, "tags": tags})
    _assert_refused(response, 400, mongodb, storage, remote_wes)
    assert response.json()["errors"] == [
        {
            "detail": "Must be at most 17 bytes long.",
            "pointer": "#/tags",
            "code": "too-long",
        }
    ]


def test_tags_too_large_by_default(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """By default, tags may have 16 KiB of JSON text."""
    prefix = '{"k": "'
    fitting = prefix + "x" * (16384 - len(prefix) - 2) + '"}'
    assert _post_run(foca_client, {**RUN_REQUEST, "tags": fitting}).status_code == 200
    too_large = prefix + "x" * (16385 - len(prefix) - 2) + '"}'
    response = _post_run(foca_client, {**RUN_REQUEST, "tags": too_large})
    assert response.status_code == 400
    assert response.json()["errors"][0]["pointer"] == "#/tags"


def test_tags_size_without_credentials(
    client_with: Callable[[str], Client],
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """The size of tags is that of the stored text (`url_credentials: strip`).

    Credentials are removed before the size is checked, and the size is that
    of the JSON text as stored and listed.
    """
    stored = '{"s": "https://example.org/x"}'
    client = client_with(
        "custom:\n  post_runs:\n    url_credentials: strip\n"
        f"    max_tags_size: {len(stored)}\n"
    )
    sent = '{"s":"https://user:s3cr3t@example.org/x"}'
    assert len(sent) > len(stored)
    response = _post_run(client, {**RUN_REQUEST, "tags": sent})
    assert response.status_code == 200
    assert remote_wes[0]["tags"] == stored


# Credentials in URLs

CREDENTIALED: dict[str, dict[str, str]] = {
    "workflow_url": {"workflow_url": "https://user:s3cr3t@example.org/wf.cwl"},
    "workflow_params": {
        "workflow_params": (
            '{"input": {"class": "File",'
            ' "location": "https://user:s3cr3t@example.org/in.txt"},'
            ' "list": ["s3://key:secret@bucket/obj", "plain"]}'
        )
    },
    "workflow_engine_parameters": {
        "workflow_engine_parameters": '{"url": "https://token@example.org/"}'
    },
    "tags": {"tags": '{"source": "ftp://user:@example.org/x"}'},
}


@pytest.mark.parametrize("fields", CREDENTIALED.values(), ids=CREDENTIALED.keys())
def test_url_credentials_rejected(
    fields: dict[str, str],
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Runs with credentials in URLs get `400` (`url_credentials: reject`)."""
    response = _post_run(foca_client, {**RUN_REQUEST, **fields})
    _assert_refused(response, 400, mongodb, storage, remote_wes)
    assert "credentials" in response.json()["detail"]
    assert "s3cr3t" not in response.text
    assert "secret" not in response.text


def test_url_credentials_stripped(
    client_with: Callable[[str], Client],
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Credentials are removed before runs are stored and forwarded (`strip`)."""
    client = client_with("custom:\n  post_runs:\n    url_credentials: strip\n")
    fields: dict[str, str] = {**RUN_REQUEST}
    for value in CREDENTIALED.values():
        fields.update(value)
    response = _post_run(client, fields)
    assert response.status_code == 200
    stored = mongodb[RUN_DB][RUN_COLLECTION].find_one({})
    assert stored is not None
    for request in (stored["run_log"]["request"], remote_wes[0]):
        assert request["workflow_url"] == "https://example.org/wf.cwl"
        assert loads(request["workflow_params"]) == {
            "input": {"class": "File", "location": "https://example.org/in.txt"},
            "list": ["s3://bucket/obj", "plain"],
        }
        assert loads(request["workflow_engine_parameters"]) == {
            "url": "https://example.org/"
        }
        assert loads(request["tags"]) == {"source": "ftp://example.org/x"}
    run = client.get(f"{BASE_PATH}/runs/{response.json()['run_id']}")
    assert run.status_code == 200
    for secret in ("user", "s3cr3t", "secret", "token"):
        assert secret not in run.text


def test_url_without_credentials_unchanged(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Run requests without credentials are stored and forwarded as sent."""
    params = '{"a":  "user@example.org", "b": "mailto:user@example.org"}'
    response = _post_run(foca_client, {**RUN_REQUEST, "workflow_params": params})
    assert response.status_code == 200
    assert remote_wes[0]["workflow_params"] == params


def test_malformed_url_refused(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Runs with malformed URLs (unchecked for credentials) get `400`."""
    fields = {**RUN_REQUEST, "workflow_params": '{"in": "https://[user@host/x"}'}
    response = _post_run(foca_client, fields)
    _assert_refused(response, 400, mongodb, storage, remote_wes)


@pytest.mark.parametrize(
    "fields",
    [
        # malformed, with a message of `urlsplit()` that names the netloc
        {"workflow_params": '{"in": "https://user:s3cr3t@h℀x/"}'},
        # invalid for another reason (a value other than a string)
        {"tags": '{"n": 1, "src": "https://user:s3cr3t@h/x"}'},
        # a lenient form, inside a string
        {"workflow_engine_parameters": '{"x": "--url=https:/user:s3cr3t@h/"}'},
    ],
    ids=["malformed", "invalid", "lenient"],
)
def test_credentials_not_logged(
    fields: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Refused runs with credentials don't leak them to responses or logs."""
    response = _post_run(foca_client, {**RUN_REQUEST, **fields})
    _assert_refused(response, 400, mongodb, storage, remote_wes)
    assert "s3cr3t" not in response.text
    assert "s3cr3t" not in caplog.text


# Validation of run requests and responses


@pytest.mark.parametrize(
    "fields",
    [
        {"tags": '{"count": 1}'},
        {"workflow_engine_parameters": '{"nested": {"a": "b"}}'},
        {"workflow_params": "[" * 100000},
        # duplicate keys, of which decoding would keep the last value only
        {"workflow_params": '{"a": "https://u:s3cr3t@h/", "a": "x"}'},
    ],
    ids=["tags", "workflow_engine_parameters", "deeply-nested", "duplicate-keys"],
)
def test_invalid_fields_refused(
    fields: dict[str, str],
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Tags and engine parameters with values other than strings get `400`.

    So do JSON fields too deeply nested to decode.
    """
    response = _post_run(foca_client, {**RUN_REQUEST, **fields})
    _assert_refused(response, 400, mongodb, storage, remote_wes)


def test_run_log_response_validated(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Run logs conform to the WES specification, also with remote logs.

    The run request is returned with objects for its JSON fields, and fields
    without value are left out.
    """
    fields = {**RUN_REQUEST, "tags": '{"key": "value"}'}
    response = _post_run(foca_client, fields)
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    mongodb[RUN_DB][RUN_COLLECTION].update_one(
        {"run_log.run_id": run_id},
        {
            "$set": {
                "run_log.state": "COMPLETE",
                "run_log.outputs": {"out": "https://example.org/out.txt"},
                "run_log.run_log": {
                    "cmd": ["cwltool"],
                    "exit_code": 0,
                    "name": None,
                    "start_time": None,
                    "end_time": None,
                    "stderr": None,
                    "stdout": "done",
                },
                "run_log.task_logs": [{"cmd": [], "exit_code": None, "name": "t"}],
            }
        },
    )
    response = foca_client.get(f"{BASE_PATH}/runs/{run_id}")
    assert response.status_code == 200
    assert response.json() == {
        "run_id": run_id,
        "state": "COMPLETE",
        "request": {
            "workflow_params": {"input": "value"},
            "workflow_type": "CWL",
            "workflow_type_version": "v1.0",
            "workflow_url": "https://example.org/workflow.cwl",
            "tags": {"key": "value"},
            "workflow_engine_parameters": {},
        },
        "outputs": {"out": "https://example.org/out.txt"},
        "run_log": {"cmd": ["cwltool"], "exit_code": 0, "stdout": "done"},
        "task_logs": [{"cmd": [], "name": "t"}],
    }


def test_stored_credentials_not_returned(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Credentials of runs stored by earlier versions are removed from run logs."""
    response = _post_run(foca_client, RUN_REQUEST)
    run_id = response.json()["run_id"]
    mongodb[RUN_DB][RUN_COLLECTION].update_one(
        {"run_log.run_id": run_id},
        {
            "$set": {
                "run_log.request.workflow_url": "https://u:s3cr3t@example.org/wf",
                "run_log.request.workflow_params": '{"in": "s3://k:s3cr3t@b/o"}',
            }
        },
    )
    response = foca_client.get(f"{BASE_PATH}/runs/{run_id}")
    assert response.status_code == 200
    assert response.json()["request"]["workflow_url"] == "https://example.org/wf"
    assert response.json()["request"]["workflow_params"] == {"in": "s3://b/o"}
    assert "s3cr3t" not in response.text


def test_stored_malformed_url(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Run logs whose stored run request has malformed URLs get `500`."""
    response = _post_run(foca_client, RUN_REQUEST)
    run_id = response.json()["run_id"]
    mongodb[RUN_DB][RUN_COLLECTION].update_one(
        {"run_log.run_id": run_id},
        {"$set": {"run_log.request.workflow_url": "https://[u@example.org/"}},
    )
    response = foca_client.get(f"{BASE_PATH}/runs/{run_id}")
    assert response.status_code == 500


def test_invalid_stored_run_log(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    storage: Path,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Stored run logs that don't conform to the model get `500`, not `400`."""
    response = _post_run(foca_client, RUN_REQUEST)
    run_id = response.json()["run_id"]
    mongodb[RUN_DB][RUN_COLLECTION].update_one(
        {"run_log.run_id": run_id},
        {"$set": {"run_log.task_logs": "not a list"}},
    )
    response = foca_client.get(f"{BASE_PATH}/runs/{run_id}")
    assert response.status_code == 500
    assert "not a list" not in response.text
