"""
Configuration loader.

Reads ``config/settings.yaml`` and merges with environment variables
loaded from ``.env``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Project root is two levels up from this file
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG = _PROJECT_ROOT / "config" / "settings.yaml"


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load YAML config and env vars.

    Args:
        path: Optional path to the YAML config file. Defaults to
              ``<project_root>/config/settings.yaml``.

    Returns:
        Merged configuration dictionary.
    """
    # Load .env first so YAML can reference env vars if needed
    dotenv_path = _PROJECT_ROOT / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path)
        logger.info("[green]Loaded .env[/green] from %s", dotenv_path)

    config_path = Path(path) if path else _DEFAULT_CONFIG
    if not config_path.exists():
        logger.warning("Config file not found at %s — using defaults", config_path)
        return _defaults()

    with open(config_path, "r", encoding="utf-8") as fh:
        config: dict[str, Any] = yaml.safe_load(fh) or {}

    logger.info("[green]Loaded config[/green] from %s", config_path)

    # Override dry_run from env if set
    env_dry = os.getenv("AGENT_DRY_RUN")
    if env_dry is not None:
        config.setdefault("agent", {})["dry_run"] = env_dry.lower() in ("true", "1", "yes")

    # Override region from env if set (boto3 uses AWS_DEFAULT_REGION)
    env_region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    if env_region:
        config.setdefault("provider", {})["region"] = env_region
        logger.info("[green]AWS Region overridden from env:[/green] %s", env_region)

    return config


def _defaults() -> dict[str, Any]:
    """Sensible defaults when no YAML is available."""
    return {
        "agent": {
            "loop_interval_seconds": 300,
            "dry_run": True,
            "require_approval": False,
        },
        "provider": {
            "name": "aws",
            "region": os.getenv("AWS_REGION", "us-east-1"),
        },
        "tools": {},
    }
