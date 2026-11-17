"""WES run states."""

from typing import ClassVar


class States:
    """WES run states."""

    UNDEFINED: ClassVar[list[str]] = [
        "UNKNOWN",
    ]

    CANCELABLE: ClassVar[list[str]] = [
        "INITIALIZING",
        "PAUSED",
        "QUEUED",
        "RUNNING",
    ]

    FINISHED: ClassVar[list[str]] = [
        "COMPLETE",
        "EXECUTOR_ERROR",
        "SYSTEM_ERROR",
        "CANCELED",
    ]

    UNFINISHED: ClassVar[list[str]] = [
        *CANCELABLE,
        "CANCELING",
    ]

    ALL: ClassVar[list[str]] = FINISHED + UNFINISHED + UNDEFINED
