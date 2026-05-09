"""
AWS CloudProvider implementation using boto3.

Wraps EC2, CloudWatch, EBS, Cost Explorer, SSM, S3, CloudTrail,
and Resource Groups Tagging API.
"""

from __future__ import annotations

import json
import os
import re
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from cloud_agent.cloud.provider import CloudProvider
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

_RETRY_CONFIG = BotoConfig(
    retries={"max_attempts": 3, "mode": "adaptive"},
)


class AWSProvider(CloudProvider):
    """Concrete AWS implementation of :class:`CloudProvider`."""

    def __init__(self, region: str | None = None) -> None:
        # Prefer explicit arg, then AWS_DEFAULT_REGION (standard boto3 env var), then AWS_REGION
        self._region = (
            region
            or os.getenv("AWS_DEFAULT_REGION")
            or os.getenv("AWS_REGION")
            or "us-east-1"
        )
        self._ec2 = boto3.client("ec2", region_name=self._region, config=_RETRY_CONFIG)
        self._cloudwatch = boto3.client("cloudwatch", region_name=self._region, config=_RETRY_CONFIG)
        self._ce = boto3.client("ce", region_name=self._region, config=_RETRY_CONFIG)
        self._ssm = boto3.client("ssm", region_name=self._region, config=_RETRY_CONFIG)
        self._s3 = boto3.client("s3", region_name=self._region, config=_RETRY_CONFIG)
        self._cloudtrail = boto3.client("cloudtrail", region_name=self._region, config=_RETRY_CONFIG)
        self._rds = boto3.client("rds", region_name=self._region, config=_RETRY_CONFIG)
        self._pi = boto3.client("pi", region_name=self._region, config=_RETRY_CONFIG)
        self._logs = boto3.client("logs", region_name=self._region, config=_RETRY_CONFIG)
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

    def _resolve_ami(self, region: str) -> str:
        """Resolve the latest Amazon Linux 2023 AMI for the given region via SSM."""
        ssm = boto3.client("ssm", region_name=region, config=_RETRY_CONFIG)
        try:
            resp = ssm.get_parameter(
                Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
            )
            ami_id = resp["Parameter"]["Value"]
            logger.info("Resolved AMI for %s: %s", region, ami_id)
            return ami_id
        except Exception:
            # Fallback: describe images owned by Amazon filtered for AL2023
            logger.warning("SSM AMI lookup failed for %s, trying ec2.describe_images fallback", region)
            ec2 = boto3.client("ec2", region_name=region, config=_RETRY_CONFIG)
            resp = ec2.describe_images(
                Owners=["amazon"],
                Filters=[
                    {"Name": "name", "Values": ["al2023-ami-2023*-kernel-*-x86_64"]},
                    {"Name": "state", "Values": ["available"]},
                    {"Name": "architecture", "Values": ["x86_64"]},
                ],
            )
            images = sorted(
                resp.get("Images", []),
                key=lambda i: i.get("CreationDate", ""),
                reverse=True,
            )
            if images:
                return images[0]["ImageId"]
            raise RuntimeError(f"Could not resolve a valid AMI for region {region}")

    def create_instance(self, instance_type: str, region: str | None = None, tags: list[dict[str, str]] | None = None) -> dict[str, Any]:
        region = region or self._region
        ec2 = boto3.client("ec2", region_name=region, config=_RETRY_CONFIG)
        logger.info("Creating new %s instance in %s", instance_type, region)

        # Dynamically resolve the latest Amazon Linux 2023 AMI for this region
        ami_id = self._resolve_ami(region)

        # Build default tags if none provided
        if not tags:
            import time as _t
            tags = [
                {"Key": "Name", "Value": f"cloud-agent-{int(_t.time())}"},
                {"Key": "Provisioner", "Value": "CloudAgentDashboard"},
            ]

        tag_spec = [{
            'ResourceType': 'instance',
            'Tags': [{"Key": t["Key"], "Value": t["Value"]} for t in tags]
        }]

        resp = ec2.run_instances(
            ImageId=ami_id,
            InstanceType=instance_type,
            MinCount=1,
            MaxCount=1,
            TagSpecifications=tag_spec,
        )
        inst = resp["Instances"][0]
        instance_id = inst["InstanceId"]
        logger.info("Instance %s launched successfully in %s", instance_id, region)
        return {
            "instance_id": instance_id,
            "instance_type": instance_type,
            "state": inst["State"]["Name"],
            "region": region,
            "ami_id": ami_id,
            "status": "launched",
        }

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
        return self._get_avg_cpu(instance_id, self._region, 86400, start, end)

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

    def create_volume(self, size_gb: int, region: str | None = None, tags: list[dict[str, str]] | None = None) -> dict[str, Any]:
        region = region or self._region
        ec2 = boto3.client("ec2", region_name=region, config=_RETRY_CONFIG)
        logger.info("Creating %d GB volume in %s", size_gb, region)
        
        kwargs: dict[str, Any] = {
            "Size": size_gb,
            "AvailabilityZone": f"{region}a", # Simplification
        }
        if tags:
            kwargs["TagSpecifications"] = [{
                'ResourceType': 'volume',
                'Tags': [{"Key": t["Key"], "Value": t["Value"]} for t in tags]
            }]

        resp = ec2.create_volume(**kwargs)
        return {"volume_id": resp["VolumeId"], "status": "creating", "region": region}

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

    def get_cost_by_service(self, days: int = 1) -> list[dict]:
        """Return cost breakdown grouped by AWS service using Cost Explorer."""
        end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            resp = self._ce.get_cost_and_usage(
                TimePeriod={"Start": start, "End": end},
                Granularity="DAILY",
                Metrics=["UnblendedCost"],
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            )
            service_totals: dict[str, float] = {}
            currency = "USD"
            for result in resp.get("ResultsByTime", []):
                for group in result.get("Groups", []):
                    svc = group["Keys"][0]
                    amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                    currency = group["Metrics"]["UnblendedCost"]["Unit"]
                    service_totals[svc] = service_totals.get(svc, 0.0) + amount
            # Average over days requested
            return [
                {"service": svc, "amount": round(total / max(days, 1), 2), "currency": currency}
                for svc, total in sorted(service_totals.items(), key=lambda x: -x[1])
                if total > 0.001
            ]
        except Exception as e:
            logger.warning("Could not fetch per-service costs: %s", e)
            return []

    # ------------------------------------------------------------------
    # RDS / Query Optimization
    # ------------------------------------------------------------------

    def list_rds_instances(self) -> list[dict[str, Any]]:
        """Return RDS instances across active regions."""
        databases: list[dict[str, Any]] = []
        for region in self._active_regions:
            try:
                rds = boto3.client("rds", region_name=region, config=_RETRY_CONFIG)
                paginator = rds.get_paginator("describe_db_instances")
                for page in paginator.paginate():
                    for db in page.get("DBInstances", []):
                        databases.append({
                            "db_instance_id": db["DBInstanceIdentifier"],
                            "engine": db.get("Engine", "unknown"),
                            "instance_class": db.get("DBInstanceClass", "unknown"),
                            "status": db.get("DBInstanceStatus", "unknown"),
                            "region": region,
                            "dbi_resource_id": db.get("DbiResourceId"),
                        })
            except Exception as e:
                logger.warning("Could not list RDS instances in %s: %s", region, e)
        return databases

    def get_rds_metrics(self, db_instance_id: str, metric_name: str) -> float:
        """Return recent average RDS metric. Latency metrics are seconds; CPU is percent."""
        db_meta = next((db for db in self.list_rds_instances() if db["db_instance_id"] == db_instance_id), None)
        region = db_meta.get("region", self._region) if db_meta else self._region
        cloudwatch = boto3.client("cloudwatch", region_name=region, config=_RETRY_CONFIG)
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=15)
        resp = cloudwatch.get_metric_statistics(
            Namespace="AWS/RDS",
            MetricName=metric_name,
            Dimensions=[{"Name": "DBInstanceIdentifier", "Value": db_instance_id}],
            StartTime=start,
            EndTime=end,
            Period=300,
            Statistics=["Average"],
        )
        datapoints = resp.get("Datapoints", [])
        if not datapoints:
            return 0.0
        return sum(dp["Average"] for dp in datapoints) / len(datapoints)

    def get_slow_queries(self, db_instance_id: str, engine: str, limit: int = 10) -> list[dict[str, Any]]:
        """Best-effort slow SQL evidence from RDS Performance Insights.

        Performance Insights does not expose EXPLAIN ANALYZE through AWS APIs, so
        the returned explain_output contains the PI evidence the optimizer can use.
        """
        db_meta = next((db for db in self.list_rds_instances() if db["db_instance_id"] == db_instance_id), None)
        resource_id = db_meta.get("dbi_resource_id") if db_meta else None
        region = db_meta.get("region", self._region) if db_meta else self._region
        if not resource_id:
            return []

        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=1)
        pi = boto3.client("pi", region_name=region, config=_RETRY_CONFIG)
        try:
            resp = pi.describe_dimension_keys(
                ServiceType="RDS",
                Identifier=resource_id,
                StartTime=start,
                EndTime=end,
                Metric="db.load.avg",
                PeriodInSeconds=300,
                GroupBy={"Group": "db.sql_tokenized", "Limit": limit},
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            message = e.response.get("Error", {}).get("Message", str(e))
            logger.warning("Could not read Performance Insights for %s: %s", db_instance_id, message)
            if code == "NotAuthorizedException":
                raise RuntimeError(
                    f"Performance Insights is not authorized for {db_instance_id}. "
                    "Enable Database Insights/Performance Insights for this DB and grant pi:DescribeDimensionKeys."
                ) from e
            raise
        except Exception as e:
            logger.warning("Could not read Performance Insights for %s: %s", db_instance_id, e)
            raise

        queries: list[dict[str, Any]] = []
        for item in resp.get("Keys", [])[:limit]:
            dims = item.get("Dimensions", {})
            sql = self._extract_pi_sql(dims)
            load = float(item.get("Total", 0.0) or 0.0)
            dimension_id = (
                dims.get("db.sql_tokenized.id")
                or dims.get("db.sql.id")
                or dims.get("db.sql.statement")
                or dims.get("db.sql_tokenized.statement")
                or ""
            )
            additional = item.get("AdditionalMetrics", {}) or {}
            avg_latency_ms = 0.0
            for key in ("db.sql_tokenized.avg_latency", "db.sql.avg_latency", "avg_latency"):
                if key in additional:
                    try:
                        avg_latency_ms = float(additional[key]) * 1000
                    except (TypeError, ValueError):
                        avg_latency_ms = 0.0
                    break
            queries.append({
                "query": sql,
                "dimension_id": dimension_id,
                "calls": 0,
                "avg_time_ms": round(avg_latency_ms or max(load * 1000, 0), 2),
                "has_duration_metric": bool(avg_latency_ms),
                "db_load": round(load, 4),
                "rows_examined": 0,
                "rows_returned": 0,
                "source": "AWS Performance Insights",
                "explain_output": (
                    f"Performance Insights db.load.avg={load:.3f}; "
                    f"dimension={dimension_id or 'unknown'}; "
                    "EXPLAIN ANALYZE is not available through the AWS API."
                ),
            })
        return queries

    def get_rds_slow_log_queries(self, db_instance_id: str, engine: str, limit: int = 10) -> list[dict[str, Any]]:
        """Best-effort fallback using exported RDS logs in CloudWatch Logs.

        MySQL needs slow_query_log enabled and exported to CloudWatch Logs.
        PostgreSQL needs relevant statement logging exported to the postgresql log.
        """
        db_meta = next((db for db in self.list_rds_instances() if db["db_instance_id"] == db_instance_id), None)
        region = db_meta.get("region", self._region) if db_meta else self._region
        logs = boto3.client("logs", region_name=region, config=_RETRY_CONFIG)
        engine_lower = engine.lower()
        suffixes = ["slowquery", "error"] if "mysql" in engine_lower or "maria" in engine_lower else ["postgresql"]
        start_ms = int((datetime.now(timezone.utc) - timedelta(hours=3)).timestamp() * 1000)
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        samples: list[dict[str, Any]] = []

        for suffix in suffixes:
            log_group = f"/aws/rds/instance/{db_instance_id}/{suffix}"
            try:
                paginator = logs.get_paginator("filter_log_events")
                for page in paginator.paginate(
                    logGroupName=log_group,
                    startTime=start_ms,
                    endTime=end_ms,
                    PaginationConfig={"MaxItems": 200},
                ):
                    for event in page.get("events", []):
                        parsed = self._parse_rds_log_query(event.get("message", ""), engine_lower)
                        if parsed:
                            samples.append(parsed)
                            if len(samples) >= limit:
                                return samples
            except logs.exceptions.ResourceNotFoundException:
                continue
            except ClientError as e:
                logger.warning("Could not read RDS log group %s: %s", log_group, e)
                continue
        return samples[:limit]

    @staticmethod
    def _parse_rds_log_query(message: str, engine_lower: str) -> dict[str, Any] | None:
        text = message.strip()
        if not text:
            return None

        if "mysql" in engine_lower or "maria" in engine_lower:
            query_time = 0.0
            match = re.search(r"Query_time:\s*([0-9.]+)", text)
            if match:
                query_time = float(match.group(1)) * 1000
            sql_candidates = [
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.startswith("#") and not line.upper().startswith("SET TIMESTAMP")
            ]
            sql = sql_candidates[-1] if sql_candidates else ""
        else:
            duration_match = re.search(r"duration:\s*([0-9.]+)\s*ms", text, flags=re.IGNORECASE)
            query_time = float(duration_match.group(1)) if duration_match else 0.0
            statement_match = re.search(r"statement:\s*(.+)$", text, flags=re.IGNORECASE | re.DOTALL)
            sql = statement_match.group(1).strip() if statement_match else text

        if not sql:
            return None

        return {
            "query": sql,
            "calls": 0,
            "avg_time_ms": round(query_time, 2),
            "has_duration_metric": bool(query_time),
            "db_load": 0.0,
            "rows_examined": 0,
            "rows_returned": 0,
            "source": "CloudWatch RDS Logs",
            "explain_output": "SQL sample parsed from exported RDS CloudWatch Logs.",
        }

    @staticmethod
    def _extract_pi_sql(dimensions: dict[str, Any]) -> str:
        """Extract the best SQL text from a Performance Insights dimension map."""
        preferred_keys = (
            "db.sql_tokenized.statement",
            "db.sql.statement",
            "db.sql_tokenized.sql",
            "db.sql.sql",
            "db.sql_tokenized.id",
            "db.sql.id",
        )
        for key in preferred_keys:
            value = dimensions.get(key)
            if value:
                return str(value)
        for key, value in dimensions.items():
            if "sql" in key.lower() and value:
                return str(value)
        return "SQL text unavailable from Performance Insights"


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

    def run_sysbench_benchmark(self, instance_id: str, timeout: int = 120) -> dict[str, Any]:
        """Run real sysbench CPU, memory, and file I/O tests through AWS SSM."""
        script = r"""
set -eu
if ! command -v sysbench >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y >/dev/null 2>&1 && sudo apt-get install -y sysbench >/dev/null 2>&1
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y sysbench >/dev/null 2>&1
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y epel-release >/dev/null 2>&1 || true
    sudo yum install -y sysbench >/dev/null 2>&1
  fi
fi
command -v sysbench >/dev/null 2>&1 || { echo "SYSBENCH_STATUS=missing"; exit 0; }
WORKDIR="$(mktemp -d /tmp/cloud-agent-sysbench.XXXXXX)"
cd "$WORKDIR"
CPU_OUT="$(sysbench cpu --threads=1 --time=5 run 2>/dev/null || true)"
MEM_OUT="$(sysbench memory --threads=1 --time=5 run 2>/dev/null || true)"
sysbench fileio --file-total-size=128M prepare >/dev/null 2>&1 || true
FILE_OUT="$(sysbench fileio --file-total-size=128M --file-test-mode=rndrw --time=5 run 2>/dev/null || true)"
sysbench fileio --file-total-size=128M cleanup >/dev/null 2>&1 || true
rm -rf "$WORKDIR"
echo "SYSBENCH_STATUS=ok"
printf '%s\n' "$CPU_OUT" | awk -F: '/events per second/ {gsub(/^[ \t]+/,"",$2); print "CPU_EVENTS_PER_SEC="$2}'
printf '%s\n' "$MEM_OUT" | awk '/transferred/ {gsub(/[()]/,""); print "MEMORY_MIB_PER_SEC="$4}'
printf '%s\n' "$FILE_OUT" | awk -F: '/reads\/s|writes\/s|fsyncs\/s/ {gsub(/^[ \t]+/,"",$2); sum+=$2} END {if (sum) print "DISK_IOPS="sum}'
printf '%s\n' "$FILE_OUT" | awk -F: '/95th percentile/ {gsub(/^[ \t]+/,"",$2); print "P95_LATENCY_MS="$2}'
"""
        output = self.run_ssm_command(instance_id, [script], timeout=timeout)
        data: dict[str, Any] = {
            "status": "ok" if "SYSBENCH_STATUS=ok" in output else "unavailable",
            "cpu_events_per_sec": None,
            "memory_mib_per_sec": None,
            "disk_iops": None,
            "p95_latency_ms": None,
            "raw": output[-2000:],
        }
        keys = {
            "CPU_EVENTS_PER_SEC": "cpu_events_per_sec",
            "MEMORY_MIB_PER_SEC": "memory_mib_per_sec",
            "DISK_IOPS": "disk_iops",
            "P95_LATENCY_MS": "p95_latency_ms",
        }
        for line in output.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in keys:
                try:
                    data[keys[key]] = round(float(value.strip()), 2)
                except ValueError:
                    pass
        return data

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
