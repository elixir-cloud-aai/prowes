"""Controllers for the `/runs` and children routes."""

from json import dumps, loads
import logging
from pathlib import Path
from typing import Any, NamedTuple

from bson.errors import InvalidId
from bson.objectid import ObjectId
from celery import uuid

# The implementation of the controllers still reads the request and its files
# from Flask, until they are read in `controllers.py` (#117)
from flask import request  # ast-grep-ignore: framework-free-tasks
from foca import get_collection, get_custom_config
from foca.errors import FieldError, InvalidFieldsProblem, json_pointer
from foca.utils.misc import generate_id
from pydantic import ValidationError
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError
from werkzeug.datastructures import (  # ast-grep-ignore: framework-free-tasks
    ImmutableMultiDict,
)
from werkzeug.utils import secure_filename  # ast-grep-ignore: framework-free-tasks

from pro_wes.config_models import CustomConfig
from pro_wes.exceptions import (
    AttachmentTooLarge,
    BadRequest,
    EngineInvalidResponse,
    EngineRefused,
    EngineUnavailable,
    Forbidden,
    IdsUnavailableProblem,
    InternalServerError,
    InvalidAttachmentName,
    RunNotCancelable,
    RunNotFound,
    StorageUnavailableProblem,
    TooManyAttachments,
    Unauthorized,
    WesEndpointProblem,
    WorkflowEngineNotSupported,
)
from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.ga4gh.wes.models import (
    STORED_CONTEXT,
    Attachment,
    DbDocument,
    ErrorResponse,
    RunLog,
    RunRequest,
    State,
    WesEndpoint,
)
from pro_wes.ga4gh.wes.routing import select_backend
from pro_wes.ga4gh.wes.versions import ENGINE_FIELDS, downgrade_run_request
from pro_wes.tasks.track_run_progress import task__track_run_progress
from pro_wes.utils.db import DbDocumentConnector
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB
from pro_wes.utils.urls import UrlCredentialsMode, sanitize_json, sanitize_url

logger = logging.getLogger(__name__)

# Maximum length of the file names of workflow attachments, in bytes (that of
# common file systems, e.g., ext4 and XFS)
MAX_FILENAME_BYTES = 255

# Fields of run requests that hold JSON objects (as strings, in forms)
JSON_FIELDS = ("workflow_params", "workflow_engine_parameters", "tags")

# JSON fields of run requests that are objects of strings (WES 1.1.0); runs
# stored by versions before 0.20.0 may have other values, which run logs
# return as their JSON text (cf. `_string_values()`)
STRING_OBJECT_FIELDS = ("workflow_engine_parameters", "tags")

# The fields of WES 1.1.0 run requests that choose the workflow engine
# (`ENGINE_FIELDS`) are refused until they are forwarded (#66), so that the
# choice isn't lost


def _page_token_id(page_token: str) -> ObjectId:
    """Get the database identifier of the last run of a page.

    Page tokens are the identifiers of the last run of the page before
    (`next_page_token` of `GET /runs`).

    Args:
        page_token: Page token of the request.

    Returns:
        Identifier.

    Raises:
        foca.errors.InvalidFieldsProblem: The page token is malformed
            (`400`); the token is not echoed.
    """
    try:
        return ObjectId(page_token)
    except InvalidId:
        raise InvalidFieldsProblem(
            [
                FieldError(
                    detail="Must be a next_page_token returned by GET /runs.",
                    code="invalid-value",
                    parameter="page_token",
                )
            ]
        ) from None


def _decode_stored_json(value: str, field: str) -> Any:
    """Decode a JSON field of a stored run request, without credentials.

    Credentials in the URLs of the decoded value are removed, for runs stored
    by earlier versions, which didn't check them.

    Args:
        value: JSON-encoded value of the field, as stored.
        field: Name of the field of the run request, for error messages.

    Returns:
        Decoded value.

    Raises:
        pro_wes.exceptions.BadRequest: A URL in the value is malformed, or a
            key without its credentials collides with another key.
        RecursionError: The value is too deeply nested to decode.
        TypeError: The value is not a string.
        ValueError: The value is not valid JSON.
    """
    decoded = loads(value)
    sanitize_json(decoded, field, "strip")
    return decoded


def _tags_too_large(tags: Any, max_tags_size: int) -> bool:
    """Check whether tags are larger than allowed.

    The size is that of the JSON text of the tags, as stored, in bytes
    (UTF-8; cf. `custom.post_runs.max_tags_size`).

    Args:
        tags: Tags of a run request, as stored (JSON text; others, e.g.,
            `None`, have no size).
        max_tags_size: Maximum size, in bytes.

    Returns:
        Whether the tags are JSON text larger than `max_tags_size`.
    """
    return isinstance(tags, str) and len(tags.encode()) > max_tags_size


class Upload(NamedTuple):
    """Workflow attachment of a run request, checked and read.

    Attributes:
        filename: Name of the file as indicated in the run request.
        name: Safe file name, under which it is written to the working
            directory of the run.
        content: Contents of the file.
    """

    filename: str
    name: str
    content: bytes


# Errors of WES instances that proWES answers with as its own, by status, when
# a run is forwarded or canceled; others become `500`
REMOTE_ERRORS: dict[int, type[Exception]] = {
    400: BadRequest,
    401: Unauthorized,
    403: Forbidden,
}


def _string_values(decoded: dict[str, Any]) -> dict[str, str]:
    """Get a decoded JSON object with strings as values.

    For the objects of strings of run requests (`STRING_OBJECT_FIELDS`), as
    returned by `GET /runs` and `GET /runs/{run_id}`: runs stored by
    versions before 0.20.0 may have other values.

    Args:
        decoded: Decoded JSON object.

    Returns:
        The object, with values other than strings replaced by their JSON
        text (e.g., `1` for the number 1, `null` for `None`).

    Raises:
        RecursionError: A value is too deeply nested to encode.
        ValueError: A value has numbers that JSON can't represent (e.g.,
            `1e999` decodes to infinity); refused rather than encoded as
            the invalid JSON text `Infinity`.
    """
    return {
        key: value if isinstance(value, str) else dumps(value, allow_nan=False)
        for key, value in decoded.items()
    }


class WorkflowRuns:
    """Class for WES API server-side controller methods.

    Attributes:
        config: Custom configuration of the app.
        db_client: Database collection storing workflow run objects.
    """

    def __init__(self) -> None:
        """Class constructor."""
        self.config: CustomConfig = get_custom_config(CustomConfig)
        self.db_client: Collection[dict[str, Any]] = get_collection(
            RUN_DB, RUN_COLLECTION
        )

    def run_workflow(
        self,
        **kwargs: Any,
    ) -> dict[str, str]:
        """Start workflow run.

        Controller for `POST /runs`.

        Args:
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            Workflow run identifier.

        Raises:
            pro_wes.exceptions.NoBackendAvailable: No WES instance is
                available (`custom.wes.backends`), or none that can run the
                request and some didn't answer so far; nothing is stored.
            foca.errors.InvalidFieldsProblem: No WES instance can run the
                request unchanged (by its WES version); nothing is stored.
        """
        document: DbDocument = DbDocument()
        controller_config = self.config.post_runs

        # engine fields sent as files are refused, too (cf. `ENGINE_FIELDS`)
        if any(field in request.files for field in ENGINE_FIELDS):
            raise WorkflowEngineNotSupported

        # validate and attach request, without credentials in URLs
        document.run_log.request = self._validate_run_request(
            form_data=request.form,
            url_credentials=controller_config.url_credentials,
            max_tags_size=controller_config.max_tags_size,
        )

        # check and read workflow attachments, before anything is stored
        uploads = self._read_attachments()

        # choose and attach the WES instance (`custom.wes.backends`) that can
        # run the request, by its WES version, before anything is stored
        backend = select_backend(document.run_log.request.model_dump())
        document.wes_endpoint = WesEndpoint(
            host=backend.url,
            base_path=backend.api_path,
            wes_version=str(backend.version),
        )

        # get and attach workflow run owner
        document.user_id = kwargs.get("user_id")

        # create run environment & insert run document into run collection
        document_stored: DbDocument = self._create_run_environment(
            document=document, uploads=uploads
        )

        # write workflow attachments; the run fails if they can't be written
        try:
            self._save_attachments(
                attachments=document_stored.attachments, uploads=uploads
            )
        except StorageUnavailableProblem:
            if document_stored.task_id is not None:
                DbDocumentConnector(
                    collection=self.db_client, task_id=document_stored.task_id
                ).set_system_error()
            raise

        # ensure WES endpoint is available
        if (
            document_stored.wes_endpoint is None
            or document_stored.wes_endpoint.base_path is None
        ):
            raise InternalServerError("No WES endpoint or base path available.")

        if document_stored.task_id is None:
            raise IdsUnavailableProblem

        # instantiate WES client
        wes_client: WesClient = WesClient(
            host=document_stored.wes_endpoint.host,
            base_path=document_stored.wes_endpoint.base_path,
            # no credentials for WES instances (`auth: none`) until #120;
            # the client's token is never passed on
            token=None,
        )

        # instantiate database connector
        db_connector = DbDocumentConnector(
            collection=self.db_client,
            task_id=document_stored.task_id,
        )

        # forward incoming WES request and validate response
        url = wes_client.url
        logger.info(
            "Sending workflow run '%s' with task identifier '%s' to WES"
            " endpoint hosted at: %s",
            document_stored.run_log.run_id,
            document_stored.task_id,
            url,
        )
        try:
            # in the WES version of the WES instance
            response = wes_client.post_run(
                form_data=downgrade_run_request(
                    document.run_log.request.model_dump(), backend.version
                ),
                timeout=controller_config.timeout_post,
            )
        except EngineUnavailable:
            db_connector.set_system_error()
            raise
        if isinstance(response, ErrorResponse):
            db_connector.set_system_error()
            raise REMOTE_ERRORS.get(response.status_code, InternalServerError)
        updated_document_stored: DbDocument = db_connector.upsert_fields_in_root_object(
            root="wes_endpoint",
            run_id=response.run_id,
        )

        # ensure WES endpoint is available
        if (
            updated_document_stored.wes_endpoint is None
            or updated_document_stored.wes_endpoint.base_path is None
        ):
            raise InternalServerError("No WES endpoint or base path available.")

        if updated_document_stored.wes_endpoint.run_id is None:
            raise WesEndpointProblem

        # track workflow progress in background; without time limit unless
        # `timeout_job` is set
        options: dict[str, Any] = {}
        if controller_config.timeout_job is not None:
            options["soft_time_limit"] = controller_config.timeout_job
        task__track_run_progress.apply_async(
            None,
            {
                "jwt": None,
                "remote_host": updated_document_stored.wes_endpoint.host,
                "remote_base_path": updated_document_stored.wes_endpoint.base_path,
                "remote_run_id": updated_document_stored.wes_endpoint.run_id,
                "remote_wes_version": str(backend.version),
            },
            task_id=updated_document_stored.task_id,
            **options,
        )

        # the run's own identifier, by which it is found (`GET /runs/{run_id}`)
        if updated_document_stored.run_log.run_id is None:
            raise IdsUnavailableProblem
        return {"run_id": updated_document_stored.run_log.run_id}

    def list_runs(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return list of workflow runs.

        Controller for `GET /runs`.

        Args:
            **kwargs: Keyword arguments passed along with request.

        Returns:
            Response object according to WES API schema `RunListResponse`. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

        Raises:
            foca.errors.InvalidFieldsProblem: The page size is below 1 or
                above `custom.list_runs.max_page_size`, or the page token is
                malformed (`400`).
        """
        # set query params; page sizes are bounded, so that no request lists
        # more runs at once (`0` doesn't list all: the specification has
        # services return at most `page_size` runs)
        page_size: int = kwargs.get(
            "page_size", self.config.list_runs.default_page_size
        )
        page_token = kwargs.get("page_token", "")
        max_page_size = self.config.list_runs.max_page_size
        if not 1 <= page_size <= max_page_size:
            raise InvalidFieldsProblem(
                [
                    FieldError(
                        detail="Must be at least 1."
                        if page_size < 1
                        else f"Must be at most {max_page_size}.",
                        code="too-small" if page_size < 1 else "too-large",
                        parameter="page_size",
                    )
                ]
            )

        # set filters
        filter_dict = {}
        if "user_id" in kwargs:
            filter_dict["user_id"] = kwargs["user_id"]
        if page_token != "":
            filter_dict["_id"] = {"$lt": _page_token_id(page_token)}

        # get resources, and one more, to tell whether older runs are left
        resources = list(
            self.db_client.find(
                filter=filter_dict,
                projection={
                    "run_log.run_id": True,
                    "run_log.state": True,
                    "run_log.request.tags": True,
                    "run_log.run_log.start_time": True,
                    "run_log.run_log.end_time": True,
                },
            )
            .sort("_id", -1)
            .limit(page_size + 1)
        )

        # set next page token: only if older runs are left
        next_page_token = ""
        if len(resources) > page_size:
            resources = resources[:page_size]
            next_page_token = str(resources[-1]["_id"])

        # format response: `RunSummary`, which WES 2.0.0 requires
        runs = [
            self._run_summary(
                run_log=run["run_log"],
                max_tags_size=self.config.post_runs.max_tags_size,
            )
            for run in resources
        ]

        return {"next_page_token": next_page_token, "runs": runs}

    def get_run_log(
        self,
        run_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return detailed information about a workflow run.

        Controller for `GET /runs/{run_id}`.

        Args:
            run_id: Workflow run identifier.
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            Response object according to WES API schema `RunLog`. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

        Raises:
            pro_wes.exceptions.Forbidden: The requester is not allowed to access the
                resource.
            pro_wes.exceptions.RunNotFound: The requested workflow run is not available.
        """
        # retrieve workflow run
        document = self.db_client.find_one(
            filter={"run_log.run_id": run_id},
            projection={
                "user_id": True,
                "run_log": True,
                "_id": False,
            },
        )

        # raise error if workflow run was not found
        if document is None:
            logger.error("Run '%s' not found.", run_id)
            raise RunNotFound

        # raise error trying to access workflow run that is not owned by user
        # only if authorization enabled
        self._check_access_permission(
            resource_id=run_id,
            owner=document.get("user_id", None),
            requester=kwargs.get("user_id"),
        )

        return self._run_log_response(run_id=run_id, run_log=document["run_log"])

    def get_run_status(
        self,
        run_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return status information about a workflow run.

        Controller for `GET /runs/{run_id}/status`.

        Args:
            run_id: Workflow run identifier.
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            Response object according to WES API schema `RunStatus`. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

        Raises:
            pro_wes.exceptions.Forbidden: The requester is not allowed to access the
                resource.
            pro_wes.exceptions.RunNotFound: The requested workflow run is not available.
        """
        # retrieve workflow run
        document = self.db_client.find_one(
            filter={"run_log.run_id": run_id},
            projection={
                "user_id": True,
                "run_log.state": True,
                "_id": False,
            },
        )

        # ensure resource is available
        if document is None:
            logger.error("Run '%s' not found.", run_id)
            raise RunNotFound

        # ensure requester has access
        self._check_access_permission(
            resource_id=run_id,
            owner=document.get("user_id", None),
            requester=kwargs.get("user_id"),
        )

        return {
            "run_id": run_id,
            "state": document["run_log"]["state"],
        }

    def cancel_run(
        self,
        run_id: str,
        **kwargs: Any,
    ) -> dict[str, str]:
        """Cancel workflow run.

        Controller for `POST /runs/{run_id}/cancel`.

        Only runs in a cancelable state (:py:attr:`State.is_cancelable`), and
        runs of unknown state or that proWES itself set to `SYSTEM_ERROR`
        (e.g., it lost track of them) that have a remote run, are canceled at
        the WES instance; others (e.g., finished runs) are refused without
        contacting it. The stored state is left as it is, also if the WES
        instance refuses the cancellation or can't be reached.

        Args:
            run_id: Workflow run identifier.
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            Workflow run identifier, once the WES instance has accepted the
            cancellation.

        Raises:
            pro_wes.exceptions.Forbidden: The requester is not allowed to access the
                resource, or the WES instance answered with `403`.
            pro_wes.exceptions.RunNotFound: The requested workflow run is not available.
            pro_wes.exceptions.RunNotCancelable: The workflow run is not in a
                cancelable state.
            pro_wes.exceptions.WesEndpointProblem: The workflow run has no remote
                run to cancel.
            pro_wes.exceptions.BadRequest: The WES instance answered with
                `400`.
            pro_wes.exceptions.Unauthorized: The WES instance answered with
                `401`.
            pro_wes.exceptions.EngineRefused: The WES instance answered with
                another error (e.g., `404`, `500`).
            pro_wes.exceptions.EngineInvalidResponse: The WES instance sent
                an invalid response.
            pro_wes.exceptions.EngineUnavailable: The WES instance couldn't
                be reached, or its response not read.
        """
        # retrieve workflow run
        document = self.db_client.find_one(
            filter={"run_log.run_id": run_id},
            projection={
                "user_id": True,
                "run_log.state": True,
                "system_error_by_prowes": True,
                "wes_endpoint.host": True,
                "wes_endpoint.base_path": True,
                "wes_endpoint.run_id": True,
                "_id": False,
            },
        )

        # ensure resource is available
        if document is None:
            logger.error("Run '%s' not found.", run_id)
            raise RunNotFound

        # ensure requester has access
        self._check_access_permission(
            resource_id=run_id,
            owner=document.get("user_id", None),
            requester=kwargs.get("user_id"),
        )

        wes_endpoint: dict[str, str] = document.get("wes_endpoint") or {}
        # a remote run needs a host and a remote run identifier, and a base path
        # (which may be empty)
        has_remote_run = (
            bool(wes_endpoint.get("host"))
            and wes_endpoint.get("base_path") is not None
            and bool(wes_endpoint.get("run_id"))
        )

        # ensure workflow run is cancelable; runs stored without (valid) state
        # are of unknown state (as in `RunLog`); runs of unknown state are
        # cancelable once they have a remote run (e.g., forwarded, but not yet
        # tracked, or reported as `UNKNOWN` by the WES instance), and so are
        # runs that proWES itself set to `SYSTEM_ERROR` (e.g., it lost track of
        # them), as they may still be running at the WES instance
        try:
            state = State(document["run_log"].get("state") or State.UNKNOWN)
        except ValueError:
            state = State.UNKNOWN
        remote_state_unknown = state is State.UNKNOWN or (
            state is State.SYSTEM_ERROR
            and document.get("system_error_by_prowes") is True
        )
        if not (state.is_cancelable or (remote_state_unknown and has_remote_run)):
            logger.error("Run '%s' not cancelable in state '%s'.", run_id, state.value)
            raise RunNotCancelable

        # ensure workflow run has a remote run
        if not has_remote_run:
            logger.error("Run '%s' has no remote run to cancel.", run_id)
            raise WesEndpointProblem

        # cancel workflow run
        wes_client: WesClient = WesClient(
            host=wes_endpoint["host"],
            base_path=wes_endpoint["base_path"],
            # no credentials for WES instances (`auth: none`) until #120;
            # the client's token is never passed on
            token=None,
        )
        try:
            response = wes_client.cancel_run(
                run_id=wes_endpoint["run_id"],
                timeout=self.config.defaults.timeout,
            )
        except ValueError as exc:
            # raised by the client for successful responses that are no
            # `RunId`; its other `ValueError` (an invalid timeout) can't
            # occur, as the timeout comes from the validated configuration
            logger.error(
                "WES instance at %s sent an invalid response to the"
                " cancellation of run '%s'.",
                wes_client.url,
                run_id,
            )
            raise EngineInvalidResponse from exc
        if isinstance(response, ErrorResponse):
            logger.error(
                "WES instance at %s answered the cancellation of run '%s'"
                " with status %s.",
                wes_client.url,
                run_id,
                response.status_code,
            )
            raise REMOTE_ERRORS.get(response.status_code, EngineRefused)

        return {"run_id": run_id}

    def _create_run_environment(
        self,
        document: DbDocument,
        uploads: list[Upload],
    ) -> DbDocument:
        """Set up run environment.

        Create unique run identifier and permanent and temporary storage directories for
        current run.

        Args:
            document: Document to be inserted into the run collection.
            uploads: Workflow attachments of the run, checked and read (cf.
                `_read_attachments()`).

        Returns:
            document: Updated documented as was inserted into the database.

        Raises:
            pro_wes.exceptions.StorageUnavailableProblem: Raised if configured storage
                location is unavailable for writing.
            pro_wes.exceptions.IdsUnavailableProblem: Raised if no unique run identifier
                could be found.
        """
        controller_config = self.config.post_runs
        # try until unused run id was found
        attempt = 1
        while attempt <= controller_config.db_insert_attempts:
            attempt += 1
            run_id = generate_id(
                charset=controller_config.id_charset,
                length=controller_config.id_length,
            )
            work_dir: Path = Path(controller_config.storage_path).resolve() / run_id

            # try to create working directory
            try:
                work_dir.mkdir(parents=False, exist_ok=False)
            except FileExistsError:
                continue
            except FileNotFoundError as exc:
                raise StorageUnavailableProblem from exc

            # populate document
            document.run_log.run_id = run_id
            document.task_id = uuid()
            document.work_dir = str(work_dir)
            document.attachments = [
                Attachment(filename=upload.filename, path=str(work_dir / upload.name))
                for upload in uploads
            ]

            # insert document into database
            try:
                self.db_client.insert_one(
                    document.model_dump(
                        exclude_none=True,
                    )
                )
            except DuplicateKeyError:
                work_dir.rmdir()
                continue
            return document

        raise IdsUnavailableProblem

    def _read_attachments(self) -> list[Upload]:
        """Check and read the workflow attachments of the request.

        Each attachment gets a safe file name (cf. `_secure_filename()`):
        without directories, so that it is written to the working directory
        of the run, never elsewhere.

        Returns:
            Workflow attachments, in the order of the request.

        Raises:
            pro_wes.exceptions.TooManyAttachments: The request has more
                attachments than `custom.post_runs.max_attachments`.
            pro_wes.exceptions.AttachmentTooLarge: An attachment is larger
                than `custom.post_runs.max_attachment_size`.
            pro_wes.exceptions.InvalidAttachmentName: An attachment has no file
                name, a safe file name longer than `MAX_FILENAME_BYTES`, or the
                same safe file name as another one.
        """
        controller_config = self.config.post_runs
        files = request.files.getlist("workflow_attachment")
        if len(files) > controller_config.max_attachments:
            raise TooManyAttachments(
                f"More than {controller_config.max_attachments} workflow attachments"
            )
        uploads: list[Upload] = []
        names: set[str] = set()
        for file in files:
            if not file.filename:
                raise InvalidAttachmentName("Workflow attachment has no filename.")
            name = str(self._secure_filename(name=Path(file.filename)))
            if len(name.encode()) > MAX_FILENAME_BYTES:
                raise InvalidAttachmentName("File name of attachment too long")
            if name in names:
                raise InvalidAttachmentName(f"Attachments with the same name: {name}")
            names.add(name)
            # one byte more than allowed, to tell whether it is larger
            content = file.stream.read(controller_config.max_attachment_size + 1)
            if len(content) > controller_config.max_attachment_size:
                raise AttachmentTooLarge(
                    f"Attachment larger than {controller_config.max_attachment_size}"
                    " bytes"
                )
            uploads.append(Upload(filename=file.filename, name=name, content=content))
        return uploads

    @staticmethod
    def _validate_run_request(
        form_data: ImmutableMultiDict[str, Any],
        url_credentials: UrlCredentialsMode,
        max_tags_size: int,
    ) -> RunRequest:
        """Convert run request form input to `RunRequest` model.

        Credentials in the URLs of the request are refused or removed (cf.
        `custom.post_runs.url_credentials`): in `workflow_url`, and in the
        strings (keys and values) of `workflow_params`,
        `workflow_engine_parameters` and `tags`, at any depth. Fields without
        credentials are kept as sent. Tags are limited in size, as stored
        (cf. `custom.post_runs.max_tags_size`), so that run lists are too.

        Args:
            form_data: Flask data structure for representing form values.
            url_credentials: Whether runs with credentials in URLs are
                rejected (`reject`), or the credentials removed (`strip`).
            max_tags_size: Maximum size of the JSON text of the tags, as
                stored, in bytes (UTF-8).

        Returns:
            The run request, without credentials in URLs.

        Raises:
            pro_wes.exceptions.WorkflowEngineNotSupported: The request
                chooses a workflow engine or its version (`ENGINE_FIELDS`,
                not empty).
            pro_wes.exceptions.UrlCredentials: A URL has credentials, and
                `url_credentials` is `reject`.
            pro_wes.exceptions.MalformedUrl: A URL is malformed.
            foca.errors.InvalidFieldsProblem: The tags are larger than
                `max_tags_size` (`400`).
        """
        dict_of_lists = form_data.to_dict(flat=False)
        # flatten single item lists
        dict_atomic: dict[str, Any] = {
            k: v[0] if len(v) == 1 else v for k, v in dict_of_lists.items()
        }
        # remove 'workflow_attachment' field
        dict_atomic.pop("workflow_attachment", None)
        # empty engine fields choose no engine; others are refused
        for field in ENGINE_FIELDS:
            if dict_atomic.pop(field, "") != "":
                raise WorkflowEngineNotSupported
        model_instance = RunRequest(**dict_atomic)
        updates: dict[str, str] = {}
        stripped = sanitize_url(
            model_instance.workflow_url, "workflow_url", url_credentials
        )
        if stripped is not None:
            updates["workflow_url"] = stripped
        for field in JSON_FIELDS:
            value = getattr(model_instance, field)
            # decodable objects, as validated by the model
            decoded = loads(value)
            if sanitize_json(decoded, field, url_credentials):
                updates[field] = dumps(decoded)
        if _tags_too_large(updates.get("tags", model_instance.tags), max_tags_size):
            raise InvalidFieldsProblem(
                [
                    FieldError(
                        detail=f"Must be at most {max_tags_size} bytes long.",
                        code="too-long",
                        pointer=json_pointer("tags"),
                    )
                ]
            )
        return model_instance.model_copy(update=updates)

    @staticmethod
    def _run_summary(run_log: dict[str, Any], max_tags_size: int) -> dict[str, Any]:
        """Get the summary of a run for `GET /runs` from its stored run log.

        Start and end time come from the engine log of the run (`run_log`),
        and are left out while the WES instance hasn't reported them. Tags
        come from the run request, as an object of strings: values that
        earlier versions stored without checking them (other than strings)
        are JSON-encoded, and credentials in URLs are removed. Tags that
        can't be read (e.g., with malformed URLs) are returned as `{}`, with
        an error logged, rather than failing the whole list; so are tags
        larger than `max_tags_size` (e.g., of runs stored before the limit),
        with a warning, without decoding them.

        Args:
            run_log: Run log, as stored (projected to the fields needed).
            max_tags_size: Maximum size of the JSON text of the tags, as
                stored, in bytes (UTF-8).

        Returns:
            Response object according to WES API schema `RunSummary`.
        """
        summary: dict[str, Any] = {
            "run_id": run_log["run_id"],
            "state": run_log["state"],
        }
        engine_log = run_log.get("run_log") or {}
        for field in ("start_time", "end_time"):
            if engine_log.get(field) is not None:
                summary[field] = engine_log[field]
        tags: dict[str, str] | None = {}
        stored_tags = (run_log.get("request") or {}).get("tags")
        if _tags_too_large(stored_tags, max_tags_size):
            logger.warning(
                "Tags of run '%s' are larger than custom.post_runs.max_tags_size"
                " (%d bytes); returned as {}.",
                summary["run_id"],
                max_tags_size,
            )
            stored_tags = None
        if stored_tags not in (None, ""):
            try:
                decoded = _decode_stored_json(stored_tags, "tags")
                tags = _string_values(decoded) if isinstance(decoded, dict) else None
            except (BadRequest, RecursionError, TypeError, ValueError):
                tags = None
            if tags is None:
                logger.error(
                    "Tags of run '%s' can't be listed; returned as {}.",
                    summary["run_id"],
                )
                tags = {}
        summary["tags"] = tags
        return summary

    @staticmethod
    def _run_log_response(run_id: str, run_log: dict[str, Any]) -> dict[str, Any]:
        """Get the response to `GET /runs/{run_id}` from a stored run log.

        The run log is validated against its model (`RunLog`), fields without
        value are left out, and the JSON fields of the run request are
        returned as objects, as in the WES specification (the form of `POST
        /runs` has them as strings), so that the response conforms to the
        specification. Credentials in the URLs of the run request are removed,
        for runs stored before they were checked. Values other than strings
        of `tags` and `workflow_engine_parameters`, which versions before
        0.20.0 accepted, are returned as their JSON text (e.g., `1` as
        `"1"`), as the specification has strings only.

        Args:
            run_id: Workflow run identifier.
            run_log: Run log, as stored.

        Returns:
            Response object according to WES API schema `RunLog`.

        Raises:
            pro_wes.exceptions.InternalServerError: The stored run log doesn't
                conform to its model, or its run request has malformed URLs,
                or values of `tags` or `workflow_engine_parameters` that
                can't be returned as JSON text (e.g., `1e999`).
        """
        try:
            model = RunLog.model_validate(run_log, context=STORED_CONTEXT)
        except ValidationError as exc:
            logger.error("Run log of run '%s' doesn't conform to its model.", run_id)
            raise InternalServerError from exc
        response: dict[str, Any] = model.model_dump(mode="json", exclude_none=True)
        request_data = response.get("request")
        if request_data is None:
            return response
        # without credentials, also for runs stored by earlier versions, which
        # didn't check them
        try:
            stripped = sanitize_url(
                request_data["workflow_url"], "workflow_url", "strip"
            )
            if stripped is not None:
                request_data["workflow_url"] = stripped
            for field in JSON_FIELDS:
                if field in request_data:
                    decoded = _decode_stored_json(request_data[field], field)
                    # objects: `RunRequest.json_serialized_object_field_valid`
                    # refuses others and makes empty values `{}`, also for
                    # stored runs (`STORED_CONTEXT`)
                    if field in STRING_OBJECT_FIELDS:
                        decoded = _string_values(decoded)
                    request_data[field] = decoded
        except BadRequest as exc:
            logger.error("Run request of run '%s' has malformed URLs.", run_id)
            raise InternalServerError from exc
        except (RecursionError, ValueError) as exc:
            logger.error(
                "Run request of run '%s' has values that can't be returned as"
                " JSON text.",
                run_id,
            )
            raise InternalServerError from exc
        return response

    @staticmethod
    def _secure_filename(name: Path) -> Path:
        """Return a secure version of a filename.

        The name has no directories (separators are replaced), no leading
        dots and only ASCII characters (cf. `werkzeug.utils.secure_filename`);
        names without any safe character are replaced by a random one.

        Args:
            name: Potentially insecure filename.

        Returns:
            Secure filename.
        """
        name_secure = secure_filename(str(name))
        if not name_secure:
            name_secure = uuid()
        return Path(name_secure)

    @staticmethod
    def _save_attachments(attachments: list[Attachment], uploads: list[Upload]) -> None:
        """Write workflow attachments to storage.

        Files are created exclusively, so that no existing file (or link) is
        overwritten or followed.

        Args:
            attachments: Attachments as stored with the run, with their paths.
            uploads: Their contents, in the same order.

        Raises:
            pro_wes.exceptions.StorageUnavailableProblem: An attachment can't be
                written.
        """
        for attachment, upload in zip(attachments, uploads, strict=True):
            try:
                with open(attachment.path, "xb") as dest:
                    dest.write(upload.content)
            except OSError as exc:
                raise StorageUnavailableProblem from exc

    @staticmethod
    def _check_access_permission(
        resource_id: str,
        owner: str | None = None,
        requester: str | None = None,
    ) -> None:
        """Check whether requester has access to resource.

        Raise `403/Forbidden` response if resource owner is known and requester is not
        owner.

        Args:
            resource_id: Workflow run identifier.
            owner: Identifier of resource owner.
            requester: Unique, persistent identifier of resource requester.

        Raises:
            pro_wes.exceptions.Forbidden: The requester is not allowed to access the
                resource.
        """
        if requester is not None and requester != owner:
            logger.error(
                "User '%s' is not allowed to access workflow run '%s'.",
                requester,
                resource_id,
            )
            raise Forbidden
