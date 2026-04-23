"""Tests for the Certificate Monitor tool."""

import pytest
from cloud_agent.tools.cert_monitor import CertMonitorTool
from cloud_agent.cloud.mock_provider import MockProvider
from cloud_agent.agent.baseagent import Action


@pytest.fixture
def cert_tool():
    """Create a certificate monitor tool with mock provider."""
    config = {
        "tools": {
            "cert_monitor": {
                "warning_threshold_days": [30, 14, 7],
            }
        }
    }
    return CertMonitorTool(MockProvider(), config)


def test_cert_tool_initialization(cert_tool):
    """Test that the cert monitor tool initializes correctly."""
    assert cert_tool.tool_name == "cert_monitor"
    assert cert_tool.provider is not None


def test_cert_check_action(cert_tool):
    """Test checking certificates."""
    action = Action(
        tool_name="cert_monitor",
        resource_id="account",
        action_type="check",
        reason="Certificate expiry check",
    )
    
    result = cert_tool.execute(action)
    
    assert result["tool"] == "cert_monitor"
    assert result["action_type"] == "check"
    assert "certificates_checked" in result
    assert result["certificates_checked"] > 0
    assert "expiring_soon" in result
    assert "expired" in result
    assert "healthy" in result
    assert "summary" in result


def test_cert_detects_expired(cert_tool):
    """Test that expired certificates are detected."""
    action = Action(
        tool_name="cert_monitor",
        resource_id="account",
        action_type="check",
        reason="Certificate check",
    )
    
    result = cert_tool.execute(action)
    
    # Mock data has 1 expired certificate (staging.example.com)
    assert len(result["expired"]) >= 1
    assert any("staging" in cert.get("domain", "") for cert in result["expired"])


def test_cert_detects_expiring_soon(cert_tool):
    """Test that expiring certificates are detected."""
    action = Action(
        tool_name="cert_monitor",
        resource_id="account",
        action_type="check",
        reason="Certificate check",
    )
    
    result = cert_tool.execute(action)
    
    # Mock data has certificates expiring in 5 and 10 days
    assert len(result["expiring_soon"]) >= 1
