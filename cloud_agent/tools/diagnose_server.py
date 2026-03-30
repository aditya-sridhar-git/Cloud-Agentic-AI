"""
Diagnose Server Tool — SSM-based contextual root cause analysis.

This is the core "agentic" differentiator: instead of just detecting
that CPU is high, the agent SSH's into the server, runs diagnostic
commands, and uses the LLM to explain *why* the server is struggling.
"""

from __future__ import annotations

import json
import os
from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.base_tool import BaseTool, register_tool
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

_DIAGNOSTIC_COMMANDS = [
    "echo '---- top (snapshot) ----' && top -bn1 | head -20",
    "echo '---- ps aux (top CPU) ----' && ps aux --sort=-%cpu | head -10",
    "echo '---- df -h ----' && df -h",
    "echo '---- free -m ----' && free -m",
    "echo '---- dmesg (last 10 min) ----' && dmesg --time-format iso 2>/dev/null | tail -20 || dmesg | tail -20",
    "echo '---- journalctl errors (last 30 min) ----' && journalctl -p err --since '30 min ago' --no-pager 2>/dev/null | tail -20 || echo 'journalctl not available'",
    "echo '---- uptime ----' && uptime",
    "echo '---- last login ----' && last -n 3 2>/dev/null || echo 'last not available'",
]

_DIAGNOSIS_PROMPT = """\
You are a senior SRE / cloud operations expert. I've run diagnostic commands
on a troubled EC2 instance. Analyse the output and provide:

1. **Root Cause**: What is causing the issue (be specific — process name, PID, error).
2. **Severity**: critical / warning / info.
3. **Recommended Action**: What should be done (kill process, restart service,
   scale up, add disk, etc.).
4. **Safe to Auto-Remediate?**: yes / no — can this be fixed automatically
   without risk of data loss?

Instance ID: {instance_id}
Trigger: {trigger_reason}

--- DIAGNOSTIC OUTPUT ---
{diagnostic_output}
--- END OUTPUT ---

Respond in JSON:
{{
  "root_cause": "...",
  "severity": "critical|warning|info",
  "recommended_action": "...",
  "safe_to_auto_remediate": true/false,
  "explanation": "Human-readable 2-3 sentence summary"
}}
"""


@register_tool("diagnose_server")
class DiagnoseServerTool(BaseTool):
    """SSH into a troubled server via SSM, run diagnostics, and ask the LLM to explain."""

    def execute(self, action: Action) -> dict[str, Any]:
        instance_id = action.resource_id
        reason = action.reason

        logger.info(
            "[bold magenta]🔍 DIAGNOSE[/bold magenta] instance [cyan]%s[/cyan] — %s",
            instance_id, reason,
        )

        # Step 1: Run diagnostic commands via SSM
        try:
            diagnostic_output = self.provider.run_ssm_command(
                instance_id,
                _DIAGNOSTIC_COMMANDS,
                timeout=30,
            )
        except Exception as exc:
            logger.warning("SSM command failed for %s: %s", instance_id, exc)
            diagnostic_output = f"[SSM UNAVAILABLE] Error: {exc}"

        # Step 2: Send to LLM for analysis
        diagnosis = self._analyse_with_llm(instance_id, reason, diagnostic_output)

        logger.info(
            "[bold magenta]🔍 DIAGNOSIS RESULT[/bold magenta] for %s:\n"
            "  Root cause: %s\n"
            "  Severity: %s\n"
            "  Recommendation: %s",
            instance_id,
            diagnosis.get("root_cause", "unknown"),
            diagnosis.get("severity", "unknown"),
            diagnosis.get("recommended_action", "none"),
        )

        return {
            "tool": self.tool_name,
            "instance_id": instance_id,
            "status": "diagnosed",
            "diagnostic_output": diagnostic_output[:2000],
            "diagnosis": diagnosis,
            "remediation": self._remediate(instance_id, diagnosis)
        }

    def _remediate(self, instance_id: str, diagnosis: dict[str, Any]) -> dict[str, Any]:
        """Attempt to fix the discovered issue if it's safe to do so."""
        if not diagnosis.get("safe_to_auto_remediate"):
            return {"status": "skipped", "reason": "Not safe for auto-remediation"}

        root_cause = diagnosis.get("root_cause", "").lower()
        
        try:
            if "disk" in root_cause or "space" in root_cause:
                logger.info("[bold green]🔧 FIXING[/bold green] disk on %s", instance_id)
                res = self.provider.cleanup_logs(instance_id)
                return {"status": "fixed", "action": "cleanup_logs", "output": res}
            
            if "java" in root_cause or "service" in root_cause:
                # Guessing service name for demo/common cases
                svc = "java-app" if "java" in root_cause else "app" 
                logger.info("[bold green]🔧 RESTARTING[/bold green] %s on %s", svc, instance_id)
                res = self.provider.restart_service(instance_id, svc)
                return {"status": "fixed", "action": f"restart_{svc}", "output": res}
                
        except Exception as e:
            return {"status": "failed", "error": str(e)}

        return {"status": "no_remediation_defined"}


    def _analyse_with_llm(self, instance_id: str, reason: str,
                          diagnostic_output: str) -> dict[str, Any]:
        """Send diagnostic output to the LLM for root-cause analysis."""
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            # Fallback: basic pattern matching
            return self._rule_based_diagnosis(diagnostic_output)

        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)

            prompt = _DIAGNOSIS_PROMPT.format(
                instance_id=instance_id,
                trigger_reason=reason,
                diagnostic_output=diagnostic_output,
            )

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            raw = response.choices[0].message.content
            return json.loads(raw)
        except Exception as exc:
            logger.warning("LLM diagnosis failed: %s — using rule-based fallback", exc)
            return self._rule_based_diagnosis(diagnostic_output)

    @staticmethod
    def _rule_based_diagnosis(output: str) -> dict[str, Any]:
        """Basic pattern matching when LLM is unavailable."""
        output_lower = output.lower()

        if "out of memory" in output_lower or "oom" in output_lower:
            return {
                "root_cause": "Out of memory — OOM killer may have triggered",
                "severity": "critical",
                "recommended_action": "Increase instance memory or identify leaking process",
                "safe_to_auto_remediate": False,
                "explanation": "The system is running out of memory. An OOM condition was detected in logs.",
            }
        elif "gc pause" in output_lower or "garbage collection" in output_lower:
            return {
                "root_cause": "Java GC pause causing high CPU — likely memory pressure on JVM",
                "severity": "critical",
                "recommended_action": "Restart the Java application or tune JVM heap settings",
                "safe_to_auto_remediate": False,
                "explanation": "A Java process is stuck in garbage collection, consuming nearly all CPU.",
            }
        elif any(kw in output_lower for kw in ("disk", "/dev/", "filesystem")) and any(pct in output for pct in ("100%", "99%", "98%", "97%", "96%", "95%", "94%", "93%", "92%", "91%", "90%", "89%")):
            return {
                "root_cause": "Disk space critically low",
                "severity": "warning",
                "recommended_action": "Clean up log files or expand the EBS volume",
                "safe_to_auto_remediate": True,
                "explanation": "The root filesystem is running out of space. Log rotation or volume expansion needed.",
            }
        elif "no application processes" in output_lower:
            return {
                "root_cause": "No application processes running — instance is genuinely idle",
                "severity": "info",
                "recommended_action": "Safe to stop this instance to save costs",
                "safe_to_auto_remediate": True,
                "explanation": "The instance has no active workloads. It's safe to shut down.",
            }
        else:
            return {
                "root_cause": "Unable to determine specific root cause from output",
                "severity": "info",
                "recommended_action": "Manual investigation recommended",
                "safe_to_auto_remediate": False,
                "explanation": "Diagnostic output didn't match known failure patterns. Human review needed.",
            }
