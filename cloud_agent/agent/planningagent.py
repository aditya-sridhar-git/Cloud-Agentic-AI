"""
Action planner — bridges reasoning and tool execution.

Optionally waits for human approval before executing destructive actions.
"""

from __future__ import annotations

import json
from typing import Any

from cloud_agent.agent.baseagent import Action, Plan
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


class Planner:
    """Validates, filters, and optionally gate-keeps action plans."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._require_approval = config.get("agent", {}).get("require_approval", False)
        self._dry_run = config.get("agent", {}).get("dry_run", True)
        tools_cfg = config.get("tools", {})
        # Build a set of enabled tool names
        self._enabled_tools: set[str] = {
            name for name, cfg in tools_cfg.items() if cfg.get("enabled", False)
        }

    def refine(self, plan: Plan) -> Plan:
        """Filter and approve actions.

        - Removes actions for disabled tools.
        - If ``require_approval`` is set, prompts the operator via stdin.

        Returns:
            A new :class:`Plan` containing only approved actions.
        """
        # Step 1: filter to enabled tools only
        filtered = [a for a in plan.actions if a.tool_name in self._enabled_tools]
        if len(filtered) < len(plan.actions):
            removed = len(plan.actions) - len(filtered)
            logger.info("Filtered out %d action(s) for disabled tools", removed)

        if not filtered:
            return Plan(actions=[], summary="No actions after filtering")

        # Step 2: optional human approval
        if self._require_approval and not self._dry_run:
            filtered = self._prompt_approval(filtered)

        for action in filtered:
            action.approved = True

        return Plan(
            actions=filtered,
            summary=f"{len(filtered)} approved action(s)",
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _prompt_approval(actions: list[Action]) -> list[Action]:
        """Print actions to console and ask the operator to approve."""
        logger.info("[bold yellow]🛡️  Approval required for %d action(s):[/bold yellow]", len(actions))
        for i, a in enumerate(actions, 1):
            logger.info(
                "  %d. [%s] %s on %s — %s",
                i, a.tool_name, a.action_type, a.resource_id, a.reason,
            )

        answer = input("\n  Approve all? (y/n/select): ").strip().lower()
        if answer == "y":
            return actions
        if answer == "n":
            logger.info("[red]All actions rejected.[/red]")
            return []

        # Selective approval: comma-separated indices
        try:
            indices = {int(x.strip()) for x in answer.split(",")}
            approved = [a for i, a in enumerate(actions, 1) if i in indices]
            logger.info("Approved %d of %d actions", len(approved), len(actions))
            return approved
        except ValueError:
            logger.warning("Invalid input — rejecting all actions for safety")
            return []
