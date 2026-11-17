"""App exceptions and their problem details.

The app raises and maps the framework-neutral error classes of FOCA
(:py:mod:`foca.errors`) rather than those of Werkzeug and Connexion. Errors
are sent as problem details (RFC 9457: `title`, `status` and `detail`), with
the media type `application/json` (cf. `exceptions` in `config.yaml`).
"""

from foca.errors import (
    AccessDeniedProblem,
    AuthenticationProblem,
    BadRequest,
    ExtraParameterProblem,
    Forbidden,
    InternalServerError,
    NotFound,
    RequestValidationProblem,
    Unauthorized,
)
from pydantic import ValidationError
from pymongo.errors import PyMongoError

__all__ = [
    "BadRequest",
    "EngineProblem",
    "EngineUnavailable",
    "Forbidden",
    "IdsUnavailableProblem",
    "InternalServerError",
    "NoSuitableEngine",
    "NotFound",
    "RunNotFound",
    "StorageUnavailableProblem",
    "Unauthorized",
    "WesEndpointProblem",
    "exceptions",
]


class EngineProblem(InternalServerError):
    """The external workflow engine appears to experience problems."""


class EngineUnavailable(EngineProblem):
    """The external workflow engine is not available."""


class NoSuitableEngine(BadRequest):
    """No suitable workflow engine known."""


class RunNotFound(NotFound):
    """Workflow run with given identifier not found."""


class IdsUnavailableProblem(PyMongoError):
    """No unique run identifier available."""


class StorageUnavailableProblem(OSError):
    """Storage unavailable for OS operations."""


class WesEndpointProblem(InternalServerError):
    """No or incomplete information on the external WES endpoint."""


# Problem details of exceptions (cf. `exceptions.mapping` in `config.yaml`);
# exceptions that are not listed get those of their closest listed base class
exceptions = {
    Exception: {
        "title": "Internal server error",
        "detail": "An unexpected error occurred.",
        "status": 500,
    },
    BadRequest: {
        "title": "Bad request",
        "detail": "The request is malformed.",
        "status": 400,
    },
    RequestValidationProblem: {
        "title": "Bad request",
        "detail": "The request is malformed.",
        "status": 400,
    },
    ExtraParameterProblem: {
        "title": "Bad request",
        "detail": "The request is malformed.",
        "status": 400,
    },
    NoSuitableEngine: {
        "title": "Bad request",
        "detail": "No suitable workflow engine known.",
        "status": 400,
    },
    ValidationError: {
        "title": "Bad request",
        "detail": "The request is malformed.",
        "status": 400,
    },
    Unauthorized: {
        "title": "Unauthorized",
        "detail": "The request is unauthorized.",
        "status": 401,
    },
    AuthenticationProblem: {
        "title": "Unauthorized",
        "detail": "The request is unauthorized.",
        "status": 401,
    },
    Forbidden: {
        "title": "Forbidden",
        "detail": "The requester is not authorized to perform this action.",
        "status": 403,
    },
    AccessDeniedProblem: {
        "title": "Forbidden",
        "detail": "The requester is not authorized to perform this action.",
        "status": 403,
    },
    NotFound: {
        "title": "Not found",
        "detail": "The requested resource wasn't found.",
        "status": 404,
    },
    RunNotFound: {
        "title": "Not found",
        "detail": "The requested run wasn't found.",
        "status": 404,
    },
    EngineUnavailable: {
        "title": "Internal server error",
        "detail": "Could not reach remote WES service.",
        "status": 500,
    },
    InternalServerError: {
        "title": "Internal server error",
        "detail": "An unexpected error occurred.",
        "status": 500,
    },
    IdsUnavailableProblem: {
        "title": "Internal server error",
        "detail": "No/few unique run identifiers available.",
        "status": 500,
    },
    StorageUnavailableProblem: {
        "title": "Internal server error",
        "detail": "Storage is not accessible.",
        "status": 500,
    },
    WesEndpointProblem: {
        "title": "Internal server error",
        "detail": "No/few requirements provided for WES endpoint.",
        "status": 500,
    },
}
