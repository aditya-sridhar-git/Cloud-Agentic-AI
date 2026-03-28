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
