"""
LLM-backed reasoning engine.

Takes an Observation and returns structured recommendations using OpenAI.
Delegates rule-based analysis to the ThresholdEvaluator to avoid duplication.
"""

from __future__ import annotations

import json
import os
from typing import Any

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
- query_optimizer   : discover RDS databases, inspect latency and slow SQL, suggest rewrites/indexes
- cert_monitor      : check SSL/TLS certificates for upcoming expiry
- backup_manager    : create EBS snapshots and enforce retention

Respond with a JSON object:
{
  "summary": "<one-line summary of what needs to happen>",
  "actions": [
    {
      "tool_name": "<tool>",
      "resource_id": "<id>",
      "action_type": "<stop|terminate|resize|tag|alert|freeze|snapshot_delete|diagnose|full_scan|correlate|start|analyze>",
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
6. If query_optimizer is enabled, suggest query_optimizer with action_type "analyze" once per cycle.
7. Never act on instances tagged PreserveInstance=true; only reference resource IDs present in the input.
8. If no actions are needed, return {"summary": "All clear", "actions": []}.
"""

# Static examples of correct interventions. Operator-approved interventions
# from the approval memory are appended to these at runtime.
_FEW_SHOT_EXAMPLES = [
    {"situation": "instance i-aaa running at 2.3% CPU for 30 min, no protection tag",
     "action": {"tool_name": "idle_server", "resource_id": "i-aaa", "action_type": "stop"}},
    {"situation": "instance i-bbb at 97% CPU",
     "action": {"tool_name": "diagnose_server", "resource_id": "i-bbb", "action_type": "diagnose"}},
    {"situation": "daily spend 145% of 30-day baseline",
     "action": {"tool_name": "cross_domain", "resource_id": "account", "action_type": "correlate"}},
]


class ReasoningEngine:
    """Use an LLM to interpret observations and produce a plan.

    Falls back to the deterministic rule engine when the LLM is disabled,
    the API key is missing, or the call fails (network, rate limit, bad JSON).
    """

    def __init__(self, config: dict[str, Any] | None = None, memory: Any = None,
                 model: str | None = None) -> None:
        llm_cfg = (config or {}).get("llm", {})
        self._model = model or llm_cfg.get("model", "gpt-4o")
        self._temperature = llm_cfg.get("temperature", 0.1)
        self._memory = memory
        self.last_mode = "rules"
        api_key = os.getenv("OPENAI_API_KEY")
        if not llm_cfg.get("enabled", True):
            logger.info("LLM reasoning disabled in config — using rule-based analysis")
            self._client = None
        elif not api_key:
            logger.warning("OPENAI_API_KEY not set — reasoning will use rule-based fallback")
            self._client = None
        else:
            from openai import OpenAI

            self._client = OpenAI(api_key=api_key)

    @property
    def llm_available(self) -> bool:
        return self._client is not None

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

    def build_prompt(self, observation: Observation, config: dict[str, Any]) -> str:
        """Serialise the observation, thresholds and examples for the LLM."""
        examples = list(_FEW_SHOT_EXAMPLES)
        if self._memory is not None:
            for fb in self._memory.successful_examples():
                examples.append({
                    "situation": fb.get("reason") or "operator-approved intervention",
                    "action": {k: fb.get(k) for k in ("tool_name", "resource_id", "action_type")},
                })
        return json.dumps(
            {
                "instances": observation.instances,
                "metrics": observation.metrics,
                "disks": observation.disks,
                "costs": observation.costs,
                "tags": observation.tags,
                "tool_config": config.get("tools", {}),
                "examples_of_successful_interventions": examples,
            },
            default=str,
        )

    def _llm_analysis(self, observation: Observation, config: dict[str, Any]) -> Plan:
        """Send observation to the LLM and parse the structured response."""
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": self.build_prompt(observation, config)},
                ],
                temperature=self._temperature,
            )
            raw = response.choices[0].message.content
            data = json.loads(raw)
            logger.info("[green]LLM reasoning complete[/green]")
        except Exception:
            logger.exception("LLM call failed — falling back to rules")
            return self._rule_based_analysis(observation, config)

        self.last_mode = "llm"
        plan = self._parse_plan(data)

        # Add deterministic checks that the LLM cannot do reliably (time-based checks)
        evaluator = ThresholdEvaluator(config)
        existing = {(a.tool_name, a.resource_id) for a in plan.actions}
        added = 0
        for sa in evaluator._check_scheduler(observation):
            if (sa.tool_name, sa.resource_id) not in existing:
                plan.actions.append(sa)
                added += 1
        if added:
            plan.summary += f" (plus {added} scheduler actions)"

        query_cfg = config.get("tools", {}).get("query_optimizer", {})
        if query_cfg.get("enabled", False) and not any(a.tool_name == "query_optimizer" for a in plan.actions):
            plan.actions.append(
                Action(
                    tool_name="query_optimizer",
                    resource_id="rds",
                    action_type="analyze",
                    reason="Periodic RDS query optimization scan",
                )
            )

        return plan

    # ------------------------------------------------------------------
    # Rule-based fallback — delegates to ThresholdEvaluator
    # ------------------------------------------------------------------

    def _rule_based_analysis(self, observation: Observation, config: dict[str, Any]) -> Plan:
        """Use the ThresholdEvaluator for deterministic analysis."""
        self.last_mode = "rules"
        actions = ThresholdEvaluator(config).evaluate(observation)
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
