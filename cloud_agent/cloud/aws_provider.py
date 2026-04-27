"""
AWS CloudProvider implementation using boto3.

Wraps EC2, CloudWatch, EBS, Cost Explorer, SSM, S3, CloudTrail,
and Resource Groups Tagging API.
"""

from __future__ import annotations

import json
import os
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.config import Config as BotoConfig

from cloud_agent.cloud.provider import CloudProvider
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

_RETRY_CONFIG = BotoConfig(
    retries={"max_attempts": 3, "mode": "adaptive"},
)


class AWSProvider(CloudProvider):
    """Concrete AWS implementation of :class:`CloudProvider`."""

    def __init__(self, region: str | None = None) -> None:
        self._region = region or os.getenv("AWS_REGION", "us-east-1")
        self._ec2 = boto3.client("ec2", region_name=self._region, config=_RETRY_CONFIG)
        self._cloudwatch = boto3.client("cloudwatch", region_name=self._region, config=_RETRY_CONFIG)
        self._ce = boto3.client("ce", region_name=self._region, config=_RETRY_CONFIG)
        self._ssm = boto3.client("ssm", region_name=self._region, config=_RETRY_CONFIG)
        self._s3 = boto3.client("s3", region_name=self._region, config=_RETRY_CONFIG)
        self._cloudtrail = boto3.client("cloudtrail", region_name=self._region, config=_RETRY_CONFIG)
        self._active_regions = self._get_active_regions()
        logger.info("[green]AWS provider initialised[/green] (region=%s, multi-region: %d active)", self._region, len(self._active_regions))

    def _get_active_regions(self) -> list[str]:
        """Fetch list of enabled regions for the account."""
        try:
            resp = self._ec2.describe_regions()
            return [r["RegionName"] for r in resp.get("Regions", [])]
        except Exception:
            return [self._region]

    # ------------------------------------------------------------------
    # Instances / Compute (MULTI-REGION)
    # ------------------------------------------------------------------

    def list_instances(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return a list of instances across ALL active regions."""
        all_instances = []
        for region in self._active_regions:
            try:
                ec2 = boto3.client("ec2", region_name=region, config=_RETRY_CONFIG)
                resp = ec2.describe_instances()
                for reservation in resp.get("Reservations", []):
                    for inst in reservation.get("Instances", []):
                        all_instances.append({
                            "instance_id": inst["InstanceId"],
                            "instance_type": inst["InstanceType"],
                            "state": inst["State"]["Name"],
                            "launch_time": str(inst["LaunchTime"]),
                            "region": region,
                            "tags": inst.get("Tags", []),
                        })
            except Exception as e:
                logger.warning("Could not list instances in %s: %s", region, e)
        return all_instances

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

    def _get_avg_cpu(self, instance_id: str, region: str, period_seconds: int, start: datetime, end: datetime) -> float:
        cw = boto3.client("cloudwatch", region_name=region, config=_RETRY_CONFIG)
        resp = cw.get_metric_statistics(
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

    def get_cpu_utilization(self, instance_id: str, region: str | None = None, minutes: int = 30) -> float:
        region = region or self._region
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes)
        # Use 60s period for short windows, 300s for standard
        period = 60 if minutes <= 5 else 300
        return self._get_avg_cpu(instance_id, region, period, start, end)

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

    # ------------------------------------------------------------------
    # SSM (Systems Manager) — for diagnosis
    # ------------------------------------------------------------------

    def run_ssm_command(self, instance_id: str, commands: list[str], timeout: int = 30) -> str:
        """Execute shell commands on an instance via SSM and return stdout."""
        logger.info("Running SSM command on %s", instance_id)
        resp = self._ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": commands},
            TimeoutSeconds=timeout,
        )
        command_id = resp["Command"]["CommandId"]

        # Poll for completion
        for _ in range(timeout):
            _time.sleep(1)
            result = self._ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            )
            if result["Status"] in ("Success", "Failed", "TimedOut", "Cancelled"):
                break

        stdout = result.get("StandardOutputContent", "")
        stderr = result.get("StandardErrorContent", "")
        return f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}" if stderr else stdout

    def start_instance(self, instance_id: str) -> dict[str, Any]:
        """Start a stopped instance."""
        logger.info("Starting instance %s", instance_id)
        resp = self._ec2.start_instances(InstanceIds=[instance_id])
        return {"instance_id": instance_id, "status": "starting", "raw": resp}

    # ------------------------------------------------------------------
    # Security — SGs, S3, IAM
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Self-Healing Primitives (SSM)
    # ------------------------------------------------------------------

    def restart_service(self, instance_id: str, service_name: str) -> str:
        """Restart a systemd service via SSM."""
        commands = [f"sudo systemctl restart {service_name}"]
        return self.run_ssm_command(instance_id, commands)

    def cleanup_logs(self, instance_id: str, days: int = 7) -> str:
        """Clean up log files older than X days."""
        commands = [
            f"sudo find /var/log -type f -name '*.gz' -mtime +{days} -delete",
            "sudo journalctl --vacuum-time=3d"
        ]
        return self.run_ssm_command(instance_id, commands)

    def expand_ebs_volume(self, volume_id: str, new_size_gb: int) -> dict[str, Any]:
        """Modify EBS volume size."""
        resp = self._ec2.modify_volume(VolumeId=volume_id, Size=new_size_gb)
        return {"volume_id": volume_id, "status": "modifying", "new_size": new_size_gb}

    # ------------------------------------------------------------------
    # Multi-Region Security
    # ------------------------------------------------------------------

    def describe_security_groups(self) -> list[dict[str, Any]]:
        """Return ALL security groups across ALL active regions."""
        all_sgs = []
        for region in self._active_regions:
            try:
                ec2 = boto3.client("ec2", region_name=region, config=_RETRY_CONFIG)
                resp = ec2.describe_security_groups()
                for sg in resp.get("SecurityGroups", []):
                    all_sgs.append({
                        "group_id": sg["GroupId"],
                        "group_name": sg["GroupName"],
                        "region": region,
                        "ingress_rules": [
                            {
                                "protocol": rule.get("IpProtocol", "all"),
                                "from_port": rule.get("FromPort", 0),
                                "to_port": rule.get("ToPort", 65535),
                                "cidr_blocks": [ip["CidrIp"] for ip in rule.get("IpRanges", [])],
                            }
                            for rule in sg.get("IpPermissions", [])
                        ],
                    })
            except Exception:
                continue
        return all_sgs

    # ------------------------------------------------------------------
    # Cost Forecasting
    # ------------------------------------------------------------------

    def get_cost_forecast(self, days_out: int = 30) -> dict[str, Any]:
        """Predict cost for the next 30 days based on recent linear trend."""
        try:
            # Get costs for past 7 days
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=7)
            resp = self._ce.get_cost_and_usage(
                TimePeriod={"Start": start.strftime("%Y-%m-%d"), "End": end.strftime("%Y-%m-%d")},
                Granularity="DAILY",
                Metrics=["UnblendedCost"]
            )
            daily_costs = [float(d["Total"]["UnblendedCost"]["Amount"]) for d in resp.get("ResultsByTime", [])]
            if not daily_costs:
                return {"forecast_30d": 0, "confidence": "low"}
            
            avg_daily = sum(daily_costs) / len(daily_costs)
            projected = avg_daily * days_out
            return {
                "forecast_30d": round(projected, 2),
                "avg_daily": round(avg_daily, 2),
                "confidence": "medium" if len(daily_costs) > 3 else "low"
            }
        except Exception:
            return {"forecast_30d": 0, "confidence": "error"}


    def list_s3_buckets_public_access(self) -> list[dict[str, Any]]:
        """Check all S3 buckets for public access."""
        buckets = self._s3.list_buckets().get("Buckets", [])
        results = []
        for b in buckets:
            name = b["Name"]
            try:
                pab = self._s3.get_public_access_block(Bucket=name)
                config = pab.get("PublicAccessBlockConfiguration", {})
                is_public = not all([
                    config.get("BlockPublicAcls", False),
                    config.get("IgnorePublicAcls", False),
                    config.get("BlockPublicPolicy", False),
                    config.get("RestrictPublicBuckets", False),
                ])
            except Exception:
                is_public = True  # No block = potentially public
            results.append({"bucket_name": name, "is_public": is_public})
        return results

    def check_ebs_encryption(self) -> list[dict[str, Any]]:
        """Return unencrypted EBS volumes."""
        resp = self._ec2.describe_volumes()
        unencrypted = []
        for vol in resp.get("Volumes", []):
            if not vol.get("Encrypted", False):
                unencrypted.append({
                    "volume_id": vol["VolumeId"],
                    "size_gb": vol["Size"],
                    "state": vol["State"],
                    "encrypted": False,
                })
        return unencrypted

    # ------------------------------------------------------------------
    # CloudTrail — for cross-domain correlation
    # ------------------------------------------------------------------

    def get_cloudtrail_events(self, hours: int = 24, event_name: str | None = None) -> list[dict[str, Any]]:
        """Query recent CloudTrail events."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)
        kwargs: dict[str, Any] = {
            "StartTime": start,
            "EndTime": end,
            "MaxResults": 50,
        }
        if event_name:
            kwargs["LookupAttributes"] = [
                {"AttributeKey": "EventName", "AttributeValue": event_name}
            ]
        resp = self._cloudtrail.lookup_events(**kwargs)
        events = []
        for e in resp.get("Events", []):
            events.append({
                "event_name": e.get("EventName", ""),
                "event_time": str(e.get("EventTime", "")),
                "username": e.get("Username", ""),
                "source_ip": e.get("CloudTrailEvent", "{}"),
                "resources": [
                    {"type": r.get("ResourceType", ""), "name": r.get("ResourceName", "")}
                    for r in e.get("Resources", [])
                ],
            })
        return events

    # ------------------------------------------------------------------
    # Snapshots / Backups
    # ------------------------------------------------------------------

    def list_snapshots(self, creator: str | None = None) -> list[dict[str, Any]]:
        """Return list of EBS snapshots, optionally filtered by creator."""
        filters = []
        if creator:
            filters.append({"Name": "owner-id", "Values": [creator]})
        else:
            filters.append({"Name": "owner-id", "Values": ["self"]})
        
        try:
            resp = self._ec2.describe_snapshots(Filters=filters)
            return [
                {
                    "snapshot_id": snap["SnapshotId"],
                    "volume_id": snap.get("VolumeId"),
                    "state": snap["State"],
                    "start_time": str(snap["StartTime"]),
                    "description": snap.get("Description", "")
                }
                for snap in resp.get("Snapshots", [])
            ]
        except Exception as e:
            logger.warning("Failed to list snapshots: %s", e)
            return []

    def delete_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        """Delete an EBS snapshot."""
        logger.info("Deleting snapshot %s", snapshot_id)
        try:
            self._ec2.delete_snapshot(SnapshotId=snapshot_id)
            return {"snapshot_id": snapshot_id, "status": "deleted"}
        except Exception as e:
            logger.error("Failed to delete snapshot %s: %s", snapshot_id, e)
            return {"snapshot_id": snapshot_id, "status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # Certificates
    # ------------------------------------------------------------------

    def list_certificates(self) -> list[dict[str, Any]]:
        """Return list of SSL/TLS certificates (ACM and imported)."""
        try:
            acm = boto3.client("acm", region_name=self._region, config=_RETRY_CONFIG)
            resp = acm.list_certificates()
            certs = []
            for cert_meta in resp.get("CertificateSummaryList", []):
                cert_arn = cert_meta["CertificateArn"]
                try:
                    cert_details = acm.describe_certificate(CertificateArn=cert_arn)
                    cert = cert_details.get("Certificate", {})
                    certs.append({
                        "certificate_arn": cert_arn,
                        "domain_name": cert.get("DomainName"),
                        "status": cert.get("Status"),
                        "in_use_by": cert.get("InUseBy", []),
                        "not_after": str(cert.get("NotAfter", "")),
                    })
                except Exception as e:
                    logger.warning("Could not describe cert %s: %s", cert_arn, e)
            return certs
        except Exception as e:
            logger.warning("Failed to list certificates: %s", e)
            return []
