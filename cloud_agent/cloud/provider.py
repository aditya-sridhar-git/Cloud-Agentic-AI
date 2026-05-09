"""
Abstract cloud provider interface.

Each cloud vendor (AWS, GCP, Azure) implements this contract so the
rest of the agent code stays cloud-agnostic.
"""

from __future__ import annotations

import abc
from typing import Any


class CloudProvider(abc.ABC):
    """Vendor-neutral interface to cloud infrastructure."""

    # ------------------------------------------------------------------
    # Instances / Compute
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def list_instances(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return a list of compute instances with basic metadata."""

    @abc.abstractmethod
    def stop_instance(self, instance_id: str) -> dict[str, Any]:
        """Stop (but don't terminate) an instance."""

    @abc.abstractmethod
    def terminate_instance(self, instance_id: str) -> dict[str, Any]:
        """Terminate an instance permanently."""

    @abc.abstractmethod
    def resize_instance(self, instance_id: str, new_type: str) -> dict[str, Any]:
        """Change the instance type (requires stop → resize → start)."""

    @abc.abstractmethod
    def start_instance(self, instance_id: str) -> dict[str, Any]:
        """Start a stopped instance."""

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def get_cpu_utilization(self, instance_id: str, minutes: int = 30) -> float:
        """Return average CPU utilisation over the last *minutes*."""

    @abc.abstractmethod
    def get_cpu_utilization_days(self, instance_id: str, days: int = 7) -> float:
        """Return average CPU utilisation over the last *days*."""

    # ------------------------------------------------------------------
    # Disks / Volumes
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def list_volumes(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return a list of block-storage volumes."""

    @abc.abstractmethod
    def snapshot_volume(self, volume_id: str) -> dict[str, Any]:
        """Create a snapshot of a volume."""

    @abc.abstractmethod
    def delete_volume(self, volume_id: str) -> dict[str, Any]:
        """Delete a volume."""

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def get_tags(self, resource_id: str) -> list[dict[str, str]]:
        """Return tags for a resource as [{Key, Value}, …]."""

    @abc.abstractmethod
    def set_tags(self, resource_id: str, tags: list[dict[str, str]]) -> dict[str, Any]:
        """Apply tags to a resource."""

    # ------------------------------------------------------------------
    # Cost
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def get_daily_cost(self, days: int = 1) -> float:
        """Return total account cost for the last N days."""

    @abc.abstractmethod
    def get_cost_baseline(self, days: int = 7) -> float:
        """Return average daily cost over the last N days (baseline)."""

    @abc.abstractmethod
    def get_cost_by_service(self, days: int = 1) -> list[dict]:
        """Return cost breakdown by service for the last N days.

        Each item: {"service": str, "amount": float, "currency": str}
        """

    # ------------------------------------------------------------------
    # SSM / Diagnosis
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def run_ssm_command(self, instance_id: str, commands: list[str], timeout: int = 30) -> str:
        """Execute shell commands on an instance and return output."""

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def describe_security_groups(self) -> list[dict[str, Any]]:
        """Return all security groups with ingress rules."""

    @abc.abstractmethod
    def list_s3_buckets_public_access(self) -> list[dict[str, Any]]:
        """Check all S3 buckets for public access status."""

    @abc.abstractmethod
    def check_ebs_encryption(self) -> list[dict[str, Any]]:
        """Return unencrypted EBS volumes."""

    # ------------------------------------------------------------------
    # CloudTrail
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def get_cloudtrail_events(self, hours: int = 24, event_name: str | None = None) -> list[dict[str, Any]]:
        """Query recent CloudTrail events."""

    # ------------------------------------------------------------------
    # Snapshots / Backups
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def list_snapshots(self, creator: str | None = None) -> list[dict[str, Any]]:
        """Return list of EBS snapshots, optionally filtered by creator."""

    @abc.abstractmethod
    def delete_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        """Delete an EBS snapshot."""

    # ------------------------------------------------------------------
    # Certificates
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def list_certificates(self) -> list[dict[str, Any]]:
        """Return list of SSL/TLS certificates (ACM and imported)."""

    # ------------------------------------------------------------------
    # RDS / Query Optimization
    # ------------------------------------------------------------------

    def list_rds_instances(self) -> list[dict[str, Any]]:
        """Return RDS instances available for query optimization."""
        return []

    def get_rds_metrics(self, db_instance_id: str, metric_name: str) -> float:
        """Return a recent RDS CloudWatch metric value."""
        return 0.0

    def get_slow_queries(self, db_instance_id: str, engine: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return slow SQL evidence when available."""
        return []
