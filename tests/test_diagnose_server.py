"""
Tests for the DiagnoseServerTool.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.diagnose_server import DiagnoseServerTool


def _make_tool(ssm_output: str = "") -> DiagnoseServerTool:
    provider = MagicMock()
    provider.run_ssm_command.return_value = ssm_output
    config = {"tools": {"diagnose_server": {"enabled": True}}}
    return DiagnoseServerTool(provider=provider, config=config)


class TestDiagnoseServer:
    def test_diagnoses_oom(self):
        output = "dmesg: Out of memory: Kill process 1234 (java)"
        tool = _make_tool(output)
        action = Action(tool_name="diagnose_server", resource_id="i-abc123",
                        action_type="diagnose", reason="CPU at 99%")
        result = tool.execute(action)
        assert result["status"] == "diagnosed"
        assert result["diagnosis"]["severity"] == "critical"
        assert "memory" in result["diagnosis"]["root_cause"].lower()

    def test_diagnoses_gc_pause(self):
        output = "java[4821]: GC pause (young) 8.2s"
        tool = _make_tool(output)
        action = Action(tool_name="diagnose_server", resource_id="i-abc123",
                        action_type="diagnose", reason="CPU at 95%")
        result = tool.execute(action)
        assert result["diagnosis"]["severity"] == "critical"
        assert "gc" in result["diagnosis"]["root_cause"].lower() or "java" in result["diagnosis"]["root_cause"].lower()

    def test_diagnoses_idle_instance(self):
        output = "No application processes running. Only system daemons active."
        tool = _make_tool(output)
        action = Action(tool_name="diagnose_server", resource_id="i-abc123",
                        action_type="diagnose", reason="CPU at 1%")
        result = tool.execute(action)
        assert result["diagnosis"]["severity"] == "info"
        assert result["diagnosis"]["safe_to_auto_remediate"] is True

    def test_diagnoses_disk_full(self):
        output = "/dev/xvda1     100G   95G   5G  95% /"
        tool = _make_tool(output)
        action = Action(tool_name="diagnose_server", resource_id="i-abc123",
                        action_type="diagnose", reason="Disk pressure")
        result = tool.execute(action)
        assert result["diagnosis"]["severity"] == "warning"
        assert "disk" in result["diagnosis"]["root_cause"].lower()

    def test_ssm_failure_handled(self):
        provider = MagicMock()
        provider.run_ssm_command.side_effect = Exception("SSM agent not installed")
        config = {"tools": {"diagnose_server": {"enabled": True}}}
        tool = DiagnoseServerTool(provider=provider, config=config)
        action = Action(tool_name="diagnose_server", resource_id="i-abc123",
                        action_type="diagnose", reason="Test")
        result = tool.execute(action)
        # Should still return a result, not crash
        assert result["status"] == "diagnosed"
        assert "SSM UNAVAILABLE" in result["diagnostic_output"]
