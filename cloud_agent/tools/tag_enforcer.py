"""
Tag Enforcer Tool — apply missing required tags to resources.
"""

from __future__ import annotations

from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


@register_tool("tag_enforcer")
class TagEnforcerTool(BaseTool):
    """Ensures all resources carry the required tags defined in config."""

    def execute(self, action: Action) -> dict[str, Any]:
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
