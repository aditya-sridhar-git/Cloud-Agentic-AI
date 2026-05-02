"""
Tag Enforcer Tool — apply missing required tags to resources.
"""

from __future__ import annotations

from typing import Any

from cloud_agent.agent.baseagent import Action, Observation
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger
from cloud_agent.monitor.collector import MetricsCollector

logger = get_logger(__name__)


@register_tool("tag_enforcer")
class TagEnforcerTool(BaseTool):
    """Ensures all resources carry the required tags defined in config."""

    def execute(self, action: Action) -> dict[str, Any]:
        action_type = action.action_type
        if action_type == "check":
            return self._check_tags_status()

        resource_id = action.resource_id
        missing_keys: list[str] = action.parameters.get("missing_tags", [])
        cfg = self.config.get("tools", {}).get("tag_enforcer", {})

        # Build a map of required tag defaults
        defaults: dict[str, str] = {
            t["Key"]: t.get("Default", "unset")
            for t in cfg.get("required_tags", [])
        }

        tags_to_apply = [
            {"Key": key, "Value": defaults.get(key, "unset")}
            for key in missing_keys
        ]

        logger.info(
            "[green]🏷️  TAG[/green] resource [cyan]%s[/cyan] — applying %d tag(s): %s",
            resource_id,
            len(tags_to_apply),
            ", ".join(t["Key"] for t in tags_to_apply),
        )

        result = self.provider.set_tags(resource_id, tags_to_apply)
        result["tool"] = self.tool_name
        return result

    def _check_tags_status(self) -> dict[str, Any]:
        """Check all instances and report which ones are missing required tags."""
        collector = MetricsCollector(self.provider, self.config)
        obs = collector.collect()
        
        from cloud_agent.monitor.evaluator import ThresholdEvaluator
        evaluator = ThresholdEvaluator(self.config)
        actions = evaluator._check_tags(obs)
        
        status_msg = "All instances are fully tagged according to policy."
        if actions:
            status_msg = f"Found {len(actions)} instances missing required tags."
            
        return {
            "tool": self.tool_name,
            "status": "check_completed",
            "reason": status_msg,
            "pending_actions": [
                {
                    "instance_id": a.resource_id, 
                    "missing_tags": a.parameters.get("missing_tags", [])
                } for a in actions
            ]
        }
