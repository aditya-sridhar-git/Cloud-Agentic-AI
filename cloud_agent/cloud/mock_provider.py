"""
Mock CloudProvider — returns realistic fake data for demos and testing.

No AWS credentials required. Every method returns plausible data so
the full Observe → Think → Act loop can be demonstrated locally.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from cloud_agent.cloud.provider import CloudProvider
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

_INSTANCE_TYPES = ["t3.micro", "t3.small", "t3.medium", "t3.large", "t3.xlarge",
                   "m5.large", "m5.xlarge", "c5.large", "c5.xlarge"]

_MOCK_INSTANCES = [
    {"instance_id": "i-0a1b2c3d4e5f60001", "instance_type": "t3.xlarge",  "state": "running",  "name": "api-server-prod",    "env": "prod"},
    {"instance_id": "i-0a1b2c3d4e5f60002", "instance_type": "m5.xlarge",  "state": "running",  "name": "worker-node-01",     "env": "prod"},
    {"instance_id": "i-0a1b2c3d4e5f60003", "instance_type": "t3.large",   "state": "running",  "name": "dev-webserver",      "env": "dev"},
    {"instance_id": "i-0a1b2c3d4e5f60004", "instance_type": "t3.medium",  "state": "running",  "name": "staging-api",        "env": "staging"},
    {"instance_id": "i-0a1b2c3d4e5f60005", "instance_type": "c5.xlarge",  "state": "running",  "name": "ml-training-gpu",    "env": "prod"},
    {"instance_id": "i-0a1b2c3d4e5f60006", "instance_type": "t3.small",   "state": "running",  "name": "test-runner",        "env": "dev"},
    {"instance_id": "i-0a1b2c3d4e5f60007", "instance_type": "t3.medium",  "state": "stopped",  "name": "legacy-backend",     "env": "dev"},
    {"instance_id": "i-0a1b2c3d4e5f60008", "instance_type": "m5.large",   "state": "running",  "name": "monitoring-stack",   "env": "prod"},
]

_MOCK_VOLUMES = [
    {"volume_id": "vol-0a1b2c3d4e5f0001", "state": "in-use",    "size_gb": 100, "encrypted": True,  "days_old": 120},
    {"volume_id": "vol-0a1b2c3d4e5f0002", "state": "available", "size_gb": 50,  "encrypted": True,  "days_old": 15},
    {"volume_id": "vol-0a1b2c3d4e5f0003", "state": "available", "size_gb": 200, "encrypted": False, "days_old": 45},
    {"volume_id": "vol-0a1b2c3d4e5f0004", "state": "in-use",    "size_gb": 500, "encrypted": False, "days_old": 90},
    {"volume_id": "vol-0a1b2c3d4e5f0005", "state": "available", "size_gb": 20,  "encrypted": True,  "days_old": 3},
]

_MOCK_SECURITY_GROUPS = [
    {
        "group_id": "sg-0a1b2c3d4e5f0001", "group_name": "web-public",
        "description": "Public-facing web tier",
        "ingress_rules": [
            {"protocol": "tcp", "from_port": 443, "to_port": 443, "cidr_blocks": ["0.0.0.0/0"]},
            {"protocol": "tcp", "from_port": 80,  "to_port": 80,  "cidr_blocks": ["0.0.0.0/0"]},
        ],
    },
    {
        "group_id": "sg-0a1b2c3d4e5f0002", "group_name": "ssh-open-DANGER",
        "description": "SSH open to the world — INSECURE",
        "ingress_rules": [
            {"protocol": "tcp", "from_port": 22, "to_port": 22, "cidr_blocks": ["0.0.0.0/0"]},
        ],
    },
    {
        "group_id": "sg-0a1b2c3d4e5f0003", "group_name": "internal-only",
        "description": "Internal services",
        "ingress_rules": [
            {"protocol": "tcp", "from_port": 5432, "to_port": 5432, "cidr_blocks": ["10.0.0.0/8"]},
        ],
    },
    {
        "group_id": "sg-0a1b2c3d4e5f0004", "group_name": "rdp-open-DANGER",
        "description": "RDP open to the world — INSECURE",
        "ingress_rules": [
            {"protocol": "tcp", "from_port": 3389, "to_port": 3389, "cidr_blocks": ["0.0.0.0/0"]},
        ],
    },
]

_MOCK_S3_BUCKETS = [
    {"bucket_name": "company-assets-prod",     "is_public": False},
    {"bucket_name": "marketing-website-static", "is_public": True},
    {"bucket_name": "data-lake-raw",            "is_public": False},
    {"bucket_name": "temp-upload-bucket",       "is_public": True},
]

# CPU profiles: (instance_index) → base CPU
_CPU_PROFILES: dict[str, float] = {
    "i-0a1b2c3d4e5f60001": 62.0,   # prod API — healthy
    "i-0a1b2c3d4e5f60002": 45.0,   # worker — moderate
    "i-0a1b2c3d4e5f60003": 2.1,    # dev — idle!
    "i-0a1b2c3d4e5f60004": 8.0,    # staging — low
    "i-0a1b2c3d4e5f60005": 88.0,   # ML training — hot
    "i-0a1b2c3d4e5f60006": 1.5,    # test runner — idle!
    "i-0a1b2c3d4e5f60008": 35.0,   # monitoring — moderate
}


class MockProvider(CloudProvider):
    """Fake cloud provider that returns realistic demo data."""

    def __init__(self, region: str = "us-east-1", scenario: str = "default") -> None:
        self._region = region
        self._scenario = scenario
        self._stopped: set[str] = set()
        self._terminated: set[str] = set()
        self._deleted_volumes: set[str] = set()
        self._applied_tags: dict[str, list[dict[str, str]]] = {}
        self._action_log: list[dict[str, Any]] = []
        logger.info("[green]Mock provider initialised[/green] (region=%s, scenario=%s)", region, scenario)

    # ------------------------------------------------------------------
    # Instances
    # ------------------------------------------------------------------

    def list_instances(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        instances = []
        for m in _MOCK_INSTANCES:
            iid = m["instance_id"]
            if iid in self._terminated:
                continue
            state = "stopped" if iid in self._stopped else m["state"]

            tags = [
                {"Key": "Name", "Value": m["name"]},
            ]
            # Intentionally leave some instances without required tags
            if m["env"] != "dev":
                tags.append({"Key": "Environment", "Value": m["env"]})
                tags.append({"Key": "Owner", "Value": "platform-team"})
                tags.append({"Key": "Project", "Value": "cloud-agent"})
            # dev instances missing Environment, Owner, Project tags → tag_enforcer should catch

            existing = self._applied_tags.get(iid, [])
            existing_keys = {t["Key"] for t in existing}
            for t in tags:
                if t["Key"] not in existing_keys:
                    existing.append(t)
            tags = existing

            launch_time = datetime.now(timezone.utc) - timedelta(days=random.randint(5, 90))
            instances.append({
                "instance_id": iid,
                "instance_type": m["instance_type"],
                "state": state,
                "launch_time": str(launch_time),
                "tags": tags,
            })
        return instances

    def stop_instance(self, instance_id: str) -> dict[str, Any]:
        self._stopped.add(instance_id)
        self._action_log.append({"action": "stop", "instance_id": instance_id, "time": time.time()})
        logger.info("[mock] Stopped %s", instance_id)
        return {"instance_id": instance_id, "status": "stopping"}

    def terminate_instance(self, instance_id: str) -> dict[str, Any]:
        self._terminated.add(instance_id)
        self._action_log.append({"action": "terminate", "instance_id": instance_id, "time": time.time()})
        logger.info("[mock] Terminated %s", instance_id)
        return {"instance_id": instance_id, "status": "terminating"}

    def resize_instance(self, instance_id: str, new_type: str) -> dict[str, Any]:
        self._action_log.append({"action": "resize", "instance_id": instance_id, "new_type": new_type, "time": time.time()})
        logger.info("[mock] Resized %s → %s", instance_id, new_type)
        return {"instance_id": instance_id, "new_type": new_type, "status": "resized"}

    def start_instance(self, instance_id: str) -> dict[str, Any]:
        self._stopped.discard(instance_id)
        self._action_log.append({"action": "start", "instance_id": instance_id, "time": time.time()})
        logger.info("[mock] Started %s", instance_id)
        return {"instance_id": instance_id, "status": "starting"}

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_cpu_utilization(self, instance_id: str, minutes: int = 30) -> float:
        base = _CPU_PROFILES.get(instance_id, 50.0)
        return round(base + random.uniform(-2, 2), 1)

    def get_cpu_utilization_days(self, instance_id: str, days: int = 7) -> float:
        base = _CPU_PROFILES.get(instance_id, 50.0)
        return round(base + random.uniform(-5, 5), 1)

    # ------------------------------------------------------------------
    # Volumes
    # ------------------------------------------------------------------

    def list_volumes(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        volumes = []
        for v in _MOCK_VOLUMES:
            if v["volume_id"] in self._deleted_volumes:
                continue
            create_time = datetime.now(timezone.utc) - timedelta(days=v["days_old"])
            unattached_days = v["days_old"] if v["state"] == "available" else 0
            volumes.append({
                "volume_id": v["volume_id"],
                "state": v["state"],
                "size_gb": v["size_gb"],
                "create_time": str(create_time),
                "unattached_days": unattached_days,
            })
        return volumes

    def snapshot_volume(self, volume_id: str) -> dict[str, Any]:
        snap_id = f"snap-mock-{volume_id[-4:]}"
        self._action_log.append({"action": "snapshot", "volume_id": volume_id, "time": time.time()})
        logger.info("[mock] Snapshot %s → %s", volume_id, snap_id)
        return {"volume_id": volume_id, "snapshot_id": snap_id}

    def delete_volume(self, volume_id: str) -> dict[str, Any]:
        self._deleted_volumes.add(volume_id)
        self._action_log.append({"action": "delete_volume", "volume_id": volume_id, "time": time.time()})
        logger.info("[mock] Deleted volume %s", volume_id)
        return {"volume_id": volume_id, "status": "deleted"}

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def get_tags(self, resource_id: str) -> list[dict[str, str]]:
        return self._applied_tags.get(resource_id, [])

    def set_tags(self, resource_id: str, tags: list[dict[str, str]]) -> dict[str, Any]:
        existing = self._applied_tags.setdefault(resource_id, [])
        existing_keys = {t["Key"] for t in existing}
        for t in tags:
            if t["Key"] not in existing_keys:
                existing.append(t)
        self._action_log.append({"action": "tag", "resource_id": resource_id, "tags": tags, "time": time.time()})
        logger.info("[mock] Tagged %s with %d tag(s)", resource_id, len(tags))
        return {"resource_id": resource_id, "tags_applied": len(tags)}

    # ------------------------------------------------------------------
    # Cost
    # ------------------------------------------------------------------

    def get_daily_cost(self, days: int = 1) -> float:
        # Simulate a cost spike scenario
        if self._scenario == "cost_spike":
            return round(random.uniform(180.0, 220.0), 2)
        return round(random.uniform(95.0, 115.0), 2)

    def get_cost_baseline(self, days: int = 7) -> float:
        return round(random.uniform(90.0, 110.0), 2)

    def get_cost_by_service(self, days: int = 1) -> list[dict]:
        """Return mock per-service cost breakdown."""
        if self._scenario == "cost_spike":
            # Simulate EC2 spike
            return [
                {"service": "Amazon EC2",         "amount": round(random.uniform(120.0, 150.0), 2), "currency": "USD"},
                {"service": "Amazon S3",           "amount": round(random.uniform(8.0, 12.0), 2),   "currency": "USD"},
                {"service": "Amazon RDS",          "amount": round(random.uniform(25.0, 35.0), 2),  "currency": "USD"},
                {"service": "AWS Lambda",          "amount": round(random.uniform(1.0, 3.0), 2),    "currency": "USD"},
                {"service": "Amazon CloudWatch",   "amount": round(random.uniform(2.0, 5.0), 2),    "currency": "USD"},
            ]
        return [
            {"service": "Amazon EC2",         "amount": round(random.uniform(50.0, 65.0), 2),  "currency": "USD"},
            {"service": "Amazon S3",          "amount": round(random.uniform(8.0, 12.0), 2),   "currency": "USD"},
            {"service": "Amazon RDS",         "amount": round(random.uniform(20.0, 30.0), 2),  "currency": "USD"},
            {"service": "AWS Lambda",         "amount": round(random.uniform(1.0, 3.0), 2),    "currency": "USD"},
            {"service": "Amazon CloudWatch",  "amount": round(random.uniform(2.0, 5.0), 2),    "currency": "USD"},
        ]


    # ------------------------------------------------------------------
    # SSM / Diagnosis
    # ------------------------------------------------------------------

    def run_ssm_command(self, instance_id: str, commands: list[str], timeout: int = 30) -> str:
        """Return realistic fake diagnostic output."""
        cpu = _CPU_PROFILES.get(instance_id, 50.0)

        if cpu > 80:
            return (
                "top - 14:32:01 up 45 days, 3:21, 1 user, load average: 4.82, 4.65, 4.50\n"
                "Tasks: 142 total, 3 running, 139 sleeping, 0 stopped, 0 zombie\n"
                "%Cpu(s): 97.2 us, 1.8 sy, 0.0 ni, 0.8 id, 0.0 wa, 0.0 hi, 0.2 si\n"
                "\n"
                "  PID USER      PR  NI    VIRT    RES    SHR S  %CPU %MEM    COMMAND\n"
                " 4821 appuser   20   0 8245632 4.2g  12340 R  97.0 52.8    java -jar ml-service.jar\n"
                " 1102 root      20   0  256912  12480  8640 S   0.3  0.2    /usr/sbin/sshd\n"
                "\n"
                "---- dmesg (last 10 min) ----\n"
                "[482910.123] java[4821]: GC pause (young) 8.2s — possible memory leak\n"
                "[482920.456] java[4821]: Out of memory: Kill process or sacrifice child\n"
                "\n"
                "---- df -h ----\n"
                "Filesystem      Size  Used Avail Use% Mounted on\n"
                "/dev/xvda1      100G   89G   11G  89% /\n"
            )
        elif cpu < 5:
            return (
                "top - 14:32:01 up 22 days, 1:05, 0 users, load average: 0.01, 0.02, 0.00\n"
                "Tasks: 68 total, 1 running, 67 sleeping, 0 stopped, 0 zombie\n"
                "%Cpu(s): 0.3 us, 0.1 sy, 0.0 ni, 99.5 id, 0.0 wa, 0.0 hi, 0.1 si\n"
                "\n"
                "  PID USER      PR  NI    VIRT    RES    SHR S  %CPU %MEM    COMMAND\n"
                "    1 root      20   0  169324  13220  8560 S   0.0  0.2    /sbin/init\n"
                "\n"
                "---- ps aux summary ----\n"
                "No application processes running. Only system daemons active.\n"
                "Last login by user: 18 days ago.\n"
                "\n"
                "---- df -h ----\n"
                "Filesystem      Size  Used Avail Use% Mounted on\n"
                "/dev/xvda1       50G    4G   46G   8% /\n"
            )
        else:
            return (
                "top - 14:32:01 up 30 days, 2:15, 2 users, load average: 1.20, 1.15, 1.10\n"
                "Tasks: 95 total, 2 running, 93 sleeping, 0 stopped, 0 zombie\n"
                f"%Cpu(s): {cpu:.1f} us, 2.0 sy, 0.0 ni, {100-cpu-2:.1f} id\n"
                "\n"
                "  PID USER      PR  NI    VIRT    RES    SHR S  %CPU %MEM    COMMAND\n"
                "  512 appuser   20   0 2048576  512m  24000 S  35.0 12.8    python app.py\n"
                "  810 postgres  20   0  650240  128m  32000 S   8.0  3.2    postgres: main\n"
                "\n"
                "System appears healthy. No anomalies detected.\n"
            )

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    def describe_security_groups(self) -> list[dict[str, Any]]:
        return list(_MOCK_SECURITY_GROUPS)

    def list_s3_buckets_public_access(self) -> list[dict[str, Any]]:
        return list(_MOCK_S3_BUCKETS)

    def check_ebs_encryption(self) -> list[dict[str, Any]]:
        return [
            {
                "volume_id": v["volume_id"],
                "size_gb": v["size_gb"],
                "state": v["state"],
                "encrypted": False,
            }
            for v in _MOCK_VOLUMES
            if not v["encrypted"]
        ]

    # ------------------------------------------------------------------
    # CloudTrail
    # ------------------------------------------------------------------

    def get_cloudtrail_events(self, hours: int = 24, event_name: str | None = None) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        events = [
            {
                "event_name": "RunInstances",
                "event_time": str(now - timedelta(hours=3)),
                "username": "deploy-bot",
                "source_ip": "10.0.1.50",
                "resources": [{"type": "AWS::EC2::Instance", "name": "i-0a1b2c3d4e5f60001"}],
            },
            {
                "event_name": "AuthorizeSecurityGroupIngress",
                "event_time": str(now - timedelta(hours=6)),
                "username": "intern-dev",
                "source_ip": "203.0.113.42",
                "resources": [{"type": "AWS::EC2::SecurityGroup", "name": "sg-0a1b2c3d4e5f0002"}],
            },
            {
                "event_name": "PutBucketPolicy",
                "event_time": str(now - timedelta(hours=12)),
                "username": "marketing-admin",
                "source_ip": "198.51.100.7",
                "resources": [{"type": "AWS::S3::Bucket", "name": "marketing-website-static"}],
            },
            {
                "event_name": "StopInstances",
                "event_time": str(now - timedelta(hours=1)),
                "username": "cloud-agent",
                "source_ip": "10.0.0.5",
                "resources": [{"type": "AWS::EC2::Instance", "name": "i-0a1b2c3d4e5f60003"}],
            },
        ]
        if event_name:
            events = [e for e in events if e["event_name"] == event_name]
        return events

    # ------------------------------------------------------------------
    # Snapshots / Backups (NEW)
    # ------------------------------------------------------------------

    def list_snapshots(self, creator: str | None = None) -> list[dict[str, Any]]:
        """Return mock snapshots."""
        now = datetime.now(timezone.utc)
        snapshots = [
            {
                "snapshot_id": f"snap-0a1b2c3d4e5f{i:04d}",
                "volume_id": "vol-0a1b2c3d4e5f0001",
                "start_time": str(now - timedelta(days=i*5)),
                "state": "completed",
                "tags": [
                    {"Key": "CreatedBy", "Value": "cloud-agent-backup-manager"},
                    {"Key": "Name", "Value": f"backup-api-server-{i}"},
                ],
            }
            for i in range(1, 8)
        ]
        
        if creator:
            snapshots = [
                s for s in snapshots
                if any(t["Key"] == "CreatedBy" and t["Value"] == creator for t in s.get("tags", []))
            ]
        
        return snapshots

    def delete_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        """Delete a mock snapshot."""
        logger.info("[mock] Deleted snapshot %s", snapshot_id)
        self._action_log.append({"action": "delete_snapshot", "snapshot_id": snapshot_id, "time": time.time()})
        return {"snapshot_id": snapshot_id, "status": "deleted"}

    def snapshot_volume(self, volume_id: str, tags: list[dict[str, str]] | None = None) -> dict[str, Any]:
        """Create a mock snapshot of a volume."""
        snapshot_id = f"snap-{volume_id.replace('vol-', 'snap-')}-{int(time.time())}"
        logger.info("[mock] Created snapshot %s of volume %s", snapshot_id, volume_id)
        self._action_log.append({
            "action": "create_snapshot",
            "snapshot_id": snapshot_id,
            "volume_id": volume_id,
            "tags": tags,
            "time": time.time(),
        })
        return {"snapshot_id": snapshot_id, "volume_id": volume_id, "status": "completed", "tags": tags or []}

    # ------------------------------------------------------------------
    # Certificates (NEW)
    # ------------------------------------------------------------------

    def list_certificates(self) -> list[dict[str, Any]]:
        """Return mock certificates with various expiry states."""
        now = datetime.now(timezone.utc)
        return [
            {
                "arn": "arn:aws:acm:us-east-1:123456789012:certificate/aaaabbbb-1111-2222-3333-444444444441",
                "domain_name": "api.example.com",
                "issuer": "Amazon",
                "not_after": str(now + timedelta(days=180)),
                "type": "AMAZON_ISSUED",
                "auto_renewal": True,
                "in_use": True,
                "key_algorithm": "RSA-2048",
            },
            {
                "arn": "arn:aws:acm:us-east-1:123456789012:certificate/aaaabbbb-1111-2222-3333-444444444442",
                "domain_name": "www.example.com",
                "issuer": "Amazon",
                "not_after": str(now + timedelta(days=5)),
                "type": "AMAZON_ISSUED",
                "auto_renewal": False,
                "in_use": True,
                "key_algorithm": "RSA-2048",
            },
            {
                "arn": "arn:aws:acm:us-east-1:123456789012:certificate/aaaabbbb-1111-2222-3333-444444444443",
                "domain_name": "staging.example.com",
                "issuer": "Amazon",
                "not_after": str(now - timedelta(days=2)),
                "type": "AMAZON_ISSUED",
                "auto_renewal": True,
                "in_use": True,
                "key_algorithm": "RSA-2048",
            },
            {
                "arn": "arn:aws:acm:us-east-1:123456789012:certificate/aaaabbbb-1111-2222-3333-444444444444",
                "domain_name": "internal.corp.local",
                "issuer": "DigiCert Inc",
                "not_after": str(now + timedelta(days=45)),
                "type": "IMPORTED",
                "auto_renewal": False,
                "in_use": True,
                "key_algorithm": "RSA-4096",
            },
            {
                "arn": "arn:aws:acm:us-east-1:123456789012:certificate/aaaabbbb-1111-2222-3333-444444444445",
                "domain_name": "dev.example.com",
                "issuer": "Amazon",
                "not_after": str(now + timedelta(days=10)),
                "type": "AMAZON_ISSUED",
                "auto_renewal": True,
                "in_use": False,
                "key_algorithm": "ECDSA-P256",
            },
        ]
