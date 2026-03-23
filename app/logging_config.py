import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

from .config import settings


def setup_logging() -> None:
    """Configure root logger with a rotating file handler and console output.

    This is safe to call multiple times; if handlers already exist we return early.
    """
    root = logging.getLogger()
    if root.handlers:
        return

    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    root.setLevel(level)

    log_path = Path(settings.LOG_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=settings.LOG_MAX_BYTES,
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(fmt)

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Ensure common servers (uvicorn) use similar level and handlers when possible
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        log = logging.getLogger(name)
        log.setLevel(level)
        if not log.handlers:
            log.addHandler(console_handler)

    logging.getLogger("app").debug("logging configured, file=%s", str(log_path))
