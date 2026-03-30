"""
Integration test — full agent cycle with MockProvider.
"""

from __future__ import annotations

from cloud_agent.cloud.mock_provider import MockProvider
from cloud_agent.utils.config import load_config


class TestFullCycle:
    def test_mock_agent_dry_run(self):
        """Run a full observe→think→act cycle in dry-run mode with mock data."""
        from cloud_agent.main import CloudOpsAgent

        config = load_config()
        config["agent"]["dry_run"] = True
        provider = MockProvider()
        agent = CloudOpsAgent(config, provider=provider)

        results = agent.run_once()

        # Should produce some actions (idle servers, orphaned disks, missing tags, etc.)
        assert len(results) > 0
        # All should be dry_run
        assert all(r.get("status") == "dry_run" for r in results)

    def test_mock_agent_finds_idle_instances(self):
        """Verify the agent detects the 2 idle instances in mock data."""
        from cloud_agent.main import CloudOpsAgent

        config = load_config()
        config["agent"]["dry_run"] = True
        provider = MockProvider()
        agent = CloudOpsAgent(config, provider=provider)

        results = agent.run_once()

        idle_actions = [r for r in results if r.get("tool") == "idle_server"]
        # Mock data has 2 instances with CPU < 5%: dev-webserver and test-runner
        assert len(idle_actions) >= 2

    def test_mock_agent_finds_missing_tags(self):
        """Verify the agent detects missing tags on dev instances."""
        from cloud_agent.main import CloudOpsAgent

        config = load_config()
        config["agent"]["dry_run"] = True
        provider = MockProvider()
        agent = CloudOpsAgent(config, provider=provider)

        results = agent.run_once()

        tag_actions = [r for r in results if r.get("tool") == "tag_enforcer"]
        # Dev instances are missing Environment, Owner, Project tags
        assert len(tag_actions) >= 1

    def test_mock_agent_finds_orphaned_disks(self):
        """Verify the agent detects orphaned volumes."""
        from cloud_agent.main import CloudOpsAgent

        config = load_config()
        config["agent"]["dry_run"] = True
        provider = MockProvider()
        agent = CloudOpsAgent(config, provider=provider)

        results = agent.run_once()

        disk_actions = [r for r in results if r.get("tool") == "disk_cleanup"]
        # 2 volumes are unattached > 7 days
        assert len(disk_actions) >= 1
