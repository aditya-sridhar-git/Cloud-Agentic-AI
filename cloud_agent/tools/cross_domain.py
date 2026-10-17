"""
Cross-Domain Correlation Tool — connect the dots across AWS services.

When a cost spike or anomaly is detected, this tool queries CloudTrail,
security groups, and other sources, then uses the LLM to find patterns
that span multiple AWS domains (cost + security + infrastructure).
"""

from __future__ import annotations

import json
import os
from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

_CORRELATION_PROMPT = """\
You are a cloud security and cost analyst. I'm providing data from multiple
AWS sources collected after detecting an anomaly. Your job is to find
correlations and patterns across domains.

ANOMALY TRIGGER: {trigger}

--- CLOUDTRAIL EVENTS (last 24h) ---
{cloudtrail_data}

--- SECURITY GROUPS ---
{security_groups}

--- SECURITY HUB / GUARDDUTY FINDINGS ---
{security_findings}

--- COST DATA ---
{cost_data}

--- INSTANCE DATA ---
{instance_data}

Analyse the cross-domain data and identify:
1. **Correlation**: Are events in CloudTrail related to the cost/security anomaly?
2. **Root Cause**: What likely caused the anomaly?
3. **Actors**: Which IAM users/roles are involved?
4. **Recommendation**: What should be done?

Respond in JSON:
{{
  "correlation_found": true/false,
  "patterns": ["sg_exposure|unauthorised_provisioning|compromise_cost|cost_only"],
  "summary": "1-2 sentence finding",
  "root_cause": "...",
  "actors": ["user1", "user2"],
  "affected_resources": ["resource1", "resource2"],
  "severity": "critical|warning|info",
  "recommendations": ["action1", "action2"],
  "explanation": "Detailed 3-4 sentence explanation connecting the dots"
}}
"""


@register_tool("cross_domain")
class CrossDomainCorrelationTool(BaseTool):
    """Correlate events across CloudTrail, costs, security, and infrastructure."""

    def execute(self, action: Action) -> dict[str, Any]:
        trigger = action.reason

        logger.info(
            "[bold blue]🔗 CROSS-DOMAIN ANALYSIS[/bold blue] — triggered by: %s",
            trigger,
        )

        # Step 1: Gather data from multiple sources
        cloudtrail_events = self._get_cloudtrail(action)
        security_groups = self._get_security_groups()
        cost_data = self._get_cost_context()
        instance_data = self._get_instance_context()
        findings = self._get_security_findings()

        # Step 2: Correlate with LLM (or rule-based fallback)
        correlation = self._correlate(
            trigger, cloudtrail_events, security_groups, cost_data, instance_data, findings,
        )

        severity = correlation.get("severity", "info")
        logger.info(
            "[bold blue]🔗 CORRELATION RESULT[/bold blue]:\n"
            "  Found: %s\n"
            "  Summary: %s\n"
            "  Severity: %s",
            correlation.get("correlation_found", False),
            correlation.get("summary", "No correlation found"),
            severity,
        )

        return {
            "tool": self.tool_name,
            "status": "analysis_complete",
            "trigger": trigger,
            "correlation": correlation,
        }

    def _get_cloudtrail(self, action: Action) -> list[dict[str, Any]]:
        try:
            return self.provider.get_cloudtrail_events(hours=24)
        except Exception as exc:
            logger.warning("CloudTrail query failed: %s", exc)
            return []

    def _get_security_groups(self) -> list[dict[str, Any]]:
        try:
            return self.provider.describe_security_groups()
        except Exception as exc:
            logger.warning("SG query failed: %s", exc)
            return []

    def _get_security_findings(self) -> list[dict[str, Any]]:
        try:
            return self.provider.get_security_findings(hours=24)
        except Exception as exc:
            logger.warning("Security Hub / GuardDuty query failed: %s", exc)
            return []
    def _get_cost_context(self) -> dict[str, Any]:
        days = self.config.get("tools", {}).get("cost_monitor", {}).get("baseline_window_days", 30)
        try:
            ctx = {
                "current_daily": self.provider.get_daily_cost(days=1),
                "baseline": self.provider.get_cost_baseline(days=days),
            }
        except Exception:
            return {}
        try:
            ctx["services"] = self.provider.get_cost_by_service(days=1)
        except Exception:
            pass
        return ctx

    def _get_instance_context(self) -> list[dict[str, Any]]:
        try:
            return self.provider.list_instances()
        except Exception:
            return []

    def _correlate(self, trigger: str, cloudtrail: list, sgs: list,
                   costs: dict, instances: list, findings: list | None = None) -> dict[str, Any]:
        """Use LLM or rule-based fallback to find cross-domain patterns."""
        findings = findings or []
        api_key = os.getenv("OPENAI_API_KEY")

        if api_key and self.config.get("llm", {}).get("enabled", True):
            try:
                return self._llm_correlate(trigger, cloudtrail, sgs, costs, instances, findings)
            except Exception as exc:
                logger.warning("LLM correlation failed: %s", exc)

        return self._rule_based_correlate(trigger, cloudtrail, sgs, costs, instances, findings)

    def _llm_correlate(self, trigger: str, cloudtrail: list, sgs: list,
                       costs: dict, instances: list, findings: list) -> dict[str, Any]:
        from openai import OpenAI  # noqa: E402
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        prompt = _CORRELATION_PROMPT.format(
            trigger=trigger,
            cloudtrail_data=json.dumps(cloudtrail[:20], default=str, indent=2),
            security_groups=json.dumps(sgs[:10], default=str, indent=2),
            security_findings=json.dumps(findings[:20], default=str, indent=2),
            cost_data=json.dumps(costs, default=str, indent=2),
            instance_data=json.dumps(instances[:10], default=str, indent=2),
        )

        response = client.chat.completions.create(
            model=self.config.get("llm", {}).get("model", "gpt-4o"),
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        result = json.loads(response.choices[0].message.content)
        result.setdefault("patterns", [])
        return result

    @staticmethod
    def _rule_based_correlate(trigger: str, cloudtrail: list, sgs: list,
                              costs: dict, instances: list,
                              findings: list | None = None) -> dict[str, Any]:
        """Deterministic cross-domain correlation used when no LLM is available.

        Detects three cross-service patterns and reports all that apply:

        * ``sg_exposure``             — CloudTrail SG change + SG now open to the internet
        * ``unauthorised_provisioning`` — cost spike + resource-creating API calls from
          an external IP address
        * ``compromise_cost``         — cost spike + Security Hub / GuardDuty findings
        """
        findings = findings or []
        result: dict[str, Any] = {
            "correlation_found": False,
            "patterns": [],
            "summary": "No cross-domain correlation found",
            "root_cause": "",
            "actors": [],
            "affected_resources": [],
            "severity": "info",
            "recommendations": [],
            "explanation": "",
        }

        baseline = costs.get("baseline", 0)
        current = costs.get("current_daily", 0)
        cost_spike = baseline > 0 and current > baseline * 1.2
        explanations: list[str] = []
        severities: list[str] = []

        # Pattern 1: security-group change that opened sensitive ports
        sg_changes = [e for e in cloudtrail if "SecurityGroup" in e.get("event_name", "")]
        changed_ids = {r.get("name") for e in sg_changes for r in e.get("resources", [])}
        open_sgs = [
            sg for sg in sgs
            if any("0.0.0.0/0" in r.get("cidr_blocks", []) and r.get("from_port", 0) in (22, 3389, 5432, 1521)
                   for r in sg.get("ingress_rules", []))
        ]
        exposed = [sg for sg in open_sgs if sg["group_id"] in changed_ids] or (open_sgs if sg_changes else [])
        if sg_changes and exposed:
            actors = sorted({e.get("username", "unknown") for e in sg_changes})
            result["patterns"].append("sg_exposure")
            result["actors"] += actors
            result["affected_resources"] += [sg["group_id"] for sg in exposed]
            result["recommendations"] += [
                "Revert the security group changes",
                "Review IAM permissions for the actors involved",
            ]
            explanations.append(
                f"CloudTrail shows {len(sg_changes)} security-group change(s) by {', '.join(actors)}; "
                f"{len(exposed)} group(s) now expose sensitive ports to 0.0.0.0/0."
            )
            severities.append("critical")

        # Pattern 2: cost spike driven by resource creation from an external IP
        creators = [
            e for e in cloudtrail
            if e.get("event_name", "").startswith(("RunInstances", "Create", "StartInstances"))
            and _is_external_ip(e.get("source_ip", ""))
        ]
        if cost_spike and creators:
            actors = sorted({e.get("username", "unknown") for e in creators})
            result["patterns"].append("unauthorised_provisioning")
            result["actors"] += actors
            result["affected_resources"] += [
                r.get("name") for e in creators for r in e.get("resources", []) if r.get("name")
            ]
            result["recommendations"] += [
                "Terminate or quarantine resources launched from the unfamiliar IP",
                "Rotate the credentials used by the actor",
            ]
            explanations.append(
                f"Spend rose to ${current:.2f}/day vs ${baseline:.2f} baseline while "
                f"{len(creators)} resource-creating call(s) came from external IP(s) by {', '.join(actors)}."
            )
            severities.append("critical")

        # Pattern 3: cost spike accompanied by threat findings
        threats = [f for f in findings if str(f.get("severity", "")).lower() in ("high", "critical")]
        if cost_spike and threats:
            result["patterns"].append("compromise_cost")
            result["affected_resources"] += [f.get("resource") for f in threats if f.get("resource")]
            result["recommendations"] += [
                "Isolate the affected resources and follow the incident-response runbook",
                "Add a WAF / NACL block for the offending source addresses",
            ]
            explanations.append(
                f"{len(threats)} high-severity Security Hub/GuardDuty finding(s) "
                f"({', '.join(sorted({str(f.get('type')) for f in threats}))}) coincide with the cost spike."
            )
            severities.append("critical")

        if not result["patterns"] and cost_spike:
            result["patterns"].append("cost_only")
            result["recommendations"] += [
                "Review recent CloudTrail events for unexpected resource creation",
                "Consider scaling down non-critical resources",
            ]
            explanations.append(
                f"Current daily cost (${current:.2f}) exceeds the baseline (${baseline:.2f}) "
                f"by {((current / baseline - 1) * 100):.0f}%, with no linked security or audit events."
            )
            severities.append("warning")

        if result["patterns"]:
            result["correlation_found"] = result["patterns"] != ["cost_only"]
            result["severity"] = "critical" if "critical" in severities else "warning"
            result["summary"] = "Patterns detected: " + ", ".join(result["patterns"])
            result["root_cause"] = explanations[0]
            result["explanation"] = " ".join(explanations)
            result["actors"] = sorted(set(result["actors"]))
            result["affected_resources"] = sorted({r for r in result["affected_resources"] if r})
        return result


def _is_external_ip(ip: str) -> bool:
    """True for public IPv4 addresses (not RFC1918 / loopback / AWS service names)."""
    import ipaddress

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    internal = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16")
    return not any(addr in ipaddress.ip_network(net) for net in internal)
