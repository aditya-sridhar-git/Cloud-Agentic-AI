"""
Cost Monitor Tool — detect daily spending anomalies and raise alerts.
"""

from __future__ import annotations

from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


@register_tool("cost_monitor")
class CostMonitorTool(BaseTool):
    """Alerts or freezes resources when daily cloud spend exceeds the baseline."""

    def execute(self, action: Action) -> dict[str, Any]:
        action_type = action.action_type  # "alert" or "freeze"

        if action_type == "freeze":
            logger.info(
                "[bold red]💰 FREEZE[/bold red] — spend anomaly detected! %s",
                action.reason,
            )
            # In a real system this would tag non-critical resources for
            # shutdown or invoke an SNS topic / PagerDuty alert.
            return {
                "tool": self.tool_name,
                "status": "freeze_initiated",
                "reason": action.reason,
            }
        else:
            logger.info(
                "[yellow]💰 ALERT[/yellow] — cost spike detected: %s",
                action.reason,
            )
            return {
                "tool": self.tool_name,
                "status": "alert_sent",
                "reason": action.reason,
            }
