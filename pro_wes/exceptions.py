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
    PayloadTooLarge,
    RequestValidationProblem,
    Unauthorized,
)
from pydantic import ValidationError
from pymongo.errors import PyMongoError

__all__ = [
    "AttachmentTooLarge",
    "BadRequest",
    "EngineProblem",
    "EngineUnavailable",
    "Forbidden",
    "IdsUnavailableProblem",
    "InternalServerError",
    "InvalidAttachmentName",
    "MalformedUrl",
    "NoSuitableEngine",
    "NotFound",
    "RunNotFound",
    "StorageUnavailableProblem",
    "TasksNotServed",
    "TooManyAttachments",
    "Unauthorized",
    "UrlCredentials",
    "WesEndpointProblem",
    "WorkflowEngineNotSupported",
    "exceptions",
]


class EngineProblem(InternalServerError):
    """The external workflow engine appears to experience problems."""


class EngineUnavailable(EngineProblem):
    """The external workflow engine is not available."""


class NoSuitableEngine(BadRequest):
    """No suitable workflow engine known."""


class WorkflowEngineNotSupported(BadRequest):
    """A run request chooses a workflow engine, which proWES can't forward yet.

    `workflow_engine` and `workflow_engine_version` of WES 1.1.0 (#66).
    """


class TasksNotServed(InternalServerError):
    """The task endpoints of WES 1.1.0 are not served yet (#124).

    `501 Not Implemented` (`exceptions`); FOCA has no error class for it, so
    it derives from the closest server-side one.
    """


class InvalidAttachmentName(BadRequest):
    """A workflow attachment has no usable file name.

    E.g., none, one longer than file systems allow, or one that another
    attachment of the run has, too (after making it safe).
    """


class TooManyAttachments(PayloadTooLarge):
    """A run has more workflow attachments than allowed.

    Cf. `custom.post_runs.max_attachments`.
    """


class AttachmentTooLarge(PayloadTooLarge):
    """A workflow attachment is larger than allowed.

    Cf. `custom.post_runs.max_attachment_size`.
    """


class UrlCredentials(BadRequest):
    """A URL of a run request has credentials (user name or password).

    Cf. `custom.post_runs.url_credentials`.
    """


class MalformedUrl(BadRequest):
    """A URL of a run request is malformed, so its credentials can't be checked."""


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
    PayloadTooLarge: {
        "title": "Content Too Large",
        "detail": "The request body exceeds the size limit.",
        "status": 413,
    },
    TooManyAttachments: {
        "title": "Content Too Large",
        "detail": "The run has more workflow attachments than allowed.",
        "status": 413,
    },
    AttachmentTooLarge: {
        "title": "Content Too Large",
        "detail": "A workflow attachment exceeds the size limit.",
        "status": 413,
    },
    InvalidAttachmentName: {
        "title": "Bad request",
        "detail": (
            "A workflow attachment has no file name, one that is too long, or"
            " one that another attachment has (after removing directories and"
            " unsafe characters)."
        ),
        "status": 400,
    },
    UrlCredentials: {
        "title": "Bad request",
        "detail": (
            "URLs of the run request must not have credentials (user names or"
            " passwords)."
        ),
        "status": 400,
    },
    MalformedUrl: {
        "title": "Bad request",
        "detail": "A URL of the run request is malformed.",
        "status": 400,
    },
    NoSuitableEngine: {
        "title": "Bad request",
        "detail": "No suitable workflow engine known.",
        "status": 400,
    },
    WorkflowEngineNotSupported: {
        "title": "Bad request",
        "detail": (
            "Choosing a workflow engine (workflow_engine,"
            " workflow_engine_version) is not supported yet."
        ),
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
    TasksNotServed: {
        "title": "Not implemented",
        "detail": "The tasks of runs are not served yet.",
        "status": 501,
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
