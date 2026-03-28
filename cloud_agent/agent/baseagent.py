"""
BaseAgent — abstract observe-think-act loop.

Every concrete agent must implement :meth:`observe`, :meth:`think`,
and :meth:`act`.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Any

from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Observation:
    """A snapshot of the current cloud state."""

    metrics: dict[str, Any] = field(default_factory=dict)
    instances: list[dict[str, Any]] = field(default_factory=list)
    disks: list[dict[str, Any]] = field(default_factory=list)
    costs: dict[str, Any] = field(default_factory=dict)
    tags: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class Action:
    """A single remediation action the agent wants to perform."""

    tool_name: str
    resource_id: str
    action_type: str  # e.g. "stop", "terminate", "resize", "tag", "alert"
    parameters: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    approved: bool = False


@dataclass
class Plan:
    """An ordered list of actions the agent plans to execute."""

    actions: list[Action] = field(default_factory=list)
    summary: str = ""


class BaseAgent(abc.ABC):
    """Abstract agent that runs an observe → think → act loop."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.dry_run: bool = config.get("agent", {}).get("dry_run", True)
        self.loop_interval: int = config.get("agent", {}).get("loop_interval_seconds", 300)

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def observe(self) -> Observation:
        """Collect current cloud state."""

    @abc.abstractmethod
    def think(self, observation: Observation) -> Plan:
        """Analyse observation and decide on a plan."""

    @abc.abstractmethod
    def act(self, plan: Plan) -> list[dict[str, Any]]:
        """Execute approved actions from the plan."""

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run_once(self) -> list[dict[str, Any]]:
        """Execute one full observe → think → act cycle.

        Returns:
            List of result dicts from executed actions.
        """
        logger.info("[bold cyan]═══ Agent Cycle Start ═══[/bold cyan]")

        # 1. OBSERVE
        logger.info("[yellow]▶ OBSERVE[/yellow] — collecting cloud state …")
        observation = self.observe()
        logger.info(
            "  Collected %d instances, %d disks",
            len(observation.instances),
            len(observation.disks),
        )

        # 2. THINK
        logger.info("[yellow]▶ THINK[/yellow] — analysing with LLM …")
        plan = self.think(observation)
        logger.info("  Plan: %s (%d actions)", plan.summary, len(plan.actions))

        if not plan.actions:
            logger.info("[green]✓ No actions needed — cloud is healthy.[/green]")
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
            return [{"action": a.action_type, "resource": a.resource_id, "status": "dry_run"} for a in plan.actions]

        logger.info("[yellow]▶ ACT[/yellow] — executing %d actions …", len(plan.actions))
        results = self.act(plan)
        logger.info("[bold cyan]═══ Agent Cycle Complete ═══[/bold cyan]")
        return results

    def run_loop(self) -> None:
        """Run the agent continuously."""
        logger.info(
            "[bold green]Starting agent loop[/bold green] (interval=%ds, dry_run=%s)",
            self.loop_interval,
            self.dry_run,
        )
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                logger.info("[red]Agent stopped by user.[/red]")
                break
            except Exception:
                logger.exception("Agent cycle failed")
            time.sleep(self.loop_interval)
