import logging
import sys
from typing import Any


def setup_logging(level: int = logging.INFO) -> None:
    """Configure basic structured-ish logging.

    The Node backend uses a logger module; in Python we keep it simple but consistent.
    """

    handler = logging.StreamHandler(sys.stdout)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = []
    root.addHandler(handler)
    root.setLevel(level)

    # Reduce noisy libs
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)


def log_extra(**kwargs: Any) -> str:
    """Render extra fields in a compact key=value string."""

    parts = []
    for k, v in kwargs.items():
        parts.append(f"{k}={v!r}")
    return " ".join(parts)
