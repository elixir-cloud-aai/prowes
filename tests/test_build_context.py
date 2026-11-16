"""Tests of the image build context (`.dockerignore`, `Dockerfile`).

Each file of the build context that the `Dockerfile` copies lands in a layer
of the image, so the build context must not contain secrets (e.g., `.env`
files or private keys), also not within the paths that the image needs.

These tests re-implement Docker's matching of `.dockerignore` patterns, to
fail early and without Docker. The authoritative check is the image build in
CI (`.github/workflows/main.yml`), which plants dummy secret files and checks
that they don't reach the image.
"""

from pathlib import Path, PurePosixPath
import json
import posixpath
import re

ROOT = Path(__file__).parents[1]


def _patterns() -> list[str]:
    """Get the patterns of `.dockerignore`, in order.

    Returns:
        Patterns, as Docker reads them: comments start in the first column,
        blank lines are skipped and surrounding whitespace is removed.
    """
    return [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _regex(pattern: str) -> str:
    """Translate a `.dockerignore` pattern into a regular expression.

    As Docker does: `**` matches any number of directories (also none), `*`
    any sequence of characters but `/`, `?` any character but `/`, and
    `[...]` a character class.

    Args:
        pattern: Pattern of `.dockerignore`, without a leading `!`.

    Returns:
        Regular expression that matches a whole path.
    """
    glob = posixpath.normpath(pattern).lstrip("/")
    regex = ""
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            regex += "(?:.*/)?"
            i += 3
        elif glob.startswith("**", i):
            regex += ".*"
            i += 2
        elif glob[i] == "*":
            regex += "[^/]*"
            i += 1
        elif glob[i] == "?":
            regex += "[^/]"
            i += 1
        elif glob[i] == "[":
            end = glob.index("]", i + 1)
            regex += "[" + glob[i + 1 : end].replace("\\", "\\\\") + "]"
            i = end + 1
        else:
            regex += re.escape(glob[i])
            i += 1
    return regex


def _ignored(path: str, patterns: list[str]) -> bool:
    """Tell whether Docker leaves a path out of the build context.

    As Docker does, a pattern applies to a path if it matches the path or
    one of its parent directories, and the last pattern that applies wins.

    Args:
        path: Path relative to the build context.
        patterns: Patterns of `.dockerignore`, in order.

    Returns:
        Whether the path is left out.
    """
    parts = PurePosixPath(path).parts
    prefixes = ["/".join(parts[: n + 1]) for n in range(len(parts))]
    ignored = False
    for pattern in patterns:
        include = pattern.startswith("!")
        regex = _regex(pattern.removeprefix("!").strip())
        if any(re.fullmatch(regex, prefix) for prefix in prefixes):
            ignored = not include
    return ignored


def _copy_sources() -> list[str]:
    """Get the sources of the `COPY` and `ADD` instructions of the Dockerfile.

    Instructions are case-insensitive, and their arguments may be given as
    a JSON array (e.g., `COPY [".", "/app"]`). Sources outside the build
    context are left out: those of other stages (`--from`), URLs and
    heredocs.

    Returns:
        Source paths, relative to the build context.
    """
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8").replace("\\\n", " ")
    sources = []
    for args in re.findall(
        r"^\s*(?:COPY|ADD)\s+(.+)$", text, flags=re.MULTILINE | re.IGNORECASE
    ):
        if "--from=" in args:
            continue
        words = [arg for arg in args.split() if not arg.startswith("--")]
        paths = words
        if words and words[0].startswith("["):
            # As Docker does, arguments that aren't valid JSON are shell form
            try:
                paths = json.loads(" ".join(words))
            except json.JSONDecodeError:
                pass
        sources.extend(
            path for path in paths[:-1] if not re.match(r"^(\w+://|<<)", path)
        )
    return sources


def test_dockerignore_secrets() -> None:
    """The build context has no secret files, also in re-included paths.

    `.dockerignore` re-includes whole directories (e.g., `pro_wes/`), so
    `.env` files (but `.env.example`) and key files in them are left out
    again; they would otherwise land in a layer of the image.
    """
    patterns = _patterns()
    for path in (
        ".env",
        ".env.local",
        "pro_wes/.env",
        "pro_wes/.env.local",
        "pro_wes/api/.env.production",
        "pro_wes/.ENV",
        "pro_wes/.Env.production",
        "pro_wes/.env.example/secret",
        "pro_wes/tls.key",
        "pro_wes/TLS.KEY",
        "pro_wes/certs/server.pem",
        "pro_wes/certs/client.p12",
        "pro_wes/certs/client.pfx",
        "pro_wes/tls.key.bak",
        "pro_wes/certs/truststore.jks",
        "pro_wes/certs/server.keystore",
        "pro_wes/id_rsa",
        "pro_wes/id_ed25519.pub",
        "pro_wes/.netrc",
        "pro_wes/.pgpass",
        "pro_wes/.aws/credentials",
        "pro_wes/kubeconfig.yaml",
        "pro_wes/KUBECONFIG",
    ):
        assert _ignored(path, patterns), f"{path} is sent to the build"


def test_dockerignore_allowlist() -> None:
    """The build context has only what the image build needs.

    Everything else (e.g., Git metadata, tests, local virtual environments,
    deployment files) is left out, so that new files don't reach the image
    unnoticed.
    """
    patterns = _patterns()
    for path in (
        ".git/config",
        ".venv/bin/python",
        "tests/test_build_context.py",
        "deployment/values.yaml",
        "docker-compose.yaml",
        "pro_wes/__pycache__/app.cpython-310.pyc",
        "pro_wes.egg-info/PKG-INFO",
        # Legacy packaging files, replaced by `pyproject.toml`
        "requirements.txt",
        "setup.py",
    ):
        assert _ignored(path, patterns), f"{path} is sent to the build"
    for path in (
        "pro_wes/app.py",
        "pro_wes/config.yaml",
        "pro_wes/api/additions.openapi.yaml",
        "pro_wes/.env.example",
        "LICENSE",
        "README.md",
        "pyproject.toml",
    ):
        assert not _ignored(path, patterns), f"{path} is not sent to the build"


def test_dockerfile_copies_needed_files_only() -> None:
    """The `Dockerfile` copies named paths, all of them in the build context.

    Not the whole build context (`.`), so that a gap in `.dockerignore`
    doesn't put a file into the image by itself.
    """
    sources = _copy_sources()
    assert sources
    patterns = _patterns()
    for source in sources:
        assert posixpath.normpath(source) not in {".", "*"}, source
        assert not _ignored(posixpath.normpath(source), patterns), source
