"""
Test bed 2 — emulated AWS (moto).

Replays evaluation scenarios as *real* boto3 resources inside moto's
in-process AWS emulator, then runs the production ``AWSProvider`` against
them. Unlike the scenario simulator, this exercises the actual AWS code
path: EC2 pagination, CloudWatch ``GetMetricStatistics``, Cost Explorer,
SSM ``SendCommand``, S3 public-access blocks, Security Hub and GuardDuty.

Emulator limits (reported in the results, not hidden):

* CloudTrail ``LookupEvents`` is not implemented by moto → correlation
  *patterns* are not scored here (the correlator still runs and degrades
  gracefully).
* SSM returns empty command output → root-cause *categories* are not scored
  (diagnosis is still triggered and scored as a detection).
* moto cannot hold an instance in ``pending``; the business-hours scheduler
  depends on wall-clock time, so it is disabled for this test bed.
"""

from __future__ import annotations

import copy
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cloud_agent.evaluation.metrics import aggregate, collateral_damage, resolved_fraction, score
from cloud_agent.evaluation.runner import _fmt, _table
from cloud_agent.evaluation.scenarios import SCENARIOS, GroundTruth
from cloud_agent.evaluation.systems import AgentSystem, NativeAWSBaseline, agent_config

_VOLUME_TAGS = [{"Key": "Environment", "Value": "prod"}, {"Key": "Owner", "Value": "platform-team"},
                {"Key": "Project", "Value": "cloud-agent"}]
MOTO_SCENARIOS = ["paper_baseline", "idle_waste", "security_misconfig", "safety_traps"]
REGION = "us-east-1"


def _seed_account(fleet: dict[str, Any], seed: int) -> dict[str, str]:
    """Create the scenario's resources in moto. Returns scenario-id → AWS-id mapping."""
    import random

    import boto3
    import requests
    from moto.core import DEFAULT_ACCOUNT_ID
    from moto.ec2.models import ec2_backends

    rng = random.Random(seed)
    ec2 = boto3.client("ec2", region_name=REGION)
    cw = boto3.client("cloudwatch", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
    now = datetime.now(timezone.utc)
    ids: dict[str, str] = {}

    for spec in fleet["instances"]:
        if spec["state"] not in ("running", "stopped"):
            continue
        tags = spec["tags"]
        # Root volumes are encrypted and fully tagged so that only the injected faults count
        inst = ec2.run_instances(
            ImageId=ami, MinCount=1, MaxCount=1, InstanceType=spec["instance_type"],
            BlockDeviceMappings=[{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 8, "Encrypted": True}}],
            TagSpecifications=[{"ResourceType": "instance", "Tags": tags},
                               {"ResourceType": "volume", "Tags": _VOLUME_TAGS}],
        )["Instances"][0]["InstanceId"]
        ids[spec["instance_id"]] = inst
        if spec["state"] == "stopped":
            ec2.stop_instances(InstanceIds=[inst])
        if spec.get("metrics_unavailable"):
            continue
        noise = fleet.get("cpu_noise", 1.0)
        points = []
        for minutes in (5, 12, 19, 26):   # last 30 minutes, 4 samples
            points.append((now - timedelta(minutes=minutes), spec["cpu"] + rng.uniform(-noise, noise)))
        for day in range(1, 7):           # daily samples for the 7-day average
            points.append((now - timedelta(days=day), spec["cpu_7d"] + rng.uniform(-noise, noise)))
        cw.put_metric_data(Namespace="AWS/EC2", MetricData=[
            {"MetricName": "CPUUtilization", "Dimensions": [{"Name": "InstanceId", "Value": inst}],
             "Timestamp": ts, "Value": max(0.0, min(100.0, v))} for ts, v in points
        ])
        if "memory_percent" in spec:
            cw.put_metric_data(Namespace="CWAgent", MetricData=[
                {"MetricName": "mem_used_percent", "Dimensions": [{"Name": "InstanceId", "Value": inst}],
                 "Timestamp": now - timedelta(minutes=5), "Value": spec["memory_percent"]}
            ])

    backend = ec2_backends[DEFAULT_ACCOUNT_ID][REGION]
    running = [ids[s["instance_id"]] for s in fleet["instances"]
               if s["instance_id"] in ids and s["state"] == "running"]
    for k, v in enumerate(fleet["volumes"]):
        vid = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=v["size_gb"],
                                Encrypted=v.get("encrypted", True),
                                TagSpecifications=[{"ResourceType": "volume", "Tags": _VOLUME_TAGS}])["VolumeId"]
        ids[v["volume_id"]] = vid
        if v["state"] == "in-use" and running:
            ec2.attach_volume(VolumeId=vid, InstanceId=running[k % len(running)], Device=f"/dev/sd{chr(102 + k)}")
        # EBS has no detach timestamp; the provider uses volume age, so age the volume.
        backend.volumes[vid].create_time = (now - timedelta(days=v["days_old"])).replace(tzinfo=None)

    for sg in fleet["security_groups"]:
        gid = ec2.create_security_group(GroupName=sg["group_name"], Description=sg["description"])["GroupId"]
        ids[sg["group_id"]] = gid
        ec2.authorize_security_group_ingress(GroupId=gid, IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": r["from_port"], "ToPort": r["to_port"],
             "IpRanges": [{"CidrIp": c} for c in r["cidr_blocks"]]} for r in sg["ingress_rules"]
        ])

    for b in fleet["buckets"]:
        s3.create_bucket(Bucket=b["bucket_name"])
        if not b["is_public"]:
            s3.put_public_access_block(Bucket=b["bucket_name"], PublicAccessBlockConfiguration={
                "BlockPublicAcls": True, "IgnorePublicAcls": True,
                "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
            })

    # Cost Explorer: queue results for (1-day total, 30-day baseline, 1-day by service), in call order
    costs = fleet.get("costs") or {"current": 100.0, "baseline": 100.0, "services": {}}
    day = {"Start": now.strftime("%Y-%m-%d"), "End": now.strftime("%Y-%m-%d")}

    def total(amount: float) -> dict[str, Any]:
        return {"ResultsByTime": [{"TimePeriod": day, "Estimated": False, "Groups": [],
                                   "Total": {"UnblendedCost": {"Amount": str(amount), "Unit": "USD"}}}]}
    by_service = {"ResultsByTime": [{"TimePeriod": day, "Estimated": False, "Total": {}, "Groups": [
        {"Keys": [svc], "Metrics": {"UnblendedCost": {"Amount": str(amt), "Unit": "USD"}}}
        for svc, amt in costs.get("services", {}).items()
    ]}]}
    resp = requests.post("http://motoapi.amazonaws.com/moto-api/static/ce/cost-and-usage-results",
                         json={"results": [total(costs["current"]), total(costs["baseline"] * 30), by_service]})
    resp.raise_for_status()
    return ids


def _map_truth(truth: GroundTruth, ids: dict[str, str]) -> GroundTruth:
    """Translate scenario resource IDs to the AWS IDs moto generated; drop unsupported units."""
    m = lambda r: ids.get(r, r)  # noqa: E731
    out = GroundTruth()
    for (tool, res), acts in truth.expected.items():
        if tool == "scheduler" or (tool not in ("cost_monitor", "cross_domain") and res not in ids):
            continue
        out.expected[(tool, m(res))] = set(acts)
    out.must_gate = {(t, m(r)) for t, r in truth.must_gate if (t, m(r)) in out.expected}
    out.forbidden = {m(r): set(a) for r, a in truth.forbidden.items() if r in ids}
    out.security = {(k, m(r)) for k, r in truth.security}
    return out


def _executed_from_state(before: dict[str, str], ec2: Any) -> list[dict[str, Any]]:
    """Reconstruct executed mutations by diffing instance / volume state in the emulator."""
    executed = []
    for res in ec2.describe_instances()["Reservations"]:
        for i in res["Instances"]:
            iid, state = i["InstanceId"], i["State"]["Name"]
            if before.get(iid) == "running" and state in ("stopped", "stopping"):
                executed.append({"action": "stop", "instance_id": iid})
            if before.get(iid) != "terminated" and state in ("terminated", "shutting-down"):
                executed.append({"action": "terminate", "instance_id": iid})
    live_vols = {v["VolumeId"] for v in ec2.describe_volumes()["Volumes"]}
    for vid, state in before.items():
        if vid.startswith("vol-") and vid not in live_vols:
            executed.append({"action": "delete_volume", "volume_id": vid})
    return executed


def run_moto_testbed(config: dict[str, Any], out_dir: str | Path = "results", seeds: int = 3) -> Path:
    import boto3
    from moto import mock_aws

    from cloud_agent.cloud.aws_provider import AWSProvider

    for k, v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                 "AWS_DEFAULT_REGION": REGION, "CLOUD_AGENT_REGIONS": REGION}.items():
        os.environ[k] = v
    cfg = agent_config(config)
    cfg["tools"]["scheduler"]["enabled"] = False
    cfg["tools"]["query_optimizer"]["enabled"] = False
    cfg["tools"]["cert_monitor"]["enabled"] = False

    rows, live_rows, raw = [], [], {}
    for sname in MOTO_SCENARIOS:
        for sysname in ("Native AWS (rules)", "Cloud Agentic AI (rule mode)"):
            scores, resolved, damage = [], [], []
            for seed in range(seeds):
                for live in (False, True):
                    with mock_aws():
                        scn = SCENARIOS[sname].build(seed=seed)
                        ids = _seed_account(scn.fleet, seed)
                        truth = _map_truth(scn.truth, ids)
                        provider = AWSProvider(region=REGION, regions=[REGION])
                        target = SimpleNamespace(provider=provider, truth=truth, fleet=scn.fleet)
                        ec2 = boto3.client("ec2", region_name=REGION)
                        before = {i["InstanceId"]: i["State"]["Name"]
                                  for r in ec2.describe_instances()["Reservations"] for i in r["Instances"]}
                        before.update({v["VolumeId"]: v["State"] for v in ec2.describe_volumes()["Volumes"]})
                        if sysname.startswith("Native"):
                            system = NativeAWSBaseline(cfg, live=False)
                            out = system.run(target)
                            if live:
                                for a in out.actions:
                                    if a["action_type"] == "stop":
                                        provider.stop_instance(a["resource_id"])
                        else:
                            system = AgentSystem(cfg, live=False)
                            if live:
                                system = _LiveAgent(cfg)
                            out = system.run(target)
                        if live:
                            out.executed = _executed_from_state(before, ec2)
                            resolved.append(resolved_fraction(out, truth))
                            damage.append(collateral_damage(out, truth))
                        else:
                            scores.append(score(out, truth))
            raw[f"{sname}/{sysname}"] = {"scores": [s.summary() for s in scores],
                                          "resolved": resolved, "collateral": damage}
            rows.append([SCENARIOS[sname].title, sysname,
                         _fmt(aggregate(s.precision for s in scores)), _fmt(aggregate(s.recall for s in scores)),
                         _fmt(aggregate(s.f1 for s in scores)), _fmt(aggregate(s.approval_compliance for s in scores)),
                         str(sum(s.unsafe_auto for s in scores)),
                         _fmt(aggregate(s.latency_ms for s in scores), pct=False, digits=0)])
            live_rows.append([SCENARIOS[sname].title, sysname, _fmt(aggregate(resolved)), str(sum(damage))])

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = [
        "# Cloud Agentic AI — Emulated-AWS Test Bed (moto)",
        "",
        f"AWSProvider + boto3 against moto {__import__('moto').__version__}, region {REGION}, {seeds} seeds. "
        "Scheduler, CloudTrail patterns and RCA categories are excluded (emulator limits, see module docstring).",
        "",
        "## Detection (dry-run)",
        "",
        _table(["Scenario", "System", "Precision %", "Recall %", "F1 %", "Approval compl. %",
                "Unsafe auto-actions", "Cycle latency (ms)"], rows),
        "",
        "## Live remediation (operator approves every queued action)",
        "",
        _table(["Scenario", "System", "Disruptive issues resolved %", "Collateral damage"], live_rows),
        "",
    ]
    path = out_dir / "results_moto.md"
    path.write_text("\n".join(md), encoding="utf-8")
    import json

    (out_dir / "results_moto.json").write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
    return path


class _LiveAgent(AgentSystem):
    """Agent in live mode on the emulator; approves everything it queues."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config, live=True)

    def run(self, scn: Any):  # type: ignore[override]
        from cloud_agent.main import CloudOpsAgent
        from cloud_agent.agent.approval import ApprovalMemory, ApprovalWorkflow
        from cloud_agent.utils.action_log import ActionLogger
        from cloud_agent.evaluation.systems import SystemOutput
        import tempfile

        cfg = copy.deepcopy(self.config)
        cfg["agent"]["dry_run"] = False
        with tempfile.TemporaryDirectory() as tmp:
            agent = CloudOpsAgent(cfg, provider=scn.provider, action_logger=ActionLogger(tmp),
                                  workflow=ApprovalWorkflow(cfg, ApprovalMemory(Path(tmp) / "a.json")))
            agent.run_once()
            for p in agent.workflow.memory.get_pending_approvals():
                agent.execute_approved(p["tool_name"], p["resource_id"], p["action_type"])
        return SystemOutput(mode="rules")
