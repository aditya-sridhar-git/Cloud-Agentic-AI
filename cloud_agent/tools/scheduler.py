"""
Scheduler Tool — stop non-production instances outside business hours.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytz  # fallback — stdlib zoneinfo may not be available on all Pythons

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


def _is_business_hours(cfg: dict[str, Any]) -> bool:
    """Check whether the current time falls within configured business hours."""
    try:
        tz = pytz.timezone(cfg.get("timezone", "US/Eastern"))
    except Exception:
        tz = pytz.UTC

    now = datetime.now(tz)
    start_h, start_m = (int(x) for x in cfg.get("start", "08:00").split(":"))
    end_h, end_m = (int(x) for x in cfg.get("end", "18:00").split(":"))

    start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

    return start <= now <= end


@register_tool("scheduler")
class SchedulerTool(BaseTool):
    """Stops dev/non-prod instances outside of business hours."""

    def execute(self, action: Action) -> dict[str, Any]:
        instance_id = action.resource_id
        bh_cfg = self.config.get("tools", {}).get("scheduler", {}).get("business_hours", {})

        if _is_business_hours(bh_cfg):
            logger.info(
                "[green]⏰ SKIP[/green] %s — within business hours", instance_id
            )
            return {
                "tool": self.tool_name,
                "instance_id": instance_id,
                "status": "skipped",
                "reason": "within business hours",
            }

        logger.info(
            "[bold yellow]⏰ STOP[/bold yellow] dev instance [cyan]%s[/cyan] — outside business hours",
            instance_id,
        )
        result = self.provider.stop_instance(instance_id)
        result["tool"] = self.tool_name
        return result
