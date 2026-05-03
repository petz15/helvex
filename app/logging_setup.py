"""Structured JSON logging setup for the application.

All logs are emitted as JSON to stdout for Grafana/ELK stack integration.
Each log entry includes: timestamp, level, logger name, message, and context (latency, result codes, errors).
"""

import json
import logging
import os
import sys
import time
from logging import LogRecord
from pythonjsonlogger import jsonlogger


class JSONFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter that adds app context to every log entry."""

    def add_fields(self, log_record: dict, record: LogRecord, message_dict: dict) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = self.formatTime(record, self.datefmt)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name


def setup_structured_logging(log_level_name: str = "INFO") -> logging.Logger:
    """Configure structured JSON logging for the entire application.

    Args:
        log_level_name: Log level as a string (e.g., "DEBUG", "INFO", "WARNING")

    Returns:
        The configured root logger for the app.
    """
    level = getattr(logging, log_level_name.upper().strip(), logging.INFO)

    # Create app logger
    app_logger = logging.getLogger("app")
    app_logger.handlers.clear()
    app_logger.setLevel(level)

    # JSON handler (stdout)
    json_handler = logging.StreamHandler(sys.stdout)
    json_handler.setFormatter(JSONFormatter())
    app_logger.addHandler(json_handler)
    app_logger.propagate = False

    # Silence noisy third-party loggers
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)

    return app_logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a specific module.

    Args:
        name: Module name (typically __name__)

    Returns:
        A logger instance.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler(sys.stdout))
    return logger
