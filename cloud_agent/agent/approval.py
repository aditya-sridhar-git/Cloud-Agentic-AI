"""
Human-approval workflow for high-impact actions.

High-impact actions (termination, resizing, volume deletion, cost freezes,
stopping production instances) are not executed automatically. The planner
places them in a persistent approval queue and notifies operators (Slack /
SNS). An operator approves or rejects each one from the dashboard or the
REST API, and every verdict is recorded as feedback. Approved interventions
are later reused as few-shot examples in the reasoning prompt.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


class ApprovalMemory:
    """JSON-backed store for pending approvals and operator feedback."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else _DEFAULT_DIR / "approvals.json"
        self._lock = threading.Lock()
        self._state: dict[str, list[dict[str, Any]]] = {"pending": [], "feedback": []}
        if self._path.exists():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                self._state["pending"] = loaded.get("pending", [])
                self._state["feedback"] = loaded.get("feedback", [])
            except (OSError, json.JSONDecodeError):
                logger.warning("Could not read approval store %s — starting empty", self._path)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._state, indent=2, default=str), encoding="utf-8")
        except OSError:
            logger.warning("Could not persist approval store %s", self._path)

    # -- pending ---------------------------------------------------------

    def get_pending_approvals(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(p) for p in self._state["pending"]]

    def set_pending_approvals(self, pending: list[dict[str, Any]]) -> None:
        with self._lock:
            self._state["pending"] = list(pending)
            self._save()

    def add_pending(self, action: Action, why: str) -> bool:
        """Queue *action* for approval. Returns False if it is already queued."""
        entry = {
            "tool_name": action.tool_name,
            "resource_id": action.resource_id,
            "action_type": action.action_type,
            "parameters": action.parameters,
            "reason": action.reason,
            "approval_reason": why,
            "requested_at": time.time(),
        }
        with self._lock:
            for p in self._state["pending"]:
                if (p["tool_name"], p["resource_id"], p["action_type"]) == action.key():
                    return False
            self._state["pending"].append(entry)
            self._save()
        return True

    def pop_pending(self, tool_name: str | None, resource_id: str, action_type: str) -> dict[str, Any] | None:
        with self._lock:
            for i, p in enumerate(self._state["pending"]):
                if p["resource_id"] == resource_id and p["action_type"] == action_type and (
                    tool_name is None or p["tool_name"] == tool_name
                ):
                    self._state["pending"].pop(i)
                    self._save()
                    return p
        return None

    # -- feedback --------------------------------------------------------

    def record_feedback(self, resource_id: str, action_type: str, verdict: str,
                        notes: str = "", tool_name: str = "", reason: str = "") -> None:
        with self._lock:
            self._state["feedback"].append({
                "tool_name": tool_name,
                "resource_id": resource_id,
                "action_type": action_type,
                "verdict": verdict,
                "notes": notes,
                "reason": reason,
                "timestamp": time.time(),
            })
            self._save()

    def get_feedback(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(f) for f in self._state["feedback"]]

    def successful_examples(self, n: int = 3) -> list[dict[str, Any]]:
        """Most recent approved interventions, used as few-shot prompt examples."""
        approved = [f for f in self.get_feedback() if f.get("verdict") == "approved" and f.get("tool_name")]
        return approved[-n:]


class ApprovalWorkflow:
    """Decides which actions need a human and routes them to the queue."""

    def __init__(self, config: dict[str, Any], memory: ApprovalMemory | None = None) -> None:
        agent_cfg = config.get("agent", {})
        self.memory = memory or ApprovalMemory(agent_cfg.get("approval_store"))
        self._high_impact = set(agent_cfg.get(
            "high_impact_actions", ["terminate", "resize", "snapshot_delete", "freeze"],
        ))
        self._protect_prod = agent_cfg.get("approve_prod_stops", True)

    def needs_approval(self, action: Action, instance: dict[str, Any] | None = None) -> str | None:
        """Return why *action* needs human approval, or None if it may auto-execute."""
        if action.action_type in self._high_impact:
            return f"'{action.action_type}' is a high-impact action"
        if self._protect_prod and action.action_type == "stop" and instance:
            env = {t["Key"].lower(): str(t["Value"]).lower() for t in instance.get("tags", [])}.get("environment")
            if env in ("prod", "production"):
                return "stopping a production instance"
        return None
