"""Tests for the rule engine additions and paper-aligned tool behaviour."""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from cloud_agent.agent.baseagent import Action, Observation
from cloud_agent.cloud.mock_provider import MockProvider
from cloud_agent.monitor.evaluator import ThresholdEvaluator
from cloud_agent.tools.cost_monitor import CostMonitorTool
from cloud_agent.tools.cross_domain import CrossDomainCorrelationTool
from cloud_agent.tools.diagnose_server import DiagnoseServerTool
from cloud_agent.tools.disk_cleanup import DiskCleanupTool
from cloud_agent.tools.scheduler import _is_business_hours, hours_for_instance
from cloud_agent.tools.security_auditor import SecurityAuditorTool
from cloud_agent.utils.config import load_config


@pytest.fixture
def config():
    return copy.deepcopy(load_config())


def _inst(iid, cpu, env="prod", itype="m5.xlarge", **extra):
    tags = [{"Key": "Environment", "Value": env}, {"Key": "Owner", "Value": "x"}, {"Key": "Project", "Value": "y"}]
    tags += [{"Key": k, "Value": v} for k, v in extra.pop("tags", {}).items()]
    return {"instance_id": iid, "state": "running", "cpu_percent": cpu, "instance_type": itype, "tags": tags, **extra}


class TestEvaluatorRules:
    def test_rightsizing_rule(self, config):
        obs = Observation(instances=[_inst("i-1", 12.0, cpu_percent_7d=11.0), _inst("i-2", 60.0, cpu_percent_7d=55.0)])
        actions = ThresholdEvaluator(config)._check_rightsizing(obs)
        assert [(a.resource_id, a.action_type, a.parameters["recommended_type"]) for a in actions] == \
            [("i-1", "recommend", "m5.large")]

    def test_memory_triggers_diagnosis(self, config):
        obs = Observation(instances=[_inst("i-1", 30.0, memory_percent=95.0)])
        actions = ThresholdEvaluator(config)._check_diagnosis(obs)
        assert actions and "memory" in actions[0].reason

    def test_single_diagnosis_per_instance(self, config):
        obs = Observation(instances=[_inst("i-1", 97.0, memory_percent=95.0)])
        actions = [a for a in ThresholdEvaluator(config).evaluate(obs) if a.tool_name == "diagnose_server"]
        assert len(actions) == 1

    def test_per_instance_idle_policy(self, config):
        obs = Observation(instances=[_inst("i-1", 1.0, tags={"IdlePolicy": "terminate"}), _inst("i-2", 1.0)])
        actions = {a.resource_id: a.action_type for a in ThresholdEvaluator(config)._check_idle_servers(obs)}
        assert actions == {"i-1": "terminate", "i-2": "stop"}

    def test_missing_metrics_never_idle(self, config):
        obs = Observation(instances=[_inst("i-1", -1.0)])
        assert ThresholdEvaluator(config)._check_idle_servers(obs) == []

    def test_scheduler_per_environment_utc(self, config):
        cfg = config["tools"]["scheduler"]
        dev = {"tags": [{"Key": "Environment", "Value": "dev"}]}
        window = hours_for_instance(cfg, dev)
        assert window["start"] == "03:30" and window["timezone"] == "UTC"
        assert hours_for_instance(cfg, {"tags": [{"Key": "Environment", "Value": "prod"}]}) is None
        wednesday_6utc = datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)
        saturday = datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc)
        assert _is_business_hours(window, now=wednesday_6utc)
        assert not _is_business_hours(window, now=saturday)


class TestPaperAlignedTools:
    def test_security_ports_from_config_include_oracle(self, config):
        provider = MockProvider(fleet={
            "instances": [], "volumes": [], "buckets": [], "security_groups": [{
                "group_id": "sg-1", "group_name": "oracle", "description": "",
                "ingress_rules": [{"protocol": "tcp", "from_port": 1521, "to_port": 1521, "cidr_blocks": ["0.0.0.0/0"]}],
            }],
        })
        result = SecurityAuditorTool(provider, config).execute(Action("security_auditor", "account", "full_scan"))
        assert any("1521" in f["detail"] for f in result["findings"])

    def test_freeze_targets_priority_low_and_never_prod(self, config):
        def spec(iid, env, prio):
            tags = [{"Key": "Environment", "Value": env}, {"Key": "priority", "Value": prio}]
            return {"instance_id": iid, "name": iid, "env": env, "instance_type": "t3.micro", "state": "running",
                    "cpu": 20.0, "tags": tags}
        provider = MockProvider(fleet={"instances": [spec("i-low", "dev", "low"), spec("i-high", "dev", "high"),
                                                     spec("i-prod-low", "prod", "low")],
                                       "volumes": [], "security_groups": [], "buckets": []})
        result = CostMonitorTool(provider, config).execute(Action("cost_monitor", "account", "freeze"))
        assert result["frozen_instances"] == ["i-low"]

    def test_diagnose_does_not_remediate_by_default(self, config):
        config["agent"]["dry_run"] = False
        provider = MockProvider(seed=1)
        provider.cleanup_logs = lambda *a, **k: pytest.fail("remediation must be disabled")  # type: ignore[attr-defined]
        tool = DiagnoseServerTool(provider, config)
        tool._analyse_with_llm = lambda *a: {"root_cause": "disk space", "safe_to_auto_remediate": True}
        result = tool.execute(Action("diagnose_server", "i-0a1b2c3d4e5f60005", "diagnose"))
        assert result["remediation"]["status"] == "skipped"

    def test_diagnose_remediates_only_when_enabled_and_live(self, config):
        config["agent"]["dry_run"] = False
        config["tools"]["diagnose_server"]["auto_remediate"] = True
        calls = []
        provider = MockProvider(seed=1)
        provider.cleanup_logs = lambda iid, *a, **k: calls.append(iid) or "ok"  # type: ignore[attr-defined]
        tool = DiagnoseServerTool(provider, config)
        tool._analyse_with_llm = lambda *a: {"root_cause": "disk space low", "safe_to_auto_remediate": True}
        tool.execute(Action("diagnose_server", "i-0a1b2c3d4e5f60005", "diagnose"))
        assert calls == ["i-0a1b2c3d4e5f60005"]
        config["agent"]["dry_run"] = True
        tool.execute(Action("diagnose_server", "i-0a1b2c3d4e5f60005", "diagnose"))
        assert calls == ["i-0a1b2c3d4e5f60005"]

    @pytest.mark.parametrize("kind", ["memory_leak", "gc_pause", "disk_full", "cpu_runaway", "healthy"])
    def test_rule_based_rca_categories(self, kind):
        from cloud_agent.evaluation.scenarios import ssm_output

        assert DiagnoseServerTool._rule_based_diagnosis(ssm_output(kind, "convert --batch"))["category"] == kind

    def test_oom_not_matched_inside_words(self):
        out = "top ... 12 zoom.us processes\nroom service healthy\nSystem appears healthy. No anomalies detected."
        assert DiagnoseServerTool._rule_based_diagnosis(out)["category"] == "healthy"

    def test_disk_cleanup_refuses_attached_volume(self, config):
        provider = MockProvider(seed=1)
        result = DiskCleanupTool(provider, config).execute(
            Action("disk_cleanup", "vol-0a1b2c3d4e5f0001", "snapshot_delete"))
        assert result["status"] == "refused"
        assert provider.action_log == []

    def test_correlator_patterns(self, config):
        from cloud_agent.evaluation.scenarios import SCENARIOS

        scn = SCENARIOS["cost_compromise"].build(seed=0)
        result = CrossDomainCorrelationTool(scn.provider, config).execute(
            Action("cross_domain", "account", "correlate", reason="cost spike"))
        assert set(result["correlation"]["patterns"]) == {"unauthorised_provisioning", "compromise_cost"}
