"""Logging setup.

Without this, nothing was configured at all. The root logger had no handlers
and its effective level was WARNING, so every logger.info() in the codebase
was discarded silently, and warnings reached stderr through logging's
last-resort handler - bare text, no timestamp, no logger name, no level. The
one thing a deployed service needs when something goes wrong was the one
thing it could not produce.

Two details matter more than the format:

    uvicorn installs its own handlers on its own loggers. Configuring the root
    logger without disabling existing ones would leave uvicorn's access log
    formatted differently from ours, or duplicated. disable_existing_loggers
    is False and uvicorn's loggers are configured explicitly, so everything
    comes out the same shape.

    Output goes to stdout, not stderr. Container platforms collect both, but
    routing ordinary operational lines to stderr makes every log line look
    like an error to anything that separates the two.

This pairs with the Dockerfile's PYTHONUNBUFFERED fix. A correctly configured
logger writing into a block-buffered pipe is still a silent service.
"""

import logging
import sys
from logging.config import dictConfig

FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


def configure_logging(level: str = "INFO") -> None:
    """Install a single stdout handler for the application and uvicorn."""
    dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {"format": FORMAT, "datefmt": DATE_FORMAT},
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "stream": sys.stdout,
            },
        },
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            # Uvicorn's own loggers, routed through the same handler so the
            # access log and the application log read as one stream.
            "uvicorn": {"handlers": ["stdout"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["stdout"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["stdout"], "level": level, "propagate": False},
            # Chatty at DEBUG and rarely what we want to read.
            "httpx": {"level": "WARNING"},
            "urllib3": {"level": "WARNING"},
        },
    })
    logging.getLogger(__name__).debug("logging configured at %s", level)
