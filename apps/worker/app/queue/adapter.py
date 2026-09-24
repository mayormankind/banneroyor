"""Queue adapter boundary. The pipeline only sees QueueMessage objects; the
PGMQ implementation can be swapped without touching pipeline code."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class QueueMessage:
    msg_id: int
    read_ct: int
    meeting_id: str
    job_id: str
    job_type: str


class QueueAdapter(Protocol):
    def receive(self, vt_seconds: int, qty: int) -> list[QueueMessage]: ...
    def ack(self, msg_id: int) -> None: ...
    def postpone(self, msg_id: int, delay_seconds: int) -> None: ...
    def archive(self, msg_id: int) -> None: ...


def parse_message(raw: dict[str, Any], msg_id: int, read_ct: int) -> QueueMessage:
    """Validate a raw queue payload. Raises ValueError on bad shape."""
    if not isinstance(raw, dict):
        raise ValueError("queue message is not an object")
    meeting_id = raw.get("meeting_id")
    job_id = raw.get("job_id")
    job_type = raw.get("job_type", "process_meeting")
    if not isinstance(meeting_id, str) or not isinstance(job_id, str):
        raise ValueError("queue message missing meeting_id/job_id")
    if job_type != "process_meeting":
        raise ValueError(f"unsupported job_type: {job_type}")
    return QueueMessage(
        msg_id=msg_id,
        read_ct=read_ct,
        meeting_id=meeting_id,
        job_id=job_id,
        job_type=job_type,
    )
