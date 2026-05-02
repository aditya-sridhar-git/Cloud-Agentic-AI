"""
Scheduler Tool — manage non-production instances based on business hours.

Bidirectional: stops dev instances outside business hours and
starts them back when business hours resume.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytz

from cloud_agent.agent.baseagent import Action, Observation
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger
from cloud_agent.monitor.collector import MetricsCollector

logger = get_logger(__name__)


def _is_business_hours(cfg: dict[str, Any]) -> bool:
    """Check whether the current time falls within configured business hours."""
    try:
        tz = pytz.timezone(cfg.get("timezone", "US/Eastern"))
    except Exception:
        tz = pytz.UTC

    now = datetime.now(tz)

    # Skip weekends
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    start_h, start_m = (int(x) for x in cfg.get("start", "08:00").split(":"))
    end_h, end_m = (int(x) for x in cfg.get("end", "18:00").split(":"))

    start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

    return start <= now <= end


@register_tool("scheduler")
class SchedulerTool(BaseTool):
    """Manages dev/non-prod instances — stops outside hours, starts during hours."""

    def execute(self, action: Action) -> dict[str, Any]:
        instance_id = action.resource_id
        bh_cfg = self.config.get("tools", {}).get("scheduler", {}).get("business_hours", {})
        action_type = action.action_type  # "stop" or "start"

        is_bh = _is_business_hours(bh_cfg)

        if action_type == "check":
            return self._check_scheduler_status()

        # --- STOP path (called outside business hours) ---
        if action_type == "stop":
            if is_bh:
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

        # --- START path (called at business hours start) ---
        elif action_type == "start":
            if not is_bh:
                logger.info(
                    "[yellow]⏰ SKIP START[/yellow] %s — still outside business hours", instance_id
                )
                return {
                    "tool": self.tool_name,
                    "instance_id": instance_id,
                    "status": "skipped",
                    "reason": "outside business hours",
                }

            logger.info(
                "[bold green]⏰ START[/bold green] dev instance [cyan]%s[/cyan] — business hours resumed",
                instance_id,
            )
            result = self.provider.start_instance(instance_id)
            result["tool"] = self.tool_name
            return result

        else:
            logger.warning("Unknown scheduler action: %s", action_type)
            return {
                "tool": self.tool_name,
                "instance_id": instance_id,
                "status": "unknown_action",
                "action_type": action_type,
            }

    def _check_scheduler_status(self) -> dict[str, Any]:
        """Check all dev instances and report what actions the scheduler would take."""
        collector = MetricsCollector(self.provider, self.config)
        obs = collector.collect()
        
        from cloud_agent.monitor.evaluator import ThresholdEvaluator
        evaluator = ThresholdEvaluator(self.config)
        actions = evaluator._check_scheduler(obs)
        
        status_msg = "All instances are correctly scheduled."
        if actions:
            status_msg = f"Found {len(actions)} instances needing scheduler actions."
            
        return {
            "tool": self.tool_name,
            "status": "check_completed",
            "reason": status_msg,
            "pending_actions": [{"instance_id": a.resource_id, "action": a.action_type, "reason": a.reason} for a in actions]
        }
