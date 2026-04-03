"""RQ worker entrypoint.

Run with:
    python -m app.worker_entrypoint

Or via the Docker image CMD:
    python -m app.worker_entrypoint

Requires REDIS_URL and USE_RQ=true in the environment.

WORKER_TYPE controls which queue(s) this worker listens to:
    zefix  — helvex-zefix-p4 ... helvex-zefix-p0 (bulk/detail/initial)
    api    — helvex-api-p4   ... helvex-api-p0   (scoring, geocode, NOGA, Claude classify)
    ml     — helvex-ml                             (HDBSCAN clustering, keyword extraction)

Defaults to "api" when WORKER_TYPE is not set (backward-compatible).

The api worker also starts a background thread that polls Anthropic Batch API
jobs every 5 minutes to handle the two-phase LLM classify flow.
"""
import logging
import os
import sys
import threading
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)

QUEUE_MAP: dict[str, list[str]] = {
    "zefix": ["helvex-zefix-p4", "helvex-zefix-p3", "helvex-zefix-p2", "helvex-zefix-p1", "helvex-zefix-p0"],
    "api":   ["helvex-api-p4", "helvex-api-p3", "helvex-api-p2", "helvex-api-p1", "helvex-api-p0"],
    "ml":    ["helvex-ml"],
}

LLM_POLL_INTERVAL = 300  # 5 minutes


class ResilientWorker:  # Thin wrapper to avoid importing rq at module import time.
    """Worker factory that tolerates stale-job cleanup races.

    RQ may raise AbandonedJobError while cleaning started-job registries if a
    previous worker died mid-job. We log and continue so one stale registry
    entry does not kill the whole worker process.
    """

    @staticmethod
    def create(queues, *, connection):
        from rq import Worker as _Worker
        from rq.exceptions import AbandonedJobError

        class _SafeWorker(_Worker):
            def run_maintenance_tasks(self):
                try:
                    super().run_maintenance_tasks()
                except AbandonedJobError as exc:
                    logger.warning("Ignored RQ abandoned job during maintenance cleanup: %s", exc)

            def handle_warm_shutdown_request(self):
                """On SIGTERM: pause running jobs cleanly before the pod is killed."""
                from app.services.job_worker import request_shutdown
                logger.info("SIGTERM received — requesting graceful job shutdown")
                request_shutdown()
                super().handle_warm_shutdown_request()

        return _SafeWorker(queues, connection=connection)


def _llm_poll_loop() -> None:
    """Background daemon thread: poll Anthropic Batch API jobs every 5 minutes."""
    from app.services.job_worker import poll_llm_batches
    logger.info("LLM batch poll thread started (interval: %ds)", LLM_POLL_INTERVAL)
    while True:
        time.sleep(LLM_POLL_INTERVAL)
        try:
            poll_llm_batches()
        except Exception as exc:  # noqa: BLE001
            logger.error("LLM poll loop error: %s", exc)


def main() -> None:
    from app.config import settings

    if not settings.redis_url:
        logger.error("REDIS_URL is not set — cannot start RQ worker")
        sys.exit(1)

    worker_type = os.environ.get("WORKER_TYPE", "api").lower()
    if worker_type not in QUEUE_MAP:
        logger.error("Unknown WORKER_TYPE=%r — must be one of: %s", worker_type, ", ".join(QUEUE_MAP))
        sys.exit(1)

    queue_names = QUEUE_MAP[worker_type]

    from redis import Redis
    from rq import Queue

    conn = Redis.from_url(settings.redis_url)
    queues = [Queue(name, connection=conn) for name in queue_names]

    logger.info(
        "Starting RQ %s-worker on queue(s) %s — redis: %s",
        worker_type,
        ", ".join(f"'{q}'" for q in queue_names),
        settings.redis_url.split("@")[-1],
    )

    if worker_type == "api":
        t = threading.Thread(target=_llm_poll_loop, daemon=True, name="llm-batch-poller")
        t.start()

    worker = ResilientWorker.create(queues, connection=conn)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
