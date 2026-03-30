"""
Tests for the MockProvider — verifies demo data and basic operations.
"""

from __future__ import annotations

from cloud_agent.cloud.mock_provider import MockProvider


class TestMockProvider:
    def test_list_instances(self):
        provider = MockProvider()
        instances = provider.list_instances()
        assert len(instances) >= 7
        assert all("instance_id" in i for i in instances)
        assert all("state" in i for i in instances)

    def test_stop_instance(self):
        provider = MockProvider()
        result = provider.stop_instance("i-0a1b2c3d4e5f60003")
        assert result["status"] == "stopping"
        # Instance should now appear as stopped
        instances = provider.list_instances()
        inst = next(i for i in instances if i["instance_id"] == "i-0a1b2c3d4e5f60003")
        assert inst["state"] == "stopped"

    def test_terminate_instance(self):
        provider = MockProvider()
        result = provider.terminate_instance("i-0a1b2c3d4e5f60003")
        assert result["status"] == "terminating"
        # Instance should no longer appear
        instances = provider.list_instances()
        ids = [i["instance_id"] for i in instances]
        assert "i-0a1b2c3d4e5f60003" not in ids

    def test_start_instance(self):
        provider = MockProvider()
        # Stop then start
        provider.stop_instance("i-0a1b2c3d4e5f60003")
        result = provider.start_instance("i-0a1b2c3d4e5f60003")
        assert result["status"] == "starting"
        instances = provider.list_instances()
        inst = next(i for i in instances if i["instance_id"] == "i-0a1b2c3d4e5f60003")
        assert inst["state"] == "running"

    def test_cpu_utilization(self):
        provider = MockProvider()
        cpu = provider.get_cpu_utilization("i-0a1b2c3d4e5f60003", minutes=30)
        assert 0 <= cpu <= 10  # Should be ~2% (idle instance)

    def test_list_volumes(self):
        provider = MockProvider()
        volumes = provider.list_volumes()
        assert len(volumes) >= 4
        orphaned = [v for v in volumes if v["state"] == "available"]
        assert len(orphaned) >= 2

    def test_snapshot_and_delete_volume(self):
        provider = MockProvider()
        snap = provider.snapshot_volume("vol-0a1b2c3d4e5f0002")
        assert "snapshot_id" in snap
        provider.delete_volume("vol-0a1b2c3d4e5f0002")
        volumes = provider.list_volumes()
        ids = [v["volume_id"] for v in volumes]
        assert "vol-0a1b2c3d4e5f0002" not in ids

    def test_set_tags(self):
        provider = MockProvider()
        result = provider.set_tags("i-0a1b2c3d4e5f60001", [
            {"Key": "CostCenter", "Value": "Engineering"},
        ])
        assert result["tags_applied"] == 1
        tags = provider.get_tags("i-0a1b2c3d4e5f60001")
        assert any(t["Key"] == "CostCenter" for t in tags)

    def test_cost(self):
        provider = MockProvider()
        daily = provider.get_daily_cost(days=1)
        assert daily > 0
        baseline = provider.get_cost_baseline(days=7)
        assert baseline > 0

    def test_ssm_command_idle(self):
        provider = MockProvider()
        output = provider.run_ssm_command("i-0a1b2c3d4e5f60003", ["top -bn1"])
        assert "No application processes" in output

    def test_ssm_command_high_cpu(self):
        provider = MockProvider()
        output = provider.run_ssm_command("i-0a1b2c3d4e5f60005", ["top -bn1"])
        assert "java" in output.lower() or "GC" in output

    def test_security_groups(self):
        provider = MockProvider()
        sgs = provider.describe_security_groups()
        assert len(sgs) >= 3
        open_ssh = [
            sg for sg in sgs
            for rule in sg["ingress_rules"]
            if rule["from_port"] == 22 and "0.0.0.0/0" in rule["cidr_blocks"]
        ]
        assert len(open_ssh) >= 1

    def test_s3_public(self):
        provider = MockProvider()
        buckets = provider.list_s3_buckets_public_access()
        public = [b for b in buckets if b["is_public"]]
        assert len(public) >= 1

    def test_ebs_encryption(self):
        provider = MockProvider()
        unencrypted = provider.check_ebs_encryption()
        assert len(unencrypted) >= 1
        assert all(v["encrypted"] is False for v in unencrypted)

    def test_cloudtrail_events(self):
        provider = MockProvider()
        events = provider.get_cloudtrail_events(hours=24)
        assert len(events) >= 3
        assert all("event_name" in e for e in events)
