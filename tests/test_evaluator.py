"""
Tests for the ThresholdEvaluator.
"""

from __future__ import annotations

from cloud_agent.agent.baseagent import Observation
from cloud_agent.monitor.evaluator import ThresholdEvaluator


_CONFIG = {
    "tools": {
        "idle_server": {
            "enabled": True,
            "cpu_threshold_percent": 5.0,
            "action": "stop",
            "exclude_tags": [{"Key": "PreserveInstance", "Value": "true"}],
        },
        "disk_cleanup": {
            "enabled": True,
            "unattached_days": 7,
        },
        "cost_monitor": {
            "enabled": True,
            "spike_threshold_percent": 120.0,
            "action": "alert",
        },
        "tag_enforcer": {
            "enabled": True,
            "required_tags": [
                {"Key": "Environment", "Default": "untagged"},
                {"Key": "Owner", "Default": "unknown"},
            ],
        },
    }
}


class TestIdleServerCheck:
    def test_detects_idle_instance(self):
        obs = Observation(
            instances=[
                {"instance_id": "i-1", "state": "running", "cpu_percent": 2.0, "tags": []},
            ],
        )
        ev = ThresholdEvaluator(_CONFIG)
        actions = ev.evaluate(obs)
        assert any(a.tool_name == "idle_server" and a.resource_id == "i-1" for a in actions)

    def test_skips_excluded_instance(self):
        obs = Observation(
            instances=[
                {
                    "instance_id": "i-2",
                    "state": "running",
                    "cpu_percent": 1.0,
                    "tags": [{"Key": "PreserveInstance", "Value": "true"}],
                },
            ],
        )
        ev = ThresholdEvaluator(_CONFIG)
        actions = ev.evaluate(obs)
        assert not any(a.tool_name == "idle_server" and a.resource_id == "i-2" for a in actions)

    def test_ignores_healthy_instance(self):
        obs = Observation(
            instances=[
                {"instance_id": "i-3", "state": "running", "cpu_percent": 50.0, "tags": []},
            ],
        )
        ev = ThresholdEvaluator(_CONFIG)
        actions = ev.evaluate(obs)
        assert not any(a.tool_name == "idle_server" for a in actions)


class TestDiskCleanupCheck:
    def test_detects_orphaned_disk(self):
        obs = Observation(
            disks=[{"volume_id": "vol-1", "state": "available", "unattached_days": 10}],
        )
        ev = ThresholdEvaluator(_CONFIG)
        actions = ev.evaluate(obs)
        assert any(a.tool_name == "disk_cleanup" and a.resource_id == "vol-1" for a in actions)

    def test_ignores_recent_disk(self):
        obs = Observation(
            disks=[{"volume_id": "vol-2", "state": "available", "unattached_days": 3}],
        )
        ev = ThresholdEvaluator(_CONFIG)
        actions = ev.evaluate(obs)
        assert not any(a.tool_name == "disk_cleanup" for a in actions)


class TestCostSpikeCheck:
    def test_detects_spike(self):
        obs = Observation(costs={"baseline_daily": 100, "current_daily": 150})
        ev = ThresholdEvaluator(_CONFIG)
        actions = ev.evaluate(obs)
        assert any(a.tool_name == "cost_monitor" for a in actions)

    def test_no_spike(self):
        obs = Observation(costs={"baseline_daily": 100, "current_daily": 110})
        ev = ThresholdEvaluator(_CONFIG)
        actions = ev.evaluate(obs)
        assert not any(a.tool_name == "cost_monitor" for a in actions)


class TestTagEnforcerCheck:
    def test_detects_missing_tags(self):
        obs = Observation(
            instances=[
                {"instance_id": "i-4", "state": "running", "cpu_percent": 50, "tags": []},
            ],
        )
        ev = ThresholdEvaluator(_CONFIG)
        actions = ev.evaluate(obs)
        tag_actions = [a for a in actions if a.tool_name == "tag_enforcer"]
        assert len(tag_actions) == 1
        assert set(tag_actions[0].parameters["missing_tags"]) == {"Environment", "Owner"}
