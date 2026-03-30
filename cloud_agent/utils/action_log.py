"""
Action Logger — persistent audit trail for all agent actions.

Every action result is appended to a JSON Lines file so you have a
complete, tamper-evident history of what the agent did and why.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


class ActionLogger:
    """Append-only JSON Lines logger for agent actions."""

    def __init__(self, log_dir: str | Path | None = None) -> None:
        self._log_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = self._log_dir / "actions.jsonl"
        self._summary_file = self._log_dir / "latest_summary.json"
        logger.info("[green]Action logger[/green] → %s", self._log_file)

    def log_action(self, action_result: dict[str, Any], cycle_id: str = "") -> None:
        """Append a single action result to the log file."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cycle_id": cycle_id,
            "epoch": time.time(),
            **action_result,
        }
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def log_cycle(self, cycle_id: str, plan_summary: str, results: list[dict[str, Any]],
                  observation_summary: dict[str, Any] | None = None) -> None:
        """Log an entire agent cycle (observation + plan + results)."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cycle_id": cycle_id,
            "epoch": time.time(),
            "plan_summary": plan_summary,
            "actions_count": len(results),
            "results": results,
            "observation_summary": observation_summary or {},
        }
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

        # Also write a "latest" summary for the dashboard to read
        self._write_summary(entry)

    def _write_summary(self, entry: dict[str, Any]) -> None:
        """Overwrite the latest-summary file for quick dashboard reads."""
        try:
            with open(self._summary_file, "w", encoding="utf-8") as f:
                json.dump(entry, f, indent=2, default=str)
        except Exception:
            logger.warning("Could not write summary file")

    def get_recent(self, n: int = 50) -> list[dict[str, Any]]:
        """Read the last N log entries."""
        if not self._log_file.exists():
            return []
        entries: list[dict[str, Any]] = []
        with open(self._log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries[-n:]

    def get_latest_summary(self) -> dict[str, Any]:
        """Read the latest cycle summary."""
        if not self._summary_file.exists():
            return {}
        try:
            with open(self._summary_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
