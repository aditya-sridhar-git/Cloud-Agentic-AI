"""
Evaluator — compares live metrics to configured thresholds.

This is the deterministic rule engine. It is used directly by the
rule-based fallback of the Reasoning Agent and, for time-based checks the
LLM cannot do reliably (business hours), also on the LLM path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cloud_agent.agent.baseagent import Action, Observation
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


def _tag_map(resource: dict[str, Any]) -> dict[str, str]:
    return {t["Key"]: str(t["Value"]) for t in resource.get("tags", [])}


class ThresholdEvaluator:
    """Compare observations against YAML thresholds and emit findings."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._tools_cfg = config.get("tools", {})

    def evaluate(self, observation: Observation) -> list[Action]:
        """Return a list of recommended actions based purely on thresholds."""
        actions: list[Action] = []
        actions.extend(self._check_idle_servers(observation))
        actions.extend(self._check_rightsizing(observation))
        actions.extend(self._check_diagnosis(observation))
        actions.extend(self._check_orphaned_disks(observation))
        cost_actions = self._check_cost_spike(observation)
        actions.extend(cost_actions)
        actions.extend(self._check_tags(observation))
        actions.extend(self._check_scheduler(observation))
        actions.extend(self._check_security(observation))
        actions.extend(self._check_cross_domain(observation, cost_actions))
        actions.extend(self._check_periodic(observation))
        actions.extend(self._check_query_optimizer(observation))
        return actions

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_idle_servers(self, obs: Observation) -> list[Action]:
        cfg = self._tools_cfg.get("idle_server", {})
        if not cfg.get("enabled"):
            return []
        thresh = cfg.get("cpu_threshold_percent", 5.0)
        default_action = cfg.get("action", "stop")
        policy_tag = cfg.get("policy_tag", "IdlePolicy")
        exclude_tags = {
            (t["Key"], str(t["Value"]).lower()) for t in cfg.get("exclude_tags", [])
        }

        results: list[Action] = []
        for inst in obs.instances:
            if inst.get("state") != "running":
                continue
            # skip excluded
            inst_tags = {(t["Key"], str(t["Value"]).lower()) for t in inst.get("tags", [])}
            if inst_tags & exclude_tags:
                continue
            cpu = inst.get("cpu_percent", 100.0)
            if 0 <= cpu < thresh:
                # Per-instance tag policy overrides the global default
                action_type = _tag_map(inst).get(policy_tag, default_action).lower()
                if action_type not in ("stop", "terminate"):
                    action_type = default_action
                results.append(
                    Action(
                        tool_name="idle_server",
                        resource_id=inst["instance_id"],
                        action_type=action_type,
                        reason=f"CPU {cpu:.1f}% < {thresh}%",
                    )
                )
        return results

    def _check_rightsizing(self, obs: Observation) -> list[Action]:
        cfg = self._tools_cfg.get("rightsizer", {})
        if not cfg.get("enabled"):
            return []
        from cloud_agent.tools.rightsizer import downgrade_for

        thresh = cfg.get("cpu_threshold_percent", 20.0)
        idle_thresh = self._tools_cfg.get("idle_server", {}).get("cpu_threshold_percent", 5.0)
        action_type = "resize" if cfg.get("action", "recommend") == "apply" else "recommend"

        results: list[Action] = []
        for inst in obs.instances:
            if inst.get("state") != "running":
                continue
            avg = inst.get("cpu_percent_7d")
            if avg is None or avg < 0:
                continue
            # Idle instances are handled by the idle-server tool, not the rightsizer
            if avg < idle_thresh or avg >= thresh:
                continue
            current = inst.get("instance_type", "")
            target = downgrade_for(current)
            if not target or target == current:
                continue
            results.append(
                Action(
                    tool_name="rightsizer",
                    resource_id=inst["instance_id"],
                    action_type=action_type,
                    parameters={"current_type": current, "recommended_type": target},
                    reason=f"7-day avg CPU {avg:.1f}% < {thresh}% — {current} → {target}",
                )
            )
        return results

    def _check_diagnosis(self, obs: Observation) -> list[Action]:
        cfg = self._tools_cfg.get("diagnose_server", {})
        if not cfg.get("enabled"):
            return []
        cpu_thresh = cfg.get("cpu_high_threshold", 85.0)
        mem_thresh = cfg.get("memory_high_threshold", 90.0)

        results: list[Action] = []
        for inst in obs.instances:
            if inst.get("state") != "running":
                continue
            cpu = inst.get("cpu_percent", 0.0) or 0.0
            mem = inst.get("memory_percent")
            reasons = []
            if cpu >= cpu_thresh:
                reasons.append(f"CPU {cpu:.1f}% >= {cpu_thresh}%")
            if mem is not None and mem >= mem_thresh:
                reasons.append(f"memory {mem:.1f}% >= {mem_thresh}%")
            if reasons:
                results.append(
                    Action(
                        tool_name="diagnose_server",
                        resource_id=inst["instance_id"],
                        action_type="diagnose",
                        reason=" and ".join(reasons) + " — investigating root cause",
                    )
                )
        return results

    # Kept for backwards compatibility with older callers
    _check_high_cpu = _check_diagnosis

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
        resources = [(i["instance_id"], i) for i in obs.instances if i.get("state") != "terminated"]
        # Volumes are only checked when the provider reports their tags
        resources += [(d["volume_id"], d) for d in obs.disks if "tags" in d]
        for rid, res in resources:
            existing = {t["Key"] for t in res.get("tags", [])}
            missing = required - existing
            if missing:
                results.append(
                    Action(
                        tool_name="tag_enforcer",
                        resource_id=rid,
                        action_type="tag",
                        parameters={"missing_tags": sorted(missing)},
                        reason=f"Missing: {', '.join(sorted(missing))}",
                    )
                )
        return results

    def _check_scheduler(self, obs: Observation) -> list[Action]:
        cfg = self._tools_cfg.get("scheduler", {})
        if not cfg.get("enabled"):
            return []

        from cloud_agent.tools.scheduler import hours_for_instance, _is_business_hours

        now = datetime.fromtimestamp(obs.timestamp, tz=timezone.utc)
        results: list[Action] = []
        for inst in obs.instances:
            window = hours_for_instance(cfg, inst)
            if window is None:
                continue
            is_bh = _is_business_hours(window, now=now)
            state = inst.get("state")
            if is_bh and state == "stopped":
                results.append(
                    Action(
                        tool_name="scheduler",
                        resource_id=inst["instance_id"],
                        action_type="start",
                        reason="Business hours resumed — starting instance",
                    )
                )
            elif not is_bh and state == "running":
                results.append(
                    Action(
                        tool_name="scheduler",
                        resource_id=inst["instance_id"],
                        action_type="stop",
                        reason="Outside business hours — stopping instance",
                    )
                )
        return results

    def _check_security(self, obs: Observation) -> list[Action]:
        if not self._tools_cfg.get("security_auditor", {}).get("enabled"):
            return []
        return [
            Action(
                tool_name="security_auditor",
                resource_id="account",
                action_type="full_scan",
                reason="Periodic security audit",
            )
        ]

    def _check_cross_domain(self, obs: Observation, cost_actions: list[Action]) -> list[Action]:
        cfg = self._tools_cfg.get("cross_domain", {})
        if not cfg.get("enabled") or not cfg.get("auto_trigger_on_cost_spike", True):
            return []
        if not cost_actions:
            return []
        return [
            Action(
                tool_name="cross_domain",
                resource_id="account",
                action_type="correlate",
                reason=f"Cross-domain analysis triggered by cost anomaly: {cost_actions[0].reason}",
            )
        ]

    def _check_periodic(self, obs: Observation) -> list[Action]:
        """Read-only periodic scans for the supplementary tools."""
        results: list[Action] = []
        if self._tools_cfg.get("cert_monitor", {}).get("enabled"):
            results.append(Action(
                tool_name="cert_monitor", resource_id="account", action_type="check",
                reason="Periodic certificate expiry scan",
            ))
        if self._tools_cfg.get("backup_manager", {}).get("enabled"):
            results.append(Action(
                tool_name="backup_manager", resource_id="account", action_type="full_cycle",
                reason="Scheduled backup and retention cycle",
            ))
        return results

    def _check_query_optimizer(self, obs: Observation) -> list[Action]:
        cfg = self._tools_cfg.get("query_optimizer", {})
        if not cfg.get("enabled"):
            return []
        return [
            Action(
                tool_name="query_optimizer",
                resource_id="rds",
                action_type="analyze",
                reason=(
                    "Discover RDS instances, check CloudWatch latency, collect slow query "
                    "evidence, and produce optimization recommendations"
                ),
            )
        ]
