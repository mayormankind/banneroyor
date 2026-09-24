"""PGMQ queue adapter. Queue operations go through the SQL RPC wrappers defined
in supabase/migrations/0006 so pgmq stays isolated behind an adapter."""

import logging
from typing import Any

from supabase import Client

from app.queue.adapter import QueueMessage, parse_message

log = logging.getLogger(__name__)


class PgmqQueueAdapter:
    def __init__(self, client: Client):
        self.client = client

    def receive(self, vt_seconds: int, qty: int) -> list[QueueMessage]:
        res = self.client.rpc(
            "read_meeting_jobs", {"p_vt_seconds": vt_seconds, "p_qty": qty}
        ).execute()
        rows: list[dict[str, Any]] = res.data or []
        messages = []
        for row in rows:
            try:
                messages.append(
                    parse_message(
                        row["message"], msg_id=row["msg_id"], read_ct=row["read_ct"]
                    )
                )
            except ValueError:
                log.warning(
                    "dropping malformed queue message",
                    extra={"job_id": None},
                )
                self.archive(row["msg_id"])
        return messages

    def ack(self, msg_id: int) -> None:
        self.client.rpc("delete_meeting_job_message", {"p_msg_id": msg_id}).execute()

    def postpone(self, msg_id: int, delay_seconds: int) -> None:
        self.client.rpc(
            "postpone_meeting_job_message",
            {"p_msg_id": msg_id, "p_delay_seconds": delay_seconds},
        ).execute()

    def archive(self, msg_id: int) -> None:
        self.client.rpc(
            "archive_meeting_job_message", {"p_msg_id": msg_id}
        ).execute()
