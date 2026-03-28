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
    "m5.2xlarge": "m5.xlarge",
    "m5.xlarge": "m5.large",
    "m5.large": "m5.large",  # already smallest in family
    "c5.2xlarge": "c5.xlarge",
    "c5.xlarge": "c5.large",
}


@register_tool("rightsizer")
class RightsizeTool(BaseTool):
    """Recommends or applies instance type downgrades for under-used VMs."""

    def execute(self, action: Action) -> dict[str, Any]:
        instance_id = action.resource_id
        current_type = action.parameters.get("current_type", "unknown")
        suggested = _DOWNGRADE_MAP.get(current_type, current_type)
        cfg = self.config.get("tools", {}).get("rightsizer", {})
        mode = cfg.get("action", "recommend")

        if mode == "apply" and suggested != current_type:
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
