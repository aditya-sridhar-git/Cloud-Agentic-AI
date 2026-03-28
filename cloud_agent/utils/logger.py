"""
Structured logging for the Cloud Agent.

Uses Python's built-in logging with Rich for pretty console output.
"""

import logging
import sys

from rich.console import Console
from rich.logging import RichHandler


console = Console()

_LOG_FORMAT = "%(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Return a named logger with Rich formatting.

    Args:
        name: Logger name (typically ``__name__``).
        level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).

    Returns:
        Configured :class:`logging.Logger`.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = RichHandler(
            console=console,
            show_time=True,
            show_path=False,
            markup=True,
            rich_tracebacks=True,
        )
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        logger.addHandler(handler)

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger
