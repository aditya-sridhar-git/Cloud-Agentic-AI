"""
Action guardrails — validate proposed actions before they reach the planner.

The reasoning engine (LLM or rules) can propose anything. Before an action
is allowed to proceed it must pass four checks:

1. **Schema**   — the tool exists and supports the requested action type.
2. **Existence** — the target resource is present in the current observation
   and is in a state where the action makes sense.
3. **Protection** — resources tagged as protected are never stopped,
   terminated, resized or frozen.
4. **Grounding** — the observed telemetry actually supports the action
   (e.g. an "idle" instance really is below the idle CPU threshold). This
   is what stops a hallucinated LLM action from reaching the cloud.

Duplicates (same tool, resource, action type) are removed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cloud_agent.agent.baseagent import Action, Observation
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

ALLOWED_ACTIONS: dict[str, set[str]] = {
    "idle_server": {"stop", "terminate", "check"},
    "rightsizer": {"recommend", "resize", "check"},
    "disk_cleanup": {"snapshot_delete", "check"},
    "tag_enforcer": {"tag", "check"},
    "scheduler": {"stop", "start", "check"},
    "cost_monitor": {"alert", "freeze", "check"},
    "diagnose_server": {"diagnose"},
    "security_auditor": {"full_scan", "check_security_groups", "check_s3_public", "check_ebs_encryption"},
    "cross_domain": {"correlate"},
    "query_optimizer": {"analyze"},
    "cert_monitor": {"check", "report", "full_scan"},
    "backup_manager": {"check", "create", "cleanup", "full_cycle"},
}

# Tools whose resource_id must be an instance in the observation
_INSTANCE_TOOLS = {"idle_server", "rightsizer", "scheduler", "diagnose_server"}
# Actions that take an instance out of service or change its shape
_DISRUPTIVE = {"stop", "terminate", "resize", "freeze"}


@dataclass
class ValidationReport:
    """Outcome of validating a list of proposed actions."""

    accepted: list[Action] = field(default_factory=list)
    rejected: list[tuple[Action, str]] = field(default_factory=list)


class ActionValidator:
    """Checks proposed actions against schema, inventory, protection tags and telemetry."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._tools_cfg = config.get("tools", {})
        safety = config.get("safety", {})
        protected = list(safety.get("protected_tags", [{"Key": "PreserveInstance", "Value": "true"}]))
        protected += self._tools_cfg.get("idle_server", {}).get("exclude_tags", [])
        self._protected = {(t["Key"], str(t["Value"]).lower()) for t in protected}
        self._allow_terminate = safety.get("allow_terminate", True)
        self._check_grounding = safety.get("grounding_checks", True)

    def validate(self, actions: list[Action], obs: Observation) -> ValidationReport:
        report = ValidationReport()
        instances = {i["instance_id"]: i for i in obs.instances}
        volumes = {d["volume_id"]: d for d in obs.disks}
        seen: set[tuple[str, str, str]] = set()

        for action in actions:
            if action.key() in seen:
                continue
            seen.add(action.key())

            reason = self._check(action, instances, volumes)
            if reason:
                report.rejected.append((action, reason))
                logger.warning(
                    "[red]Guardrail rejected[/red] %s/%s on %s — %s",
                    action.tool_name, action.action_type, action.resource_id, reason,
                )
            else:
                report.accepted.append(action)
        return report

    # ------------------------------------------------------------------

    def _check(self, a: Action, instances: dict[str, dict], volumes: dict[str, dict]) -> str | None:
        allowed = ALLOWED_ACTIONS.get(a.tool_name)
        if allowed is None:
            return f"unknown tool '{a.tool_name}'"
        if a.action_type not in allowed:
            return f"action '{a.action_type}' not supported by {a.tool_name}"

        if a.action_type == "terminate" and not self._allow_terminate:
            a.action_type = "stop"
            a.reason += " (terminate downgraded to stop by safety policy)"

        if a.tool_name in _INSTANCE_TOOLS:
            inst = instances.get(a.resource_id)
            if inst is None:
                return f"instance {a.resource_id} not found in current inventory"
            return self._check_instance(a, inst)

        if a.tool_name == "disk_cleanup" and a.action_type == "snapshot_delete":
            vol = volumes.get(a.resource_id)
            if vol is None:
                return f"volume {a.resource_id} not found in current inventory"
            if vol.get("state") != "available":
                return f"volume {a.resource_id} is attached (state={vol.get('state')})"
            min_days = self._tools_cfg.get("disk_cleanup", {}).get("unattached_days", 7)
            if self._check_grounding and vol.get("unattached_days", 0) < min_days:
                return f"volume unattached {vol.get('unattached_days', 0)}d < {min_days}d"

        if a.tool_name == "tag_enforcer" and a.action_type == "tag":
            if a.resource_id not in instances and a.resource_id not in volumes:
                return f"resource {a.resource_id} not found in current inventory"

        return None

    def _check_instance(self, a: Action, inst: dict[str, Any]) -> str | None:
        state = inst.get("state")
        tags = {(t["Key"], str(t["Value"]).lower()) for t in inst.get("tags", [])}

        if a.action_type in _DISRUPTIVE and tags & self._protected:
            return "instance carries a protection tag"

        if a.tool_name == "scheduler":
            if a.action_type == "stop" and state != "running":
                return f"cannot stop instance in state {state}"
            if a.action_type == "start" and state != "stopped":
                return f"cannot start instance in state {state}"
            return None

        if state != "running":
            return f"instance is {state}, not running"

        if not self._check_grounding:
            return None

        cpu = inst.get("cpu_percent")
        if a.tool_name == "idle_server":
            thresh = self._tools_cfg.get("idle_server", {}).get("cpu_threshold_percent", 5.0)
            if cpu is None or cpu < 0 or cpu >= thresh:
                return f"telemetry does not show idleness (cpu={cpu}, threshold={thresh})"
        if a.tool_name == "rightsizer" and a.action_type == "resize":
            thresh = self._tools_cfg.get("rightsizer", {}).get("cpu_threshold_percent", 20.0)
            avg = inst.get("cpu_percent_7d", cpu)
            if avg is None or avg < 0 or avg >= thresh:
                return f"telemetry does not show over-provisioning (cpu={avg}, threshold={thresh})"
        return None
