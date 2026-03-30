"""
Tests for the SecurityAuditorTool.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.security_auditor import SecurityAuditorTool


def _make_tool() -> SecurityAuditorTool:
    provider = MagicMock()
    provider.describe_security_groups.return_value = [
        {
            "group_id": "sg-001", "group_name": "ssh-open",
            "description": "SSH open",
            "ingress_rules": [
                {"protocol": "tcp", "from_port": 22, "to_port": 22, "cidr_blocks": ["0.0.0.0/0"]},
            ],
        },
        {
            "group_id": "sg-002", "group_name": "internal",
            "description": "Internal",
            "ingress_rules": [
                {"protocol": "tcp", "from_port": 5432, "to_port": 5432, "cidr_blocks": ["10.0.0.0/8"]},
            ],
        },
    ]
    provider.list_s3_buckets_public_access.return_value = [
        {"bucket_name": "private-bucket", "is_public": False},
        {"bucket_name": "public-bucket", "is_public": True},
    ]
    provider.check_ebs_encryption.return_value = [
        {"volume_id": "vol-001", "size_gb": 100, "state": "in-use", "encrypted": False},
    ]
    config = {"tools": {"security_auditor": {"enabled": True}}}
    return SecurityAuditorTool(provider=provider, config=config)


class TestSecurityAuditor:
    def test_full_scan_finds_open_sg(self):
        tool = _make_tool()
        action = Action(tool_name="security_auditor", resource_id="account",
                        action_type="full_scan", reason="test")
        result = tool.execute(action)
        assert result["findings_count"] >= 1
        sg_findings = [f for f in result["findings"] if f["type"] == "OPEN_SECURITY_GROUP"]
        assert len(sg_findings) >= 1
        assert sg_findings[0]["resource"] == "sg-001"

    def test_full_scan_finds_public_s3(self):
        tool = _make_tool()
        action = Action(tool_name="security_auditor", resource_id="account",
                        action_type="full_scan", reason="test")
        result = tool.execute(action)
        s3_findings = [f for f in result["findings"] if f["type"] == "PUBLIC_S3_BUCKET"]
        assert len(s3_findings) == 1
        assert s3_findings[0]["resource"] == "public-bucket"

    def test_full_scan_finds_unencrypted_ebs(self):
        tool = _make_tool()
        action = Action(tool_name="security_auditor", resource_id="account",
                        action_type="full_scan", reason="test")
        result = tool.execute(action)
        ebs_findings = [f for f in result["findings"] if f["type"] == "UNENCRYPTED_EBS"]
        assert len(ebs_findings) == 1

    def test_ignores_internal_sg(self):
        tool = _make_tool()
        action = Action(tool_name="security_auditor", resource_id="account",
                        action_type="check_security_groups", reason="test")
        result = tool.execute(action)
        # sg-002 has internal CIDR only, should not appear
        sg_findings = [f for f in result["findings"] if f["resource"] == "sg-002"]
        assert len(sg_findings) == 0
