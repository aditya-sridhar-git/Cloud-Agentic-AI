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
        min_days = cfg.get("unattached_days", 7)

        if action.action_type == "check":
            orphaned = [
                v for v in self.provider.list_volumes()
                if v.get("state") == "available" and v.get("unattached_days", 0) >= min_days
            ]
            return {
                "tool": self.tool_name,
                "status": "check_completed",
                "orphaned_volumes": orphaned,
                "count": len(orphaned),
                "reclaimable_gb": sum(v.get("size_gb", 0) for v in orphaned),
            }

        # Re-verify against live state: never delete an attached or unknown volume.
        vol = next((v for v in self.provider.list_volumes() if v["volume_id"] == volume_id), None)
        if vol is None or vol.get("state") != "available":
            logger.warning("Refusing to delete %s — not an unattached volume", volume_id)
            return {
                "tool": self.tool_name,
                "volume_id": volume_id,
                "status": "refused",
                "reason": "volume missing or attached",
            }

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
