"""
Disk Cleanup Tool — snapshot and delete orphaned EBS volumes.
"""

from __future__ import annotations

from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


@register_tool("disk_cleanup")
class DiskCleanupTool(BaseTool):
    """Snapshots and deletes EBS volumes that have been unattached too long."""

    def execute(self, action: Action) -> dict[str, Any]:
        volume_id = action.resource_id
        cfg = self.config.get("tools", {}).get("disk_cleanup", {})
        do_snapshot = cfg.get("snapshot_before_delete", True)

        snapshot_id = None
        if do_snapshot:
            logger.info(
                "[blue]📸 SNAPSHOT[/blue] volume [cyan]%s[/cyan] before deletion",
                volume_id,
            )
            snap_result = self.provider.snapshot_volume(volume_id)
            snapshot_id = snap_result.get("snapshot_id")

        logger.info(
            "[bold red]🗑️  DELETE[/bold red] volume [cyan]%s[/cyan] — %s",
            volume_id,
            action.reason,
        )
        delete_result = self.provider.delete_volume(volume_id)

        return {
            "tool": self.tool_name,
            "volume_id": volume_id,
            "snapshot_id": snapshot_id,
            "status": "deleted",
        }
