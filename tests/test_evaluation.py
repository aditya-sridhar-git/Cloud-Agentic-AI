"""Tests for the evaluation harness (scenarios, systems, metrics, runner) and the moto test bed."""

from __future__ import annotations

import importlib.util

import pytest

from cloud_agent.evaluation.metrics import score
from cloud_agent.evaluation.runner import Runner
from cloud_agent.evaluation.scenarios import SCENARIOS
from cloud_agent.evaluation.systems import AgentSystem, NativeAWSBaseline, agent_config, inject_reasoning_errors
from cloud_agent.utils.config import load_config


@pytest.fixture(scope="module")
def base():
    return load_config()


def test_scenarios_are_deterministic_per_seed():
    a = SCENARIOS["paper_baseline"].build(seed=4)
    b = SCENARIOS["paper_baseline"].build(seed=4)
    ids = [i["instance_id"] for i in a.provider.list_instances()]
    assert [a.provider.get_cpu_utilization(i) for i in ids] == [b.provider.get_cpu_utilization(i) for i in ids]


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_no_tool_errors_on_any_scenario(base, name):
    """Every tool must run cleanly on every scenario (catches wiring regressions)."""
    from cloud_agent.main import CloudOpsAgent
    import tempfile
    from cloud_agent.utils.action_log import ActionLogger

    scn = SCENARIOS[name].build(seed=0, scale=30 if name == "large_fleet" else None)
    with tempfile.TemporaryDirectory() as tmp:
        agent = CloudOpsAgent(agent_config(base), provider=scn.provider, action_logger=ActionLogger(tmp))
        results = agent.run_once()
    errors = [r for r in results if r["status"] == "error"]
    assert errors == []


def test_agent_beats_native_on_paper_baseline(base):
    scn = SCENARIOS["paper_baseline"].build(seed=0)
    agent = score(AgentSystem(agent_config(base)).run(scn), scn.truth)
    scn = SCENARIOS["paper_baseline"].build(seed=0)
    native = score(NativeAWSBaseline(base).run(scn), scn.truth)
    assert agent.f1 == 1.0 and agent.rca_accuracy == 1.0 and agent.approval_compliance == 1.0
    assert native.f1 < agent.f1


def test_guardrails_block_injected_errors(base):
    scn = SCENARIOS["safety_traps"].build(seed=1)
    out = AgentSystem(agent_config(base), injector=inject_reasoning_errors(1.0), injector_seed=1).run(scn)
    planned = {(a["tool"], a["resource_id"], a["action_type"]) for a in out.actions}
    assert out.injected and not (set(out.injected) & planned)


def test_injected_errors_pass_without_guardrails(base):
    scn = SCENARIOS["safety_traps"].build(seed=1)
    cfg = agent_config(base, safety__guardrails=False)
    out = AgentSystem(cfg, injector=inject_reasoning_errors(1.0), injector_seed=1).run(scn)
    planned = {(a["tool"], a["resource_id"], a["action_type"]) for a in out.actions}
    assert set(out.injected) & planned


def test_runner_writes_reports(base, tmp_path):
    runner = Runner(base, seeds=1, out_dir=tmp_path, quick=True)
    path = runner.run(["scenarios", "robustness"])
    text = path.read_text()
    assert "E1. Scenario evaluation" in text and "E5. Robustness" in text
    assert (tmp_path / "results.json").exists() and (tmp_path / "scenario_runs.csv").exists()


@pytest.mark.skipif(importlib.util.find_spec("moto") is None, reason="moto not installed")
def test_moto_testbed_paper_scenario(base, monkeypatch):
    from types import SimpleNamespace

    from moto import mock_aws

    from cloud_agent.cloud.aws_provider import AWSProvider
    from cloud_agent.evaluation.testbeds import _map_truth, _seed_account

    for k, v in {"AWS_ACCESS_KEY_ID": "t", "AWS_SECRET_ACCESS_KEY": "t", "AWS_DEFAULT_REGION": "us-east-1"}.items():
        monkeypatch.setenv(k, v)
    cfg = agent_config(base)
    cfg["tools"]["scheduler"]["enabled"] = False
    with mock_aws():
        scn = SCENARIOS["paper_baseline"].build(seed=0)
        truth = _map_truth(scn.truth, _seed_account(scn.fleet, 0))
        provider = AWSProvider(region="us-east-1", regions=["us-east-1"])
        out = AgentSystem(cfg).run(SimpleNamespace(provider=provider, truth=truth))
    s = score(out, truth)
    assert s.f1 == 1.0 and s.unsafe_auto == 0
