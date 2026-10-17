"""Tests for action guardrails, the approval workflow, the planner and the consensus fail-safe."""

from __future__ import annotations

import copy

import pytest

from cloud_agent.agent.approval import ApprovalMemory, ApprovalWorkflow
from cloud_agent.agent.baseagent import Action, Observation, Plan
from cloud_agent.agent.consensus import ConsensusEngine
from cloud_agent.agent.guardrails import ActionValidator
from cloud_agent.agent.planningagent import Planner
from cloud_agent.utils.config import load_config


def _obs() -> Observation:
    def inst(iid, cpu, state="running", env="dev", extra=None):
        tags = [{"Key": "Environment", "Value": env}] + [{"Key": k, "Value": v} for k, v in (extra or {}).items()]
        return {"instance_id": iid, "state": state, "cpu_percent": cpu, "tags": tags, "instance_type": "m5.large"}

    return Observation(
        instances=[
            inst("i-idle", 1.0),
            inst("i-busy", 80.0, env="prod"),
            inst("i-protected", 0.5, extra={"PreserveInstance": "true"}),
            inst("i-stopped", 0.0, state="stopped"),
        ],
        disks=[
            {"volume_id": "vol-orphan", "state": "available", "unattached_days": 30},
            {"volume_id": "vol-attached", "state": "in-use", "unattached_days": 0},
            {"volume_id": "vol-fresh", "state": "available", "unattached_days": 2},
        ],
    )


@pytest.fixture
def config():
    return copy.deepcopy(load_config())


class TestGuardrails:
    def test_accepts_grounded_actions(self, config):
        report = ActionValidator(config).validate(
            [Action("idle_server", "i-idle", "stop"), Action("disk_cleanup", "vol-orphan", "snapshot_delete")], _obs())
        assert len(report.accepted) == 2 and not report.rejected

    @pytest.mark.parametrize("action, why", [
        (Action("idle_server", "i-doesnotexist", "stop"), "not found"),
        (Action("idle_server", "i-busy", "stop"), "idleness"),
        (Action("idle_server", "i-protected", "terminate"), "protection"),
        (Action("idle_server", "i-stopped", "stop"), "not running"),
        (Action("disk_cleanup", "vol-attached", "snapshot_delete"), "attached"),
        (Action("disk_cleanup", "vol-fresh", "snapshot_delete"), "unattached"),
        (Action("rightsizer", "i-idle", "terminate"), "not supported"),
        (Action("kill_switch", "account", "shutdown"), "unknown tool"),
    ])
    def test_rejects_unsafe_or_hallucinated(self, config, action, why):
        report = ActionValidator(config).validate([action], _obs())
        assert not report.accepted
        assert why in report.rejected[0][1]

    def test_deduplicates(self, config):
        a = Action("idle_server", "i-idle", "stop")
        report = ActionValidator(config).validate([a, Action("idle_server", "i-idle", "stop")], _obs())
        assert len(report.accepted) == 1

    def test_terminate_downgraded_when_disallowed(self, config):
        config["safety"]["allow_terminate"] = False
        report = ActionValidator(config).validate([Action("idle_server", "i-idle", "terminate")], _obs())
        assert report.accepted[0].action_type == "stop"


class TestApprovalWorkflow:
    def test_high_impact_and_prod_stop_need_approval(self, config, tmp_path):
        wf = ApprovalWorkflow(config, ApprovalMemory(tmp_path / "a.json"))
        obs = _obs()
        prod = obs.instances[1]
        assert wf.needs_approval(Action("disk_cleanup", "vol-orphan", "snapshot_delete"))
        assert wf.needs_approval(Action("idle_server", "i-busy", "stop"), prod)
        assert wf.needs_approval(Action("idle_server", "i-idle", "stop"), obs.instances[0]) is None

    def test_memory_persists_and_dedupes(self, tmp_path):
        mem = ApprovalMemory(tmp_path / "a.json")
        a = Action("disk_cleanup", "vol-1", "snapshot_delete", reason="orphaned")
        assert mem.add_pending(a, "high impact")
        assert not mem.add_pending(a, "high impact")
        mem.record_feedback("vol-1", "snapshot_delete", "approved", tool_name="disk_cleanup", reason="orphaned")
        reloaded = ApprovalMemory(tmp_path / "a.json")
        assert len(reloaded.get_pending_approvals()) == 1
        assert reloaded.successful_examples()[0]["resource_id"] == "vol-1"
        assert reloaded.pop_pending("disk_cleanup", "vol-1", "snapshot_delete") is not None
        assert reloaded.get_pending_approvals() == []

    def test_planner_flags_gated_actions(self, config, tmp_path):
        config["agent"]["dry_run"] = False
        planner = Planner(config, ApprovalWorkflow(config, ApprovalMemory(tmp_path / "a.json")))
        plan = planner.refine(Plan([Action("idle_server", "i-idle", "stop"),
                                    Action("disk_cleanup", "vol-orphan", "snapshot_delete")]), _obs())
        flags = {a.tool_name: (a.approved, a.requires_approval) for a in plan.actions}
        assert flags["idle_server"] == (True, False)
        assert flags["disk_cleanup"] == (False, True)

    def test_planner_drops_disabled_tools(self, config, tmp_path):
        config["tools"]["idle_server"]["enabled"] = False
        planner = Planner(config, ApprovalWorkflow(config, ApprovalMemory(tmp_path / "a.json")))
        assert planner.refine(Plan([Action("idle_server", "i-idle", "stop")])).actions == []


class TestConsensusFailSafe:
    def test_escalates_without_llm(self, config, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config["agent"]["multi_agent_consensus"] = True
        result = ConsensusEngine(config).validate_action(Action("idle_server", "i-1", "terminate"), _obs())
        assert result.status == "ESCALATED"

    def test_read_only_actions_bypass(self, config):
        config["agent"]["multi_agent_consensus"] = True
        result = ConsensusEngine(config).validate_action(Action("diagnose_server", "i-1", "diagnose"), _obs())
        assert result.status == "APPROVED"
