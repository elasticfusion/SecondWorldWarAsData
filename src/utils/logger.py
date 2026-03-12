"""Logging configuration and setup."""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add TRACE level (below DEBUG)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def trace(self, message, *args, **kwargs):  # type: ignore
    """Log trace level message."""
    if self.isEnabledFor(TRACE):
        self._log(TRACE, message, args, **kwargs)


logging.Logger.trace = trace  # type: ignore


def setup_logging(
    level: str = "INFO", log_file: Optional[str] = None, console: bool = True
):
    """
    Configure logging for the application.

    Levels: TRACE (5), DEBUG (10), INFO (20), WARNING (30), ERROR (40), CRITICAL (50)

    If log_file is provided, creates a timestamped version for each run.
    """
    logger = logging.getLogger()

    # Map level names
    level_map = {
        "TRACE": TRACE,
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "FATAL": logging.CRITICAL,
        "CRITICAL": logging.CRITICAL,
    }

    logger.setLevel(level_map.get(level.upper(), logging.INFO))

    # Clear existing handlers
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        # Force UTF-8 encoding to prevent null byte artifacts
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        logger.addHandler(console_handler)

    if log_file:
        # Create timestamped log file
        log_path = Path(log_file)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        timestamped_log = (
            log_path.parent / f"{log_path.stem}_{timestamp}{log_path.suffix}"
        )

        # Ensure log directory exists
        timestamped_log.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(timestamped_log)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        logger.info("Logging to: %s", timestamped_log)

    return logger
