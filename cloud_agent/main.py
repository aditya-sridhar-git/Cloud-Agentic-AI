"""
Cloud Agent — main entry point and orchestrator.

Usage::

    # Dry-run with mock data (no AWS credentials needed)
    python -m cloud_agent.main --mock

    # Dry-run against real AWS
    python -m cloud_agent.main --dry-run

    # Live run against real AWS
    python -m cloud_agent.main --live

    # Single cycle then exit
    python -m cloud_agent.main --mock --once

    # Launch the web dashboard
    python -m cloud_agent.main --mock --dashboard
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from typing import Any

from cloud_agent.agent.approval import ApprovalWorkflow
from cloud_agent.agent.baseagent import Action, BaseAgent, Observation, Plan
from cloud_agent.agent.guardrails import ActionValidator
from cloud_agent.agent.planningagent import Planner
from cloud_agent.agent.reasoningagent import ReasoningEngine
from cloud_agent.cloud.provider import CloudProvider
from cloud_agent.monitor.collector import MetricsCollector
from cloud_agent.tools.base_tool import BaseTool, is_mutating, load_all_tools
from cloud_agent.utils.action_log import ActionLogger
from cloud_agent.utils.config import load_config
from cloud_agent.utils.logger import get_logger
from cloud_agent.utils.notifier import Notifier

logger = get_logger(__name__)


class CloudOpsAgent(BaseAgent):
    """Concrete agent that wires together provider, tools, and reasoning."""

    def __init__(self, config: dict[str, Any], provider: CloudProvider | None = None,
                 action_logger: ActionLogger | None = None,
                 workflow: ApprovalWorkflow | None = None) -> None:
        super().__init__(config)

        # Use injected provider or default to AWS
        if provider is not None:
            self._provider = provider
        else:
            from cloud_agent.cloud.aws_provider import AWSProvider
            provider_cfg = config.get("provider", {})
            self._provider = AWSProvider(
                region=provider_cfg.get("region", "us-east-1"), regions=provider_cfg.get("regions"),
            )

        self.workflow = workflow or ApprovalWorkflow(config)
        self._collector = MetricsCollector(self._provider, config)
        self._reasoning = ReasoningEngine(config, memory=self.workflow.memory)
        self._validator = ActionValidator(config)
        self._planner = Planner(config, workflow=self.workflow)
        self._action_logger = action_logger or ActionLogger()
        self._notifier = Notifier(config.get("notifications", {}))
        self._guardrails = config.get("safety", {}).get("guardrails", True)
        self._read_only_in_dry_run = config.get("agent", {}).get("read_only_in_dry_run", True)
        self.last_cycle: dict[str, Any] = {}

        # Instantiate all registered tools
        registry = load_all_tools()
        self._tools: dict[str, BaseTool] = {
            name: cls(self._provider, config) for name, cls in registry.items()
        }
        logger.info(
            "Loaded tools: %s",
            ", ".join(self._tools.keys()),
        )

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    def observe(self) -> Observation:
        return self._collector.collect()

    def think(self, observation: Observation) -> Plan:
        plan = self._reasoning.analyse(observation, self.config)
        proposed = len(plan.actions)

        rejected: list[tuple[Action, str]] = []
        if self._guardrails:
            report = self._validator.validate(plan.actions, observation)
            plan.actions = report.accepted
            rejected = report.rejected

        refined_plan = self._planner.refine(plan, observation)

        # Optional adversarial multi-agent consensus on risky actions
        if self.config.get("agent", {}).get("multi_agent_consensus", False):
            from cloud_agent.agent.consensus import ConsensusEngine
            consensus = ConsensusEngine(self.config)

            kept: list[Action] = []
            for action in refined_plan.actions:
                result = consensus.validate_action(action, observation)
                if result.status in ("APPROVED", "MODIFIED"):
                    kept.append(action)
                elif result.status == "ESCALATED":
                    action.requires_approval = True
                    action.approved = False
                    action.parameters.setdefault("approval_reason", result.judge_reasoning)
                    kept.append(action)
                else:
                    rejected.append((action, f"consensus: {result.judge_reasoning}"))
                    logger.warning(
                        "[bold red]❌ ACTION REJECTED BY CONSENSUS ENGINE:[/bold red] %s on %s — %s",
                        action.action_type, action.resource_id, result.judge_reasoning,
                    )
            refined_plan.actions = kept

        self.last_cycle.update({
            "reasoning_mode": self._reasoning.last_mode,
            "proposed": proposed,
            "rejected": [
                {"tool_name": a.tool_name, "resource_id": a.resource_id,
                 "action_type": a.action_type, "why": why}
                for a, why in rejected
            ],
        })
        return refined_plan

    def act(self, plan: Plan, approved_by_operator: bool = False) -> list[dict[str, Any]]:
        """Execute a plan.

        * Actions awaiting approval are queued (unless *approved_by_operator*).
        * In dry-run mode, mutating actions are never executed; read-only ones
          (diagnose, scans, correlation) run unless ``read_only_in_dry_run`` is off.
        """
        results: list[dict[str, Any]] = []
        queued: list[Action] = []
        for action in plan.actions:
            base = {
                "tool": action.tool_name,
                "resource_id": action.resource_id,
                "action_type": action.action_type,
                "reason": action.reason,
            }
            tool = self._tools.get(action.tool_name)
            if tool is None:
                logger.warning("No tool registered for '%s'", action.tool_name)
                results.append({**base, "status": "error", "error": "unknown tool"})
                continue

            if action.requires_approval and not approved_by_operator:
                if self.workflow.memory.add_pending(action, action.parameters.get("approval_reason", "")):
                    queued.append(action)
                results.append({**base, "status": "pending_approval"})
                continue

            mutating = is_mutating(action)
            if self.dry_run and (mutating or not self._read_only_in_dry_run):
                results.append({**base, "status": "dry_run"})
                continue

            try:
                result = tool.execute(action)
                results.append({**base, **result, "status": result.get("status", "executed")})
                logger.info("  ✓ %s on %s — %s", action.tool_name, action.resource_id, result.get("status"))
            except Exception as exc:
                logger.exception("  ✗ %s on %s failed", action.tool_name, action.resource_id)
                results.append({**base, "status": "error", "error": str(exc)})

        if queued:
            self._notifier.request_approval(queued, self.config.get("dashboard", {}).get("public_url"))
        return results

    def execute_approved(self, tool_name: str | None, resource_id: str, action_type: str,
                         notes: str = "") -> dict[str, Any]:
        """Execute an action an operator approved from the queue."""
        entry = self.workflow.memory.pop_pending(tool_name, resource_id, action_type)
        if entry is None:
            return {"status": "not_found"}
        action = Action(
            tool_name=entry["tool_name"], resource_id=entry["resource_id"],
            action_type=entry["action_type"], parameters=entry.get("parameters", {}),
            reason=entry.get("reason", ""), approved=True,
        )
        self.workflow.memory.record_feedback(
            resource_id, action_type, "approved", notes, tool_name=action.tool_name, reason=action.reason,
        )
        results = self.act(Plan(actions=[action], summary="Operator-approved action"), approved_by_operator=True)
        for r in results:
            self._action_logger.log_action({**r, "approved_by": "operator"})
        return results[0] if results else {"status": "error"}

    def run_once(self) -> list[dict[str, Any]]:
        """Execute one full observe → think → act cycle with logging & notifications."""
        cycle_id = str(uuid.uuid4())[:8]
        self.last_cycle = {"cycle_id": cycle_id}
        logger.info("[bold cyan]═══ Agent Cycle %s Start ═══[/bold cyan]", cycle_id)

        # 1. OBSERVE
        t0 = time.perf_counter()
        logger.info("[yellow]▶ OBSERVE[/yellow] — collecting cloud state …")
        observation = self.observe()
        t1 = time.perf_counter()
        logger.info(
            "  Collected %d instances, %d disks",
            len(observation.instances),
            len(observation.disks),
        )

        obs_summary = {
            "instances": len(observation.instances),
            "disks": len(observation.disks),
            "costs": observation.costs,
        }

        # 2. THINK
        logger.info("[yellow]▶ THINK[/yellow] — analysing …")
        plan = self.think(observation)
        t2 = time.perf_counter()
        logger.info("  Plan: %s (%d actions)", plan.summary, len(plan.actions))

        # 3. ACT
        if not plan.actions:
            logger.info("[green]✓ No actions needed — cloud is healthy.[/green]")
            results: list[dict[str, Any]] = []
        else:
            mode = "DRY RUN" if self.dry_run else "ACT"
            logger.info("[yellow]▶ %s[/yellow] — %d action(s) …", mode, len(plan.actions))
            results = self.act(plan)
        t3 = time.perf_counter()

        self.last_cycle.update({
            "plan": plan,
            "observation": observation,
            "timings_ms": {
                "observe": round((t1 - t0) * 1000, 2),
                "think": round((t2 - t1) * 1000, 2),
                "act": round((t3 - t2) * 1000, 2),
                "total": round((t3 - t0) * 1000, 2),
            },
        })

        # 4. LOG & NOTIFY
        self._action_logger.log_cycle(cycle_id, plan.summary or "All clear", results, obs_summary)
        executed = [r for r in results if r.get("status") not in ("dry_run", "pending_approval")]
        self._notifier.alert_actions(plan.summary, len(executed), executed)

        logger.info("[bold cyan]═══ Agent Cycle %s Complete ═══[/bold cyan]", cycle_id)
        return results

    @property
    def action_logger(self) -> ActionLogger:
        """Expose action logger for dashboard access."""
        return self._action_logger

    @property
    def provider(self) -> CloudProvider:
        """Expose provider for dashboard access."""
        return self._provider


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cloud Agentic AI — autonomous cloud operations agent",
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help="Path to settings.yaml (default: config/settings.yaml)",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Disable dry-run mode — actions WILL be executed",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single observe-think-act cycle then exit",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Plan only, never change resources (the default unless --live is given)",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Use mock provider (no AWS credentials needed)",
    )
    parser.add_argument(
        "--dashboard", action="store_true",
        help="Launch the web dashboard",
    )
    parser.add_argument(
        "--port", type=int, default=8080,
        help="Dashboard port (default: 8080)",
    )
    parser.add_argument(
        "--host", type=str, default="127.0.0.1",
        help="Dashboard bind address (default: 127.0.0.1; set DASHBOARD_TOKEN before exposing it)",
    )
    parser.add_argument(
        "--scenario", type=str, default=None,
        help="Mock scenario to load with --mock (see `python -m cloud_agent.evaluation --list`)",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entry point."""
    args = _parse_args()
    config = load_config(args.config)

    if args.live and args.dry_run:
        logger.error("--live and --dry-run are mutually exclusive")
        sys.exit(2)
    if args.live:
        config.setdefault("agent", {})["dry_run"] = False
        logger.info("[bold red]⚠  LIVE MODE — actions will be executed![/bold red]")
    else:
        config.setdefault("agent", {})["dry_run"] = True

    # Choose provider
    provider: CloudProvider | None = None
    if args.mock:
        from cloud_agent.cloud.mock_provider import MockProvider
        if args.scenario:
            from cloud_agent.evaluation.scenarios import get_scenario
            provider = get_scenario(args.scenario).build().provider
        else:
            provider = MockProvider(
                region=config.get("provider", {}).get("region", "us-east-1"),
            )
        logger.info("[bold green]🧪 MOCK MODE — using simulated cloud data[/bold green]")

    agent = CloudOpsAgent(config, provider=provider)

    # Dashboard mode
    if args.dashboard:
        from cloud_agent.dashboard.app import run_dashboard
        logger.info("[bold cyan]🌐 Launching dashboard on port %d …[/bold cyan]", args.port)
        run_dashboard(agent, port=args.port, host=args.host)
        return

    # Normal agent run
    agent.run(continuous=not args.once)


if __name__ == "__main__":
    main()
