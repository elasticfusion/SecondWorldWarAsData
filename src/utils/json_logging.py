"""Structured JSON logging for CloudWatch Logs Insights."""

import json
import logging
import os
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON for CloudWatch Logs Insights."""

    def format(self, record):
        log = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "phase": os.environ.get("PIPELINE_PHASE", ""),
            "book": os.environ.get("BOOK_NAME", ""),
            "task_id": os.environ.get("ECS_TASK_ID", "local"),
        }
        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_fields"):
            log.update(record.extra_fields)
        return json.dumps(log, default=str)


def configure_json_logging():
    """Replace all root logger handlers with a JSON-formatted handler."""
    root = logging.getLogger()
    root.setLevel(os.getenv("LOG_LEVEL", "INFO"))
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
