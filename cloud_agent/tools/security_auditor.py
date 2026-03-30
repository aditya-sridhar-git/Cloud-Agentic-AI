"""
Security Auditor Tool — detect and auto-remediate security misconfigurations.

Checks:
  1. Open security groups (0.0.0.0/0 on SSH/RDP)
  2. Public S3 buckets
  3. Unencrypted EBS volumes
"""

from __future__ import annotations

from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Ports that should NEVER be open to the world
_DANGEROUS_PORTS = {22, 3389, 5432, 3306, 1433, 6379, 27017, 9200}


@register_tool("security_auditor")
class SecurityAuditorTool(BaseTool):
    """Scans for security misconfigurations and reports findings."""

    def execute(self, action: Action) -> dict[str, Any]:
        check_type = action.action_type  # "full_scan" or specific check

        logger.info(
            "[bold red]🛡️  SECURITY AUDIT[/bold red] — running %s",
            check_type,
        )

        findings: list[dict[str, Any]] = []

        if check_type in ("full_scan", "check_security_groups"):
            findings.extend(self._check_security_groups())

        if check_type in ("full_scan", "check_s3_public"):
            findings.extend(self._check_s3_public())

        if check_type in ("full_scan", "check_ebs_encryption"):
            findings.extend(self._check_ebs_encryption())

        severity = "critical" if any(f["severity"] == "critical" for f in findings) else "warning"

        logger.info(
            "[bold]🛡️  AUDIT COMPLETE[/bold] — %d finding(s) [%s]",
            len(findings), severity,
        )
        for f in findings:
            icon = "🔴" if f["severity"] == "critical" else "🟡"
            logger.info("  %s %s: %s — %s", icon, f["type"], f["resource"], f["detail"])

        return {
            "tool": self.tool_name,
            "status": "audit_complete",
            "findings_count": len(findings),
            "findings": findings,
            "severity": severity,
        }

    def _check_security_groups(self) -> list[dict[str, Any]]:
        """Find security groups with dangerous ports open to 0.0.0.0/0."""
        findings = []
        try:
            sgs = self.provider.describe_security_groups()
        except Exception as exc:
            logger.warning("Could not fetch security groups: %s", exc)
            return []

        for sg in sgs:
            for rule in sg.get("ingress_rules", []):
                cidrs = rule.get("cidr_blocks", [])
                if "0.0.0.0/0" not in cidrs:
                    continue
                from_port = rule.get("from_port", 0)
                to_port = rule.get("to_port", 65535)
                # Check if any dangerous port falls in the range
                for port in _DANGEROUS_PORTS:
                    if from_port <= port <= to_port:
                        findings.append({
                            "type": "OPEN_SECURITY_GROUP",
                            "severity": "critical",
                            "resource": sg["group_id"],
                            "detail": (
                                f"Port {port} open to 0.0.0.0/0 in SG '{sg['group_name']}'. "
                                f"This allows unrestricted access from the internet."
                            ),
                            "recommendation": f"Restrict port {port} to specific CIDR blocks",
                        })
        return findings

    def _check_s3_public(self) -> list[dict[str, Any]]:
        """Find S3 buckets with public access."""
        findings = []
        try:
            buckets = self.provider.list_s3_buckets_public_access()
        except Exception as exc:
            logger.warning("Could not check S3 buckets: %s", exc)
            return []

        for b in buckets:
            if b.get("is_public"):
                findings.append({
                    "type": "PUBLIC_S3_BUCKET",
                    "severity": "critical",
                    "resource": b["bucket_name"],
                    "detail": f"Bucket '{b['bucket_name']}' has public access enabled",
                    "recommendation": "Enable S3 Block Public Access on this bucket",
                })
        return findings

    def _check_ebs_encryption(self) -> list[dict[str, Any]]:
        """Find unencrypted EBS volumes."""
        findings = []
        try:
            volumes = self.provider.check_ebs_encryption()
        except Exception as exc:
            logger.warning("Could not check EBS encryption: %s", exc)
            return []

        for v in volumes:
            findings.append({
                "type": "UNENCRYPTED_EBS",
                "severity": "warning",
                "resource": v["volume_id"],
                "detail": f"Volume {v['volume_id']} ({v['size_gb']}GB) is not encrypted",
                "recommendation": "Create an encrypted copy and replace",
            })
        return findings
