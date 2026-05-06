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
        action_type = action.action_type  # "check", "stop" or "terminate"

        if action_type == "check":
            logger.info("Scanning for idle instances...")
            instances = self.provider.list_instances()
            idle_instances = []
            
            # Use threshold from config or default to 5%
            threshold = self.config.get("idle_threshold_percent", 5.0)
            
            for inst in instances:
                if inst.get("state") == "running":
                    cpu = self.provider.get_cpu_utilization(inst["instance_id"])
                    if cpu < threshold:
                        inst["avg_cpu"] = cpu
                        idle_instances.append(inst)
            
            return {
                "success": True,
                "idle_instances": idle_instances,
                "threshold": threshold,
                "count": len(idle_instances)
            }

        logger.info(
            "[bold red]🔌 %s[/bold red] instance [cyan]%s[/cyan] — %s",
            action_type.upper(),
            instance_id,
            action.reason,
        )

        if not instance_id:
            raise ValueError("Instance ID is required for stop/terminate actions")

        if action_type == "terminate":
            result = self.provider.terminate_instance(instance_id)
        else:
            result = self.provider.stop_instance(instance_id)

        result["tool"] = self.tool_name
        result["action"] = action_type
        return result
