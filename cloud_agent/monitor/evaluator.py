"""
Evaluator — compares live metrics to configured thresholds.

This is a lightweight, non-LLM path that can flag issues without calling
the reasoning engine (useful for fast, deterministic checks).
"""

from __future__ import annotations

from typing import Any

from cloud_agent.agent.baseagent import Action, Observation
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


class ThresholdEvaluator:
    """Compare observations against YAML thresholds and emit findings."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._tools_cfg = config.get("tools", {})

    def evaluate(self, observation: Observation) -> list[Action]:
        """Return a list of recommended actions based purely on thresholds."""
        actions: list[Action] = []
        actions.extend(self._check_idle_servers(observation))
        actions.extend(self._check_high_cpu(observation))
        actions.extend(self._check_orphaned_disks(observation))
        actions.extend(self._check_cost_spike(observation))
        actions.extend(self._check_tags(observation))
        return actions

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_idle_servers(self, obs: Observation) -> list[Action]:
        cfg = self._tools_cfg.get("idle_server", {})
        if not cfg.get("enabled"):
            return []
        thresh = cfg.get("cpu_threshold_percent", 5.0)
        action_type = cfg.get("action", "stop")
        exclude_tags = {
            (t["Key"], t["Value"]) for t in cfg.get("exclude_tags", [])
        }

        results: list[Action] = []
        for inst in obs.instances:
            if inst.get("state") != "running":
                continue
            # skip excluded
            inst_tags = {(t["Key"], t["Value"]) for t in inst.get("tags", [])}
            if inst_tags & exclude_tags:
                continue
            cpu = inst.get("cpu_percent", 100.0)
            if 0 <= cpu < thresh:
                results.append(
                    Action(
                        tool_name="idle_server",
                        resource_id=inst["instance_id"],
                        action_type=action_type,
                        reason=f"CPU {cpu:.1f}% < {thresh}%",
                    )
                )
        return results
    def _check_high_cpu(self, obs: Observation) -> list[Action]:
        cfg = self._tools_cfg.get("diagnose_server", {})
        if not cfg.get("enabled"):
            return []
        thresh = cfg.get("cpu_high_threshold", 85.0)

        results: list[Action] = []
        for inst in obs.instances:
            if inst.get("state") != "running":
                continue
            cpu = inst.get("cpu_percent", 0.0)
            if cpu >= thresh:
                results.append(
                    Action(
                        tool_name="diagnose_server",
                        resource_id=inst["instance_id"],
                        action_type="diagnose",
                        reason=f"CPU {cpu:.1f}% >= threshold {thresh}%",
                    )
                )
        return results


    def _check_orphaned_disks(self, obs: Observation) -> list[Action]:
        cfg = self._tools_cfg.get("disk_cleanup", {})
        if not cfg.get("enabled"):
            return []
        max_days = cfg.get("unattached_days", 7)

        results: list[Action] = []
        for disk in obs.disks:
            if disk.get("state") == "available" and disk.get("unattached_days", 0) >= max_days:
                results.append(
                    Action(
                        tool_name="disk_cleanup",
                        resource_id=disk["volume_id"],
                        action_type="snapshot_delete",
                        reason=f"Unattached {disk['unattached_days']}d (limit {max_days}d)",
                    )
                )
        return results

    def _check_cost_spike(self, obs: Observation) -> list[Action]:
        cfg = self._tools_cfg.get("cost_monitor", {})
        if not cfg.get("enabled"):
            return []
        thresh_pct = cfg.get("spike_threshold_percent", 120.0)
        baseline = obs.costs.get("baseline_daily", 0)
        current = obs.costs.get("current_daily", 0)

        if baseline > 0 and current > baseline * (thresh_pct / 100.0):
            return [
                Action(
                    tool_name="cost_monitor",
                    resource_id="account",
                    action_type=cfg.get("action", "alert"),
                    reason=f"${current:.2f}/day vs baseline ${baseline:.2f}/day ({thresh_pct}% limit)",
                )
            ]
        return []

    def _check_tags(self, obs: Observation) -> list[Action]:
        cfg = self._tools_cfg.get("tag_enforcer", {})
        if not cfg.get("enabled"):
            return []
        required = {t["Key"] for t in cfg.get("required_tags", [])}

        results: list[Action] = []
        for inst in obs.instances:
            existing = {t["Key"] for t in inst.get("tags", [])}
            missing = required - existing
            if missing:
                results.append(
                    Action(
                        tool_name="tag_enforcer",
                        resource_id=inst["instance_id"],
                        action_type="tag",
                        parameters={"missing_tags": list(missing)},
                        reason=f"Missing: {', '.join(sorted(missing))}",
                    )
                )
        return results
