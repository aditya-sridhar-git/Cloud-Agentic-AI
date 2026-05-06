"""Tests for the Backup Manager tool."""

import pytest
from cloud_agent.tools.backup_manager import BackupManagerTool
from cloud_agent.cloud.mock_provider import MockProvider
from cloud_agent.agent.baseagent import Action


@pytest.fixture
def backup_tool():
    """Create a backup manager tool with mock provider."""
    config = {
        "tools": {
            "backup_manager": {
                "retention_days": 30,
                "target_environments": ["prod", "staging"],
            }
        }
    }
    return BackupManagerTool(MockProvider(), config)


def test_backup_tool_initialization(backup_tool):
    """Test that the backup tool initializes correctly."""
    assert backup_tool.tool_name == "backup_manager"
    assert backup_tool.provider is not None
    assert backup_tool.config["tools"]["backup_manager"]["retention_days"] == 30


def test_backup_create_action(backup_tool):
    """Test creating backups."""
    action = Action(
        tool_name="backup_manager",
        resource_id="account",
        action_type="create",
        reason="Scheduled backup creation",
    )
    
    result = backup_tool.execute(action)
    
    assert result["tool"] == "backup_manager"
    assert result["action_type"] == "create"
    assert "snapshots_created" in result
    assert isinstance(result["snapshots_created"], list)


def test_backup_cleanup_action(backup_tool):
    """Test cleaning up old snapshots."""
    action = Action(
        tool_name="backup_manager",
        resource_id="account",
        action_type="cleanup",
        reason="Cleanup old snapshots",
    )
    
    result = backup_tool.execute(action)
    
    assert result["tool"] == "backup_manager"
    assert result["action_type"] == "cleanup"
    assert "snapshots_deleted" in result
    assert isinstance(result["snapshots_deleted"], list)


def test_backup_full_cycle(backup_tool):
    """Test full backup cycle (create + cleanup)."""
    action = Action(
        tool_name="backup_manager",
        resource_id="account",
        action_type="full_cycle",
        reason="Full backup cycle",
    )
    
    result = backup_tool.execute(action)
    
    assert result["tool"] == "backup_manager"
    assert result["action_type"] == "full_cycle"
    assert "snapshots_created" in result
    assert "snapshots_deleted" in result
