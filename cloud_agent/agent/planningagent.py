"""
Action planner — bridges reasoning and tool execution.

Filters actions to enabled tools and marks high-impact actions as requiring
human approval. Depending on ``agent.approval_mode`` those actions are either
confirmed interactively on the console (``cli``) or placed in the approval
queue for the dashboard / Slack (``queue``, the default).
"""

from __future__ import annotations

import sys
from typing import Any

from cloud_agent.agent.approval import ApprovalWorkflow
from cloud_agent.agent.baseagent import Action, Observation, Plan
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


class Planner:
    """Validates, filters, and gate-keeps action plans."""

    def __init__(self, config: dict[str, Any], workflow: ApprovalWorkflow | None = None) -> None:
        agent_cfg = config.get("agent", {})
        self._require_approval = agent_cfg.get("require_approval", True)
        self._approval_mode = agent_cfg.get("approval_mode", "queue")
        self._dry_run = agent_cfg.get("dry_run", True)
        self._workflow = workflow or ApprovalWorkflow(config)
        tools_cfg = config.get("tools", {})
        self._enabled_tools: set[str] = {
            name for name, cfg in tools_cfg.items() if cfg.get("enabled", False)
        }

    def refine(self, plan: Plan, observation: Observation | None = None) -> Plan:
        """Filter actions to enabled tools and flag the ones needing approval.

        Returns:
            A new :class:`Plan`. Actions that may run automatically have
            ``approved=True``; high-impact ones have ``requires_approval=True``
            and stay unapproved until an operator signs off.
        """
        filtered = [a for a in plan.actions if a.tool_name in self._enabled_tools]
        if len(filtered) < len(plan.actions):
            logger.info("Filtered out %d action(s) for disabled tools", len(plan.actions) - len(filtered))

        if not filtered:
            return Plan(actions=[], summary="No actions after filtering")

        instances = {i["instance_id"]: i for i in (observation.instances if observation else [])}
        for action in filtered:
            why = self._workflow.needs_approval(action, instances.get(action.resource_id))
            action.requires_approval = bool(self._require_approval and why)
            action.approved = not action.requires_approval
            if action.requires_approval:
                action.parameters.setdefault("approval_reason", why)

        gated = [a for a in filtered if a.requires_approval]
        if gated and self._approval_mode == "cli" and not self._dry_run and sys.stdin.isatty():
            approved = self._prompt_approval(gated)
            for a in gated:
                if a in approved:
                    a.approved = True
                    a.requires_approval = False

        auto = sum(1 for a in filtered if a.approved)
        return Plan(
            actions=filtered,
            summary=f"{auto} auto-approved action(s), {len(filtered) - auto} awaiting human approval",
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
