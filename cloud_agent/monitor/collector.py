"""
Metrics collector — pulls live data from the cloud provider.
"""

from __future__ import annotations

from typing import Any

from cloud_agent.agent.baseagent import Observation
from cloud_agent.cloud.provider import CloudProvider
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


class MetricsCollector:
    """Gathers instances, volumes, tags, costs, and CPU metrics from the cloud."""

    def __init__(self, provider: CloudProvider, config: dict[str, Any]) -> None:
        self._provider = provider
        self._config = config

    def collect(self) -> Observation:
        """Build a full :class:`Observation` snapshot."""
        logger.info("Collecting instances …")
        instances = self._provider.list_instances()

        # Enrich with CPU metrics
        idle_cfg = self._config.get("tools", {}).get("idle_server", {})
        duration = idle_cfg.get("duration_minutes", 30)
        for inst in instances:
            if inst.get("state") == "running":
                try:
                    inst["cpu_percent"] = self._provider.get_cpu_utilization(
                        inst["instance_id"], 
                        region=inst.get("region"),
                        minutes=duration
                    )
                except Exception:
                    logger.warning("Could not get CPU for %s", inst["instance_id"])
                    inst["cpu_percent"] = -1.0

        logger.info("Collecting volumes …")
        disks = self._provider.list_volumes()

        logger.info("Collecting costs …")
        try:
            cost_cfg = self._config.get("tools", {}).get("cost_monitor", {})
            baseline_days = cost_cfg.get("baseline_window_days", 7)
            costs = {
                "current_daily": self._provider.get_daily_cost(days=1),
                "baseline_daily": self._provider.get_cost_baseline(days=baseline_days),
            }
        except Exception:
            logger.warning("Cost data unavailable")
            costs = {}

        return Observation(
            instances=instances,
            disks=disks,
            costs=costs,
            metrics={},
            tags={},
        )
