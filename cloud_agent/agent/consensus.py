"""
Adversarial Multi-Agent Consensus Engine.

Simulates organizational friction by passing proposed actions through
FinOps, SecOps, and DevOps personas before a Judge makes a final decision.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional
from dataclasses import dataclass

from cloud_agent.agent.baseagent import Action, Observation
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

@dataclass
class ConsensusResult:
    action: Action
    status: str  # "APPROVED", "REJECTED", or "MODIFIED"
    judge_reasoning: str
    debate_transcript: dict[str, str]


class ConsensusEngine:
    """Runs a multi-agent debate to validate destructive or risky cloud actions."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.enabled = config.get("agent", {}).get("multi_agent_consensus", False)
        
        # Tools that require consensus (read-only tools bypass this)
        self.risky_actions = {"stop", "terminate", "snapshot_delete", "resize", "freeze"}

    def validate_action(self, action: Action, obs: Observation) -> Optional[ConsensusResult]:
        """Runs the debate. Returns ConsensusResult if the debate completes, or None if skipped."""
        if not self.enabled:
            return ConsensusResult(action, "APPROVED", "Consensus engine disabled", {})
            
        if action.action_type not in self.risky_actions:
            return ConsensusResult(action, "APPROVED", f"Action {action.action_type} is not considered risky", {})

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("ConsensusEngine requires OPENAI_API_KEY. Approving by default.")
            return ConsensusResult(action, "APPROVED", "Missing API key, auto-approved", {})

        logger.info(
            "[bold purple]⚖️ CONSENSUS ENGINE[/bold purple] — Initiating debate for: %s on %s", 
            action.action_type, action.resource_id
        )

        try:
            return self._run_llm_debate(action, obs, api_key)
        except Exception as e:
            logger.error("Debate failed: %s", e)
            # In case of failure, we fail open (approve) so the pipeline doesn't completely stall,
            # though in a strict production environment we would fail closed.
            return ConsensusResult(action, "APPROVED", f"Debate error: {e}", {})

    def _run_llm_debate(self, action: Action, obs: Observation, api_key: str) -> ConsensusResult:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        
        # Prepare context for the agents
        target_instance = next((i for i in obs.instances if i.get("instance_id") == action.resource_id), None)
        target_disk = next((d for d in obs.disks if d.get("volume_id") == action.resource_id), None)
        
        context = f"PROPOSED ACTION: {action.action_type} on {action.resource_id}\n"
        context += f"REASON GIVEN: {action.reason}\n\n"
        if target_instance:
            context += f"TARGET RESOURCE CONTEXT:\n{json.dumps(target_instance, indent=2)}\n"
        elif target_disk:
            context += f"TARGET RESOURCE CONTEXT:\n{json.dumps(target_disk, indent=2)}\n"

        # Persona Prompts
        finops_prompt = (
            "You are a strict FinOps Cloud Analyst. Your goal is to maximize cost savings and eliminate waste. "
            "Review the proposed action and context. Argue for it if it saves money, or against it if it wastes money. "
            "Keep your response to 2-3 sentences max."
        )
        
        devops_prompt = (
            "You are a paranoid SRE/DevOps Engineer. Your goal is 100% uptime and reliability. "
            "Review the proposed action. Attack it mercilessly if there is ANY chance it brings down production, disrupts users, "
            "or if the instance has tags suggesting it is 'prod' or critical. "
            "Keep your response to 2-3 sentences max."
        )

        secops_prompt = (
            "You are a cautious Cloud Security Engineer (SecOps). Your goal is minimizing risk. "
            "Review the proposed action. Does stopping or modifying this resource expose us? "
            "Does it violate compliance? "
            "Keep your response to 2-3 sentences max."
        )

        # 1. Gather Opinions concurrently (simulated by sequential fast calls)
        opinions = {}
        for persona, prompt in [("FinOps", finops_prompt), ("DevOps", devops_prompt), ("SecOps", secops_prompt)]:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": context}
                ],
                temperature=0.7,
                max_tokens=150
            )
            opinions[persona] = resp.choices[0].message.content.strip()
            logger.debug("[dim]%s says: %s[/dim]", persona, opinions[persona])

        # 2. Judge Decision
        judge_prompt = f"""
You are the VP of Cloud Engineering. You must review a proposed action and the resulting debate from your team.
You must make the final call: "APPROVED" or "REJECTED". 

PROPOSED ACTION: {action.action_type} on {action.resource_id}
REASON: {action.reason}

TEAM OPINIONS:
FinOps: {opinions['FinOps']}
DevOps: {opinions['DevOps']}
SecOps: {opinions['SecOps']}

Output JSON only:
{{
  "decision": "APPROVED" | "REJECTED",
  "reasoning": "A 1-2 sentence explanation of your decision based on the debate"
}}
"""
        judge_resp = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": judge_prompt}],
            temperature=0.0
        )
        
        judge_result = json.loads(judge_resp.choices[0].message.content)
        status = judge_result.get("decision", "APPROVED").upper()
        
        color = "green" if status == "APPROVED" else "red"
        logger.info(
            f"[{color}]⚖️ JUDGE VERDICT: {status}[/{color}] — {judge_result.get('reasoning')}"
        )

        return ConsensusResult(
            action=action,
            status=status,
            judge_reasoning=judge_result.get("reasoning", ""),
            debate_transcript=opinions
        )
