"""Worker entrypoint: poll the durable queue, run the pipeline, ack/postpone.

Concurrency is bounded by WORKER_CONCURRENCY; each message is processed by the
pipeline which owns all job lifecycle decisions. Shutdown is graceful: SIGTERM
stops polling and waits briefly for in-flight work.
"""

import logging
import signal
import threading
from concurrent.futures import ThreadPoolExecutor

from supabase import create_client

from app.config.settings import load_settings
from app.log import configure_logging
from app.pipeline.errors import InvalidMessageError
from app.pipeline.orchestrator import Pipeline, backoff_seconds
from app.providers.fake import FakeIntelligenceProvider, FakeTranscriptionProvider
from app.providers.openai_intelligence import OpenAIIntelligenceProvider
from app.providers.openai_transcription import OpenAITranscriptionProvider
from app.queue.pgmq import PgmqQueueAdapter
from app.store.supabase_store import SupabaseStore

log = logging.getLogger(__name__)

_SHUTDOWN = threading.Event()


def _handle_signal(signum, _frame):
    log.info("received signal %s — shutting down after current work", signum)
    _SHUTDOWN.set()


def build_pipeline(settings) -> tuple[PgmqQueueAdapter, Pipeline]:
    client = create_client(
        settings.supabase_url, settings.supabase_service_role_key
    )
    store = SupabaseStore(client, settings.supabase_storage_bucket)
    queue = PgmqQueueAdapter(client)

    if settings.worker_fake_providers:
        log.warning("WORKER_FAKE_PROVIDERS enabled — using deterministic fakes")
        transcription = FakeTranscriptionProvider()
        intelligence = FakeIntelligenceProvider()
    else:
        transcription = OpenAITranscriptionProvider(
            settings.openai_api_key, settings.openai_transcription_model
        )
        intelligence = OpenAIIntelligenceProvider(
            settings.openai_api_key, settings.openai_intelligence_model
        )

    return queue, Pipeline(
        settings=settings,
        store=store,
        transcription=transcription,
        intelligence=intelligence,
    )


def _process_one(queue: PgmqQueueAdapter, pipeline: Pipeline, msg) -> None:
    try:
        action, delay = pipeline.process(msg)
    except InvalidMessageError as exc:
        log.warning("invalid queue message: %s", exc.safe_message)
        queue.archive(msg.msg_id)
        return
    except Exception:  # noqa: BLE001 — last line of defense
        log.exception("unhandled worker error")
        # Bound redelivery for unhandled errors using the queue's read count.
        if msg.read_ct >= 5:
            queue.archive(msg.msg_id)
        else:
            queue.postpone(msg.msg_id, backoff_seconds(msg.read_ct))
        return

    if action == "done":
        queue.ack(msg.msg_id)
    elif action == "retry":
        queue.postpone(msg.msg_id, delay)
    else:  # failed — terminal, keep in archive for operators
        queue.archive(msg.msg_id)


def main() -> None:
    configure_logging()
    settings = load_settings()
    log.info(
        "worker starting (env=%s concurrency=%d queue=%s)",
        settings.worker_env,
        settings.worker_concurrency,
        settings.queue_name,
    )

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    queue, pipeline = build_pipeline(settings)

    with ThreadPoolExecutor(max_workers=settings.worker_concurrency) as pool:
        while not _SHUTDOWN.is_set():
            try:
                messages = queue.receive(
                    vt_seconds=settings.queue_visibility_timeout_seconds,
                    qty=settings.worker_concurrency,
                )
            except Exception:
                log.exception("queue poll failed; backing off")
                _SHUTDOWN.wait(10)
                continue

            if not messages:
                _SHUTDOWN.wait(settings.worker_poll_interval_seconds)
                continue

            futures = [
                pool.submit(_process_one, queue, pipeline, m) for m in messages
            ]
            for f in futures:
                f.result()  # propagate nothing — errors handled in _process_one

    log.info("worker stopped")


if __name__ == "__main__":
    main()
