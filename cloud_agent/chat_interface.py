"""
Natural Language Chat Interface for Cloud Agent.

This module allows users to interact with the cloud agent using natural language.
It parses user queries, maps them to existing tools, executes the tools, and
returns summarized responses.
"""

import os
import re
from typing import Dict, List, Any, Optional, Tuple
from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.cert_monitor import CertMonitorTool
from cloud_agent.tools.idle_server import IdleServerTool
from cloud_agent.tools.cost_monitor import CostMonitorTool
from cloud_agent.tools.security_auditor import SecurityAuditorTool
from cloud_agent.tools.disk_cleanup import DiskCleanupTool
from cloud_agent.tools.backup_manager import BackupManagerTool
from cloud_agent.tools.rightsizer import RightsizeTool
from cloud_agent.tools.scheduler import SchedulerTool
from cloud_agent.tools.cross_domain import CrossDomainCorrelationTool
from cloud_agent.tools.tag_enforcer import TagEnforcerTool
from cloud_agent.tools.query_optimizer import QueryOptimizerTool
from cloud_agent.cloud.provider import CloudProvider
from cloud_agent.utils.logger import get_logger
from cloud_agent.utils.config import load_config

logger = get_logger(__name__)

# Read-only action used for each intent. The chat interface never changes
# infrastructure: remediation goes through the agent's plan / approval flow.
_READ_ONLY_ACTION = {
    "cert_monitor": "check",
    "idle_server": "check",
    "cost_monitor": "check",
    "security_audit": "full_scan",
    "disk_cleanup": "check",
    "backup_manager": "check",
    "rightsizer": "check",
    "scheduler": "check",
    "tag_enforcer": "check",
    "cross_domain": "correlate",
    "query_optimizer": "analyze",
}

# Ties are broken by this order (more specific intents first).
_INTENT_PRIORITY = [
    "cert_monitor", "backup_manager", "query_optimizer", "rightsizer", "scheduler",
    "tag_enforcer", "cross_domain", "idle_server", "cost_monitor", "disk_cleanup",
    "security_audit",
]

_DESTRUCTIVE_VERBS = re.compile(
    r"\b(stop|shut\s*down|halt|turn\s+off|terminate|delete|destroy|remove|kill|"
    r"start|boot|turn\s+on|resume|resize|downgrade|freeze)\b"
)
_GREETINGS = {"help", "hello", "hi", "hey", "status", "menu", "commands"}


def _keyword_pattern(keyword: str) -> re.Pattern:
    """Whole-word match that tolerates simple plurals / tenses (cert → certs, expire → expired)."""
    body = r"\s+".join(re.escape(w) for w in keyword.split())
    return re.compile(rf"\b{body}(?:s|es|d|ed|ing)?\b")


class ChatInterface:
    """
    Handles natural language queries and maps them to specific cloud agent tools.

    Pipeline (paper §VI, Fig. 4): keyword/regex intent classification →
    read-only tool dispatch → formatted response (optionally rephrased by
    the LLM when an API key is configured).
    """

    def __init__(self, provider: CloudProvider, config: Optional[Dict[str, Any]] = None):
        self.provider = provider
        self.config = config or load_config()
        self.tools: Dict[str, Any] = {
            "cert_monitor": CertMonitorTool(provider, self.config),
            "idle_server": IdleServerTool(provider, self.config),
            "cost_monitor": CostMonitorTool(provider, self.config),
            "security_audit": SecurityAuditorTool(provider, self.config),
            "disk_cleanup": DiskCleanupTool(provider, self.config),
            "backup_manager": BackupManagerTool(provider, self.config),
            "rightsizer": RightsizeTool(provider, self.config),
            "scheduler": SchedulerTool(provider, self.config),
            "cross_domain": CrossDomainCorrelationTool(provider, self.config),
            "tag_enforcer": TagEnforcerTool(provider, self.config),
            "query_optimizer": QueryOptimizerTool(provider, self.config),
        }
        self.intent_map = self._build_intent_map()
        self._patterns = {
            intent: [(_keyword_pattern(k), len(k.split())) for k in kws]
            for intent, kws in self.intent_map.items()
        }

    def _build_intent_map(self) -> Dict[str, List[str]]:
        """
        Maps keywords/phrases to intents. Multi-word phrases score higher.
        ``general_status`` is a fallback used only when no specific intent matches.
        """
        return {
            "cert_monitor": [
                "certificate", "cert", "ssl", "tls", "expire", "expiry", "expiration",
                "https", "domain", "renewal", "renew", "acm",
            ],
            "idle_server": [
                "idle", "unused", "inactive", "sleeping", "zombie", "underutilized",
                "underused", "not used", "doing nothing", "low cpu", "low usage", "wasting",
            ],
            "cost_monitor": [
                "cost", "spend", "bill", "billing", "price", "expense", "budget", "money",
                "how much", "expensive", "saving", "savings", "forecast", "anomaly",
                "anomalies", "anomalous", "daily cost",
            ],
            "security_audit": [
                "security", "secure", "vulnerability", "vulnerable", "risk", "open port",
                "port", "public", "firewall", "audit", "compliance", "breach", "threat",
                "attack", "exposed", "unsafe", "permission", "encryption", "encrypted",
                "security group",
            ],
            "disk_cleanup": [
                "disk", "storage", "space", "cleanup", "clean up", "orphan", "orphaned",
                "unattached", "volume", "ebs", "disk usage", "free up",
            ],
            "backup_manager": [
                "backup", "back up", "snapshot", "restore", "recovery", "disaster", "retention",
            ],
            "rightsizer": [
                "rightsize", "right-size", "right size", "oversized", "over-provisioned",
                "overprovisioned", "downsize", "smaller instance", "instance type",
            ],
            "scheduler": [
                "schedule", "scheduler", "business hours", "after hours", "overnight",
                "weekend", "office hours",
            ],
            "tag_enforcer": [
                "tag", "tagging", "untagged", "label", "owner tag", "cost allocation",
            ],
            "cross_domain": [
                "correlate", "correlation", "cloudtrail", "root cause", "why did", "investigate",
                "guardduty", "security hub",
            ],
            "query_optimizer": [
                "query", "sql", "database", "rds", "slow query", "postgres", "postgresql",
                "mysql", "index", "explain", "db performance",
            ],
            "general_status": [
                "instance", "server", "running", "stopped", "overview", "fleet", "ec2",
                "compute", "machine", "node", "infrastructure", "health", "utilization",
                "how many",
            ],
        }

    def _parse_query(self, query: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Return (intent, requested_verb, resource_id) for *query*."""
        q = query.lower().strip()
        resource_id = None
        id_match = re.search(r"\b(i-[0-9a-f]{8,17}|vol-[0-9a-f]{8,17}|sg-[0-9a-f]{8,17})\b", q)
        if id_match:
            resource_id = id_match.group(1)

        verb_match = _DESTRUCTIVE_VERBS.search(q)
        verb = verb_match.group(1) if verb_match else None

        words = re.findall(r"[a-z0-9'-]+", q)
        if (len(words) <= 2 and set(words) & _GREETINGS) or "what can you do" in q:
            return "help", verb, resource_id

        scores: Dict[str, int] = {}
        for intent, patterns in self._patterns.items():
            if intent == "general_status":
                continue
            score = sum(weight for pat, weight in patterns if pat.search(q))
            if score:
                scores[intent] = score
        if scores:
            best = max(scores.values())
            for intent in _INTENT_PRIORITY:
                if scores.get(intent) == best:
                    return intent, verb, resource_id

        if any(pat.search(q) for pat, _ in self._patterns["general_status"]):
            return "general_status", verb, resource_id
        return None, verb, resource_id

    def _detect_intent(self, query: str) -> Optional[str]:
        """Classify *query* into an intent name (or None if unrecognised)."""
        return self._parse_query(query)[0]

    def _execute_tool(self, tool_name: str, action_type: str = "check", resource_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Executes the read-only variant of the specified tool and returns the result.
        """
        # Handle general_status directly (not a registered tool)
        if tool_name == "general_status":
            try:
                instances = self.provider.list_instances()
                volumes = self.provider.list_volumes()
                running = [i for i in instances if i.get("state") == "running"]
                stopped = [i for i in instances if i.get("state") == "stopped"]
                orphaned = [v for v in volumes if v.get("state") == "available"]
                return {
                    "success": True,
                    "data": {
                        "instances": instances,
                        "running": running,
                        "stopped": stopped,
                        "volumes": volumes,
                        "orphaned_volumes": orphaned,
                        "total_instances": len(instances),
                        "total_running": len(running),
                        "total_stopped": len(stopped),
                        "total_volumes": len(volumes),
                        "total_orphaned": len(orphaned),
                    }
                }
            except Exception as e:
                logger.error(f"Error fetching general status: {str(e)}")
                return {"success": False, "error": str(e)}

        if tool_name not in self.tools:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        tool = self.tools[tool_name]
        safe_action = _READ_ONLY_ACTION[tool_name]
        logger.info(f"Executing tool: {tool_name} (Action: {safe_action}, Resource: {resource_id})")

        try:
            action = Action(
                tool_name=tool_name,
                action_type=safe_action,
                resource_id=resource_id or "",
                reason="Chat interface request",
                parameters={"remediate": False},
            )

            result = tool.execute(action)
            return {"success": True, "data": result}
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {str(e)}")
            return {"success": False, "error": str(e)}

    def _read_only_notice(self, verb: Optional[str], resource_id: Optional[str]) -> str:
        if not verb:
            return ""
        target = f" `{resource_id}`" if resource_id else ""
        return (
            f"\n\nℹ️ The chat assistant is read-only, so I did not {verb}{target}. "
            "Changes are proposed by the agent each cycle and executed after approval in the dashboard."
        )

    def _polish(self, query: str, text: str) -> str:
        """Optionally rephrase the response with the LLM (paper §VI); facts are kept verbatim."""
        if not self.config.get("chat", {}).get("llm_responses", False) or not os.getenv("OPENAI_API_KEY"):
            return text
        try:
            from openai import OpenAI

            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            resp = client.chat.completions.create(
                model=self.config.get("llm", {}).get("model", "gpt-4o"),
                messages=[
                    {"role": "system", "content": (
                        "Rewrite the cloud-operations report as a concise answer to the user's question. "
                        "Keep every number, resource ID and finding exactly; do not add facts.")},
                    {"role": "user", "content": f"Question: {query}\n\nReport:\n{text}"},
                ],
                temperature=0.2,
                max_tokens=400,
            )
            return resp.choices[0].message.content.strip() or text
        except Exception as exc:
            logger.warning("LLM response generation failed: %s", exc)
            return text

    def _format_response(self, intent: str, result: Dict[str, Any]) -> str:
        """
        Formats the tool result into a natural language response.
        """
        if not result.get("success"):
            return f"❌ Error: {result.get('error', 'Unknown error occurred')}"

        data = result.get("data", {})
        
        if intent == "cert_monitor":
            return self._format_cert_response(data)
        elif intent == "idle_server":
            return self._format_idle_response(data)
        elif intent == "cost_monitor":
            return self._format_cost_response(data)
        elif intent == "security_audit":
            return self._format_security_response(data)
        elif intent == "disk_cleanup":
            return self._format_disk_response(data)
        elif intent == "query_optimizer":
            return self._format_query_optimizer_response(data)
        elif intent == "backup_manager":
            return self._format_backup_response(data)
        elif intent == "rightsizer":
            return self._format_rightsizer_response(data)
        elif intent == "scheduler":
            return self._format_scheduler_response(data)
        elif intent == "tag_enforcer":
            return self._format_tag_enforcer_response(data)
        elif intent == "cross_domain":
            return self._format_cross_domain_response(data)
        elif intent == "general_status":
            return self._format_general_status(data)
        elif intent == "help":
            return self._get_help_message()
        
        return f"✅ Action completed. Details: {str(data)}"

    def _format_general_status(self, data: Dict) -> str:
        """Format general infrastructure status."""
        total = data.get("total_instances", 0)
        running = data.get("total_running", 0)
        stopped = data.get("total_stopped", 0)
        vols = data.get("total_volumes", 0)
        orphaned = data.get("total_orphaned", 0)

        msg = [f"### Infrastructure Overview"]
        msg.append(f"Currently monitoring **{total}** instances ({running} running, {stopped} stopped) and **{vols}** volumes ({orphaned} orphaned).")

        instances = data.get("instances", [])
        if instances:
            msg.append(f"\n**Instance Details:**")
            for inst in instances[:10]:
                state_icon = "🟢" if inst.get("state") == "running" else "🔴"
                cpu = inst.get("cpu_percent", inst.get("cpu", "N/A"))
                if isinstance(cpu, (int, float)):
                    cpu_str = f"{cpu:.1f}%"
                else:
                    cpu_str = str(cpu)
                
                name = inst.get("name") or inst.get("instance_id", "unknown")
                msg.append(f"- {state_icon} **{name}**: {inst.get('state', '—')} (CPU: {cpu_str})")
            
            if len(instances) > 10:
                msg.append(f"- *... and {len(instances) - 10} more*")

        return "\n".join(msg)

    def _format_cert_response(self, data: Dict) -> str:
        # Handle both 'certificates' key (old format) and 'expired'/'expiring_soon' keys (new format)
        certs = data.get("certificates", [])
        
        # If no certificates key, check for expired/expiring_soon lists directly
        if not certs:
            expired = data.get("expired", [])
            expiring = data.get("expiring_soon", [])
            
            if not expired and not expiring:
                return "🔒 **Security Status:** No certificate issues found. All monitored certificates are healthy."
            
            msg = [f"### Certificate Audit Results"]
            
            if expired:
                msg.append(f"\n🚨 **{len(expired)} Expired:**")
                for c in expired:
                    expiry_date = c.get('expiry_date', '')
                    if hasattr(expiry_date, 'strftime'):
                        expiry_date = expiry_date.strftime("%Y-%m-%d")
                    msg.append(f"- **{c.get('domain')}**: Expired on {expiry_date}")
            
            if expiring:
                msg.append(f"\n⚠️ **{len(expiring)} Expiring Soon:**")
                for c in expiring:
                    expiry_date = c.get('expiry_date', '')
                    if hasattr(expiry_date, 'strftime'):
                        expiry_date = expiry_date.strftime("%Y-%m-%d")
                    msg.append(f"- **{c.get('domain')}**: {c.get('days_until_expiry')} days left ({expiry_date})")
            
            return "\n".join(msg) if msg else "🔒 All certificates are valid."
        
        # Original logic for backward compatibility
        expired = [c for c in certs if c.get("status") == "expired"]
        expiring = [c for c in certs if c.get("status") == "expiring_soon"]
        
        msg = [f"### Certificate Audit Results"]
        if expired:
            msg.append(f"\n🚨 **{len(expired)} Expired:**")
            for c in expired:
                msg.append(f"- **{c.get('domain')}**: Expired on {c.get('expiry_date')}")
        
        if expiring:
            msg.append(f"\n⚠️ **{len(expiring)} Expiring Soon:**")
            for c in expiring:
                msg.append(f"- **{c.get('domain')}**: {c.get('days_remaining')} days left ({c.get('expiry_date')})")
        
        return "\n".join(msg) if msg else "🔒 All certificates are valid."

    def _format_idle_response(self, data: Dict) -> str:
        instances = data.get("idle_instances", [])
        if not instances:
            return "🟢 **Utilization Check:** Good news — no idle servers detected. Your infrastructure is well optimized!"
        
        msg = [f"### Idle Resource Analysis"]
        msg.append(f"Detected **{len(instances)}** underutilized instance(s):")
        for inst in instances:
            name = next((t["Value"] for t in inst.get("tags", []) if t.get("Key") == "Name"), inst.get("name", "N/A"))
            msg.append(f"- **{inst.get('instance_id')}** ({name}): CPU {inst.get('avg_cpu', 0):.1f}%")
        return "\n".join(msg)

    def _format_cost_response(self, data: Dict) -> str:
        total = data.get("total_cost", 0)
        currency = data.get("currency", "$")
        anomalies = data.get("anomalies", [])
        
        msg = [f"💰 **Current Estimated Cost:** {currency}{total:.2f}"]
        if anomalies:
            msg.append(f"\n⚠️ **{len(anomalies)} Cost Anomaly(ies) Detected:**")
            for a in anomalies:
                msg.append(f"   - {a.get('service')}: {a.get('message')}")
        return "\n".join(msg)

    def _format_security_response(self, data: Dict) -> str:
        risks = data.get("risks", [])
        if not risks:
            risks = data.get("findings", [])
            
        if not risks:
            return "🛡️ **Security Status:** No security risks detected. Your environment is secure!"
        
        high = [r for r in risks if r.get("severity") in ("critical", "high")]
        medium = [r for r in risks if r.get("severity") in ("medium", "warning")]
        low = [r for r in risks if r.get("severity") in ("low", "info")]
        
        msg = [f"### Infrastructure Security Audit"]
        msg.append(f"Found **{len(risks)}** potential security issues.")
        
        if high:
            msg.append(f"\n🔴 **Critical/High Risk:**")
            for r in high:
                res = r.get('resource') or r.get('resource_id', 'Unknown')
                msg.append(f"- **{res}**: {r.get('detail') or r.get('description')}")
        if medium:
            msg.append(f"\n🟠 **Medium Risk:**")
            for r in medium:
                res = r.get('resource') or r.get('resource_id', 'Unknown')
                msg.append(f"- **{res}**: {r.get('detail') or r.get('description')}")
        if low:
            msg.append(f"\n🟡 **Low Risk:**")
            for r in low:
                res = r.get('resource') or r.get('resource_id', 'Unknown')
                msg.append(f"- **{res}**: {r.get('detail') or r.get('description')}")
        
        return "\n".join(msg)

    def _format_disk_response(self, data: Dict) -> str:
        orphaned = data.get("orphaned_volumes", [])
        if not orphaned:
            return "🧹 **Storage Check:** No orphaned volumes found."
        msg = [f"### Orphaned Volumes",
               f"Found **{len(orphaned)}** unattached volume(s), **{data.get('reclaimable_gb', 0)} GB** reclaimable:"]
        for v in orphaned:
            msg.append(f"- **{v.get('volume_id')}**: {v.get('size_gb')} GB, unattached {v.get('unattached_days')} days")
        return "\n".join(msg)

    def _format_query_optimizer_response(self, data: Dict) -> str:
        status = data.get("status", "unknown")
        scanned = data.get("databases_scanned", 0)
        slow = data.get("slow_databases", [])
        analyses = data.get("optimizations", [])
        pi_unavailable = data.get("pi_unavailable", [])
        total_opts = sum(len(a.get("optimizations", [])) for a in analyses)

        if status == "pi_unavailable" or pi_unavailable:
            msg = [f"### Query Optimizer"]
            msg.append(f"Scanned **{scanned}** RDS database(s), but Performance Insights was unavailable for **{len(pi_unavailable)}** high-latency database(s).")
            for item in pi_unavailable[:5]:
                msg.append(f"- **{item.get('db_instance_id')}**: {item.get('diagnostic')}")
                if item.get("recommendation"):
                    msg.append(f"  Fix: {item.get('recommendation')}")
            return "\n".join(msg)

        if status in ("no_databases", "all_healthy", "no_slow_queries") or total_opts == 0:
            return f"### Query Optimizer\nScanned **{scanned}** RDS database(s). No slow queries above threshold were found."

        msg = [f"### Query Optimizer Report"]
        msg.append(f"Scanned **{scanned}** RDS database(s), found **{len(slow)}** high-latency database(s), and suggested **{total_opts}** optimization(s).")
        for analysis in analyses:
            msg.append(f"\n**{analysis.get('db_instance_id')}** ({analysis.get('engine')})")
            for opt in analysis.get("optimizations", [])[:5]:
                msg.append(f"- **{opt.get('severity', 'info').upper()}**: {opt.get('problem')}")
                if opt.get("index_suggestions"):
                    msg.append(f"  Index: `{opt['index_suggestions'][0]}`")
                msg.append(f"  Improvement: {opt.get('estimated_improvement', 'unknown')}")
        return "\n".join(msg)

    def _format_backup_response(self, data: Dict) -> str:
        status = data.get("status", "unknown")
        msg = f"💾 **Backup Status:** {status}"
        if "snapshots" in data:
            msg += f"\n   - Managed {len(data['snapshots'])} snapshot(s)."
        return msg

    def _format_rightsizer_response(self, data: Dict) -> str:
        if "recommendations" in data:
            recs = data["recommendations"]
            if not recs:
                return "📐 **Rightsizing:** No over-provisioned instances found."
            lines = [f"### Rightsizing Recommendations ({len(recs)})"]
            for r in recs:
                lines.append(f"- **{r.get('instance_id')}**: `{r.get('current_type')}` → `{r.get('recommended_type')}` ({r.get('reason')})")
            return "\n".join(lines)
        instance_id = data.get("instance_id", "unknown")
        current = data.get("current_type", "unknown")
        recommended = data.get("recommended_type", data.get("new_type", current))
        status = data.get("status", "recommendation")
        if status == "resized":
            return f"📐 **Instance Resized:** {instance_id} changed from `{current}` → `{recommended}`."
        return f"📐 **Rightsizing Recommendation:** {instance_id} — consider downsizing from `{current}` → `{recommended}` to reduce cost."

    def _format_scheduler_response(self, data: Dict) -> str:
        status = data.get("status", "unknown")
        if status == "check_completed":
            reason = data.get("reason", "")
            pending = data.get("pending_actions", [])
            if not pending:
                return f"⏰ **Scheduler Check:** {reason}"
            msg = [f"⏰ **Scheduler Check:** {reason}"]
            for act in pending:
                msg.append(f"   - {act['instance_id']}: Will `{act['action']}` ({act['reason']})")
            return "\n".join(msg)
            
        instance_id = data.get("instance_id", "unknown")
        reason = data.get("reason", "")
        if status == "skipped":
            return f"⏰ **Scheduler (Skipped):** {instance_id} — {reason}."
        action_type = data.get("action_type", data.get("action", ""))
        if status == "stopping" or action_type == "stop":
            return f"⏰ **Scheduler (Stop):** {instance_id} — outside business hours."
        elif status == "starting" or action_type == "start":
            return f"⏰ **Scheduler (Start):** {instance_id} — business hours resumed."
        return f"⏰ **Scheduler:** Action on {instance_id} completed."

    def _format_tag_enforcer_response(self, data: Dict) -> str:
        status = data.get("status", "unknown")
        if status == "check_completed":
            reason = data.get("reason", "")
            pending = data.get("pending_actions", [])
            if not pending:
                return f"🏷️ **Tag Enforcer:** {reason}"
            msg = [f"🏷️ **Tag Enforcer:** {reason}"]
            for act in pending:
                tags = act.get("missing_tags", [])
                msg.append(f"   - {act['instance_id']}: Missing {len(tags)} tag(s) -> {', '.join(tags)}")
            return "\n".join(msg)
            
        return f"🏷️ **Tag Enforcer:** Action completed."

    def _format_cross_domain_response(self, data: Dict) -> str:
        correlation = data.get("correlation", {})
        found = correlation.get("correlation_found", False)
        summary = correlation.get("summary", "Analysis complete.")
        
        msg = [f"🔗 **Cross-Domain Analysis:** {summary}"]
        
        if found:
            root_cause = correlation.get("root_cause", "")
            if root_cause:
                msg.append(f"   - **Root Cause:** {root_cause}")
                
            actors = correlation.get("actors", [])
            if actors:
                msg.append(f"   - **Actors:** {', '.join(actors)}")
                
            recs = correlation.get("recommendations", [])
            if recs:
                msg.append("   - **Recommendations:**")
                for r in recs:
                    msg.append(f"     * {r}")
                    
            explanation = correlation.get("explanation", "")
            if explanation:
                msg.append(f"\n   *Explanation:* {explanation}")
        
        return "\n".join(msg)

    def _get_help_message(self) -> str:
        return """
👋 **Hello! I'm your Cloud Agent Assistant.**
I can help you manage your cloud infrastructure. Try asking me:

*   "Are there any expired SSL certificates?"
*   "Show me idle servers that are wasting money."
*   "What is my current cloud cost?"
*   "Run a security audit on my resources."
*   "Clean up disk space on my instances."
*   "Check the status of my backups."
*   "Rightsize my instances / downgrade over-provisioned servers."
*   "Check for missing tags on my instances."
*   "Run the scheduler for dev instances outside business hours."
*   "Correlate events and investigate recent anomalies."

Type 'exit' to quit.
        """

    def chat(self, query: str) -> str:
        """
        Main entry point for processing a user query.
        """
        return self.process_query(query)["summary"]

    def process_query(self, query: str) -> Dict[str, Any]:
        """
        Process a query and return structured data suitable for API response.
        Returns a dict with 'summary' (text response), 'data' (structured data) and 'intent'.
        """
        if not query.strip():
            return {"summary": "Please enter a valid question.", "data": [], "intent": None}

        intent, verb, resource_id = self._parse_query(query)

        if not intent:
            return {
                "summary": "🤔 I'm not sure what you mean. Try asking about certificates, costs, idle servers, "
                           "security, disk space, or backups. Type 'help' for more info.",
                "data": [],
                "intent": None,
            }

        # Handle special "help" intent
        if intent == "help":
            return {"summary": self._get_help_message(), "data": [], "intent": "help"}

        result = self._execute_tool(intent, resource_id=resource_id)

        # Extract structured data from result
        data_list: list = []
        if result.get("success") and "data" in result:
            tool_data = result["data"]
            if intent == "cert_monitor":
                data_list = tool_data.get("expired", []) + tool_data.get("expiring_soon", [])
            elif intent == "idle_server":
                data_list = tool_data.get("idle_instances", [])
            elif intent == "cost_monitor":
                data_list = tool_data.get("anomalies", [])
            elif intent == "security_audit":
                data_list = tool_data.get("findings", [])
            elif intent == "disk_cleanup":
                data_list = tool_data.get("orphaned_volumes", [])
            elif intent == "query_optimizer":
                data_list = tool_data.get("optimizations", []) or tool_data.get("pi_unavailable", [])
            elif intent == "backup_manager":
                data_list = tool_data.get("snapshots", [])
            elif intent == "rightsizer":
                data_list = tool_data.get("recommendations", [])
            elif intent in ("scheduler", "tag_enforcer"):
                data_list = tool_data.get("pending_actions", [])
            elif intent == "general_status":
                data_list = tool_data.get("instances", [])

        summary = self._format_response(intent, result) + self._read_only_notice(verb, resource_id)
        return {"summary": self._polish(query, summary), "data": data_list, "intent": intent}
