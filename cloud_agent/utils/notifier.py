"""
Notification system — Slack webhook + AWS SNS alerts.

Sends human-readable alert messages when the agent takes or plans actions.
Falls back gracefully if neither Slack nor SNS is configured.
"""

from __future__ import annotations

import json
import os
from typing import Any

from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)


class Notifier:
    """Send notifications via Slack webhook and/or AWS SNS."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._slack_webhook = cfg.get("slack_webhook") or os.getenv("SLACK_WEBHOOK_URL")
        self._sns_topic = cfg.get("sns_topic_arn") or os.getenv("SNS_TOPIC_ARN")
        self._enabled = bool(self._slack_webhook or self._sns_topic)

        if self._enabled:
            channels = []
            if self._slack_webhook:
                channels.append("Slack")
            if self._sns_topic:
                channels.append("SNS")
            logger.info("[green]Notifier enabled[/green] — %s", " + ".join(channels))
        else:
            logger.info("[yellow]Notifier disabled[/yellow] — no Slack webhook or SNS topic configured")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def send(self, title: str, message: str, severity: str = "info", data: dict[str, Any] | None = None) -> None:
        """Send a notification to all configured channels."""
        if not self._enabled:
            logger.debug("Notifier: skipping (not configured)")
            return

        if self._slack_webhook:
            self._send_slack(title, message, severity, data)

        if self._sns_topic:
            self._send_sns(title, message, severity, data)

    def _send_slack(self, title: str, message: str, severity: str, data: dict[str, Any] | None) -> None:
        """Post a message to a Slack webhook."""
        emoji_map = {"critical": "🔴", "warning": "🟡", "info": "🔵", "success": "🟢"}
        emoji = emoji_map.get(severity, "⚪")

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{emoji} {title}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": message},
            },
        ]
        if data:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"```{json.dumps(data, indent=2, default=str)[:2000]}```"},
            })

        payload = {"blocks": blocks}

        try:
            import httpx
            resp = httpx.post(self._slack_webhook, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.info("[green]Slack notification sent[/green]: %s", title)
            else:
                logger.warning("Slack returned %d: %s", resp.status_code, resp.text[:200])
        except Exception:
            logger.exception("Failed to send Slack notification")

    def _send_sns(self, title: str, message: str, severity: str, data: dict[str, Any] | None) -> None:
        """Publish a message to an AWS SNS topic."""
        try:
            import boto3
            sns = boto3.client("sns")
            full_message = f"{message}\n\n{json.dumps(data, indent=2, default=str)}" if data else message
            sns.publish(
                TopicArn=self._sns_topic,
                Subject=f"[Cloud Agent] {title}"[:100],
                Message=full_message[:262144],  # SNS limit
            )
            logger.info("[green]SNS notification sent[/green]: %s", title)
        except Exception:
            logger.exception("Failed to send SNS notification")

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def alert_actions(self, plan_summary: str, actions_count: int, results: list[dict[str, Any]]) -> None:
        """Send a summary of agent actions (called at end of each cycle)."""
        if actions_count == 0:
            return
        severity = "warning" if any(r.get("status") == "error" for r in results) else "info"
        message = (
            f"*Plan*: {plan_summary}\n"
            f"*Actions executed*: {actions_count}\n"
            f"*Errors*: {sum(1 for r in results if r.get('status') == 'error')}"
        )
        self.send(
            title=f"Agent Cycle Complete — {actions_count} action(s)",
            message=message,
            severity=severity,
            data={"results": results[:10]},
        )

    def alert_security(self, findings: list[dict[str, Any]]) -> None:
        """Send a security alert."""
        if not findings:
            return
        message = f"*{len(findings)} security finding(s) detected*\n"
        for f in findings[:5]:
            message += f"• {f.get('type', 'unknown')}: {f.get('resource', '')} — {f.get('detail', '')}\n"
        self.send(
            title=f"🛡️ Security Alert — {len(findings)} finding(s)",
            message=message,
            severity="critical",
            data={"findings": findings[:10]},
        )

    def alert_cost_spike(self, current: float, baseline: float, threshold: float) -> None:
        """Send a cost spike alert."""
        pct = (current / baseline * 100) if baseline > 0 else 0
        self.send(
            title=f"💰 Cost Spike — ${current:.2f}/day ({pct:.0f}% of baseline)",
            message=(
                f"*Current daily*: ${current:.2f}\n"
                f"*Baseline*: ${baseline:.2f}/day\n"
                f"*Threshold*: {threshold}%"
            ),
            severity="warning",
        )
