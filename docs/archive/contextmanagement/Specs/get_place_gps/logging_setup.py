"""Logging configuration for GPS geocoding."""

import logging
from datetime import datetime
from pathlib import Path

from config import LOG_FILENAME_PATTERN, LOG_TIMESTAMP_FORMAT

# Forbidden characters in log filenames (shell metacharacters)
_FORBIDDEN_CHARS = r"$`\'\""

def setup_logging(
    log_dir: Path | str | None = None, log_level: str = "INFO"
) -> None:
    """Configure logging with console and optional file handler."""
    if not isinstance(log_level, str):
        raise TypeError(
            f"log_level must be a string, got {type(log_level).__name__}"
        )

    level = getattr(logging, log_level.upper(), None)
    if level is None:
        raise ValueError(
            f"Invalid log level: {log_level}. Must be one of: "
            "DEBUG, INFO, WARNING, ERROR, CRITICAL"
        )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers to prevent duplicates
    root_logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if log_dir:
        if not isinstance(log_dir, (str, Path)):
            root_logger.error("log_dir must be a string or Path")
            return
        log_dir = Path(log_dir) if isinstance(log_dir, str) else log_dir
        log_dir = log_dir.resolve()
        home = Path.home()
        if not (log_dir.is_relative_to(home) or log_dir.is_relative_to(Path("/tmp"))):
            root_logger.error(
                "Log directory must be under home or /tmp: %s", log_dir
            )
            return
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime(LOG_TIMESTAMP_FORMAT)

        # Validate LOG_FILENAME_PATTERN to prevent injection
        if not isinstance(LOG_FILENAME_PATTERN, str):
            root_logger.error("LOG_FILENAME_PATTERN must be a string")
            return
        if "{timestamp}" not in LOG_FILENAME_PATTERN:
            root_logger.error(
                "LOG_FILENAME_PATTERN must contain {timestamp} placeholder"
            )
            return

        try:
            filename = LOG_FILENAME_PATTERN.format(timestamp=timestamp)
        except KeyError as e:
            root_logger.error("Invalid placeholder in LOG_FILENAME_PATTERN: %s", e)
            return
        if ".." in filename or "/" in filename or any(c in filename for c in _FORBIDDEN_CHARS):
            root_logger.error(
                "LOG_FILENAME_PATTERN produced invalid filename: %s",
                filename,
            )
            return
        log_file = (log_dir / filename).resolve()
        if not log_file.is_relative_to(log_dir.resolve()):
            root_logger.error(
                "LOG_FILENAME_PATTERN produced path outside log_dir: %s",
                filename,
            )
            return
        try:
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
            root_logger.info(
                "Log file created: %s (level: %s)",
                log_file,
                log_level.upper(),
            )
        except (OSError, PermissionError) as e:
            root_logger.error("Failed to create log file: %s", e)
