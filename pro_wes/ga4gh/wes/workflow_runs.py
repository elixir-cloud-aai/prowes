"""Controllers for the `/runs` and children routes."""

from json import dumps, loads
import logging
from pathlib import Path
from typing import Any, NamedTuple

from bson.objectid import ObjectId
from celery import uuid

# The implementation of the controllers still reads the request and its files
# from Flask, until they are read in `controllers.py` (#117)
from flask import request  # ast-grep-ignore: framework-free-tasks
from foca import get_collection, get_custom_config
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
    EngineUnavailable,
    Forbidden,
    IdsUnavailableProblem,
    InternalServerError,
    InvalidAttachmentName,
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

# Fields of WES 1.1.0 run requests that choose the workflow engine; refused
# until they are forwarded (#66), so that the choice isn't lost
ENGINE_FIELDS = ("workflow_engine", "workflow_engine_version")


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
        )

        # check and read workflow attachments, before anything is stored
        uploads = self._read_attachments()

        # get and attach suitable WES endpoint
        document.wes_endpoint = WesEndpoint(
            # The only backend until backends are configurable (#118)
            host="https://wes-na.cloud.e-infra.cz",  # ast-grep-ignore: hard-coded-url
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
                ).update_run_state(state=State.SYSTEM_ERROR.value)
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
            token=kwargs.get("jwt"),
        )

        # instantiate database connector
        db_connector = DbDocumentConnector(
            collection=self.db_client,
            task_id=document_stored.task_id,
        )

        # forward incoming WES request and validate response
        url = (
            f"{document_stored.wes_endpoint.host.rstrip('/')}/"
            f"{document_stored.wes_endpoint.base_path.strip('/')}"
        )
        logger.info(
            "Sending workflow run '%s' with task identifier '%s' to WES"
            " endpoint hosted at: %s",
            document_stored.run_log.run_id,
            document_stored.task_id,
            url,
        )
        try:
            response = wes_client.post_run(
                form_data=document.run_log.request.model_dump(),
                timeout=controller_config.timeout_post,
            )
        except EngineUnavailable:
            db_connector.update_run_state(state=State.SYSTEM_ERROR.value)
            raise
        if isinstance(response, ErrorResponse):
            db_connector.update_run_state(state=State.SYSTEM_ERROR.value)
            if response.status_code == 400:
                raise BadRequest
            if response.status_code == 401:
                raise Unauthorized
            if response.status_code == 403:
                raise Forbidden
            raise InternalServerError
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
                "jwt": kwargs.get("jwt"),
                "remote_host": updated_document_stored.wes_endpoint.host,
                "remote_base_path": updated_document_stored.wes_endpoint.base_path,
                "remote_run_id": updated_document_stored.wes_endpoint.run_id,
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
        """
        # set query params
        page_size: int = kwargs.get(
            "page_size", self.config.list_runs.default_page_size
        )
        page_token = kwargs.get("page_token", "")

        # set filters
        filter_dict = {}
        if "user_id" in kwargs:
            filter_dict["user_id"] = kwargs["user_id"]
        if page_token != "":
            filter_dict["_id"] = {"$lt": ObjectId(page_token)}

        # get resources
        resources = list(
            self.db_client.find(
                filter=filter_dict,
                projection={
                    "run_log.run_id": True,
                    "run_log.state": True,
                },
            )
            .sort("_id", -1)
            .limit(page_size)
        )

        # set next page token
        next_page_token = str(resources[-1]["_id"]) if resources else ""

        # format response
        for run in resources:
            run["run_id"] = run["run_log"]["run_id"]
            run["state"] = run["run_log"]["state"]
            del run["run_log"]
            del run["_id"]

        return {"next_page_token": next_page_token, "runs": resources}

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

        Controller for `GET /runs/{run_id}/cancel`.

        Args:
            run_id: Workflow run identifier.
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            Workflow run identifier.

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

        # cancel workflow run
        wes_client: WesClient = WesClient(
            host=document["wes_endpoint"]["host"],
            base_path=document["wes_endpoint"]["base_path"],
            token=kwargs.get("jwt"),
        )
        wes_client.cancel_run(
            run_id=document["wes_endpoint"]["run_id"],
            timeout=self.config.defaults.timeout,
        )

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
        url_credentials: UrlCredentialsMode = "reject",
    ) -> RunRequest:
        """Convert run request form input to `RunRequest` model.

        Credentials in the URLs of the request are refused or removed (cf.
        `custom.post_runs.url_credentials`): in `workflow_url`, and in the
        strings (keys and values) of `workflow_params`,
        `workflow_engine_parameters` and `tags`, at any depth. Fields without
        credentials are kept as sent.

        Args:
            form_data: Flask data structure for representing form values.
            url_credentials: Whether runs with credentials in URLs are
                rejected (`reject`), or the credentials removed (`strip`).

        Returns:
            The run request, without credentials in URLs.

        Raises:
            pro_wes.exceptions.WorkflowEngineNotSupported: The request
                chooses a workflow engine or its version (`ENGINE_FIELDS`,
                not empty).
            pro_wes.exceptions.UrlCredentials: A URL has credentials, and
                `url_credentials` is `reject`.
            pro_wes.exceptions.MalformedUrl: A URL is malformed.
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
        return model_instance.model_copy(update=updates)

    @staticmethod
    def _run_log_response(run_id: str, run_log: dict[str, Any]) -> dict[str, Any]:
        """Get the response to `GET /runs/{run_id}` from a stored run log.

        The run log is validated against its model (`RunLog`), fields without
        value are left out, and the JSON fields of the run request are
        returned as objects, as in the WES specification (the form of `POST
        /runs` has them as strings), so that the response conforms to the
        specification. Credentials in the URLs of the run request are removed,
        for runs stored before they were checked.

        Args:
            run_id: Workflow run identifier.
            run_log: Run log, as stored.

        Returns:
            Response object according to WES API schema `RunLog`.

        Raises:
            pro_wes.exceptions.InternalServerError: The stored run log doesn't
                conform to its model, or its run request has malformed URLs.
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
                    decoded = loads(request_data[field])
                    sanitize_json(decoded, field, "strip")
                    request_data[field] = decoded
        except BadRequest as exc:
            logger.error("Run request of run '%s' has malformed URLs.", run_id)
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
