"""Celery background task to track the progress of workflow runs."""

import logging
from time import sleep
from typing import Any

from celery import Task
from foca import get_collection, get_custom_config

from pro_wes.celery_worker import celery
from pro_wes.config_models import CustomConfig
from pro_wes.exceptions import EngineProblem, EngineUnavailable, WesEndpointProblem
from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.ga4gh.wes.models import (
    DbDocument,
    RunStatus,
    State,
)
from pro_wes.utils.db import DbDocumentConnector
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB

logger = logging.getLogger(__name__)


@celery.task(
    name="tasks.track_run_progress",
    bind=True,
    ignore_result=True,
    track_started=True,
)
def task__track_run_progress(
    self: Task[Any, Any],
    remote_host: str,
    remote_base_path: str,
    remote_run_id: str,
    jwt: str | None,
) -> str:
    """Relay workflow run request to remote WES and track run progress.

    The collection of the runs is taken from FOCA when the task runs, as
    FOCA replaces the MongoDB client of the worker after each fork (cf.
    :py:func:`foca.get_collection`).

    Args:
        self: The bound task (`bind=True`).
        remote_host: Host at which the WES API is served that is processing
            this request; note that this should include the path information
            but *not* the base path path defined in the WES API specification;
            e.g., specify https://my.wes.com/api if the actual API is hosted at
            https://my.wes.com/api/ga4gh/wes/v1.
        remote_base_path: Override the default path suffix defined in the WES
            API specification, i.e., `/ga4gh/wes/v1`.
        remote_run_id: Workflow run identifier on remote WES service.
        jwt: Authorization bearer token to be passed on with workflow run
            request to remote service.

    Returns:
        Task identifier.

    Raises:
        ValueError: The task has no identifier.
        pro_wes.exceptions.EngineUnavailable: The remote service is unavailable
            or is not a valid WES service.
        pro_wes.exceptions.EngineProblem: The remote service sent too many
            error responses.
        pro_wes.exceptions.WesEndpointProblem: The run document has no
            identifier of the run at the remote service.
    """
    custom_config: CustomConfig = get_custom_config(CustomConfig)
    controller_config = custom_config.post_runs

    task_id = self.request.id
    if task_id is None:
        raise ValueError("Task has no identifier.")
    logger.info("[%s] Start processing...", task_id)

    db_client = DbDocumentConnector(
        collection=get_collection(RUN_DB, RUN_COLLECTION),
        task_id=task_id,
    )

    # update state: INITIALIZING
    db_client.update_run_state(state=State.INITIALIZING.value)

    # instantiate WES client
    wes_client: WesClient = WesClient(
        host=remote_host,
        base_path=remote_base_path,
        token=jwt,
    )

    # fetch run log and upsert database document
    document: DbDocument = _update_run_log(
        db_client=db_client,
        wes_client=wes_client,
        run_id=remote_run_id,
    )

    # track workflow run progress
    run_state: State = State.UNKNOWN
    attempt: int = 1
    while not run_state.is_finished:
        sleep(controller_config.polling_wait)

        # ensure WES endpoint is available
        if document.wes_endpoint is None or document.wes_endpoint.run_id is None:
            raise WesEndpointProblem
        try:
            response = wes_client.get_run_status(
                run_id=document.wes_endpoint.run_id,
                timeout=custom_config.defaults.timeout,
            )
        except EngineUnavailable as exc:
            if attempt <= controller_config.polling_attempts:
                attempt += 1
                logger.warning(exc, exc_info=True)
                continue
            db_client.update_run_state(state=State.SYSTEM_ERROR.value)
            raise
        if not isinstance(response, RunStatus):
            if attempt <= controller_config.polling_attempts:
                attempt += 1
                logger.warning("Received error response: %s", response)
                continue
            db_client.update_run_state(state=State.SYSTEM_ERROR.value)
            raise EngineProblem("Received too many error responses.")
        attempt = 1
        if response.state != run_state:
            run_state = response.state
            db_client.update_run_state(state=run_state.value)

    # fetch final run log and upsert database document
    _update_run_log(
        db_client=db_client,
        wes_client=wes_client,
        run_id=remote_run_id,
    )

    logger.info("[%s] Processing completed.", task_id)
    return task_id


def _update_run_log(
    db_client: DbDocumentConnector,
    wes_client: WesClient,
    run_id: str,
) -> DbDocument:
    """Fetch the run log from the remote WES and store it in the run document.

    The run log is stored without validation, except for the original run
    request, which is left out (workaround for cwl-WES, whose responses don't
    conform to the model).

    Args:
        db_client: Connector to the run document.
        wes_client: Client of the remote WES.
        run_id: Workflow run identifier on remote WES service.

    Returns:
        Updated run document.

    Raises:
        pro_wes.exceptions.EngineUnavailable: The remote service is
            unavailable; the run state is set to `SYSTEM_ERROR`.
    """
    try:
        response = wes_client.get_run(run_id=run_id)
    except EngineUnavailable:
        db_client.update_run_state(state=State.SYSTEM_ERROR.value)
        raise
    response.pop("request", None)
    return db_client.upsert_fields_in_root_object(
        root="run_log",
        **response,
    )
