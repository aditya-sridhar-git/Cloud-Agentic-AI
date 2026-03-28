"""
Tests for the IdleServerTool.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.idle_server import IdleServerTool


def _make_tool() -> IdleServerTool:
    provider = MagicMock()
    provider.stop_instance.return_value = {"instance_id": "i-abc123", "status": "stopping"}
    provider.terminate_instance.return_value = {"instance_id": "i-abc123", "status": "terminating"}
    config = {"tools": {"idle_server": {"enabled": True, "cpu_threshold_percent": 5.0}}}
    return IdleServerTool(provider=provider, config=config)


class TestIdleServerTool:
    def test_stop_action(self):
        tool = _make_tool()
        action = Action(
            tool_name="idle_server",
            resource_id="i-abc123",
            action_type="stop",
            reason="CPU at 2%",
        )
        result = tool.execute(action)
        assert result["status"] == "stopping"
        assert result["tool"] == "idle_server"
        tool.provider.stop_instance.assert_called_once_with("i-abc123")

    def test_terminate_action(self):
        tool = _make_tool()
        action = Action(
            tool_name="idle_server",
            resource_id="i-abc123",
            action_type="terminate",
            reason="CPU at 0%",
        )
        result = tool.execute(action)
        assert result["status"] == "terminating"
        tool.provider.terminate_instance.assert_called_once_with("i-abc123")
