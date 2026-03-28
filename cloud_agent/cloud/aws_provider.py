"""
AWS CloudProvider implementation using boto3.

Wraps EC2, CloudWatch, EBS, Cost Explorer, and Resource Groups Tagging API.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3

from cloud_agent.cloud.provider import CloudProvider
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


class AWSProvider(CloudProvider):
    """Concrete AWS implementation of :class:`CloudProvider`."""

    def __init__(self, region: str | None = None) -> None:
        self._region = region or os.getenv("AWS_REGION", "us-east-1")
        self._ec2 = boto3.client("ec2", region_name=self._region)
        self._cloudwatch = boto3.client("cloudwatch", region_name=self._region)
        self._ce = boto3.client("ce", region_name=self._region)
        logger.info("[green]AWS provider initialised[/green] (region=%s)", self._region)

    # ------------------------------------------------------------------
    # Instances
    # ------------------------------------------------------------------

    def list_instances(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        aws_filters = []
        if filters:
            for key, val in filters.items():
                aws_filters.append({"Name": key, "Values": val if isinstance(val, list) else [val]})

        paginator = self._ec2.get_paginator("describe_instances")
        instances: list[dict[str, Any]] = []
        for page in paginator.paginate(Filters=aws_filters or []):
            for res in page["Reservations"]:
                for inst in res["Instances"]:
                    instances.append(
                        {
                            "instance_id": inst["InstanceId"],
                            "instance_type": inst["InstanceType"],
                            "state": inst["State"]["Name"],
                            "launch_time": str(inst.get("LaunchTime", "")),
                            "tags": [
                                {"Key": t["Key"], "Value": t["Value"]}
                                for t in inst.get("Tags", [])
                            ],
                        }
                    )
        return instances

    def stop_instance(self, instance_id: str) -> dict[str, Any]:
        logger.info("Stopping instance %s", instance_id)
        resp = self._ec2.stop_instances(InstanceIds=[instance_id])
        return {"instance_id": instance_id, "status": "stopping", "raw": resp}

    def terminate_instance(self, instance_id: str) -> dict[str, Any]:
        logger.info("Terminating instance %s", instance_id)
        resp = self._ec2.terminate_instances(InstanceIds=[instance_id])
        return {"instance_id": instance_id, "status": "terminating", "raw": resp}

    def resize_instance(self, instance_id: str, new_type: str) -> dict[str, Any]:
        logger.info("Resizing %s → %s (stop → modify → start)", instance_id, new_type)
        self._ec2.stop_instances(InstanceIds=[instance_id])
        waiter = self._ec2.get_waiter("instance_stopped")
        waiter.wait(InstanceIds=[instance_id])
        self._ec2.modify_instance_attribute(InstanceId=instance_id, InstanceType={"Value": new_type})
        self._ec2.start_instances(InstanceIds=[instance_id])
        return {"instance_id": instance_id, "new_type": new_type, "status": "resized"}

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _get_avg_cpu(self, instance_id: str, period_seconds: int, start: datetime, end: datetime) -> float:
        resp = self._cloudwatch.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start,
            EndTime=end,
            Period=period_seconds,
            Statistics=["Average"],
        )
        datapoints = resp.get("Datapoints", [])
        if not datapoints:
            return 0.0
        return sum(dp["Average"] for dp in datapoints) / len(datapoints)

    def get_cpu_utilization(self, instance_id: str, minutes: int = 30) -> float:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes)
        return self._get_avg_cpu(instance_id, 300, start, end)

    def get_cpu_utilization_days(self, instance_id: str, days: int = 7) -> float:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        return self._get_avg_cpu(instance_id, 86400, start, end)

    # ------------------------------------------------------------------
    # Volumes
    # ------------------------------------------------------------------

    def list_volumes(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        aws_filters = []
        if filters:
            for key, val in filters.items():
                aws_filters.append({"Name": key, "Values": val if isinstance(val, list) else [val]})

        resp = self._ec2.describe_volumes(Filters=aws_filters or [])
        volumes: list[dict[str, Any]] = []
        for vol in resp.get("Volumes", []):
            # Calculate days unattached
            unattached_days = 0
            if vol["State"] == "available":
                create_time = vol["CreateTime"]
                if isinstance(create_time, str):
                    create_time = datetime.fromisoformat(create_time)
                unattached_days = (datetime.now(timezone.utc) - create_time).days

            volumes.append(
                {
                    "volume_id": vol["VolumeId"],
                    "state": vol["State"],
                    "size_gb": vol["Size"],
                    "create_time": str(vol["CreateTime"]),
                    "unattached_days": unattached_days,
                }
            )
        return volumes

    def snapshot_volume(self, volume_id: str) -> dict[str, Any]:
        logger.info("Creating snapshot of %s", volume_id)
        resp = self._ec2.create_snapshot(
            VolumeId=volume_id,
            Description=f"Auto-snapshot by Cloud Agent before cleanup — {volume_id}",
        )
        return {"volume_id": volume_id, "snapshot_id": resp["SnapshotId"]}

    def delete_volume(self, volume_id: str) -> dict[str, Any]:
        logger.info("Deleting volume %s", volume_id)
        self._ec2.delete_volume(VolumeId=volume_id)
        return {"volume_id": volume_id, "status": "deleted"}

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def get_tags(self, resource_id: str) -> list[dict[str, str]]:
        resp = self._ec2.describe_tags(
            Filters=[{"Name": "resource-id", "Values": [resource_id]}]
        )
        return [{"Key": t["Key"], "Value": t["Value"]} for t in resp.get("Tags", [])]

    def set_tags(self, resource_id: str, tags: list[dict[str, str]]) -> dict[str, Any]:
        logger.info("Tagging %s with %d tag(s)", resource_id, len(tags))
        self._ec2.create_tags(
            Resources=[resource_id],
            Tags=[{"Key": t["Key"], "Value": t["Value"]} for t in tags],
        )
        return {"resource_id": resource_id, "tags_applied": len(tags)}

    # ------------------------------------------------------------------
    # Cost
    # ------------------------------------------------------------------

    def get_daily_cost(self, days: int = 1) -> float:
        end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        resp = self._ce.get_cost_and_usage(
            TimePeriod={"Start": start, "End": end},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
        )
        total = sum(
            float(r["Total"]["UnblendedCost"]["Amount"])
            for r in resp.get("ResultsByTime", [])
        )
        return total / max(days, 1)

    def get_cost_baseline(self, days: int = 7) -> float:
        return self.get_daily_cost(days=days)
