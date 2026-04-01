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
    from rq import Queue, Worker

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

    worker = Worker(queues, connection=conn)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
