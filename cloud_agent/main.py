"""
Cloud Agent — main entry point and orchestrator.

Usage::

    # Dry-run (default)
    python -m cloud_agent.main

    # Live run
    python -m cloud_agent.main --live

    # Single cycle then exit
    python -m cloud_agent.main --once
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from cloud_agent.agent.baseagent import BaseAgent, Observation, Plan
from cloud_agent.agent.planningagent import Planner
from cloud_agent.agent.reasoningagent import ReasoningEngine
from cloud_agent.cloud.aws_provider import AWSProvider
from cloud_agent.monitor.collector import MetricsCollector
from cloud_agent.tools.base_tool import BaseTool, get_tool_registry
from cloud_agent.utils.config import load_config
from cloud_agent.utils.logger import get_logger

# Import tools so the @register_tool decorators fire
import cloud_agent.tools.idle_server  # noqa: F401
import cloud_agent.tools.rightsizer  # noqa: F401
import cloud_agent.tools.disk_cleanup  # noqa: F401
import cloud_agent.tools.tag_enforcer  # noqa: F401
import cloud_agent.tools.scheduler  # noqa: F401
import cloud_agent.tools.cost_monitor  # noqa: F401

logger = get_logger(__name__)


class CloudOpsAgent(BaseAgent):
    """Concrete agent that wires together provider, tools, and reasoning."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        region = config.get("provider", {}).get("region", "us-east-1")
        self._provider = AWSProvider(region=region)
        self._collector = MetricsCollector(self._provider, config)
        self._reasoning = ReasoningEngine()
        self._planner = Planner(config)

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

    agent = CloudOpsAgent(config)

    if args.once:
        agent.run_once()
    else:
        agent.run_loop()


if __name__ == "__main__":
    main()
