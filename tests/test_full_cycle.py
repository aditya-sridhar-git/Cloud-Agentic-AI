"""
Integration test — full agent cycle with MockProvider.
"""

from __future__ import annotations

import pytest

from cloud_agent.agent.approval import ApprovalMemory, ApprovalWorkflow
from cloud_agent.cloud.mock_provider import MockProvider
from cloud_agent.tools.base_tool import READ_ONLY_ACTIONS
from cloud_agent.utils.action_log import ActionLogger
from cloud_agent.utils.config import load_config


@pytest.fixture
def make_agent(tmp_path):
    def _make(dry_run: bool = True, **overrides):
        from cloud_agent.main import CloudOpsAgent

        config = load_config()
        config["agent"]["dry_run"] = dry_run
        config["agent"].update(overrides)
        provider = MockProvider(seed=7)
        workflow = ApprovalWorkflow(config, ApprovalMemory(tmp_path / "approvals.json"))
        agent = CloudOpsAgent(config, provider=provider, action_logger=ActionLogger(tmp_path),
                              workflow=workflow)
        return agent, provider
    return _make


class TestFullCycle:
    def test_mock_agent_dry_run(self, make_agent):
        """Dry-run: mutating actions are never executed, read-only tools still run."""
        agent, provider = make_agent(dry_run=True)

        results = agent.run_once()

        assert len(results) > 0
        for r in results:
            if r["action_type"] in READ_ONLY_ACTIONS:
                assert r["status"] not in ("dry_run",)
            else:
                assert r["status"] in ("dry_run", "pending_approval")
        # Nothing touched the (mock) cloud
        assert provider.action_log == []

    def test_mock_agent_finds_idle_instances(self, make_agent):
        """Verify the agent detects the 2 idle instances in mock data."""
        agent, _ = make_agent()
        results = agent.run_once()

        idle_actions = [r for r in results if r.get("tool") == "idle_server"]
        # Mock data has 2 instances with CPU < 5%: dev-webserver and legacy-api-prod
        assert len(idle_actions) >= 2

    def test_mock_agent_finds_missing_tags(self, make_agent):
        """Verify the agent detects missing tags on dev instances."""
        agent, _ = make_agent()
        results = agent.run_once()

        tag_actions = [r for r in results if r.get("tool") == "tag_enforcer"]
        assert len(tag_actions) >= 1

    def test_mock_agent_finds_orphaned_disks(self, make_agent):
        """Verify the agent detects orphaned volumes."""
        agent, _ = make_agent()
        results = agent.run_once()

        disk_actions = [r for r in results if r.get("tool") == "disk_cleanup"]
        # 2 volumes are unattached > 7 days
        assert len(disk_actions) >= 1

    def test_live_mode_queues_high_impact_and_executes_rest(self, make_agent):
        """Live: low-impact actions execute, high-impact ones wait for approval."""
        agent, provider = make_agent(dry_run=False)
        results = agent.run_once()

        by_status = {}
        for r in results:
            by_status.setdefault(r["status"], []).append(r)

        # Orphaned-disk deletion is high impact → queued, volume still exists
        assert any(r["tool"] == "disk_cleanup" for r in by_status.get("pending_approval", []))
        assert "vol-0a1b2c3d4e5f0002" in {v["volume_id"] for v in provider.list_volumes()}
        # Idle dev instance is stopped automatically; idle prod instance needs approval
        stopped = {e["instance_id"] for e in provider.action_log if e["action"] == "stop"}
        assert "i-0a1b2c3d4e5f60003" in stopped
        assert "i-0a1b2c3d4e5f60006" not in stopped
        pending = agent.workflow.memory.get_pending_approvals()
        assert any(p["resource_id"] == "i-0a1b2c3d4e5f60006" for p in pending)

    def test_operator_approval_executes_queued_action(self, make_agent):
        agent, provider = make_agent(dry_run=False)
        agent.run_once()

        result = agent.execute_approved("disk_cleanup", "vol-0a1b2c3d4e5f0002", "snapshot_delete")

        assert result["status"] == "deleted"
        assert "vol-0a1b2c3d4e5f0002" not in {v["volume_id"] for v in provider.list_volumes()}
        assert agent.workflow.memory.successful_examples()[-1]["resource_id"] == "vol-0a1b2c3d4e5f0002"

    def test_operator_approval_respects_dry_run(self, make_agent):
        agent, provider = make_agent(dry_run=True, require_approval=True)
        agent.run_once()
        agent.workflow.memory.add_pending(
            __import__("cloud_agent.agent.baseagent", fromlist=["Action"]).Action(
                tool_name="disk_cleanup", resource_id="vol-0a1b2c3d4e5f0002", action_type="snapshot_delete"),
            "test",
        )
        result = agent.execute_approved("disk_cleanup", "vol-0a1b2c3d4e5f0002", "snapshot_delete")
        assert result["status"] == "dry_run"
        assert provider.action_log == []

    def test_cycle_records_timings(self, make_agent):
        agent, _ = make_agent()
        agent.run_once()
        timings = agent.last_cycle["timings_ms"]
        assert set(timings) == {"observe", "think", "act", "total"}
        assert timings["total"] >= timings["think"]
