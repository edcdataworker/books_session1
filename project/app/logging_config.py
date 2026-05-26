"""Configuration centralisee du logging pour le pipeline."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


DEFAULT_LOG_FILE_NAME = "pipeline.log"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 5
DEFAULT_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DEFAULT_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: Optional[str], default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _resolve_log_file(default_log_dir: Optional[Path]) -> Path:
    log_file_env = os.getenv("LOG_FILE")
    if log_file_env:
        return Path(log_file_env).expanduser().resolve()

    log_dir_env = os.getenv("LOG_DIR")
    if log_dir_env:
        log_dir = Path(log_dir_env).expanduser().resolve()
    elif default_log_dir is not None:
        log_dir = default_log_dir.resolve()
    else:
        log_dir = Path.cwd()

    return log_dir / DEFAULT_LOG_FILE_NAME


def setup_logging(default_log_dir: Optional[Path] = None) -> None:
    log_level_name = os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    formatter = logging.Formatter(DEFAULT_LOG_FORMAT, DEFAULT_LOG_DATE_FORMAT)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    if _parse_bool(os.getenv("LOG_TO_FILE"), default=True):
        log_file = _resolve_log_file(default_log_dir)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        max_bytes = _parse_int(os.getenv("LOG_MAX_BYTES"), DEFAULT_LOG_MAX_BYTES)
        backup_count = _parse_int(os.getenv("LOG_BACKUP_COUNT"), DEFAULT_LOG_BACKUP_COUNT)

        file_handler = RotatingFileHandler(
            filename=log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
