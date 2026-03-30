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
import uuid
from typing import Any

from cloud_agent.agent.baseagent import BaseAgent, Observation, Plan
from cloud_agent.agent.planningagent import Planner
from cloud_agent.agent.reasoningagent import ReasoningEngine
from cloud_agent.cloud.provider import CloudProvider
from cloud_agent.monitor.collector import MetricsCollector
from cloud_agent.tools.base_tool import BaseTool, get_tool_registry
from cloud_agent.utils.action_log import ActionLogger
from cloud_agent.utils.config import load_config
from cloud_agent.utils.logger import get_logger
from cloud_agent.utils.notifier import Notifier

# Import tools so the @register_tool decorators fire
import cloud_agent.tools.idle_server       # noqa: F401
import cloud_agent.tools.rightsizer        # noqa: F401
import cloud_agent.tools.disk_cleanup     # noqa: F401
import cloud_agent.tools.tag_enforcer     # noqa: F401
import cloud_agent.tools.scheduler        # noqa: F401
import cloud_agent.tools.cost_monitor     # noqa: F401
import cloud_agent.tools.diagnose_server  # noqa: F401
import cloud_agent.tools.security_auditor # noqa: F401
import cloud_agent.tools.cross_domain     # noqa: F401

logger = get_logger(__name__)


class CloudOpsAgent(BaseAgent):
    """Concrete agent that wires together provider, tools, and reasoning."""

    def __init__(self, config: dict[str, Any], provider: CloudProvider | None = None) -> None:
        super().__init__(config)

        # Use injected provider or default to AWS
        if provider is not None:
            self._provider = provider
        else:
            from cloud_agent.cloud.aws_provider import AWSProvider
            region = config.get("provider", {}).get("region", "us-east-1")
            self._provider = AWSProvider(region=region)

        self._collector = MetricsCollector(self._provider, config)
        self._reasoning = ReasoningEngine()
        self._planner = Planner(config)
        self._action_logger = ActionLogger()
        self._notifier = Notifier(config.get("notifications", {}))

        # Instantiate all registered tools
        registry = get_tool_registry()
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
        return self._planner.refine(plan)

    def act(self, plan: Plan) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for action in plan.actions:
            tool = self._tools.get(action.tool_name)
            if tool is None:
                logger.warning("No tool registered for '%s'", action.tool_name)
                continue
            try:
                result = tool.execute(action)
                results.append(result)
                logger.info("  ✓ %s on %s — %s", action.tool_name, action.resource_id, result.get("status"))
            except Exception:
                logger.exception("  ✗ %s on %s failed", action.tool_name, action.resource_id)
                results.append({
                    "tool": action.tool_name,
                    "resource_id": action.resource_id,
                    "status": "error",
                })
        return results

    def run_once(self) -> list[dict[str, Any]]:
        """Execute one full observe → think → act cycle with logging & notifications."""
        cycle_id = str(uuid.uuid4())[:8]
        logger.info("[bold cyan]═══ Agent Cycle %s Start ═══[/bold cyan]", cycle_id)

        # 1. OBSERVE
        logger.info("[yellow]▶ OBSERVE[/yellow] — collecting cloud state …")
        observation = self.observe()
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
        logger.info("[yellow]▶ THINK[/yellow] — analysing with LLM …")
        plan = self.think(observation)
        logger.info("  Plan: %s (%d actions)", plan.summary, len(plan.actions))

        if not plan.actions:
            logger.info("[green]✓ No actions needed — cloud is healthy.[/green]")
            self._action_logger.log_cycle(cycle_id, "All clear", [], obs_summary)
            return []

        # 3. ACT
        if self.dry_run:
            logger.info("[magenta]▶ DRY RUN[/magenta] — actions will NOT be executed:")
            for action in plan.actions:
                logger.info(
                    "  • [%s] %s on %s — %s",
                    action.tool_name,
                    action.action_type,
                    action.resource_id,
                    action.reason,
                )
            results = [{"action": a.action_type, "resource": a.resource_id,
                        "tool": a.tool_name, "reason": a.reason, "status": "dry_run"} for a in plan.actions]
        else:
            logger.info("[yellow]▶ ACT[/yellow] — executing %d actions …", len(plan.actions))
            results = self.act(plan)

        # 4. LOG & NOTIFY
        self._action_logger.log_cycle(cycle_id, plan.summary, results, obs_summary)
        self._notifier.alert_actions(plan.summary, len(results), results)

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
        "--dry-run", action="store_true", default=True,
        help="Enable dry-run mode (default)",
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
    return parser.parse_args()


def main() -> None:
    """CLI entry point."""
    args = _parse_args()
    config = load_config(args.config)

    if args.live:
        config.setdefault("agent", {})["dry_run"] = False
        logger.info("[bold red]⚠  LIVE MODE — actions will be executed![/bold red]")
    else:
        config.setdefault("agent", {})["dry_run"] = True

    # Choose provider
    provider: CloudProvider | None = None
    if args.mock:
        from cloud_agent.cloud.mock_provider import MockProvider
        provider = MockProvider(
            region=config.get("provider", {}).get("region", "us-east-1"),
        )
        logger.info("[bold green]🧪 MOCK MODE — using simulated cloud data[/bold green]")

    agent = CloudOpsAgent(config, provider=provider)

    # Dashboard mode
    if args.dashboard:
        from cloud_agent.dashboard.app import run_dashboard
        logger.info("[bold cyan]🌐 Launching dashboard on port %d …[/bold cyan]", args.port)
        run_dashboard(agent, port=args.port)
        return

    # Normal agent run
    if args.once:
        agent.run_once()
    else:
        agent.run_loop()


if __name__ == "__main__":
    main()
