"""
Certificate Expiry Monitor — Track SSL/TLS certificate expiration.

Monitors ACM certificates and alerts before expiry to prevent outages.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


@register_tool("cert_monitor")
class CertMonitorTool(BaseTool):
    """Monitors SSL/TLS certificates for upcoming expiration."""

    def execute(self, action: Action) -> dict[str, Any]:
        action_type = action.action_type  # "check", "report", or "full_scan"

        logger.info(
            "[bold yellow]🔒 CERTIFICATE MONITOR[/bold yellow] — running %s",
            action_type,
        )

        cfg = self.config.get("tools", {}).get("cert_monitor", {})
        warning_days = cfg.get("warning_threshold_days", [30, 14, 7])
        
        results = {
            "tool": self.tool_name,
            "action_type": action_type,
            "certificates_checked": 0,
            "expiring_soon": [],
            "expired": [],
            "healthy": [],
            "summary": "",
        }

        try:
            certificates = self.provider.list_certificates()
        except Exception as exc:
            logger.error("Failed to list certificates: %s", exc)
            results["summary"] = f"Error: {exc}"
            return results

        results["certificates_checked"] = len(certificates)
        now = datetime.now().astimezone()

        for cert in certificates:
            cert_info = self._analyze_certificate(cert, warning_days, now)
            
            if cert_info["status"] == "expired":
                results["expired"].append(cert_info)
            elif cert_info["status"] == "expiring_soon":
                results["expiring_soon"].append(cert_info)
            else:
                results["healthy"].append(cert_info)

        # Generate summary
        if results["expired"]:
            results["summary"] = f"CRITICAL: {len(results['expired'])} expired certificate(s)!"
        elif results["expiring_soon"]:
            soonest = min(results["expiring_soon"], key=lambda x: x["days_until_expiry"])
            results["summary"] = (
                f"WARNING: {len(results['expiring_soon'])} certificate(s) expiring soon. "
                f"Soonest: {soonest['domain']} in {soonest['days_until_expiry']} days"
            )
        else:
            results["summary"] = f"All {len(certificates)} certificates are healthy"

        # Log findings
        if results["expired"]:
            for cert in results["expired"]:
                logger.error(
                    "[bold red]🔴 EXPIRED[/bold red] certificate: %s (expired %d days ago)",
                    cert["domain"], abs(cert["days_until_expiry"]),
                )
        
        if results["expiring_soon"]:
            for cert in results["expiring_soon"]:
                icon = "🔴" if cert["days_until_expiry"] <= 7 else "🟡"
                logger.warning(
                    "%s EXPIRING SOON %s — %d days remaining (expires: %s)",
                    icon, cert["domain"], cert["days_until_expiry"],
                    cert["expiry_date"].strftime("%Y-%m-%d"),
                )

        logger.info(
            "[bold]🔒 CERTIFICATE SCAN COMPLETE[/bold] — %s",
            results["summary"],
        )

        return results

    def _analyze_certificate(self, cert: dict[str, Any], 
                             warning_days: list[int], 
                             now: datetime) -> dict[str, Any]:
        """Analyze a single certificate for expiration status."""
        domain = cert.get("domain_name", cert.get("arn", "unknown"))
        arn = cert.get("arn", "")
        
        # Get expiry date
        expiry_date = cert.get("not_after")
        if isinstance(expiry_date, str):
            try:
                expiry_date = datetime.fromisoformat(expiry_date.replace("Z", "+00:00"))
            except Exception:
                expiry_date = datetime.max
        
        if not isinstance(expiry_date, datetime):
            expiry_date = datetime.max

        days_until_expiry = (expiry_date - now).days
        
        # Determine status
        if days_until_expiry < 0:
            status = "expired"
            severity = "critical"
        elif any(days_until_expiry <= d for d in warning_days):
            status = "expiring_soon"
            if days_until_expiry <= 7:
                severity = "critical"
            elif days_until_expiry <= 14:
                severity = "high"
            else:
                severity = "warning"
        else:
            status = "healthy"
            severity = "info"

        # Check issuer
        issuer = cert.get("issuer", "unknown")
        is_acm = "Amazon" in issuer or "ACM" in issuer
        
        # Check auto-renewal status
        auto_renewal = cert.get("auto_renewal", False)
        in_use = cert.get("in_use", True)

        return {
            "domain": domain,
            "arn": arn,
            "expiry_date": expiry_date,
            "days_until_expiry": days_until_expiry,
            "status": status,
            "severity": severity,
            "issuer": issuer,
            "is_acm": is_acm,
            "auto_renewal": auto_renewal,
            "in_use": in_use,
            "type": cert.get("type", "imported"),
            "key_algorithm": cert.get("key_algorithm", "unknown"),
            "recommendation": self._get_recommendation(status, days_until_expiry, is_acm, auto_renewal),
        }

    def _get_recommendation(self, status: str, days_left: int, 
                           is_acm: bool, auto_renewal: bool) -> str:
        """Generate remediation recommendation."""
        if status == "expired":
            if is_acm:
                return "IMMEDIATE: Certificate has expired. ACM should auto-renew if configured. Verify renewal status and deploy immediately."
            else:
                return "IMMEDIATE: Import renewed certificate and deploy to all associated resources (ALB, CloudFront, API Gateway)."
        
        if status == "expiring_soon":
            if days_left <= 7:
                urgency = "URGENT"
            else:
                urgency = "HIGH"
            
            if is_acm and auto_renewal:
                return f"{urgency}: Certificate expires in {days_left} days. ACM auto-renewal is enabled — verify renewal completes successfully."
            elif is_acm:
                return f"{urgency}: Enable auto-renewal for this ACM certificate or manually renew before expiration ({days_left} days)."
            else:
                return f"{urgency}: Import renewed certificate within {days_left} days. Consider migrating to ACM for auto-renewal."
        
        return "No action needed"
