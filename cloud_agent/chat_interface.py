"""
Natural Language Chat Interface for Cloud Agent.

This module allows users to interact with the cloud agent using natural language.
It parses user queries, maps them to existing tools, executes the tools, and
returns summarized responses.
"""

import re
from typing import Dict, List, Any, Optional, Tuple
from cloud_agent.agent.baseagent import Action
from cloud_agent.tools.cert_monitor import CertMonitorTool
from cloud_agent.tools.idle_server import IdleServerTool
from cloud_agent.tools.cost_monitor import CostMonitorTool
from cloud_agent.tools.security_auditor import SecurityAuditorTool
from cloud_agent.tools.disk_cleanup import DiskCleanupTool
from cloud_agent.tools.backup_manager import BackupManagerTool
from cloud_agent.cloud.provider import CloudProvider
from cloud_agent.utils.logger import get_logger
from cloud_agent.utils.config import load_config

logger = get_logger(__name__)

class ChatInterface:
    """
    Handles natural language queries and maps them to specific cloud agent tools.
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
        }
        self.intent_map = self._build_intent_map()

    def _build_intent_map(self) -> Dict[str, List[str]]:
        """
        Maps keywords/phrases to tool names.
        """
        return {
            "cert_monitor": [
                "certificate", "ssl", "tls", "expire", "expiry", "cert", 
                "https", "domain", "secure", "renewal", "renew"
            ],
            "idle_server": [
                "idle", "unused", "inactive", "waste", "low cpu", "low usage",
                "zombie", "empty", "underutilized", "underused", "not used",
                "wasting", "doing nothing"
            ],
            "cost_monitor": [
                "cost", "spend", "bill", "price", "expense", "budget", 
                "money", "charging", "how much", "spending", "expensive",
                "cheap", "saving", "savings", "forecast", "daily cost"
            ],
            "security_audit": [
                "security", "vulnerability", "risk", "open port", "public",
                "firewall", "audit", "compliance", "breach", "threat",
                "attack", "exposed", "unsafe", "protect", "permission",
                "iam", "access", "encryption", "encrypted"
            ],
            "disk_cleanup": [
                "disk", "storage", "space", "cleanup", "clean", "full", 
                "delete", "remove", "free up", "volume", "ebs", "orphan",
                "orphaned", "unattached", "disk usage"
            ],
            "backup_manager": [
                "backup", "snapshot", "restore", "recovery", "save", 
                "copy", "archive", "replicate", "disaster"
            ],
            "general_status": [
                "instance", "instances", "server", "servers", "running",
                "stopped", "status", "overview", "show", "list", "tell",
                "current", "what", "how many", "count", "health",
                "infrastructure", "fleet", "ec2", "compute", "machine",
                "machines", "node", "nodes", "cpu", "utilization"
            ]
        }

    def _detect_intent(self, query: str) -> Tuple[Optional[str], str, Optional[str]]:
        """
        Detects the user's intent, action type, and resource ID.
        Returns (tool_name, action_type, resource_id).
        """
        query_lower = query.lower()
        
        # 1. Extract Resource ID (e.g., i-0a1b2c3d4e5f6g7h8)
        resource_id = None
        id_match = re.search(r'i-[a-z0-9]{8,17}', query_lower)
        if id_match:
            resource_id = id_match.group(0)
            
        # 2. Detect Action Type
        action_type = "check"
        if any(word in query_lower for word in ["stop", "shutdown", "halt", "turn off"]):
            action_type = "stop"
        elif any(word in query_lower for word in ["start", "boot", "turn on", "resume"]):
            action_type = "start"
        elif any(word in query_lower for word in ["terminate", "delete", "destroy", "remove"]):
            action_type = "terminate"
            
        # 3. Detect Tool Intent
        scores: Dict[str, int] = {tool: 0 for tool in self.intent_map}
        for tool, keywords in self.intent_map.items():
            for keyword in keywords:
                if keyword in query_lower:
                    scores[tool] += 1
        
        best_tool = max(scores, key=scores.get)
        if scores[best_tool] > 0:
            return best_tool, action_type, resource_id
        
        # Fallback for generic help or status
        if any(word in query_lower for word in ["help", "hello", "hi", "hey", "what can you do"]):
            return "help", "check", None
            
        return None, "check", None

    def _execute_tool(self, tool_name: str, action_type: str = "check", resource_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Executes the specified tool and returns the result.
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
        logger.info(f"Executing tool: {tool_name} (Action: {action_type}, Resource: {resource_id})")
        
        try:
            action = Action(
                tool_name=tool_name,
                action_type=action_type, 
                resource_id=resource_id or "", 
                reason="Chat interface request",
                parameters={}
            )
            
            result = tool.execute(action)
            return {"success": True, "data": result}
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {str(e)}")
            return {"success": False, "error": str(e)}

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
        elif intent == "backup_manager":
            return self._format_backup_response(data)
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

        msg = [f"🖥️ **Infrastructure Overview:**"]
        msg.append(f"   • **{total}** total instance(s): {running} running, {stopped} stopped")
        msg.append(f"   • **{vols}** volume(s): {orphaned} orphaned/unattached")

        instances = data.get("instances", [])
        if instances:
            msg.append(f"\n📋 **Instance Details:**")
            for inst in instances[:10]:
                state_icon = "🟢" if inst.get("state") == "running" else "🔴"
                cpu = inst.get("cpu_percent", inst.get("cpu", "N/A"))
                name = inst.get("name", inst.get("instance_id", "unknown"))
                msg.append(f"   {state_icon} {name} ({inst.get('instance_type', '—')}) — {inst.get('state', '—')}, CPU: {cpu}%")
            if len(instances) > 10:
                msg.append(f"   ... and {len(instances) - 10} more")

        return "\n".join(msg)

    def _format_cert_response(self, data: Dict) -> str:
        # Handle both 'certificates' key (old format) and 'expired'/'expiring_soon' keys (new format)
        certs = data.get("certificates", [])
        
        # If no certificates key, check for expired/expiring_soon lists directly
        if not certs:
            expired = data.get("expired", [])
            expiring = data.get("expiring_soon", [])
            
            if not expired and not expiring:
                return "🔒 No certificate issues found. All monitored certificates are healthy."
            
            msg = []
            if expired:
                msg.append(f"🚨 **{len(expired)} Expired Certificate(s):**")
                for c in expired:
                    expiry_date = c.get('expiry_date', '')
                    if hasattr(expiry_date, 'strftime'):
                        expiry_date = expiry_date.strftime("%Y-%m-%d")
                    msg.append(f"   - {c.get('domain')}: Expired on {expiry_date}")
            
            if expiring:
                msg.append(f"⚠️ **{len(expiring)} Expiring Soon:**")
                for c in expiring:
                    expiry_date = c.get('expiry_date', '')
                    if hasattr(expiry_date, 'strftime'):
                        expiry_date = expiry_date.strftime("%Y-%m-%d")
                    msg.append(f"   - {c.get('domain')}: Expires in {c.get('days_until_expiry')} days ({expiry_date})")
            
            return "\n".join(msg) if msg else "🔒 All certificates are valid."
        
        # Original logic for backward compatibility
        expired = [c for c in certs if c.get("status") == "expired"]
        expiring = [c for c in certs if c.get("status") == "expiring_soon"]
        
        msg = []
        if expired:
            msg.append(f"🚨 **{len(expired)} Expired Certificate(s):**")
            for c in expired:
                msg.append(f"   - {c.get('domain')}: Expired on {c.get('expiry_date')}")
        
        if expiring:
            msg.append(f"⚠️ **{len(expiring)} Expiring Soon:**")
            for c in expiring:
                msg.append(f"   - {c.get('domain')}: Expires in {c.get('days_remaining')} days ({c.get('expiry_date')})")
        
        return "\n".join(msg) if msg else "🔒 All certificates are valid."

    def _format_idle_response(self, data: Dict) -> str:
        instances = data.get("idle_instances", [])
        if not instances:
            return "🟢 No idle servers detected. Your infrastructure utilization looks good."
        
        msg = [f"🐢 Found **{len(instances)}** idle instance(s):"]
        for inst in instances:
            msg.append(f"   - {inst.get('id')} ({inst.get('name')}): CPU {inst.get('avg_cpu')}%, Network {inst.get('avg_network')}%")
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
            return "🛡️ No security risks detected. Your configuration looks secure."
        
        high = [r for r in risks if r.get("severity") == "high"]
        medium = [r for r in risks if r.get("severity") == "medium"]
        low = [r for r in risks if r.get("severity") == "low"]
        
        msg = [f"🛡️ **Security Audit Results:** {len(risks)} issue(s) found."]
        if high:
            msg.append(f"\n🔴 **High Severity ({len(high)}):**")
            for r in high:
                msg.append(f"   - {r.get('resource')}: {r.get('description')}")
        if medium:
            msg.append(f"\n🟠 **Medium Severity ({len(medium)}):**")
            for r in medium:
                msg.append(f"   - {r.get('resource')}: {r.get('description')}")
        if low:
            msg.append(f"\n🟡 **Low Severity ({len(low)}):**")
            for r in low:
                msg.append(f"   - {r.get('resource')}: {r.get('description')}")
        
        return "\n".join(msg)

    def _format_disk_response(self, data: Dict) -> str:
        # Disk cleanup usually returns stats on what was cleaned or could be cleaned
        freed = data.get("space_freed_gb", 0)
        files_removed = data.get("files_removed", 0)
        
        if freed > 0 or files_removed > 0:
            return f"🧹 **Cleanup Complete:** Freed {freed:.2f} GB by removing {files_removed} files."
        return "🧹 No unnecessary files found to clean up."

    def _format_backup_response(self, data: Dict) -> str:
        status = data.get("status", "unknown")
        msg = f"💾 **Backup Status:** {status}"
        if "snapshots" in data:
            msg += f"\n   - Managed {len(data['snapshots'])} snapshot(s)."
        return msg

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

Type 'exit' to quit.
        """

    def chat(self, query: str) -> str:
        """
        Main entry point for processing a user query.
        """
        if not query.strip():
            return "Please enter a valid question."
        
        intent, action, resource_id = self._detect_intent(query)
        
        if not intent:
            return "🤔 I'm not sure what you mean. Try asking about certificates, costs, idle servers, security, disk space, or backups. Type 'help' for more info."
        
        # Handle special "help" intent which doesn't need tool execution
        if intent == "help":
            return self._get_help_message()
        
        result = self._execute_tool(intent, action, resource_id)
        return self._format_response(intent, result)

    def process_query(self, query: str) -> Dict[str, Any]:
        """
        Process a query and return structured data suitable for API response.
        Returns a dict with 'summary' (text response) and 'data' (structured data).
        """
        if not query.strip():
            return {"summary": "Please enter a valid question.", "data": []}
        
        intent, action, resource_id = self._detect_intent(query)
        
        if not intent:
            return {
                "summary": "🤔 I'm not sure what you mean. Try asking about certificates, costs, idle servers, security, disk space, or backups.",
                "data": []
            }
        
        # Handle special "help" intent
        if intent == "help":
            return {"summary": self._get_help_message(), "data": []}
        
        # Execute the tool
        result = self._execute_tool(intent, action, resource_id)
        
        # Extract structured data from result
        data_list = []
        if result.get("success") and "data" in result:
            tool_data = result["data"]
            
            # Convert tool-specific data structures to list format
            if intent == "cert_monitor":
                expired = tool_data.get("expired", [])
                expiring = tool_data.get("expiring_soon", [])
                data_list = expired + expiring
            elif intent == "idle_server":
                data_list = tool_data.get("idle_instances", [])
            elif intent == "cost_monitor":
                data_list = tool_data.get("anomalies", [])
            elif intent == "security_audit":
                data_list = tool_data.get("risks", [])
            elif intent == "disk_cleanup":
                data_list = [tool_data]  # Single summary object
            elif intent == "backup_manager":
                data_list = tool_data.get("snapshots", [])
            elif intent == "general_status":
                data_list = tool_data.get("instances", [])
        
        # Format the text summary
        summary = self._format_response(intent, result)
        
        return {"summary": summary, "data": data_list}
