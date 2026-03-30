"""
Cost Monitor Tool — detect daily spending anomalies, alert, or freeze.

The "freeze" action now actually stops non-critical instances tagged as
dev/staging to contain the cost spike.
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

            # Actually freeze: stop all non-critical (dev/staging) instances
            frozen_instances = self._freeze_non_critical()

            return {
                "tool": self.tool_name,
                "status": "freeze_initiated",
                "reason": action.reason,
                "frozen_instances": frozen_instances,
                "frozen_count": len(frozen_instances),
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

    def _freeze_non_critical(self) -> list[str]:
        """Stop all dev/staging instances to contain cost spike."""
        frozen: list[str] = []
        freeze_envs = {"dev", "staging", "test", "qa"}

        try:
            instances = self.provider.list_instances()
        except Exception:
            logger.warning("Could not list instances for freeze")
            return frozen

        for inst in instances:
            if inst.get("state") != "running":
                continue
            tags = {t["Key"].lower(): t["Value"].lower() for t in inst.get("tags", [])}
            env = tags.get("environment", "")
            if env in freeze_envs:
                try:
                    self.provider.stop_instance(inst["instance_id"])
                    frozen.append(inst["instance_id"])
                    logger.info(
                        "  🧊 Froze %s (env=%s)", inst["instance_id"], env,
                    )
                except Exception:
                    logger.warning("Could not freeze %s", inst["instance_id"])

        logger.info("[bold red]💰 FREEZE COMPLETE[/bold red] — stopped %d instance(s)", len(frozen))
        return frozen
