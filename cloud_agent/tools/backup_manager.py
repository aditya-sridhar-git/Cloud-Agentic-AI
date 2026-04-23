"""
Backup Manager Tool — Automated backup creation and lifecycle management.

Creates snapshots of EBS volumes attached to production instances,
manages retention policies, and deletes expired snapshots.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


@register_tool("backup_manager")
class BackupManagerTool(BaseTool):
    """Manages automated backups for EBS volumes with retention policies."""

    def execute(self, action: Action) -> dict[str, Any]:
        action_type = action.action_type  # "create", "cleanup", or "full_cycle"

        logger.info(
            "[bold blue]💾 BACKUP MANAGER[/bold blue] — running %s",
            action_type,
        )

        results = {
            "tool": self.tool_name,
            "action_type": action_type,
            "snapshots_created": [],
            "snapshots_deleted": [],
            "errors": [],
        }

        if action_type in ("create", "full_cycle"):
            created = self._create_backshots(action)
            results["snapshots_created"] = created

        if action_type in ("cleanup", "full_cycle"):
            deleted = self._cleanup_old_snapshots()
            results["snapshots_deleted"] = deleted

        logger.info(
            "[bold blue]💾 BACKUP COMPLETE[/bold blue] — created %d, deleted %d snapshots",
            len(results["snapshots_created"]),
            len(results["snapshots_deleted"]),
        )

        return results

    def _create_backshots(self, action: Action) -> list[dict[str, Any]]:
        """Create snapshots of volumes based on configuration."""
        created = []
        cfg = self.config.get("tools", {}).get("backup_manager", {})
        
        # Get target resources
        target_envs = set(cfg.get("target_environments", ["prod"]))
        skip_tags = cfg.get("skip_if_tagged_with", [{"Key": "Backup", "Value": "false"}])
        
        try:
            instances = self.provider.list_instances()
        except Exception as exc:
            logger.error("Failed to list instances: %s", exc)
            return created

        for inst in instances:
            # Filter by environment tag
            tags = {t["Key"].lower(): t["Value"].lower() for t in inst.get("tags", [])}
            env = tags.get("environment", "")
            
            if env not in [e.lower() for e in target_envs]:
                continue
            
            # Check skip tags
            should_skip = False
            for skip_tag in skip_tags:
                key = skip_tag.get("Key", "").lower()
                value = skip_tag.get("Value", "").lower()
                if tags.get(key) == value:
                    should_skip = True
                    break
            
            if should_skip:
                logger.info("Skipping backup for %s (explicitly excluded)", inst["instance_id"])
                continue

            # Get volumes attached to this instance
            try:
                volumes = self.provider.list_volumes(filters={"attachment.instance-id": inst["instance_id"]})
            except Exception as exc:
                logger.warning("Could not list volumes for %s: %s", inst["instance_id"], exc)
                continue

            for vol in volumes:
                snapshot_result = self._create_snapshot(vol, inst, cfg)
                if snapshot_result:
                    created.append(snapshot_result)

        return created

    def _create_snapshot(self, volume: dict[str, Any], instance: dict[str, Any], 
                         cfg: dict[str, Any]) -> dict[str, Any] | None:
        """Create a snapshot of a single volume."""
        volume_id = volume["volume_id"]
        instance_id = instance["instance_id"]
        instance_name = next(
            (t["Value"] for t in instance.get("tags", []) if t["Key"] == "Name"),
            instance_id
        )

        # Generate snapshot name
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        snapshot_name = f"backup-{instance_name}-{volume_id}-{timestamp}"

        # Add tags
        tags = [
            {"Key": "Name", "Value": snapshot_name},
            {"Key": "BackupType", "Value": "automated"},
            {"Key": "SourceVolume", "Value": volume_id},
            {"Key": "SourceInstance", "Value": instance_id},
            {"Key": "Environment", "Value": next(
                (t["Value"] for t in instance.get("tags", []) if t["Key"] == "Environment"),
                "unknown"
            )},
            {"Key": "RetentionDays", "Value": str(cfg.get("retention_days", 30))},
            {"Key": "CreatedBy", "Value": "cloud-agent-backup-manager"},
        ]

        try:
            logger.info(
                "[cyan]📸 Creating snapshot[/cyan] of volume %s (instance: %s)",
                volume_id, instance_name,
            )
            
            result = self.provider.snapshot_volume(volume_id, tags=tags)
            
            logger.info(
                "[green]✓ Snapshot created[/green]: %s → %s",
                volume_id, result.get("snapshot_id", "unknown"),
            )

            return {
                "volume_id": volume_id,
                "instance_id": instance_id,
                "instance_name": instance_name,
                "snapshot_id": result.get("snapshot_id"),
                "snapshot_name": snapshot_name,
                "size_gb": volume.get("size_gb", 0),
                "timestamp": timestamp,
                "status": "success",
            }

        except Exception as exc:
            logger.error("Failed to create snapshot of %s: %s", volume_id, exc)
            return {
                "volume_id": volume_id,
                "instance_id": instance_id,
                "status": "error",
                "error": str(exc),
            }

    def _cleanup_old_snapshots(self) -> list[dict[str, Any]]:
        """Delete snapshots older than retention period."""
        deleted = []
        cfg = self.config.get("tools", {}).get("backup_manager", {})
        retention_days = cfg.get("retention_days", 30)
        cutoff_date = datetime.now().astimezone() - timedelta(days=retention_days)

        try:
            snapshots = self.provider.list_snapshots(creator="cloud-agent-backup-manager")
        except Exception as exc:
            logger.error("Failed to list snapshots: %s", exc)
            return deleted

        for snap in snapshots:
            # Parse creation time
            try:
                created_time = snap.get("start_time")
                if isinstance(created_time, str):
                    created_time = datetime.fromisoformat(created_time.replace("Z", "+00:00"))
                elif isinstance(created_time, datetime):
                    # Ensure timezone-aware for comparison
                    if created_time.tzinfo is None:
                        created_time = created_time.astimezone()
                else:
                    continue
            except Exception:
                logger.warning("Could not parse snapshot time for %s", snap.get("snapshot_id"))
                continue

            # Check if expired
            if created_time < cutoff_date:
                snapshot_id = snap["snapshot_id"]
                try:
                    logger.info(
                        "[yellow]🗑️  Deleting expired snapshot[/yellow] %s (created: %s)",
                        snapshot_id, created_time.strftime("%Y-%m-%d"),
                    )
                    
                    self.provider.delete_snapshot(snapshot_id)
                    
                    deleted.append({
                        "snapshot_id": snapshot_id,
                        "created_time": created_time.isoformat(),
                        "age_days": (datetime.now() - created_time).days,
                        "status": "deleted",
                    })
                    
                    logger.info(
                        "[green]✓ Deleted[/green] snapshot %s (was %d days old)",
                        snapshot_id, (datetime.now() - created_time).days,
                    )

                except Exception as exc:
                    logger.error("Failed to delete snapshot %s: %s", snapshot_id, exc)
                    deleted.append({
                        "snapshot_id": snapshot_id,
                        "status": "error",
                        "error": str(exc),
                    })

        return deleted
