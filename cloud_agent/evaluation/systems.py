"""
Systems under test for the evaluation harness.

* ``native``      — emulation of AWS-native automation (rules only, one signal per service)
* ``agent_rules`` — Cloud Agentic AI with the deterministic reasoning fallback
* ``agent_llm``   — Cloud Agentic AI with LLM reasoning (requires OPENAI_API_KEY)
* ablation variants — ``agent_rules`` with one component switched off

All systems observe the same scenario through the same ``MetricsCollector``
so differences come from decision-making, not data access.
"""

from __future__ import annotations

import copy
import math
import os
import random
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cloud_agent.agent.approval import ApprovalMemory, ApprovalWorkflow
from cloud_agent.agent.baseagent import Action, Observation, Plan
from cloud_agent.evaluation.scenarios import ScenarioInstance
from cloud_agent.monitor.collector import MetricsCollector
from cloud_agent.monitor.evaluator import ThresholdEvaluator
from cloud_agent.utils.action_log import ActionLogger


@dataclass
class SystemOutput:
    """Normalised output of one system on one scenario run."""

    actions: list[dict[str, Any]] = field(default_factory=list)
    findings: set[tuple[str, str]] = field(default_factory=set)
    rca: dict[str, str] = field(default_factory=dict)
    patterns: set[str] = field(default_factory=set)
    latency_ms: float = 0.0
    mode: str = ""
    injected: list[tuple[str, str, str]] = field(default_factory=list)
    executed: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Baseline: AWS-native automation emulation
# ---------------------------------------------------------------------------

# AWS Config managed rules: restricted-ssh (22) and restricted-common-ports
# (default blockedPort1-5 = 20, 21, 3389, 3306, 4333).
_NATIVE_BLOCKED_PORTS = {22, 20, 21, 3389, 3306, 4333}


class NativeAWSBaseline:
    """Rule emulation of the native services the paper compares against (Table 5).

    ============================  ==================================================
    Native service                Emulated behaviour
    ============================  ==================================================
    CloudWatch alarm + EC2 action CPU < idle threshold → stop (no tag awareness)
    Compute Optimizer             7-day CPU < 20 % → downsizing recommendation
    CloudWatch alarm              CPU ≥ high threshold → alert (no root cause)
    Trusted Advisor               unattached EBS ≥ 7 days → flag (no remediation)
    AWS Config required-tags      missing tag → AWS-SetRequiredTags remediation
    Instance Scheduler on AWS     tag-based business-hours stop/start
    Cost Anomaly Detection        spend > 120 % of baseline → alert
    AWS Config managed rules      restricted-ssh, restricted-common-ports,
                                  s3-bucket-public-read-prohibited, encrypted-volumes
    (none)                        no cross-service correlation, no memory trigger
    ============================  ==================================================
    """

    def __init__(self, config: dict[str, Any], live: bool = False) -> None:
        self.config = config
        self.tools = config.get("tools", {})
        self.live = live

    def run(self, scn: ScenarioInstance) -> SystemOutput:
        out = self._decide(scn)
        if self.live:
            # Native alarm/Config/Scheduler actions execute directly — no approval step.
            p = scn.provider
            for a in out.actions:
                if a["action_type"] == "stop":
                    p.stop_instance(a["resource_id"])
                elif a["action_type"] == "start":
                    p.start_instance(a["resource_id"])
                elif a["action_type"] == "tag":
                    p.set_tags(a["resource_id"], [{"Key": k, "Value": "unset"} for k in a.get("missing_tags", [])])
            out.executed = p.action_log
        return out

    def _decide(self, scn: ScenarioInstance) -> SystemOutput:
        start = time.perf_counter()
        obs = MetricsCollector(scn.provider, self.config).collect()
        actions: list[Action] = []
        idle_t = self.tools.get("idle_server", {}).get("cpu_threshold_percent", 5.0)
        hot_t = self.tools.get("diagnose_server", {}).get("cpu_high_threshold", 85.0)
        rs_t = self.tools.get("rightsizer", {}).get("cpu_threshold_percent", 20.0)
        from cloud_agent.tools.rightsizer import downgrade_for

        for inst in obs.instances:
            if inst.get("state") != "running":
                continue
            cpu = inst.get("cpu_percent", -1)
            iid = inst["instance_id"]
            if 0 <= cpu < idle_t:
                actions.append(Action("idle_server", iid, "stop", reason="CloudWatch alarm"))
            if cpu >= hot_t:
                actions.append(Action("diagnose_server", iid, "alert", reason="CloudWatch alarm"))
            avg = inst.get("cpu_percent_7d", -1)
            if 0 <= avg < rs_t and downgrade_for(inst.get("instance_type", "")):
                actions.append(Action("rightsizer", iid, "recommend", reason="Compute Optimizer"))
        for disk in obs.disks:
            if disk.get("state") == "available" and disk.get("unattached_days", 0) >= 7:
                actions.append(Action("disk_cleanup", disk["volume_id"], "alert", reason="Trusted Advisor"))
        rules = ThresholdEvaluator(self.config)
        actions += rules._check_tags(obs)          # AWS Config + SSM remediation
        actions += rules._check_scheduler(obs)     # Instance Scheduler
        baseline = obs.costs.get("baseline_daily", 0)
        if baseline and obs.costs.get("current_daily", 0) > baseline * 1.2:
            actions.append(Action("cost_monitor", "account", "alert", reason="Cost Anomaly Detection"))

        findings: set[tuple[str, str]] = set()
        p = scn.provider
        for sg in p.describe_security_groups():
            for rule in sg.get("ingress_rules", []):
                if "0.0.0.0/0" in rule.get("cidr_blocks", []) and any(
                    rule.get("from_port", 0) <= port <= rule.get("to_port", 0) for port in _NATIVE_BLOCKED_PORTS
                ):
                    findings.add(("OPEN_SECURITY_GROUP", sg["group_id"]))
        for b in p.list_s3_buckets_public_access():
            if b.get("is_public"):
                findings.add(("PUBLIC_S3_BUCKET", b["bucket_name"]))
        for v in p.check_ebs_encryption():
            findings.add(("UNENCRYPTED_EBS", v["volume_id"]))

        return SystemOutput(
            actions=[_action_dict(a) for a in actions],
            findings=findings,
            latency_ms=(time.perf_counter() - start) * 1000,
            mode="native",
        )


# ---------------------------------------------------------------------------
# Cloud Agentic AI
# ---------------------------------------------------------------------------

def _action_dict(a: Action) -> dict[str, Any]:
    return {"tool": a.tool_name, "resource_id": a.resource_id, "action_type": a.action_type,
            "requires_approval": a.requires_approval,
            "missing_tags": a.parameters.get("missing_tags", [])}


class AgentSystem:
    """Runs the real ``CloudOpsAgent`` for one cycle on a scenario."""

    def __init__(self, config: dict[str, Any], name: str = "agent_rules",
                 injector: Callable[[Plan, Observation, random.Random], list[tuple[str, str, str]]] | None = None,
                 injector_seed: int = 0, live: bool = False) -> None:
        self.config = copy.deepcopy(config)
        self.name = name
        self.injector = injector
        self.injector_seed = injector_seed
        self.live = live

    def run(self, scn: ScenarioInstance) -> SystemOutput:
        from cloud_agent.main import CloudOpsAgent

        cfg = copy.deepcopy(self.config)
        cfg.setdefault("agent", {})["dry_run"] = not self.live
        with tempfile.TemporaryDirectory() as tmp:
            workflow = ApprovalWorkflow(cfg, ApprovalMemory(Path(tmp) / "approvals.json"))
            agent = CloudOpsAgent(cfg, provider=scn.provider, action_logger=ActionLogger(tmp), workflow=workflow)
            injected: list[tuple[str, str, str]] = []
            if self.injector is not None:
                original = agent._reasoning.analyse
                rng = random.Random(self.injector_seed)

                def analyse(observation: Observation, config: dict[str, Any]) -> Plan:
                    plan = original(observation, config)
                    injected.extend(self.injector(plan, observation, rng))
                    return plan

                agent._reasoning.analyse = analyse  # type: ignore[method-assign]

            results = agent.run_once()
            out = SystemOutput(
                latency_ms=agent.last_cycle.get("timings_ms", {}).get("total", 0.0),
                mode=agent.last_cycle.get("reasoning_mode", "rules"),
                injected=injected,
            )
            plan = agent.last_cycle.get("plan")
            if plan is not None:
                out.actions = [_action_dict(a) for a in plan.actions]
            for r in results:
                if r.get("tool") == "security_auditor":
                    out.findings |= {(f["type"], f["resource"]) for f in r.get("findings", [])}
                elif r.get("tool") == "diagnose_server" and "diagnosis" in r:
                    out.rca[r["resource_id"]] = r["diagnosis"].get("category", "unknown")
                elif r.get("tool") == "cross_domain":
                    out.patterns |= set(r.get("correlation", {}).get("patterns", []))
            if self.live:
                # An operator approves everything that was queued; measure the final effect.
                for p in agent.workflow.memory.get_pending_approvals():
                    agent.execute_approved(p["tool_name"], p["resource_id"], p["action_type"])
                out.executed = scn.provider.action_log
        return out


def agent_config(base: dict[str, Any], *, llm: bool = False, **changes: Any) -> dict[str, Any]:
    """Copy *base* for an agent variant. ``changes`` are dotted-path overrides."""
    cfg = copy.deepcopy(base)
    cfg.setdefault("llm", {})["enabled"] = llm
    for dotted, value in changes.items():
        node = cfg
        parts = dotted.split("__")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return cfg


def llm_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


# ---------------------------------------------------------------------------
# Reasoning-error injection (robustness study)
# ---------------------------------------------------------------------------

def inject_reasoning_errors(rate: float) -> Callable[[Plan, Observation, random.Random], list[tuple[str, str, str]]]:
    """Return an injector that appends erroneous actions to the reasoner's plan.

    Simulates the failure modes reported for LLM planners: hallucinated
    resource IDs, actions on busy or protected resources, unsupported
    tool/action combinations and deletion of attached volumes. The number of
    injected actions is ``ceil(rate × |plan|)`` (at least one when rate > 0).
    """

    def inject(plan: Plan, obs: Observation, rng: random.Random) -> list[tuple[str, str, str]]:
        if rate <= 0:
            return []
        running = [i for i in obs.instances if i.get("state") == "running"]
        busy = sorted(running, key=lambda i: -(i.get("cpu_percent") or 0))
        protected = [i for i in obs.instances if any(t["Key"] == "PreserveInstance" for t in i.get("tags", []))]
        attached = [d for d in obs.disks if d.get("state") == "in-use"]
        makers: list[Callable[[], Action | None]] = [
            lambda: Action("idle_server", f"i-{rng.getrandbits(52):013x}0000", "stop", reason="hallucinated id"),
            lambda: Action("idle_server", busy[0]["instance_id"], "stop", reason="busy instance") if busy else None,
            lambda: Action("idle_server", protected[0]["instance_id"], "terminate", reason="protected")
            if protected else None,
            lambda: Action("disk_cleanup", attached[0]["volume_id"], "snapshot_delete", reason="attached volume")
            if attached else None,
            lambda: Action("rightsizer", busy[-1]["instance_id"], "terminate", reason="bad action type")
            if busy else None,
            lambda: Action("kill_switch", "account", "shutdown_all", reason="unknown tool"),
        ]
        count = max(1, math.ceil(rate * max(len(plan.actions), 1)))
        injected = []
        for _ in range(count * 3):
            if len(injected) >= count:
                break
            action = rng.choice(makers)()
            if action is None:
                continue
            plan.actions.append(action)
            injected.append((action.tool_name, action.resource_id, action.action_type))
        return injected

    return inject
