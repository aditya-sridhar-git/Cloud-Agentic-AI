"""
Cost Monitor Tool — detect daily spending anomalies, alert, or freeze.

Action types:
  - "check"  : Fetch live cost data and return breakdown with anomalies (used by chat)
  - "alert"  : Log a cost spike alert (called by the reasoning engine)
  - "freeze" : Stop non-critical (dev/staging) instances to contain the spike
"""

from __future__ import annotations

from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


@register_tool("cost_monitor")
class CostMonitorTool(BaseTool):
    """Alerts or freezes resources when daily cloud spend exceeds the baseline."""

    def execute(self, action: Action) -> dict[str, Any]:
        action_type = action.action_type  # "check", "alert", or "freeze"

        if action_type == "check":
            return self._check_costs()

        if action_type == "freeze":
            logger.info(
                "[bold red]FREEZE[/bold red] -- spend anomaly detected! %s",
                action.reason,
            )
            frozen_instances = self._freeze_non_critical()
            return {
                "tool": self.tool_name,
                "status": "freeze_initiated",
                "reason": action.reason,
                "frozen_instances": frozen_instances,
                "frozen_count": len(frozen_instances),
            }

        # Default: "alert"
        logger.info(
            "[yellow]ALERT[/yellow] -- cost spike detected: %s",
            action.reason,
        )
        return {
            "tool": self.tool_name,
            "status": "alert_sent",
            "reason": action.reason,
        }

    # ------------------------------------------------------------------
    # Check: fetch live costs and compute anomalies
    # ------------------------------------------------------------------

    def _check_costs(self) -> dict[str, Any]:
        """Pull current + baseline cost, detect per-service anomalies."""
        cfg = self.config.get("tools", {}).get("cost_monitor", {})
        spike_pct = cfg.get("spike_threshold_percent", 120.0)
        baseline_days = cfg.get("baseline_window_days", 7)

        try:
            current = self.provider.get_daily_cost(days=1)
        except Exception as exc:
            logger.warning("Could not fetch daily cost: %s", exc)
            current = 0.0

        try:
            baseline = self.provider.get_cost_baseline(days=baseline_days)
        except Exception as exc:
            logger.warning("Could not fetch cost baseline: %s", exc)
            baseline = 0.0

        try:
            services = self.provider.get_cost_by_service(days=1)
        except Exception as exc:
            logger.warning("Could not fetch per-service costs: %s", exc)
            services = []

        # Compute overall delta %
        delta_pct = 0.0
        if baseline > 0:
            delta_pct = round(((current - baseline) / baseline) * 100.0, 1)

        # Build per-service anomaly list
        # We flag any service whose spend is unusually high relative to total
        anomalies: list[dict[str, Any]] = []
        total_from_services = sum(s["amount"] for s in services) or current or 1.0
        for svc in services:
            share_pct = round((svc["amount"] / total_from_services) * 100, 1)
            msg_parts = [f"${svc['amount']:.2f}/day ({share_pct}% of total)"]

            # Heuristic: flag EC2 if >50% of bill, others if >30%
            is_anomaly = False
            if svc["service"] == "Amazon EC2" and share_pct > 50:
                is_anomaly = True
                msg_parts.append("EC2 dominates bill — check for over-provisioned instances")
            elif share_pct > 30:
                is_anomaly = True
                msg_parts.append("unusually high service share — investigate")

            if is_anomaly:
                anomalies.append({
                    "service": svc["service"],
                    "amount": svc["amount"],
                    "share_pct": share_pct,
                    "message": " | ".join(msg_parts),
                })

        # Add a global spike anomaly if the overall day is above threshold
        if baseline > 0 and current > baseline * (spike_pct / 100.0):
            anomalies.insert(0, {
                "service": "All Services",
                "amount": current,
                "share_pct": 100.0,
                "message": (
                    f"Daily total ${current:.2f} exceeds baseline ${baseline:.2f} "
                    f"by {delta_pct:+.1f}% (threshold {spike_pct:.0f}%)"
                ),
            })

        currency = services[0]["currency"] if services else "USD"
        result = {
            "tool": self.tool_name,
            "total_cost": current,
            "baseline": baseline,
            "delta_pct": delta_pct,
            "currency": currency,
            "services": services,
            "anomalies": anomalies,
            "status": "spike" if anomalies else "normal",
        }
        logger.info(
            "[cyan]Cost check[/cyan]: $%.2f today vs $%.2f baseline (%+.1f%%), %d anomaly(ies)",
            current, baseline, delta_pct, len(anomalies),
        )
        return result

    # ------------------------------------------------------------------
    # Freeze: stop dev/staging instances to contain spike
    # ------------------------------------------------------------------

    def _freeze_non_critical(self) -> list[str]:
        """Stop all dev/staging instances to contain cost spike."""
        frozen: list[str] = []
        freeze_envs = {"dev", "staging", "test", "qa"}

        try:
            instances = self.provider.list_instances()
        except Exception:
            logger.warning("Could not list instances for freeze")
            return frozen

        for inst in instances:
            if inst.get("state") != "running":
                continue
            tags = {t["Key"].lower(): t["Value"].lower() for t in inst.get("tags", [])}
            env = tags.get("environment", "")
            if env in freeze_envs:
                try:
                    self.provider.stop_instance(inst["instance_id"])
                    frozen.append(inst["instance_id"])
                    logger.info(
                        "  Froze %s (env=%s)", inst["instance_id"], env,
                    )
                except Exception:
                    logger.warning("Could not freeze %s", inst["instance_id"])

        logger.info("[bold red]FREEZE COMPLETE[/bold red] -- stopped %d instance(s)", len(frozen))
        return frozen
