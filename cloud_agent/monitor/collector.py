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

        # Enrich with CPU / memory metrics
        tools_cfg = self._config.get("tools", {})
        duration = tools_cfg.get("idle_server", {}).get("duration_minutes", 30)
        want_7d = tools_cfg.get("rightsizer", {}).get("enabled", False)
        eval_days = tools_cfg.get("rightsizer", {}).get("evaluation_days", 7)
        # sysbench runs real load on the instance (and installs packages), so it is
        # strictly opt-in and never part of the default observe phase.
        bench_cfg = self._config.get("benchmarks", {}).get("sysbench", {})
        run_bench = bench_cfg.get("enabled", False)

        for inst in instances:
            if inst.get("state") != "running":
                continue
            region = inst.get("region")
            try:
                inst["cpu_percent"] = self._provider.get_cpu_utilization(
                    inst["instance_id"], region=region, minutes=duration,
                )
            except Exception:
                logger.warning("Could not get CPU for %s", inst["instance_id"])
                inst["cpu_percent"] = -1.0
            if want_7d:
                try:
                    inst["cpu_percent_7d"] = self._provider.get_cpu_utilization_days(
                        inst["instance_id"], days=eval_days, region=region,
                    )
                except Exception:
                    inst["cpu_percent_7d"] = -1.0
            if inst.get("memory_percent") is None:
                try:
                    mem = self._provider.get_memory_utilization(inst["instance_id"], region=region)
                except Exception:
                    mem = None
                if mem is not None:
                    inst["memory_percent"] = mem
            if run_bench:
                try:
                    inst["sysbench"] = self._provider.run_sysbench_benchmark(
                        inst["instance_id"], timeout=bench_cfg.get("timeout_seconds", 120),
                    )
                    inst["benchmark_profile"] = inst["sysbench"].get("status", "ok")
                except Exception as exc:
                    logger.warning("Could not run sysbench for %s: %s", inst["instance_id"], exc)
                    inst["sysbench"] = {"status": "error", "error": str(exc)}
                    inst["benchmark_profile"] = "error"

        logger.info("Collecting volumes …")
        disks = self._provider.list_volumes()

        logger.info("Collecting costs …")
        try:
            cost_cfg = self._config.get("tools", {}).get("cost_monitor", {})
            baseline_days = cost_cfg.get("baseline_window_days", 30)
            current_daily = self._provider.get_daily_cost(days=1)
            baseline_daily = self._provider.get_cost_baseline(days=baseline_days)

            # Per-service breakdown for the last day
            try:
                services = self._provider.get_cost_by_service(days=1)
            except Exception:
                services = []

            # Compute spike percentage
            delta_pct = 0.0
            if baseline_daily > 0:
                delta_pct = round(((current_daily - baseline_daily) / baseline_daily) * 100.0, 1)

            costs = {
                "current_daily": current_daily,
                "baseline_daily": baseline_daily,
                "delta_pct": delta_pct,
                "currency": "USD",
                "services": services,
            }
        except Exception:
            logger.warning("Cost data unavailable")
            costs = {}


        clock = getattr(self._provider, "clock", None)
        observation = Observation(
            instances=instances,
            disks=disks,
            costs=costs,
            metrics={},
            tags={},
        )
        if callable(clock):
            observation.timestamp = clock()
        if self._config.get("tools", {}).get("cross_domain", {}).get("enabled", False):
            try:
                observation.metrics["security_findings"] = self._provider.get_security_findings()
            except Exception:
                observation.metrics["security_findings"] = []
        return observation
