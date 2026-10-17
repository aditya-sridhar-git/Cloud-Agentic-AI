#!/usr/bin/env python3
"""
Test bed 3 — a real AWS sandbox account.

Creates a small, clearly-tagged set of resources with known faults, runs the
agent once in DRY-RUN against the account, scores the plan against the
injected faults, and (with --teardown) deletes everything it created.

    # 1. create resources (≈ US$0.02/hour: three t3.micro + two 1 GB gp3 volumes)
    python scripts/live_aws_testbed.py provision --region us-east-1 --yes
    # 2. wait ≥ 35 minutes so CloudWatch has a full idle window, then
    python scripts/live_aws_testbed.py evaluate  --region us-east-1
    # 3. always clean up
    python scripts/live_aws_testbed.py teardown  --region us-east-1 --yes

Use a sandbox account. Every resource is tagged CloudAgentTestbed=true and
teardown only touches resources carrying that tag. The security group has
no instances attached. EBS has no detach timestamp, so the orphaned-volume
check is evaluated with ``disk_cleanup.unattached_days = 0``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TAG = {"Key": "CloudAgentTestbed", "Value": "true"}
STATE_FILE = ROOT / "results" / "live_testbed_state.json"


def _tags(name: str, env: str, complete: bool) -> list[dict[str, str]]:
    tags = [TAG, {"Key": "Name", "Value": name}, {"Key": "Environment", "Value": env}]
    if complete:
        tags += [{"Key": "Owner", "Value": "testbed"}, {"Key": "Project", "Value": "cloud-agent"}]
    return tags


def provision(region: str) -> None:
    import boto3

    ec2 = boto3.client("ec2", region_name=region)
    ssm = boto3.client("ssm", region_name=region)
    ami = ssm.get_parameter(Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")[
        "Parameter"]["Value"]
    specs = [
        ("testbed-idle-dev", "dev", False, {}),                                   # idle + missing tags
        ("testbed-idle-protected", "prod", True, {"PreserveInstance": "true"}),  # idle but protected
        ("testbed-idle-prod", "prod", True, {}),                                 # idle prod → gated
    ]
    state: dict[str, object] = {"region": region, "instances": {}, "volumes": {}, "sg": None}
    for name, env, complete, extra in specs:
        tags = _tags(name, env, complete) + [{"Key": k, "Value": v} for k, v in extra.items()]
        iid = ec2.run_instances(
            ImageId=ami, InstanceType="t3.micro", MinCount=1, MaxCount=1,
            TagSpecifications=[{"ResourceType": "instance", "Tags": tags},
                               {"ResourceType": "volume", "Tags": _tags(name, env, True)}],
            BlockDeviceMappings=[{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 8, "Encrypted": True}}],
        )["Instances"][0]["InstanceId"]
        state["instances"][name] = iid  # type: ignore[index]
    az = ec2.describe_availability_zones()["AvailabilityZones"][0]["ZoneName"]
    for name, encrypted in (("testbed-orphan-encrypted", True), ("testbed-orphan-plain", False)):
        vid = ec2.create_volume(AvailabilityZone=az, Size=1, VolumeType="gp3", Encrypted=encrypted,
                                TagSpecifications=[{"ResourceType": "volume",
                                                    "Tags": _tags(name, "dev", True)}])["VolumeId"]
        state["volumes"][name] = vid  # type: ignore[index]
    vpc = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    gid = ec2.create_security_group(GroupName=f"cloud-agent-testbed-{int(time.time())}", VpcId=vpc,
                                    Description="Cloud Agentic AI testbed — no instances attached",
                                    TagSpecifications=[{"ResourceType": "security-group", "Tags": [TAG]}])["GroupId"]
    ec2.authorize_security_group_ingress(GroupId=gid, IpPermissions=[
        {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
    state["sg"] = gid
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    print(f"Provisioned. State in {STATE_FILE}. Wait ≥ 35 minutes, then run `evaluate`.")


def evaluate(region: str) -> None:
    from cloud_agent.cloud.aws_provider import AWSProvider
    from cloud_agent.evaluation.metrics import SCORED_TOOLS, score
    from cloud_agent.evaluation.scenarios import GroundTruth
    from cloud_agent.evaluation.systems import AgentSystem, NativeAWSBaseline, agent_config
    from cloud_agent.utils.config import load_config
    from cloud_agent.utils.logger import set_global_level
    from types import SimpleNamespace

    set_global_level("WARNING")
    st = json.loads(STATE_FILE.read_text())
    inst, vols = st["instances"], st["volumes"]
    truth = GroundTruth()
    truth.expect("idle_server", inst["testbed-idle-dev"], "stop", "terminate")
    truth.expect("tag_enforcer", inst["testbed-idle-dev"], "tag")
    truth.expect("idle_server", inst["testbed-idle-prod"], "stop", "terminate", gated=True)
    truth.forbid(inst["testbed-idle-protected"])
    for vid in vols.values():
        truth.expect("disk_cleanup", vid, "snapshot_delete", gated=True)
    truth.security = {("OPEN_SECURITY_GROUP", st["sg"]), ("UNENCRYPTED_EBS", vols["testbed-orphan-plain"])}

    cfg = agent_config(load_config())
    cfg["tools"]["disk_cleanup"]["unattached_days"] = 0
    for tool in ("scheduler", "query_optimizer", "cert_monitor", "rightsizer"):
        cfg["tools"][tool]["enabled"] = False

    ours = set(inst.values()) | set(vols.values()) | {st["sg"], "account"}
    report = {}
    for name, system in (("native", NativeAWSBaseline(cfg)), ("agent_rules", AgentSystem(cfg))):
        provider = AWSProvider(region=region, regions=[region])
        out = system.run(SimpleNamespace(provider=provider, truth=truth))
        # Only score resources created by the testbed (the account may contain others)
        out.actions = [a for a in out.actions if a["resource_id"] in ours and a["tool"] in SCORED_TOOLS]
        out.findings = {f for f in out.findings if f[1] in ours}
        s = score(out, truth)
        report[name] = s.summary()
        print(f"{name:12} P={s.precision:.2f} R={s.recall:.2f} F1={s.f1:.2f} "
              f"approval={s.approval_compliance:.2f} unsafe_auto={s.unsafe_auto} latency={s.latency_ms:.0f}ms")
    (STATE_FILE.parent / "results_live_aws.json").write_text(json.dumps(report, indent=2, default=str))


def teardown(region: str) -> None:
    import boto3

    ec2 = boto3.client("ec2", region_name=region)
    flt = [{"Name": f"tag:{TAG['Key']}", "Values": [TAG["Value"]]}]
    ids = [i["InstanceId"] for r in ec2.describe_instances(Filters=flt)["Reservations"] for i in r["Instances"]
           if i["State"]["Name"] != "terminated"]
    if ids:
        ec2.terminate_instances(InstanceIds=ids)
        ec2.get_waiter("instance_terminated").wait(InstanceIds=ids)
    for v in ec2.describe_volumes(Filters=flt)["Volumes"]:
        if v["State"] == "available":
            ec2.delete_volume(VolumeId=v["VolumeId"])
    for sg in ec2.describe_security_groups(Filters=flt)["SecurityGroups"]:
        ec2.delete_security_group(GroupId=sg["GroupId"])
    print(f"Removed {len(ids)} instance(s) and tagged volumes / security groups.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["provision", "evaluate", "teardown"])
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--yes", action="store_true", help="Confirm creating/deleting real AWS resources")
    args = parser.parse_args()
    if args.command in ("provision", "teardown") and not args.yes:
        raise SystemExit("This changes real AWS resources. Re-run with --yes in a sandbox account.")
    {"provision": provision, "evaluate": evaluate, "teardown": teardown}[args.command](args.region)


if __name__ == "__main__":
    main()
