"""
Rightsizer Tool — recommend or apply instance type downgrades.
"""

from __future__ import annotations

from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Simple mapping of instance types to their next-smaller equivalent
_DOWNGRADE_MAP: dict[str, str] = {
    "t3.2xlarge": "t3.xlarge",
    "t3.xlarge": "t3.large",
    "t3.large": "t3.medium",
    "t3.medium": "t3.small",
    "t3.small": "t3.micro",
    "m5.4xlarge": "m5.2xlarge",
    "m5.2xlarge": "m5.xlarge",
    "m5.xlarge": "m5.large",
    "c5.4xlarge": "c5.2xlarge",
    "c5.2xlarge": "c5.xlarge",
    "c5.xlarge": "c5.large",
    "r5.2xlarge": "r5.xlarge",
    "r5.xlarge": "r5.large",
}


def downgrade_for(instance_type: str) -> str | None:
    """Next-smaller type in the same family, or None if already the smallest/unknown."""
    return _DOWNGRADE_MAP.get(instance_type)


@register_tool("rightsizer")
class RightsizeTool(BaseTool):
    """Recommends or applies instance type downgrades for under-used VMs."""

    def execute(self, action: Action) -> dict[str, Any]:
        if action.action_type == "check":
            return self._check_recommendations()

        instance_id = action.resource_id
        current_type = action.parameters.get("current_type", "unknown")
        suggested = action.parameters.get("recommended_type") or downgrade_for(current_type) or current_type

        # Only an explicit "resize" action changes the instance; "recommend" never does.
        if action.action_type == "resize" and suggested != current_type:
            logger.info(
                "[bold yellow]📐 RESIZE[/bold yellow] %s: %s → %s — %s",
                instance_id, current_type, suggested, action.reason,
            )
            result = self.provider.resize_instance(instance_id, suggested)
        else:
            logger.info(
                "[yellow]📐 RECOMMEND[/yellow] resize %s: %s → %s — %s",
                instance_id, current_type, suggested, action.reason,
            )
            result = {
                "instance_id": instance_id,
                "current_type": current_type,
                "recommended_type": suggested,
                "status": "recommendation",
            }

        result["tool"] = self.tool_name
        return result

    def _check_recommendations(self) -> dict[str, Any]:
        """Read-only scan used by the chat interface."""
        from cloud_agent.monitor.collector import MetricsCollector
        from cloud_agent.monitor.evaluator import ThresholdEvaluator

        obs = MetricsCollector(self.provider, self.config).collect()
        actions = ThresholdEvaluator(self.config)._check_rightsizing(obs)
        return {
            "tool": self.tool_name,
            "status": "check_completed",
            "recommendations": [
                {"instance_id": a.resource_id, **a.parameters, "reason": a.reason} for a in actions
            ],
        }
