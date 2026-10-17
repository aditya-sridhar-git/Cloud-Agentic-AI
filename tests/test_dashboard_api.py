"""Tests for the dashboard API: authentication, dry-run enforcement, approvals and Slack interactivity."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import cloud_agent.dashboard.app as dash
from cloud_agent.agent.approval import ApprovalMemory, ApprovalWorkflow
from cloud_agent.agent.baseagent import Action
from cloud_agent.cloud.mock_provider import MockProvider
from cloud_agent.utils.action_log import ActionLogger
from cloud_agent.utils.config import load_config


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def _make(dry_run: bool = True, token: str = ""):
        from cloud_agent.main import CloudOpsAgent

        config = load_config()
        config["agent"]["dry_run"] = dry_run
        provider = MockProvider(seed=3)
        agent = CloudOpsAgent(config, provider=provider, action_logger=ActionLogger(tmp_path),
                              workflow=ApprovalWorkflow(config, ApprovalMemory(tmp_path / "a.json")))
        monkeypatch.setattr(dash, "_agent", agent)
        monkeypatch.setattr(dash, "_DASHBOARD_TOKEN", token)
        monkeypatch.setattr(dash, "_pace_factor", 0.0)
        dash._run_agent_cycle()
        return TestClient(dash.app), agent, provider
    return _make


def test_status_reports_mode_and_actions(make_client):
    client, agent, _ = make_client()
    body = client.get("/api/status").json()
    assert body["dry_run"] is True
    assert any(a["requires_approval"] for a in body["actions"])


def test_token_required_for_mutations(make_client):
    client, _, _ = make_client(token="s3cret")
    assert client.post("/api/run-cycle").status_code == 401
    assert client.post("/api/run-cycle", headers={"X-Dashboard-Token": "wrong"}).status_code == 401
    assert client.post("/api/run-cycle", headers={"X-Dashboard-Token": "s3cret"}).status_code == 200
    assert client.get("/api/status").status_code == 200  # reads stay open


def test_approval_respects_dry_run(make_client):
    client, agent, provider = make_client(dry_run=True)
    body = client.post("/api/approve-action", json={
        "tool_name": "disk_cleanup", "resource_id": "vol-0a1b2c3d4e5f0002", "action_type": "snapshot_delete"}).json()
    assert body["status"] == "dry_run"
    assert provider.action_log == []


def test_approval_executes_when_live(make_client):
    client, agent, provider = make_client(dry_run=False)
    body = client.post("/api/approve-action", json={
        "tool_name": "disk_cleanup", "resource_id": "vol-0a1b2c3d4e5f0002", "action_type": "snapshot_delete"}).json()
    assert body["status"] == "deleted"
    assert any(e["action"] == "delete_volume" for e in provider.action_log)


def test_provisioning_blocked_in_dry_run(make_client):
    client, _, provider = make_client(dry_run=True)
    assert client.post("/api/allocate-instance", json={"instance_type": "t3.micro"}).json()["status"] == "dry_run"
    assert provider.action_log == []


def test_resolve_queue_and_reject(make_client):
    client, agent, _ = make_client(dry_run=False)
    agent.workflow.memory.add_pending(Action("idle_server", "i-0a1b2c3d4e5f60006", "stop"), "prod stop")
    assert len(client.get("/api/approvals").json()["pending"]) == 1
    body = client.post("/api/approvals/resolve", json={
        "tool_name": "idle_server", "resource_id": "i-0a1b2c3d4e5f60006", "action_type": "stop",
        "verdict": "rejected"}).json()
    assert body["result"]["status"] == "rejected"
    assert agent.workflow.memory.get_feedback()[-1]["verdict"] == "rejected"


def test_history_is_archived_not_deleted(make_client, tmp_path):
    client, agent, _ = make_client()
    agent.run_once()
    client.delete("/api/history")
    assert list(tmp_path.glob("actions.*.jsonl"))


def _slack_request(secret: str, payload: dict, ts: int | None = None):
    body = urlencode({"payload": json.dumps(payload)}).encode()
    ts = ts or int(time.time())
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    headers = {"X-Slack-Request-Timestamp": str(ts), "X-Slack-Signature": sig,
               "Content-Type": "application/x-www-form-urlencoded"}
    return body, headers


def test_slack_interactivity_signed(make_client, monkeypatch):
    client, agent, provider = make_client(dry_run=False)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    agent.workflow.memory.add_pending(Action("disk_cleanup", "vol-0a1b2c3d4e5f0002", "snapshot_delete"), "x")
    payload = {"user": {"username": "oncall"}, "actions": [{
        "action_id": "approve",
        "value": json.dumps({"tool_name": "disk_cleanup", "resource_id": "vol-0a1b2c3d4e5f0002",
                             "action_type": "snapshot_delete"})}]}

    body, headers = _slack_request("wrong-secret", payload)
    assert client.post("/api/slack/actions", content=body, headers=headers).status_code == 401
    body, headers = _slack_request("slack-secret", payload, ts=int(time.time()) - 3600)
    assert client.post("/api/slack/actions", content=body, headers=headers).status_code == 401

    body, headers = _slack_request("slack-secret", payload)
    resp = client.post("/api/slack/actions", content=body, headers=headers)
    assert resp.status_code == 200 and "approved" in resp.json()["text"]
    assert any(e["action"] == "delete_volume" for e in provider.action_log)


def test_refuses_public_bind_without_token(monkeypatch):
    monkeypatch.setattr(dash, "_DASHBOARD_TOKEN", "")

    class _Agent:
        config: dict = {}

    with pytest.raises(SystemExit):
        dash.run_dashboard(_Agent(), host="0.0.0.0")
