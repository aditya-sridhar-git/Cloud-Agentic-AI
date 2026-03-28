"""
LLM-backed reasoning engine.

Takes an Observation and returns structured recommendations using OpenAI.
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

from cloud_agent.agent.baseagent import Action, Observation, Plan
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = """\
You are an autonomous cloud operations agent. Given the current cloud state
(instances, metrics, disks, costs, tags), determine which remediation actions
should be taken.

Available tools:
- idle_server   : stop or terminate instances with very low CPU utilisation
- rightsizer     : recommend or apply instance type downgrades for under-used VMs
- disk_cleanup   : snapshot and delete unattached EBS volumes
- tag_enforcer   : apply missing required tags to resources
- scheduler      : stop non-production instances outside business hours
- cost_monitor   : alert or freeze resources when daily spend spikes

Respond with a JSON object:
{
  "summary": "<one-line summary of what needs to happen>",
  "actions": [
    {
      "tool_name": "<tool>",
      "resource_id": "<id>",
      "action_type": "<stop|terminate|resize|tag|alert|freeze|snapshot_delete>",
      "parameters": {},
      "reason": "<why this action is needed>"
    }
  ]
}

If no actions are needed, return {"summary": "All clear", "actions": []}.
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

        return self._parse_plan(data)

    # ------------------------------------------------------------------
    # Rule-based fallback
    # ------------------------------------------------------------------

    def _rule_based_analysis(self, observation: Observation, config: dict[str, Any]) -> Plan:
        """Simple threshold-based analysis when no LLM is available."""
        actions: list[Action] = []
        tools_cfg = config.get("tools", {})

        # --- Idle server check ---
        idle_cfg = tools_cfg.get("idle_server", {})
        if idle_cfg.get("enabled", False):
            cpu_thresh = idle_cfg.get("cpu_threshold_percent", 5.0)
            for inst in observation.instances:
                cpu = inst.get("cpu_percent", 100.0)
                state = inst.get("state", "")
                if state == "running" and cpu < cpu_thresh:
                    actions.append(
                        Action(
                            tool_name="idle_server",
                            resource_id=inst["instance_id"],
                            action_type=idle_cfg.get("action", "stop"),
                            reason=f"CPU at {cpu}% (threshold {cpu_thresh}%)",
                        )
                    )

        # --- Orphaned disk check ---
        disk_cfg = tools_cfg.get("disk_cleanup", {})
        if disk_cfg.get("enabled", False):
            max_days = disk_cfg.get("unattached_days", 7)
            for disk in observation.disks:
                if disk.get("state") == "available" and disk.get("unattached_days", 0) >= max_days:
                    actions.append(
                        Action(
                            tool_name="disk_cleanup",
                            resource_id=disk["volume_id"],
                            action_type="snapshot_delete",
                            reason=f"Unattached for {disk['unattached_days']} days",
                        )
                    )

        # --- Tag enforcement ---
        tag_cfg = tools_cfg.get("tag_enforcer", {})
        if tag_cfg.get("enabled", False):
            required = {t["Key"] for t in tag_cfg.get("required_tags", [])}
            for inst in observation.instances:
                existing = {t["Key"] for t in inst.get("tags", [])}
                missing = required - existing
                if missing:
                    actions.append(
                        Action(
                            tool_name="tag_enforcer",
                            resource_id=inst["instance_id"],
                            action_type="tag",
                            parameters={"missing_tags": list(missing)},
                            reason=f"Missing tags: {', '.join(missing)}",
                        )
                    )

        # --- Cost spike ---
        cost_cfg = tools_cfg.get("cost_monitor", {})
        if cost_cfg.get("enabled", False):
            threshold = cost_cfg.get("spike_threshold_percent", 120.0)
            baseline = observation.costs.get("baseline_daily", 0)
            current = observation.costs.get("current_daily", 0)
            if baseline > 0 and current > baseline * (threshold / 100.0):
                actions.append(
                    Action(
                        tool_name="cost_monitor",
                        resource_id="account",
                        action_type=cost_cfg.get("action", "alert"),
                        reason=f"Daily spend ${current:.2f} exceeds {threshold}% of baseline ${baseline:.2f}",
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
