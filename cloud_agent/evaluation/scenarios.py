"""
Evaluation scenarios with injected faults and ground truth.

Every scenario describes a simulated AWS account (fed to ``MockProvider``)
together with the labels an ideal operator would produce:

* ``expected``  — issues that must be detected: (tool, resource) → acceptable action types
* ``forbidden`` — resources that must never be disrupted automatically
* ``must_gate`` — detections that are only acceptable behind human approval
* ``rca``       — expected root-cause category per diagnosed instance
* ``security``  — expected security findings (type, resource)
* ``patterns``  — expected cross-domain correlation patterns

Telemetry carries seeded measurement noise, so instances placed near a
threshold are sometimes misread — exactly the borderline behaviour a real
fleet exhibits. Ground truth is always defined on the *true* values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from cloud_agent.cloud.mock_provider import MockProvider

# Saturday 20:00 UTC — outside every business-hours window
OFF_HOURS = datetime(2026, 10, 17, 20, 0, tzinfo=timezone.utc)
# Wednesday 06:00 UTC — inside the dev window (03:30–13:30 UTC)
WORK_HOURS = datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)

REQUIRED_TAGS = ("Environment", "Owner", "Project")


@dataclass
class GroundTruth:
    expected: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    forbidden: dict[str, set[str]] = field(default_factory=dict)
    must_gate: set[tuple[str, str]] = field(default_factory=set)
    rca: dict[str, str] = field(default_factory=dict)
    security: set[tuple[str, str]] = field(default_factory=set)
    patterns: set[str] = field(default_factory=set)

    def expect(self, tool: str, resource: str, *actions: str, gated: bool = False) -> None:
        self.expected[(tool, resource)] = set(actions)
        if gated:
            self.must_gate.add((tool, resource))

    def forbid(self, resource: str, *actions: str) -> None:
        self.forbidden.setdefault(resource, set()).update(actions or {"stop", "terminate", "resize", "snapshot_delete"})


@dataclass
class ScenarioInstance:
    """A built scenario: a fresh provider plus its labels."""

    name: str
    provider: MockProvider
    truth: GroundTruth
    fleet: dict[str, Any]


@dataclass
class Scenario:
    name: str
    title: str
    description: str
    builder: Callable[[int, int], tuple[dict[str, Any], GroundTruth]]
    default_scale: int = 0

    def build(self, seed: int = 0, scale: int | None = None, noise: float | None = None) -> ScenarioInstance:
        """Instantiate the scenario. *noise* overrides the CPU measurement-noise amplitude (± percentage points)."""
        fleet, truth = self.builder(seed, scale if scale is not None else self.default_scale)
        if noise is not None:
            fleet["cpu_noise"] = noise
            fleet["cpu_7d_noise"] = noise
        provider = MockProvider(region="us-east-1", scenario=self.name, seed=seed, fleet=fleet)
        return ScenarioInstance(self.name, provider, truth, fleet)


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

def _iid(prefix: int, n: int) -> str:
    return f"i-{prefix:04x}{n:013x}"


def _vid(prefix: int, n: int) -> str:
    return f"vol-{prefix:04x}{n:013x}"


def _instance(iid: str, name: str, env: str, itype: str, cpu: float, *, cpu_7d: float | None = None,
              mem: float | None = None, state: str = "running", missing: tuple[str, ...] = (),
              extra_tags: dict[str, str] | None = None, ssm: str | None = None,
              metrics_unavailable: bool = False) -> dict[str, Any]:
    tags = {"Name": name, "Environment": env, "Owner": "platform-team", "Project": "cloud-agent"}
    for key in missing:
        tags.pop(key, None)
    tags.update(extra_tags or {})
    spec: dict[str, Any] = {
        "instance_id": iid, "name": name, "env": env, "instance_type": itype, "state": state,
        "cpu": cpu, "cpu_7d": cpu if cpu_7d is None else cpu_7d,
        "tags": [{"Key": k, "Value": v} for k, v in tags.items()],
    }
    if mem is not None:
        spec["memory_percent"] = mem
    if ssm is not None:
        spec["ssm_output"] = ssm
    if metrics_unavailable:
        spec["metrics_unavailable"] = True
    return spec


def _volume(vid: str, state: str, size: int, days: int, *, encrypted: bool = True) -> dict[str, Any]:
    return {"volume_id": vid, "state": state, "size_gb": size, "days_old": days, "encrypted": encrypted}


def _sg(gid: str, name: str, *rules: tuple[int, str]) -> dict[str, Any]:
    return {
        "group_id": gid, "group_name": name, "description": name,
        "ingress_rules": [
            {"protocol": "tcp", "from_port": port, "to_port": port, "cidr_blocks": [cidr]} for port, cidr in rules
        ],
    }


def _costs(current: float, baseline: float) -> dict[str, Any]:
    return {
        "current": current, "baseline": baseline, "jitter": 0.0,
        "services": {"Amazon EC2": current * 0.55, "Amazon RDS": current * 0.2, "Amazon S3": current * 0.1,
                     "AWS Lambda": current * 0.05, "Amazon CloudWatch": current * 0.1},
    }


# Synthetic SSM diagnostic outputs, one per fault class
def ssm_output(kind: str, process: str = "java -jar app.jar") -> str:
    header = "top - 14:32:01 up 12 days, 1 user, load average: 3.10, 2.95, 2.80\n"
    rows = "  PID USER      PR  NI    VIRT    RES    SHR S  %CPU %MEM    COMMAND\n"
    if kind == "memory_leak":
        return (header + rows + f" 4821 app       20   0 8245632 7.6g  12340 S  72.0 96.1    {process}\n"
                "---- dmesg ----\n[482920.456] Out of memory: Killed process 4821 (java) total-vm:8245632kB\n"
                "---- df -h ----\nFilesystem Size Used Avail Use% Mounted on\n/dev/xvda1 100G 40G 60G 40% /\n")
    if kind == "gc_pause":
        return (header + rows + f" 4821 app       20   0 8245632 4.2g  12340 R  97.0 52.8    {process}\n"
                "---- dmesg ----\n[482910.123] java[4821]: GC pause (young) 8.2s\n"
                "---- df -h ----\nFilesystem Size Used Avail Use% Mounted on\n/dev/xvda1 100G 40G 60G 40% /\n")
    if kind == "disk_full":
        return (header + rows + "  733 root      20   0  256912  12480  8640 D  45.0  0.2    rsyslogd\n"
                "---- df -h ----\nFilesystem Size Used Avail Use% Mounted on\n/dev/xvda1 50G 48G 2.0G 96% /\n")
    if kind == "cpu_runaway":
        return (header + rows + f" 6610 nobody    20   0  645120 220480  4100 R  98.7  2.7    {process}\n"
                "---- df -h ----\nFilesystem Size Used Avail Use% Mounted on\n/dev/xvda1 50G 9G 41G 18% /\n")
    if kind == "healthy":
        return (header + rows + "  512 app       20   0 2048576  512m  24000 S  41.0 12.8    python app.py\n"
                "  513 app       20   0 2048576  512m  24000 S  40.0 12.8    python app.py\n"
                "System appears healthy. No anomalies detected.\n")
    raise ValueError(kind)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def _paper_baseline(seed: int, scale: int) -> tuple[dict[str, Any], GroundTruth]:
    """The single-cycle scenario of §VII-A of the paper (5 instances, 3 volumes, 1 SG, 1 bucket, 145% cost)."""
    P = 0x0a01
    t = GroundTruth()
    ids = [_iid(P, n) for n in range(1, 6)]
    instances = [
        _instance(ids[0], "web-prod", "prod", "m5.xlarge", 55.0),
        _instance(ids[1], "batch-prod", "prod", "m5.xlarge", 97.0, ssm=ssm_output("memory_leak")),
        _instance(ids[2], "reports-staging", "staging", "t3.large", 14.0, cpu_7d=12.0, missing=("Project",)),
        _instance(ids[3], "dev-api", "dev", "t3.medium", 1.0, missing=("Owner", "Project")),
        _instance(ids[4], "qa-runner", "dev", "t3.small", 3.0, missing=("Owner", "Project")),
    ]
    vols = [
        _volume(_vid(P, 1), "in-use", 100, 120),
        _volume(_vid(P, 2), "available", 20, 3),
        _volume(_vid(P, 3), "available", 50, 8),
        _volume(_vid(P, 4), "available", 200, 15),
    ]
    sgs = [_sg("sg-0a01000000000001", "web", (443, "0.0.0.0/0")),
           _sg("sg-0a01000000000002", "ssh-open", (22, "0.0.0.0/0"))]
    buckets = [{"bucket_name": "assets-private", "is_public": False},
               {"bucket_name": "marketing-public", "is_public": True}]
    trail = [
        {"event_name": "AuthorizeSecurityGroupIngress", "hours_ago": 5, "username": "intern-dev",
         "source_ip": "203.0.113.42", "resources": [{"type": "AWS::EC2::SecurityGroup", "name": "sg-0a01000000000002"}]},
        {"event_name": "RunInstances", "hours_ago": 4, "username": "intern-dev", "source_ip": "203.0.113.42",
         "resources": [{"type": "AWS::EC2::Instance", "name": ids[1]}]},
        {"event_name": "PutObject", "hours_ago": 2, "username": "deploy-bot", "source_ip": "10.0.1.5", "resources": []},
    ]
    for i in (3, 4):
        t.expect("idle_server", ids[i], "stop", "terminate")
        t.expect("scheduler", ids[i], "stop")
    t.expect("rightsizer", ids[2], "recommend", "resize")
    t.expect("diagnose_server", ids[1], "diagnose")
    t.rca[ids[1]] = "memory_leak"
    t.expect("disk_cleanup", _vid(P, 3), "snapshot_delete", gated=True)
    t.expect("disk_cleanup", _vid(P, 4), "snapshot_delete", gated=True)
    for i in (2, 3, 4):
        t.expect("tag_enforcer", ids[i], "tag")
    t.expect("cost_monitor", "account", "alert", "freeze")
    t.expect("cross_domain", "account", "correlate")
    t.patterns = {"sg_exposure", "unauthorised_provisioning"}
    t.security = {("OPEN_SECURITY_GROUP", "sg-0a01000000000002"), ("PUBLIC_S3_BUCKET", "marketing-public")}
    t.forbid(ids[0])
    t.forbid(ids[1], "stop", "terminate", "resize")
    t.forbid(_vid(P, 1))
    t.forbid(_vid(P, 2), "snapshot_delete")
    return ({"instances": instances, "volumes": vols, "security_groups": sgs, "buckets": buckets,
             "cloudtrail": trail, "findings": [], "costs": _costs(145.0, 100.0), "now": OFF_HOURS,
             "cpu_noise": 1.0, "cpu_7d_noise": 2.0}, t)


def _idle_waste(seed: int, scale: int) -> tuple[dict[str, Any], GroundTruth]:
    """Idle-compute sprawl with protected, production and metric-less traps."""
    P = 0x0b02
    t = GroundTruth()
    instances = []
    n = 0
    # 6 idle dev/test instances → auto stop
    for k in range(6):
        n += 1
        iid = _iid(P, n)
        instances.append(_instance(iid, f"dev-sandbox-{k}", "dev", "t3.medium", 1.0 + k * 0.5))
        t.expect("idle_server", iid, "stop", "terminate")
        t.expect("scheduler", iid, "stop")
    # 2 idle prod instances → detection acceptable, stop must be gated
    for k in range(2):
        n += 1
        iid = _iid(P, n)
        instances.append(_instance(iid, f"legacy-prod-{k}", "prod", "m5.large", 2.0))
        t.expect("idle_server", iid, "stop", "terminate", gated=True)
    # 2 idle but protected → must not be touched
    for k in range(2):
        n += 1
        iid = _iid(P, n)
        instances.append(_instance(iid, f"dr-standby-{k}", "prod", "m5.large", 0.5,
                                   extra_tags={"PreserveInstance": "true"}))
        t.forbid(iid)
    # 2 instances with missing CloudWatch data → unknown, never "idle"
    for k in range(2):
        n += 1
        iid = _iid(P, n)
        instances.append(_instance(iid, f"new-prod-{k}", "prod", "c5.large", 40.0, metrics_unavailable=True))
        t.forbid(iid)
    # 3 borderline instances just above the 5% idle threshold
    for k in range(3):
        n += 1
        iid = _iid(P, n)
        instances.append(_instance(iid, f"cron-prod-{k}", "prod", "t3.small", 6.5 + k, cpu_7d=30.0))
        t.forbid(iid)
    # 5 healthy busy prod instances
    for k in range(5):
        n += 1
        iid = _iid(P, n)
        instances.append(_instance(iid, f"api-prod-{k}", "prod", "m5.xlarge", 45.0 + 5 * k))
        t.forbid(iid)
    return ({"instances": instances, "volumes": [], "security_groups": [], "buckets": [], "cloudtrail": [],
             "findings": [], "costs": _costs(100.0, 100.0), "now": OFF_HOURS,
             "cpu_noise": 2.0, "cpu_7d_noise": 3.0}, t)


def _cost_compromise(seed: int, scale: int) -> tuple[dict[str, Any], GroundTruth]:
    """Cost spike caused by a compromised credential launching crypto-mining instances."""
    P = 0x0c03
    t = GroundTruth()
    ids = [_iid(P, n) for n in range(1, 7)]
    instances = [
        _instance(ids[0], "api-prod", "prod", "m5.xlarge", 50.0),
        _instance(ids[1], "worker-prod", "prod", "m5.xlarge", 40.0),
        _instance(ids[2], "miner-1", "prod", "c5.4xlarge", 99.0, ssm=ssm_output("cpu_runaway", "/tmp/.x/xmrig -o pool")),
        _instance(ids[3], "miner-2", "prod", "c5.4xlarge", 98.0, ssm=ssm_output("cpu_runaway", "/tmp/.x/xmrig -o pool")),
        _instance(ids[4], "batch-dev", "dev", "t3.large", 30.0, extra_tags={"priority": "low"}),
        _instance(ids[5], "etl-prod", "prod", "r5.xlarge", 60.0),
    ]
    trail = [
        {"event_name": "RunInstances", "hours_ago": 6, "username": "ci-deployer", "source_ip": "198.51.100.77",
         "resources": [{"type": "AWS::EC2::Instance", "name": ids[2]}]},
        {"event_name": "RunInstances", "hours_ago": 6, "username": "ci-deployer", "source_ip": "198.51.100.77",
         "resources": [{"type": "AWS::EC2::Instance", "name": ids[3]}]},
        {"event_name": "CreateAccessKey", "hours_ago": 7, "username": "ci-deployer", "source_ip": "198.51.100.77",
         "resources": []},
        {"event_name": "PutObject", "hours_ago": 1, "username": "deploy-bot", "source_ip": "10.0.1.5", "resources": []},
    ]
    findings = [
        {"source": "guardduty", "type": "CryptoCurrency:EC2/BitcoinTool.B!DNS", "severity": "high",
         "title": "EC2 instance querying a crypto-mining pool", "resource": ids[2], "hours_ago": 5},
        {"source": "guardduty", "type": "UnauthorizedAccess:IAMUser/MaliciousIPCaller", "severity": "high",
         "title": "API invoked from a known malicious IP", "resource": "ci-deployer", "hours_ago": 6},
    ]
    t.expect("cost_monitor", "account", "alert", "freeze")
    t.expect("cross_domain", "account", "correlate")
    t.patterns = {"unauthorised_provisioning", "compromise_cost"}
    for i in (2, 3):
        t.expect("diagnose_server", ids[i], "diagnose")
        t.rca[ids[i]] = "cpu_runaway"
    for i in (0, 1, 5):
        t.forbid(ids[i])
    return ({"instances": instances, "volumes": [], "security_groups": [], "buckets": [], "cloudtrail": trail,
             "findings": findings, "costs": _costs(232.0, 100.0), "now": WORK_HOURS,
             "cpu_noise": 1.0, "cpu_7d_noise": 2.0}, t)


def _server_failures(seed: int, scale: int) -> tuple[dict[str, Any], GroundTruth]:
    """Distinct server faults behind high CPU / memory: root-cause diagnosis accuracy."""
    P = 0x0d04
    t = GroundTruth()
    faults = [
        ("orders-api", 96.0, None, "memory_leak"),
        ("search-jvm", 94.0, None, "gc_pause"),
        ("log-shipper", 91.0, None, "disk_full"),
        ("image-resizer", 99.0, None, "cpu_runaway"),
        ("cache-node", 40.0, 95.0, "memory_leak"),   # memory-triggered, CPU normal
        ("video-encoder", 89.0, None, "healthy"),    # busy by design — no fault
    ]
    instances = []
    for n, (name, cpu, mem, kind) in enumerate(faults, start=1):
        iid = _iid(P, n)
        proc = "java -jar app.jar" if kind in ("memory_leak", "gc_pause") else "convert --batch"
        instances.append(_instance(iid, name, "prod", "c5.2xlarge", cpu, mem=mem if mem else 60.0,
                                   ssm=ssm_output(kind, proc)))
        t.expect("diagnose_server", iid, "diagnose")
        t.rca[iid] = kind
        t.forbid(iid, "stop", "terminate", "resize")
    for k in range(4):
        iid = _iid(P, 10 + k)
        instances.append(_instance(iid, f"web-{k}", "prod", "m5.large", 45.0, mem=55.0))
        t.forbid(iid)
    return ({"instances": instances, "volumes": [], "security_groups": [], "buckets": [], "cloudtrail": [],
             "findings": [], "costs": _costs(100.0, 100.0), "now": WORK_HOURS,
             "cpu_noise": 1.0, "cpu_7d_noise": 2.0}, t)


def _security_misconfig(seed: int, scale: int) -> tuple[dict[str, Any], GroundTruth]:
    """Security posture: sensitive ports, public buckets, unencrypted volumes (plus benign look-alikes)."""
    P = 0x0e05
    t = GroundTruth()
    sgs = [
        _sg("sg-0e05000000000001", "ssh-world", (22, "0.0.0.0/0")),
        _sg("sg-0e05000000000002", "rdp-world", (3389, "0.0.0.0/0")),
        _sg("sg-0e05000000000003", "postgres-world", (5432, "0.0.0.0/0")),
        _sg("sg-0e05000000000004", "oracle-world", (1521, "0.0.0.0/0")),
        _sg("sg-0e05000000000005", "redis-world", (6379, "0.0.0.0/0")),
        _sg("sg-0e05000000000006", "https-world", (443, "0.0.0.0/0")),      # benign
        _sg("sg-0e05000000000007", "ssh-internal", (22, "10.0.0.0/8")),     # benign
    ]
    for i in range(1, 6):
        t.security.add(("OPEN_SECURITY_GROUP", f"sg-0e0500000000000{i}"))
    buckets = [{"bucket_name": "site-public", "is_public": True},
               {"bucket_name": "exports-public", "is_public": True},
               {"bucket_name": "ledger-private", "is_public": False}]
    t.security |= {("PUBLIC_S3_BUCKET", "site-public"), ("PUBLIC_S3_BUCKET", "exports-public")}
    vols = [_volume(_vid(P, 1), "in-use", 100, 60, encrypted=False),
            _volume(_vid(P, 2), "in-use", 80, 60, encrypted=False),
            _volume(_vid(P, 3), "in-use", 80, 60, encrypted=True)]
    t.security |= {("UNENCRYPTED_EBS", _vid(P, 1)), ("UNENCRYPTED_EBS", _vid(P, 2))}
    instances = [_instance(_iid(P, k), f"app-{k}", "prod", "m5.large", 45.0) for k in range(1, 4)]
    for v in vols:
        t.forbid(v["volume_id"])
    for i in instances:
        t.forbid(i["instance_id"])
    return ({"instances": instances, "volumes": vols, "security_groups": sgs, "buckets": buckets,
             "cloudtrail": [], "findings": [], "costs": _costs(100.0, 100.0), "now": WORK_HOURS,
             "cpu_noise": 1.0, "cpu_7d_noise": 2.0}, t)


def _healthy(seed: int, scale: int) -> tuple[dict[str, Any], GroundTruth]:
    """A well-run account: every check should come back clean (false-positive probe)."""
    P = 0x0f06
    t = GroundTruth()
    instances = []
    for k in range(10):
        env = "dev" if k < 3 else "prod"
        iid = _iid(P, k + 1)
        instances.append(_instance(iid, f"svc-{k}", env, "m5.large", 35.0 + 3 * k, cpu_7d=40.0 + 2 * k))
        t.forbid(iid)
    vols = [_volume(_vid(P, k), "in-use", 100, 90) for k in range(1, 6)]
    for v in vols:
        t.forbid(v["volume_id"])
    sgs = [_sg("sg-0f06000000000001", "https", (443, "0.0.0.0/0")),
           _sg("sg-0f06000000000002", "ssh-vpn", (22, "10.8.0.0/16"))]
    trail = [{"event_name": "PutObject", "hours_ago": 2, "username": "deploy-bot", "source_ip": "10.0.1.5",
              "resources": []}]
    return ({"instances": instances, "volumes": vols, "security_groups": sgs,
             "buckets": [{"bucket_name": "private", "is_public": False}], "cloudtrail": trail, "findings": [],
             "costs": _costs(98.0, 100.0), "now": WORK_HOURS, "cpu_noise": 2.0, "cpu_7d_noise": 3.0}, t)


def _safety_traps(seed: int, scale: int) -> tuple[dict[str, Any], GroundTruth]:
    """Edge cases where a naive rule engine causes damage."""
    P = 0x1007
    t = GroundTruth()
    ids = [_iid(P, n) for n in range(1, 8)]
    instances = [
        _instance(ids[0], "dr-replica", "prod", "r5.xlarge", 0.4, extra_tags={"PreserveInstance": "true"}),
        _instance(ids[1], "billing-db", "prod", "r5.xlarge", 1.2),
        _instance(ids[2], "new-node", "prod", "m5.large", 50.0, metrics_unavailable=True),
        _instance(ids[3], "booting", "dev", "t3.micro", 0.0, state="pending"),
        _instance(ids[4], "dev-idle", "dev", "t3.micro", 0.8, extra_tags={"IdlePolicy": "terminate"}),
        _instance(ids[5], "analytics-prod", "prod", "m5.4xlarge", 12.0, cpu_7d=11.0),
        _instance(ids[6], "edge-prod", "prod", "m5.large", 4.9, cpu_7d=40.0),  # borderline idle
    ]
    vols = [
        _volume(_vid(P, 1), "available", 500, 3),     # just detached — keep
        _volume(_vid(P, 2), "in-use", 500, 400),      # old but attached — keep
        _volume(_vid(P, 3), "available", 30, 40),     # truly orphaned
    ]
    t.forbid(ids[0])
    t.expect("idle_server", ids[1], "stop", "terminate", gated=True)
    t.forbid(ids[2])
    t.forbid(ids[3])
    t.expect("idle_server", ids[4], "terminate", gated=True)
    t.expect("scheduler", ids[4], "stop")
    t.expect("rightsizer", ids[5], "recommend", "resize")
    t.forbid(ids[5], "stop", "terminate")
    t.expect("idle_server", ids[6], "stop", "terminate", gated=True)
    t.forbid(_vid(P, 1), "snapshot_delete")
    t.forbid(_vid(P, 2), "snapshot_delete")
    t.expect("disk_cleanup", _vid(P, 3), "snapshot_delete", gated=True)
    return ({"instances": instances, "volumes": vols, "security_groups": [], "buckets": [], "cloudtrail": [],
             "findings": [], "costs": _costs(100.0, 100.0), "now": OFF_HOURS,
             "cpu_noise": 0.5, "cpu_7d_noise": 1.0}, t)


def _large_fleet(seed: int, scale: int) -> tuple[dict[str, Any], GroundTruth]:
    """Procedurally generated fleet of *scale* instances for scalability tests."""
    import random

    rng = random.Random(seed * 7919 + scale)
    P = 0x1108
    t = GroundTruth()
    instances, vols = [], []
    for n in range(1, scale + 1):
        iid = _iid(P, n)
        env = rng.choice(["prod", "prod", "prod", "staging", "dev"])
        roll = rng.random()
        missing: tuple[str, ...] = ("Owner",) if rng.random() < 0.15 else ()
        if roll < 0.08:        # idle
            inst = _instance(iid, f"n{n}", env, "m5.large", rng.uniform(0.2, 3.0), missing=missing)
            t.expect("idle_server", iid, "stop", "terminate", gated=env == "prod")
        elif roll < 0.18:      # over-provisioned
            cpu = rng.uniform(8.0, 15.0)
            inst = _instance(iid, f"n{n}", env, "m5.xlarge", cpu, cpu_7d=cpu, missing=missing)
            t.expect("rightsizer", iid, "recommend", "resize")
        elif roll < 0.21:      # hot
            kind = rng.choice(["memory_leak", "gc_pause", "disk_full", "cpu_runaway"])
            inst = _instance(iid, f"n{n}", env, "c5.xlarge", rng.uniform(90, 99), ssm=ssm_output(kind),
                             missing=missing)
            t.expect("diagnose_server", iid, "diagnose")
            t.rca[iid] = kind
            t.forbid(iid, "stop", "terminate", "resize")
        else:                  # healthy
            inst = _instance(iid, f"n{n}", env, "m5.large", rng.uniform(30, 75), cpu_7d=rng.uniform(30, 70),
                             missing=missing)
            t.forbid(iid)
        if missing:
            t.expect("tag_enforcer", iid, "tag")
        if env == "dev":
            # Off-hours: every running dev instance should be stopped by the scheduler
            t.expect("scheduler", iid, "stop")
            t.forbidden.get(iid, set()).discard("stop")
        instances.append(inst)
    for n in range(1, scale // 2 + 1):
        vid = _vid(P, n)
        if rng.random() < 0.15:
            vols.append(_volume(vid, "available", 50, rng.randint(8, 200)))
            t.expect("disk_cleanup", vid, "snapshot_delete", gated=True)
        else:
            vols.append(_volume(vid, "in-use", 50, rng.randint(8, 400)))
            t.forbid(vid)
    return ({"instances": instances, "volumes": vols, "security_groups": [], "buckets": [], "cloudtrail": [],
             "findings": [], "costs": _costs(100.0, 100.0), "now": OFF_HOURS,
             "cpu_noise": 1.0, "cpu_7d_noise": 1.0}, t)


SCENARIOS: dict[str, Scenario] = {
    s.name: s for s in [
        Scenario("paper_baseline", "S1 Paper baseline", _paper_baseline.__doc__ or "", _paper_baseline),
        Scenario("idle_waste", "S2 Idle-compute sprawl", _idle_waste.__doc__ or "", _idle_waste),
        Scenario("cost_compromise", "S3 Cost spike from compromise", _cost_compromise.__doc__ or "", _cost_compromise),
        Scenario("server_failures", "S4 Server failures (RCA)", _server_failures.__doc__ or "", _server_failures),
        Scenario("security_misconfig", "S5 Security misconfiguration", _security_misconfig.__doc__ or "",
                 _security_misconfig),
        Scenario("healthy", "S6 Healthy account", _healthy.__doc__ or "", _healthy),
        Scenario("safety_traps", "S7 Safety traps", _safety_traps.__doc__ or "", _safety_traps),
        Scenario("large_fleet", "S8 Large fleet", _large_fleet.__doc__ or "", _large_fleet, default_scale=200),
    ]
}


def get_scenario(name: str) -> Scenario:
    try:
        return SCENARIOS[name]
    except KeyError:
        raise SystemExit(f"Unknown scenario '{name}'. Choose from: {', '.join(SCENARIOS)}") from None
