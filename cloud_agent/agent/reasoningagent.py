"""
LLM-backed reasoning engine.

Takes an Observation and returns structured recommendations using OpenAI.
Delegates rule-based analysis to the ThresholdEvaluator to avoid duplication.
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

from cloud_agent.agent.baseagent import Action, Observation, Plan
from cloud_agent.monitor.evaluator import ThresholdEvaluator
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = """\
You are an autonomous cloud operations agent. Given the current cloud state
(instances, metrics, disks, costs, tags), determine which remediation actions
should be taken.

Available tools:
- idle_server       : stop or terminate instances with very low CPU utilisation
- rightsizer        : recommend or apply instance type downgrades for under-used VMs
- disk_cleanup      : snapshot and delete unattached EBS volumes
- tag_enforcer      : apply missing required tags to resources
- scheduler         : stop/start non-production instances based on business hours
- cost_monitor      : alert or freeze resources when daily spend spikes
- diagnose_server   : SSM into a troubled instance, run diagnostics, explain the root cause
- security_auditor  : scan for open security groups, public S3 buckets, unencrypted EBS
- cross_domain      : correlate events across CloudTrail, costs, security, and infrastructure

Respond with a JSON object:
{
  "summary": "<one-line summary of what needs to happen>",
  "actions": [
    {
      "tool_name": "<tool>",
      "resource_id": "<id>",
      "action_type": "<stop|terminate|resize|tag|alert|freeze|snapshot_delete|diagnose|full_scan|correlate|start>",
      "parameters": {},
      "reason": "<why this action is needed>"
    }
  ]
}

IMPORTANT RULES:
1. Use the thresholds provided in 'tool_config' for all tools (e.g., diagnose_server's cpu_high_threshold).
2. If an instance crosses its high-CPU threshold, suggest diagnose_server BEFORE stopping or resizing it.
3. If a cost spike is detected, also suggest cross_domain correlation.
4. Always suggest security_auditor with action_type "full_scan" once per cycle.
5. For idle instances (CPU < threshold), suggest idle_server to stop them.
6. If no actions are needed, return {"summary": "All clear", "actions": []}.
"""


class ReasoningEngine:
    """Use an LLM to interpret observations and produce a plan."""

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set — reasoning will use rule-based fallback")
            self._client = None
        else:
            self._client = OpenAI(api_key=api_key)
        self._model = model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyse(self, observation: Observation, config: dict[str, Any]) -> Plan:
        """Analyse an observation and return a plan.

        Falls back to rule-based analysis if the LLM is unavailable.
        """
        if self._client is None:
            return self._rule_based_analysis(observation, config)

        return self._llm_analysis(observation, config)

    # ------------------------------------------------------------------
    # LLM path
    # ------------------------------------------------------------------

    def _llm_analysis(self, observation: Observation, config: dict[str, Any]) -> Plan:
        """Send observation to the LLM and parse the structured response."""
        user_content = json.dumps(
            {
                "instances": observation.instances,
                "metrics": observation.metrics,
                "disks": observation.disks,
                "costs": observation.costs,
                "tags": observation.tags,
                "tool_config": config.get("tools", {}),
            },
            default=str,
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.1,
            )
            raw = response.choices[0].message.content
            data = json.loads(raw)
            logger.info("[green]LLM reasoning complete[/green]")
        except Exception:
            logger.exception("LLM call failed — falling back to rules")
            return self._rule_based_analysis(observation, config)

        plan = self._parse_plan(data)
        
        # Add deterministic checks that the LLM cannot do easily (e.g., time-based checks)
        evaluator = ThresholdEvaluator(config)
        scheduler_actions = evaluator._check_scheduler(observation)
        
        if scheduler_actions:
            # Filter out any duplicate actions if the LLM hallucinated them
            existing = {(a.tool_name, a.resource_id) for a in plan.actions}
            for sa in scheduler_actions:
                if (sa.tool_name, sa.resource_id) not in existing:
                    plan.actions.append(sa)
            plan.summary += f" (plus {len(scheduler_actions)} scheduler actions)"

        return plan

    # ------------------------------------------------------------------
    # Rule-based fallback — delegates to ThresholdEvaluator
    # ------------------------------------------------------------------

    def _rule_based_analysis(self, observation: Observation, config: dict[str, Any]) -> Plan:
        """Use the ThresholdEvaluator for deterministic analysis."""
        evaluator = ThresholdEvaluator(config)
        actions = evaluator.evaluate(observation)

        # Also add diagnosis for high-CPU instances
        diag_cfg = config.get("tools", {}).get("diagnose_server", {})
        high_cpu_thresh = diag_cfg.get("cpu_high_threshold", 85.0)

        for inst in observation.instances:
            cpu = inst.get("cpu_percent", 0)
            if cpu >= high_cpu_thresh and inst.get("state") == "running":
                actions.append(
                    Action(
                        tool_name="diagnose_server",
                        resource_id=inst["instance_id"],
                        action_type="diagnose",
                        reason=f"CPU high at {cpu:.1f}% (threshold {high_cpu_thresh}%) — investigating root cause",
                    )
                )

        # Add a security scan
        sec_cfg = config.get("tools", {}).get("security_auditor", {})
        if sec_cfg.get("enabled", False):
            actions.append(
                Action(
                    tool_name="security_auditor",
                    resource_id="account",
                    action_type="full_scan",
                    reason="Periodic security audit",
                )
            )

        # Add cross-domain correlation if cost spike detected
        cost_actions = [a for a in actions if a.tool_name == "cost_monitor"]
        if cost_actions:
            actions.append(
                Action(
                    tool_name="cross_domain",
                    resource_id="account",
                    action_type="correlate",
                    reason=f"Cross-domain analysis triggered by cost anomaly: {cost_actions[0].reason}",
                )
            )

        summary = f"{len(actions)} issue(s) detected via rule-based analysis"
        logger.info("[yellow]Rule-based analysis:[/yellow] %s", summary)
        return Plan(actions=actions, summary=summary)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_plan(data: dict[str, Any]) -> Plan:
        """Parse the LLM JSON response into a Plan dataclass."""
        actions = []
        for item in data.get("actions", []):
            actions.append(
                Action(
                    tool_name=item.get("tool_name", "unknown"),
                    resource_id=item.get("resource_id", ""),
                    action_type=item.get("action_type", ""),
                    parameters=item.get("parameters", {}),
                    reason=item.get("reason", ""),
                )
            )
        return Plan(actions=actions, summary=data.get("summary", ""))
