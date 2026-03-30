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

        # Step 2: Correlate with LLM (or rule-based fallback)
        correlation = self._correlate(
            trigger, cloudtrail_events, security_groups, cost_data, instance_data,
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

    def _get_cost_context(self) -> dict[str, Any]:
        try:
            return {
                "current_daily": self.provider.get_daily_cost(days=1),
                "baseline": self.provider.get_cost_baseline(days=7),
            }
        except Exception:
            return {}

    def _get_instance_context(self) -> list[dict[str, Any]]:
        try:
            return self.provider.list_instances()
        except Exception:
            return []

    def _correlate(self, trigger: str, cloudtrail: list, sgs: list,
                   costs: dict, instances: list) -> dict[str, Any]:
        """Use LLM or rule-based fallback to find cross-domain patterns."""
        api_key = os.getenv("OPENAI_API_KEY")

        if api_key:
            try:
                return self._llm_correlate(trigger, cloudtrail, sgs, costs, instances)
            except Exception as exc:
                logger.warning("LLM correlation failed: %s", exc)

        return self._rule_based_correlate(trigger, cloudtrail, sgs, costs, instances)

    def _llm_correlate(self, trigger: str, cloudtrail: list, sgs: list,
                       costs: dict, instances: list) -> dict[str, Any]:
        from openai import OpenAI  # noqa: E402
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        prompt = _CORRELATION_PROMPT.format(
            trigger=trigger,
            cloudtrail_data=json.dumps(cloudtrail[:20], default=str, indent=2),
            security_groups=json.dumps(sgs[:10], default=str, indent=2),
            cost_data=json.dumps(costs, default=str, indent=2),
            instance_data=json.dumps(instances[:10], default=str, indent=2),
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        return json.loads(response.choices[0].message.content)

    @staticmethod
    def _rule_based_correlate(trigger: str, cloudtrail: list, sgs: list,
                              costs: dict, instances: list) -> dict[str, Any]:
        """Simple rule-based cross-domain correlation."""
        findings = {
            "correlation_found": False,
            "summary": "Rule-based analysis complete",
            "root_cause": "",
            "actors": [],
            "affected_resources": [],
            "severity": "info",
            "recommendations": [],
            "explanation": "",
        }

        # Check for security group changes in CloudTrail
        sg_changes = [e for e in cloudtrail if "SecurityGroup" in e.get("event_name", "")]
        # Check for open SGs
        open_sgs = [
            sg for sg in sgs
            for rule in sg.get("ingress_rules", [])
            if "0.0.0.0/0" in rule.get("cidr_blocks", [])
            and rule.get("from_port", 0) in (22, 3389)
        ]

        # Check for cost spike
        baseline = costs.get("baseline", 0)
        current = costs.get("current_daily", 0)
        cost_spike = baseline > 0 and current > baseline * 1.2

        if sg_changes and open_sgs:
            actors = list({e.get("username", "unknown") for e in sg_changes})
            findings.update({
                "correlation_found": True,
                "summary": f"Security group changes by {', '.join(actors)} opened dangerous ports",
                "root_cause": "Recent CloudTrail events show security group modifications that opened sensitive ports to the internet",
                "actors": actors,
                "affected_resources": [sg["group_id"] for sg in open_sgs],
                "severity": "critical",
                "recommendations": [
                    "Revert the security group changes",
                    "Review IAM permissions for the actors involved",
                    "Enable AWS Config rules to prevent open SGs",
                ],
                "explanation": (
                    f"CloudTrail shows {len(sg_changes)} security group modification(s) in the last 24 hours. "
                    f"{len(open_sgs)} security group(s) now have SSH/RDP open to 0.0.0.0/0. "
                    f"This was done by: {', '.join(actors)}. "
                    "This is a critical security risk that should be addressed immediately."
                ),
            })
        elif cost_spike:
            findings.update({
                "correlation_found": True,
                "summary": f"Cost spike detected: ${current:.2f}/day vs ${baseline:.2f}/day baseline",
                "root_cause": "Daily spending exceeds baseline by more than 20%",
                "severity": "warning",
                "recommendations": [
                    "Review recent CloudTrail events for unexpected resource creation",
                    "Check for new instances or services that were launched",
                    "Consider scaling down non-critical resources",
                ],
                "explanation": (
                    f"Current daily cost (${current:.2f}) exceeds the 7-day baseline (${baseline:.2f}) "
                    f"by {((current/baseline - 1) * 100):.0f}%. "
                    f"CloudTrail shows {len(cloudtrail)} events in the last 24 hours."
                ),
            })

        return findings
