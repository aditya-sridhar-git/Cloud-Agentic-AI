"""
Idle Server Tool — stop or terminate instances with low CPU usage.
"""

from __future__ import annotations

from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


@register_tool("idle_server")
class IdleServerTool(BaseTool):
    """Shuts down servers that have been idle beyond the configured threshold."""

    def execute(self, action: Action) -> dict[str, Any]:
        instance_id = action.resource_id
        action_type = action.action_type  # "stop" or "terminate"

        logger.info(
            "[bold red]🔌 %s[/bold red] instance [cyan]%s[/cyan] — %s",
            action_type.upper(),
            instance_id,
            action.reason,
        )

        if action_type == "terminate":
            result = self.provider.terminate_instance(instance_id)
        else:
            result = self.provider.stop_instance(instance_id)

        result["tool"] = self.tool_name
        result["action"] = action_type
        return result
