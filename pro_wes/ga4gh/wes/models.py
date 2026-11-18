"""proWES schema models."""

from enum import Enum, EnumMeta
from json import JSONDecodeError, loads
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationInfo,
    field_validator,
    model_validator,
)

from pro_wes.exceptions import NoSuitableEngine
from pro_wes.ga4gh.service_info.models import Service
from pro_wes.utils.config import workflow_type_versions

# Validation context of stored documents (e.g., `DbDocument`): their run
# requests aren't checked against the service info of the process reading
# them (cf. `RunRequest.workflow_type_and_version_supported`)
STORED_KEY = "stored"
STORED_CONTEXT = {STORED_KEY: True}


def _without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Make an object of decoded JSON, refusing duplicate keys.

    Args:
        pairs: Keys and values of the object, in order.

    Returns:
        The object.

    Raises:
        JSONDecodeError: A key occurs more than once.
    """
    result = dict(pairs)
    if len(result) != len(pairs):
        raise JSONDecodeError("duplicate keys", "", 0)
    return result


class MetaEnum(EnumMeta):
    """Metaclass for enumerators."""

    def __contains__(cls, item: object) -> bool:
        """Tell whether a value is a value of the enumerator."""
        try:
            cls(item)
        except ValueError:
            return False
        return True


class BaseEnum(Enum, metaclass=MetaEnum):
    """Base class for enumerators."""


class Attachment(BaseModel):
    """Model for workflow attachment.

    Stored with the run; the contents are written to the storage of the runs
    only (not to the database).

    Args:
        filename: Name of the file as indicated in the run request.
        path: Path to the file on the app's storage system, inside the
            working directory of the run, with a safe file name.
    """

    filename: str
    path: str


class RunRequest(BaseModel):
    """Model for WES API `RunRequest` schema.

    `workflow_engine` and `workflow_engine_version` of WES 1.1.0 are not
    modeled: requests that set them are refused before the model is built
    (`pro_wes.ga4gh.wes.workflow_runs.ENGINE_FIELDS`, #66).

    Args:
        workflow_params: Workflow parameters. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`
        workflow_type: Workflow type. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`
        workflow_type_version: Workflow type version. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`
        tags: Tags. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`
        workflow_engine_parameters: Workflow engine parameters. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`
        workflow_url: Workflow URL. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

    Attributes:
        workflow_params: Workflow parameters. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`
        workflow_type: Workflow type. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`
        workflow_type_version: Workflow type version. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`
        tags: Tags. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`
        workflow_engine_parameters: Workflow engine parameters. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`
        workflow_url: Workflow URL. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

    Raises:
        pro_wes.exceptions.ValidationError: The class was instantianted with an
            illegal data type.
    """

    workflow_params: str
    workflow_type: str
    workflow_type_version: str
    tags: str | None = "{}"
    workflow_engine_parameters: str | None = "{}"
    workflow_url: str

    # Validation errors, which are logged, without the values, which may hold
    # credentials (cf. `custom.post_runs.url_credentials`)
    model_config = ConfigDict(hide_input_in_errors=True)

    @field_validator(
        "workflow_type",
        "workflow_type_version",
        "workflow_url",
    )
    @classmethod
    def required_str_field_not_empty(
        cls,
        value: str,
    ) -> str:
        """Ensure that required strings are not empty.

        Args:
            value: Field value to be validated.

        Returns:
            Validated field value.

        Raises:
            ValueError: The value is either missing or constitutes an empty
                string.
        """
        if value == "" or value is None:
            raise ValueError("field required")
        return value

    @field_validator(
        "workflow_params",
        "tags",
        "workflow_engine_parameters",
    )
    @classmethod
    def json_serialized_object_field_valid(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str:
        """Ensure that a string can be JSON deserialized into an object.

        Due to limitations of OpenAPI, the form data fields indicated in the
        decorator were defined in the WES API specification as atomic strings,
        even though those strings are expected to be JSON-serialized
        representations of objects. This validator ensures that the values
        provided to these form fields can indeed be deserialized into objects.

        Note that the WES specification does not currently dictate how clients
        should indicate that no value is to be specified for optional fields.
        In order to maintain compatibility with upstream clients, while
        ensuring that a JSON deserializable representation is propagated,
        empty strings, missing field values and `null` for are set to the
        empty JSON string object '{}' for optional fields and cause a
        `ValueError` to be raised for required fields.

        The values of `tags` and `workflow_engine_parameters` must be strings,
        as in the WES specification (not checked for stored runs, validation
        context `STORED_CONTEXT`).

        Args:
            value: Field value to be validated.
            info: Validation context, with the name of the field.

        Returns:
            Validated (and possibly sanitized) field value.

        Raises:
            ValueError: The value is required but it is either missing or does
                not represent any actual content.
            ValueError: The value could not be JSON deserialized, or has an
                object with duplicate keys (not checked for stored runs).
            ValueError: The value could be JSON decoded, but the deserialized
                value does not represent an object/dictionary.
            ValueError: The tags or workflow engine parameters have values
                other than strings.
        """
        if value is None or value in {"", "null"}:
            if info.field_name == "workflow_params":
                raise ValueError("field required")
            return "{}"
        stored = bool(info.context and info.context.get(STORED_KEY))
        try:
            # duplicate keys refused for new requests, as decoding keeps only
            # the last value, so that the others (e.g., with credentials in
            # URLs) would be forwarded unchecked
            decoded = loads(
                value, object_pairs_hook=dict if stored else _without_duplicates
            )
        except (JSONDecodeError, RecursionError) as exc:
            # also JSON too deeply nested to decode
            raise ValueError("could not be JSON deserialized") from exc
        if not isinstance(decoded, dict):
            raise ValueError("could not be interpreted as object")
        # objects of strings, as the WES specification defines them; not
        # checked for stored runs, which earlier versions accepted without
        if (
            info.field_name != "workflow_params"
            and not stored
            and not all(isinstance(item, str) for item in decoded.values())
        ):
            raise ValueError("values must be strings")
        return value

    @model_validator(mode="after")
    def workflow_type_and_version_supported(self, info: ValidationInfo) -> "RunRequest":
        """Ensure that this instance supports the workflow type and version.

        Requests of stored runs (validation context `STORED_CONTEXT`) are
        not checked: the app accepted them, possibly with another
        configuration than that of the process reading them (e.g., the
        worker).

        Args:
            info: Validation context.

        Returns:
            The validated model.

        Raises:
            NoSuitableEngine: No suitable workflow engine known to process request.
        """
        if info.context and info.context.get(STORED_KEY):
            return self
        type_versions = workflow_type_versions()
        _type = self.workflow_type
        version = self.workflow_type_version
        if _type not in type_versions or version not in (
            type_versions[_type].get("workflow_type_version") or []
        ):
            raise NoSuitableEngine(
                f"No suitable workflow engine known for workflow type '{_type}' and"
                f" version '{version}'; supported workflow engines: {type_versions}"
            )
        return self


class Log(BaseModel):
    """Model for task log, including the log of the workflow engine itself.

    Cf. `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

    Attributes:
        cmd: List of executed commands.
        end_time: Time when the task stopped executing, in ISO 8601 format
            "%Y-%m-%dT%H:%M:%SZ".
        exit_code: Exit code of the task.
        name: The name of the task.
        start_time: Time when the task started executing, in ISO 8601 format
            "%Y-%m-%dT%H:%M:%SZ".
        stderr: A URL to retrieve standard error logs of the task.
        stdout: A URL to retrieve standard output logs of the task.
        system_logs: Logs of the system that are relevant to the task or
            run, but not tied to it (e.g., the error message of a
            `SYSTEM_ERROR`); WES 1.1.0.
    """

    cmd: list[str] | None = []
    end_time: str | None = None
    exit_code: int | None = None
    name: str | None = None
    start_time: str | None = None
    stderr: str | None = None
    stdout: str | None = None
    system_logs: list[str] | None = None


class State(BaseEnum):
    """Enumerator for supported workflow run states.

    Cf. `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

    Attributes:
        UNKNOWN: The state of the task is unknown.
        QUEUED: The task is queued.
        INITIALIZING: The task has been assigned to a worker and is currently
            preparing to run.
        RUNNING: The task is running.
        PAUSED: The task is paused.
        COMPLETE: The task has completed running.
        EXECUTOR_ERROR: The task encountered an error in one of the Executor
            processes.
        SYSTEM_ERROR: The task was stopped due to a system error.
        CANCELED: The task was canceled by the user.
        CANCELING: The task was canceled by the user, and is in the process of
            stopping.
        PREEMPTED: The task was stopped (preempted) by the system (WES
            1.1.0), e.g., as the resources it ran on were reclaimed; final:
            proWES doesn't resubmit the run.
    """

    UNKNOWN = "UNKNOWN"
    QUEUED = "QUEUED"
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETE = "COMPLETE"
    EXECUTOR_ERROR = "EXECUTOR_ERROR"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    CANCELED = "CANCELED"
    CANCELING = "CANCELING"
    PREEMPTED = "PREEMPTED"

    @property
    def is_finished(self) -> bool:
        """Check if a state is among the set of finished states."""
        return self in (
            self.COMPLETE,
            self.EXECUTOR_ERROR,
            self.SYSTEM_ERROR,
            self.CANCELED,
            self.PREEMPTED,
        )

    @property
    def is_cancelable(self) -> bool:
        """Check if a state is among the set of cancelable states."""
        return self in (
            self.QUEUED,
            self.INITIALIZING,
            self.RUNNING,
            self.PAUSED,
        )


class RunLog(BaseModel):
    """Model for entire workflow run log.

    Args:
        outputs: Name and destination of workflow outputs.
        request: Form data passed during original workflow run request.
        run_id: Unique identifier of workflow run.
        run_log: Log of workflow engine itself.
        state: State of workflow run.
        task_logs: List of workflow task/job logs.

    Attributes:
        outputs: Name and destination of workflow outputs.
        request: Form data passed during original workflow run request.
        run_id: Unique identifier of workflow run.
        run_log: Log of workflow engine itself.
        state: State of workflow run.
        task_logs: List of workflow task/job logs.
    """

    outputs: dict[str, Any] | None = None
    request: RunRequest | None = None
    run_id: str | None = None
    run_log: Log | None = None
    state: State = State.UNKNOWN
    task_logs: list[Log] | None = []

    # States as their values, also the default (stored as is)
    model_config = ConfigDict(use_enum_values=True, validate_default=True)


class WesEndpoint(BaseModel):
    """Model for information on the external WES endpoint.

    The endpoint to which the incoming workflow run request was relayed.

    Args:
        host: Host at which the WES API is served that is processing this
            request; note that this should include the path information but
            *not* the base path path defined in the WES API specification;
            e.g., specify https://my.wes.com/api if the actual API is hosted at
            https://my.wes.com/api/ga4gh/wes/v1.
        base_path: Override the default path suffix defined in the WES API
            specification, i.e., `/ga4gh/wes/v1`.
        run_id: Identifier for workflow run on external WES endpoint.

    Attributes:
        host: Host at which the WES API is served that is processing this
            request; note that this should include the path information but
            *not* the base path path defined in the WES API specification;
            e.g., specify https://my.wes.com/api if the actual API is hosted at
            https://my.wes.com/api/ga4gh/wes/v1.
        base_path: Override the default path suffix defined in the WES API
            specification, i.e., `/ga4gh/wes/v1`.
        run_id: Identifier for workflow run on external WES endpoint.
    """

    host: str
    base_path: str | None = "/ga4gh/wes/v1"
    run_id: str | None = None


class DbDocument(BaseModel):
    """Model for workflow run request database document.

    Args:
        attachments: Names of attached files.
        run_log: Complete logging information for workflow run.
        task_id: Identifier of worker task.
        user_id: Identifier of resource owner.
        wes_endpoint: Information about the endpoint to where the run request
            was forwarded.
        work_dir: Working directory for workflow run.

    Attributes:
        attachments: Names of attached files.
        run_log: Complete logging information for workflow run.
        task_id: Identifier of worker task.
        user_id: Identifier of resource owner.
        wes_endpoint: Information about the endpoint to where the run request
            was forwarded.
        work_dir: Working directory for workflow run.
    """

    attachments: list[Attachment] = []
    run_log: RunLog = RunLog()
    task_id: str | None = None
    user_id: str | None = None
    work_dir: str | None = None
    wes_endpoint: WesEndpoint | None = None


class RunId(BaseModel):
    """Response model for `POST /runs`.

    Args:
        run_id: Workflow run identifier.

    Attributes:
        run_id: Workflow run identifier.
    """

    run_id: str


class RunStatus(BaseModel):
    """Response model for `GET /runs/{run_id}/status`.

    Args:
        run_id: Workflow run identifier.
        state: Workflow run state.

    Attributes:
        run_id: Workflow run identifier.
        state: Workflow run state.
    """

    run_id: str
    state: State


class ErrorResponse(BaseModel):
    """Response model for WES errors.

    Args:
        msg: Detailed error message.
        status_code: HTTP status code.

    Attributes:
        msg: Detailed error message.
        status_code: HTTP status code.
    """

    msg: str | None = None
    status_code: int


class RunListResponse(BaseModel):
    """List of workflow runs that the service has processed or is processing.

    Args:
        runs: List of runs, indicating the run identifier and status for each.
        next_page_token: Token to receive the next page of results.
    """

    runs: list[RunStatus] | None = []
    next_page_token: str | None = None


class WorkflowTypeVersion(BaseModel):
    """Available workflow types supported by a given instance of the service.

    Args:
        workflow_type_version: List of one or more acceptable versions for the
            workflow type.
    """

    workflow_type_version: list[str] | None = []


class WorkflowEngineVersion(BaseModel):
    """Available versions of a workflow engine (WES 1.1.0).

    Args:
        workflow_engine_version: List of one or more available versions of
            the workflow engine; an array, as in the WES specification (not
            `null`).
    """

    workflow_engine_version: list[str] = []


class DefaultWorkflowEngineParameter(BaseModel):
    """Model for default workflow engine parameters.

    Args:
        name: Parameter name.
        type: Parameter type.
        default_value: Stringified version of default parameter.

    Attributes:
        name: Parameter name.
        type: Parameter type.
        default_value: Stringified version of default parameter.
    """

    name: str | None = None
    type: str | None = None
    default_value: str | None = None


class ServiceInfoWesBase(BaseModel):
    """Base model for WES-specific service info fields.

    Args:
        workflow_type_versions: Workflow types and versions supported by this
            service.
        supported_wes_versions: The version(s) of the WES schema supported by
            this service.
        supported_filesystem_protocols: The filesystem protocols supported by
            this service.
        workflow_engine_versions: Workflow engine versions supported by this
            service.
        default_workflow_engine_parameters: Default workflow engine parameters
            set by this service.
        auth_instructions_url: URL to web page with human-readable instructions
            on how to get an authorization token for use with this service.
        tags: Additional information about this service as key-value pairs.

    Attributes:
        workflow_type_versions: Workflow types and versions supported by this
            service.
        supported_wes_versions: The version(s) of the WES schema supported by
            this service.
        supported_filesystem_protocols: The filesystem protocols supported by
            this service.
        workflow_engine_versions: Workflow engine versions supported by this
            service.
        default_workflow_engine_parameters: Default workflow engine parameters
            set by this service.
        auth_instructions_url: URL to web page with human-readable instructions
            on how to get an authorization token for use with this service.
        tags: Additional information about this service as key-value pairs.
    """

    workflow_type_versions: dict[str, WorkflowTypeVersion]
    supported_wes_versions: list[str] = []
    supported_filesystem_protocols: list[str] = []
    workflow_engine_versions: dict[str, WorkflowEngineVersion]
    default_workflow_engine_parameters: list[DefaultWorkflowEngineParameter] = []
    auth_instructions_url: str
    tags: dict[str, str]


class ServiceInfoBase(ServiceInfoWesBase, Service):
    """Based model for service info."""


class ServiceInfo(ServiceInfoBase):
    """Full model for service info.

    Checks the service info that FOCA serves on startup (`pro_wes.app`), as
    FOCA checks the fields of the GA4GH service info only, and the service
    info of remote WES services (`pro_wes.ga4gh.wes.client_wes`).

    Args:
        system_state_counts: State counts for workflow runs available on this
            service.

    Attributes:
        system_state_counts: State counts for workflow runs available on this
            service.
    """

    system_state_counts: dict[str, int]
