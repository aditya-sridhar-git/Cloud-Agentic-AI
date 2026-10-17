"""
Structured logging for the Cloud Agent.

Uses Python's built-in logging with Rich for pretty console output.
Handles Windows cp1252 encoding gracefully by forcing UTF-8 on stdout.
"""

import io
import logging
import os
import sys

from rich.console import Console
from rich.logging import RichHandler


def _make_console() -> Console:
    """Create a Rich Console that works on Windows cp1252 terminals."""
    # If stdout can't handle unicode (e.g. Windows legacy console),
    # wrap it in a UTF-8 TextIOWrapper so Rich doesn't crash.
    try:
        # Check (without writing anything) whether stdout can encode unicode
        "\u25b6".encode(getattr(sys.stdout, "encoding", None) or "ascii")
        return Console()
    except (UnicodeEncodeError, LookupError):
        pass

    # Windows fallback: wrap stdout with UTF-8 encoding, errors='replace'
    try:
        utf8_stdout = io.TextIOWrapper(
            sys.stdout.buffer,
            encoding="utf-8",
            errors="replace",
            line_buffering=True,
        )
        return Console(file=utf8_stdout, highlight=False)
    except AttributeError:
        # sys.stdout has no .buffer (e.g. StringIO in tests) — use plain console
        return Console(highlight=False, no_color=True)


console = _make_console()

_LOG_FORMAT = "%(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str, level: str | None = None) -> logging.Logger:
    """Return a named logger with Rich formatting.

    Args:
        name: Logger name (typically ``__name__``).
        level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
            Defaults to the ``AGENT_LOG_LEVEL`` environment variable, else INFO.

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

    level = level or os.getenv("AGENT_LOG_LEVEL", "INFO")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger


def set_global_level(level: str) -> None:
    """Change the level of every cloud_agent logger (used by the evaluation harness)."""
    os.environ["AGENT_LOG_LEVEL"] = level
    numeric = getattr(logging, level.upper(), logging.INFO)
    for name, obj in logging.root.manager.loggerDict.items():
        if name.startswith("cloud_agent") and isinstance(obj, logging.Logger):
            obj.setLevel(numeric)
