"""Application metrics for Prometheus integration.

Tracks:
- Job execution durations and error rates
- API call latencies (Zefix, Serper, Claude, geocoding)
- Database operation metrics
- Worker thread status
"""

from prometheus_client import Counter, Gauge, Histogram


# Job execution metrics
JOB_DURATION_SECONDS = Histogram(
    "job_duration_seconds",
    "Job execution duration in seconds",
    labelnames=["job_type", "status"],  # status: success, failed, cancelled
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800),
)

JOB_ERRORS_TOTAL = Counter(
    "job_errors_total",
    "Total number of job errors",
    labelnames=["job_type", "error_type"],  # error_type: timeout, rate_limit, db, api, unknown
)

JOB_PROGRESS = Gauge(
    "job_progress_ratio",
    "Current job progress (0.0-1.0)",
    labelnames=["org_id", "job_type"],
)

ACTIVE_JOBS = Gauge(
    "active_jobs_count",
    "Number of currently running jobs",
    labelnames=["job_type"],
)


# External API metrics
API_CALL_DURATION_SECONDS = Histogram(
    "api_call_duration_seconds",
    "External API call duration in seconds",
    labelnames=["api_name", "status_code"],  # api_name: zefix, serper, claude, geocoding, etc.
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30),
)

API_CALL_ERRORS_TOTAL = Counter(
    "api_call_errors_total",
    "Total number of external API errors",
    labelnames=["api_name", "error_type"],
)


# Database metrics
DB_QUERY_DURATION_SECONDS = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    labelnames=["operation"],  # operation: select, insert, update, delete
    buckets=(0.001, 0.01, 0.1, 0.5, 1),
)

DB_CONNECTION_POOL_SIZE = Gauge(
    "db_connection_pool_size",
    "Current database connection pool size",
)


# Crawler metrics
CRAWL_RESULT_TOTAL = Counter(
    "crawl_result_total",
    "Total crawl outcomes per tier and final status",
    labelnames=["tier", "status"],
    # tier: http | playwright
    # status: crawled | bot_blocked | js_required | http_error | timeout | no_content | error
)


# Worker thread metrics
WORKER_THREAD_RUNNING = Gauge(
    "worker_thread_running",
    "Whether the background worker thread is running (0=stopped, 1=running)",
)

WORKER_THREAD_ITERATIONS_TOTAL = Counter(
    "worker_thread_iterations_total",
    "Total number of worker thread iterations (polling cycles)",
)


def record_job_duration(job_type: str, duration_seconds: float, status: str) -> None:
    """Record a job execution duration.

    Args:
        job_type: Type of job (e.g., "bulk_import", "batch_enrich")
        duration_seconds: Execution time
        status: "success", "failed", or "cancelled"
    """
    JOB_DURATION_SECONDS.labels(job_type=job_type, status=status).observe(duration_seconds)


def record_api_call(api_name: str, duration_seconds: float, status_code: int) -> None:
    """Record an external API call.

    Args:
        api_name: Name of the API (e.g., "serper", "zefix", "claude")
        duration_seconds: Call duration
        status_code: HTTP status code
    """
    API_CALL_DURATION_SECONDS.labels(api_name=api_name, status_code=status_code).observe(
        duration_seconds
    )


def record_crawl_result(tier: str, status: str) -> None:
    """Record a single-company crawl outcome.

    tier: "http" or "playwright"
    status: "crawled" | "bot_blocked" | "js_required" | "http_error" | "timeout" | "no_content" | "error"
    """
    CRAWL_RESULT_TOTAL.labels(tier=tier, status=status).inc()


def record_api_error(api_name: str, error_type: str) -> None:
    """Record an external API error.

    Args:
        api_name: Name of the API
        error_type: Error category (e.g., "timeout", "auth", "rate_limit")
    """
    API_CALL_ERRORS_TOTAL.labels(api_name=api_name, error_type=error_type).inc()
