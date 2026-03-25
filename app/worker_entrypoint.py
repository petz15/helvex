"""RQ worker entrypoint.

Run with:
    python -m app.worker_entrypoint

Or via the Docker image CMD:
    python -m app.worker_entrypoint

Requires REDIS_URL and USE_RQ=true in the environment.
"""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)


def main() -> None:
    from app.config import settings

    if not settings.redis_url:
        logger.error("REDIS_URL is not set — cannot start RQ worker")
        sys.exit(1)

    from redis import Redis
    from rq import Queue, Worker

    conn = Redis.from_url(settings.redis_url)
    queues = [Queue("helvex", connection=conn)]

    logger.info("Starting RQ worker on queue 'helvex' — redis: %s", settings.redis_url.split("@")[-1])
    worker = Worker(queues, connection=conn)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
