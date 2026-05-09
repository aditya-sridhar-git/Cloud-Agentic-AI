"""
Cloud Agent Dashboard — FastAPI server with WebSocket support.

Serves a real-time dashboard showing agent status, action history,
instance health, and cost metrics. Runs the agent in a background
thread and pushes updates to all connected WebSocket clients.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from cloud_agent.agent.baseagent import Plan
if TYPE_CHECKING:
    from cloud_agent.main import CloudOpsAgent

from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Cloud Agentic AI — Dashboard")

# Add CORS middleware to allow WebSocket connections
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
_agent: CloudOpsAgent | None = None
_connected_clients: list[WebSocket] = []
_current_plan: Plan | None = None
_cycle_lock = threading.Lock()
_cycle_thread: threading.Thread | None = None
_latest_state: dict[str, Any] = {
    "status": "idle",
    "cycle_count": 0,
    "last_cycle": None,
    "instances": [],
    "volumes": [],
    "costs": {},
    "actions": [],
    "security_findings": [],
    "reasoning_summary": "Initial scan in progress...",
    "thoughts": [],
}
_main_loop: asyncio.AbstractEventLoop | None = None


# ------------------------------------------------------------------
# REST endpoints
# ------------------------------------------------------------------


def _emit_thought(text: str) -> None:
    """Push a 'live thought' from the AI strategy engine to the dashboard."""
    _latest_state["thoughts"].append({
        "text": text,
        "timestamp": time.time()
    })
    # Maintain reasonable history for the 'live' console
    if len(_latest_state["thoughts"]) > 15:
        _latest_state["thoughts"].pop(0)
    _broadcast(_latest_state)
    logger.info("[magenta]Thought emitted: %s[/magenta]", text)


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the dashboard HTML."""
    html_file = _STATIC_DIR / "index.html"
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))


@app.get("/style.css")
async def style():
    return FileResponse(_STATIC_DIR / "style.css", media_type="text/css")


@app.get("/app.js")
async def script():
    return FileResponse(_STATIC_DIR / "app.js", media_type="application/javascript")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    """Serve the live dashboard HTML."""
    html_file = _STATIC_DIR / "dashboard.html"
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))


@app.get("/dashboard/{view}", response_class=HTMLResponse)
async def dashboard_view_page(view: str):
    """Serve dashboard subroutes for client-side focused views."""
    html_file = _STATIC_DIR / "dashboard.html"
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))




@app.get("/dashboard.css")
async def dashboard_style():
    return FileResponse(_STATIC_DIR / "dashboard.css", media_type="text/css")


@app.get("/confidence.css")
async def confidence_style():
    return FileResponse(_STATIC_DIR / "confidence.css", media_type="text/css")


@app.get("/dashboard.js")
async def dashboard_script():
    return FileResponse(_STATIC_DIR / "dashboard.js", media_type="application/javascript")



@app.get("/api/status")
async def get_status():
    """Return current agent state."""
    return _latest_state


@app.get("/api/history")
async def get_history():
    """Return action history from the log file."""
    if not _agent:
        return {"history": []}
    
    flattened = []
    # get_recent pulls raw JSONL which contains cycle events
    for cycle in _agent.action_logger.get_recent(50):
        # Some legacy entries might be individual log_action calls
        if "tool" in cycle or "tool_name" in cycle:
            flattened.append(cycle)
        elif "results" in cycle and isinstance(cycle["results"], list):
            if not cycle["results"]:
                # If it's an empty cycle, log it as a scan event
                flattened.append({
                    "timestamp": cycle.get("timestamp"),
                    "cycle_id": cycle.get("cycle_id"),
                    "tool": "scanner",
                    "resource": "cloud_env",
                    "action": "Analysis Complete",
                    "status": "dry_run" if _agent.dry_run else "success",
                    "reason": cycle.get("plan_summary") or "Observation complete"
                })
            else:
                for res in cycle["results"]:
                    item = res.copy()
                    item["timestamp"] = cycle.get("timestamp")
                    item["cycle_id"] = cycle.get("cycle_id")
                    flattened.append(item)
    return {"history": flattened}


@app.delete("/api/history")
async def clear_history():
    """Clear the persistent log file."""
    if _agent and _agent.action_logger._log_file.exists():
        _agent.action_logger._log_file.unlink()
        _agent.action_logger._log_file.touch()
    return {"status": "cleared"}

@app.get("/api/instances")
async def get_instances():
    """Return current instance list."""
    return {"instances": _latest_state.get("instances", [])}


@app.get("/api/confidence-metrics")
async def get_confidence_metrics():
    """
    Return a 4-pillar confidence score reflecting true infrastructure health.

    Pillars & weights
    -----------------
    Resource Health   50 %  — CPU (cloud-native curve), memory, idle ratio, network I/O
    Financial Health  20 %  — cost vs baseline delta, 7-day trend direction
    Security & Compliance 20 % — critical/warning findings (hard-capped), tag compliance
    Operational Health 10 % — alert noise ratio, SLO proxy via uptime ratio

    The old "action completion" pillar (10 %) has been removed: it measured
    the agent's own output, not infrastructure state, making it circular.
    """
    instances       = _latest_state.get("instances", [])
    security        = _latest_state.get("security_findings", [])
    costs           = _latest_state.get("costs", {})
    actions         = _latest_state.get("actions", [])

    running = [i for i in instances if i.get("state") == "running"]

    # ------------------------------------------------------------------
    # PILLAR 1 — Resource Health (50 %)
    # Sub-metrics: CPU (30 %), memory (26 %), idle ratio (22 %), network (22 %)
    # ------------------------------------------------------------------
    cpu_score = 0.0
    mem_score = 0.0
    idle_score = 0.0
    net_score = 0.0

    if running:
        # CPU: cloud-native bell curve peaking at 70 %, tolerable to 95 %
        cpus = [max(0.0, float(i.get("cpu_percent", i.get("cpu", 50)))) for i in running]
        avg_cpu = sum(cpus) / len(cpus)
        if avg_cpu <= 70:
            cpu_score = (avg_cpu / 70) * 100          # ramp up to 100 at 70 %
        else:
            cpu_score = max(0, 100 - (avg_cpu - 70) * (100 / 30))  # drops to 0 at 100 %

        # Memory: healthy band 30–80 %; penalise extremes
        mems = [max(0.0, float(i.get("memory_percent", i.get("mem", 50)))) for i in running]
        avg_mem = sum(mems) / len(mems)
        if 30 <= avg_mem <= 80:
            mem_score = 100.0
        elif avg_mem < 30:
            mem_score = (avg_mem / 30) * 100
        else:
            mem_score = max(0, 100 - (avg_mem - 80) * 5)

        # Idle ratio: penalise if >40 % of running fleet is near-zero CPU
        idle_count = sum(1 for c in cpus if c < 5)
        idle_ratio = idle_count / len(running)
        idle_score = max(0, (1.0 - idle_ratio * 2.5)) * 100  # 40 % idle → 0

        # Network: normalised — non-zero I/O signals active workload
        nets = [max(0.0, float(i.get("network_in", i.get("network", 10)))) for i in running]
        avg_net = sum(nets) / len(nets)
        net_score = min(100, (avg_net / 50) * 100) if avg_net > 0 else 50.0  # unknown → neutral

    resource_pillar = (
        0.30 * cpu_score +
        0.26 * mem_score +
        0.22 * idle_score +
        0.22 * net_score
    )

    # ------------------------------------------------------------------
    # PILLAR 2 — Financial Health (20 %)
    # Sub-metrics: spend vs baseline (60 %), 7-day trend (40 %)
    # ------------------------------------------------------------------
    baseline  = float(costs.get("baseline_daily", 0) or 0)
    current   = float(costs.get("daily", costs.get("current_daily", costs.get("daily_cost", 0))) or 0)
    delta_pct = abs(current - baseline) / max(baseline, 0.01) * 100

    # Spend score: up to 30 % over baseline is fine; beyond that, linear decay
    if delta_pct <= 10:
        spend_score = 100.0
    elif delta_pct <= 30:
        spend_score = 100 - (delta_pct - 10) * 2.5       # 100 → 50 over the 10-30 % band
    else:
        spend_score = max(0, 50 - (delta_pct - 30) * 1.0) # hard drop beyond 30 %

    # Trend: positive delta (costs rising) penalises; negative (falling) rewards
    trend_pct = float(costs.get("delta_pct", 0) or 0)    # signed: + means rising
    if trend_pct <= 0:
        trend_score = min(100, 70 + abs(trend_pct))       # falling costs = bonus
    else:
        trend_score = max(0, 100 - trend_pct * 2)

    financial_pillar = 0.60 * spend_score + 0.40 * trend_score

    # ------------------------------------------------------------------
    # PILLAR 3 — Security & Compliance (20 %)
    # Each critical: -18 pts (hard cap prevents one finding from killing score)
    # Each warning:  -4 pts
    # Tag compliance adds up to +20 bonus pts within this pillar
    # ------------------------------------------------------------------
    critical_count = sum(1 for f in security if f.get("severity") == "critical")
    warning_count  = len(security) - critical_count

    security_base = 100.0
    security_base -= min(critical_count * 18, 70)  # hard cap: max -70 from criticals
    security_base -= min(warning_count  *  4, 30)  # hard cap: max -30 from warnings
    security_base  = max(0.0, security_base)

    # Tag compliance bonus (0 – 20 pts)
    required_tags = {"Owner", "Environment", "Project"}
    compliant = sum(
        1 for inst in instances
        if required_tags.issubset({t.get("Key") for t in inst.get("tags", [])})
    )
    tag_pct      = (compliant / len(instances) * 100) if instances else 100.0
    tag_bonus    = tag_pct * 0.20   # max +20 pts into the pillar base
    security_pillar = min(100, security_base + tag_bonus)

    # ------------------------------------------------------------------
    # PILLAR 4 — Operational Health (10 %)
    # Sub-metrics: SLO proxy — % instances healthy (60 %),
    #              alert noise — % actions that are alerts vs real actions (40 %)
    # ------------------------------------------------------------------
    # SLO proxy: running & CPU < 85 %
    healthy = sum(
        1 for i in running
        if float(i.get("cpu_percent", i.get("cpu", 0))) < 85
    )
    slo_score = (healthy / len(running) * 100) if running else 100.0

    # Alert noise: high proportion of alert/freeze actions signals instability
    alert_actions = sum(
        1 for a in actions
        if a.get("action_type") in ("alert", "freeze")
    )
    noise_ratio   = alert_actions / max(len(actions), 1)
    noise_score   = max(0, 100 - noise_ratio * 100)

    ops_pillar = 0.60 * slo_score + 0.40 * noise_score

    # ------------------------------------------------------------------
    # Overall weighted confidence
    # ------------------------------------------------------------------
    overall = (
        0.50 * resource_pillar +
        0.20 * financial_pillar +
        0.20 * security_pillar +
        0.10 * ops_pillar
    )

    metrics = {
        "resource_health": {
            "label": "Resource Health",
            "weight": 0.50,
            "value": round(resource_pillar, 1),
            "description": "CPU (cloud-native 70% peak), memory, idle-instance ratio, and network I/O across running fleet.",
            "sub": {
                "cpu":   round(cpu_score,  1),
                "memory": round(mem_score, 1),
                "idle":  round(idle_score, 1),
                "network": round(net_score, 1),
            },
        },
        "financial_health": {
            "label": "Financial Health",
            "weight": 0.20,
            "value": round(financial_pillar, 1),
            "description": "Spend vs baseline delta (±30% tolerance) combined with 7-day cost trend direction.",
            "sub": {
                "spend_vs_baseline": round(spend_score, 1),
                "trend":             round(trend_score, 1),
            },
        },
        "security_compliance": {
            "label": "Security & Compliance",
            "weight": 0.20,
            "value": round(security_pillar, 1),
            "description": "Critical findings cap at -70 pts; warnings at -30 pts. Tag compliance adds up to +20 pts.",
            "sub": {
                "findings_base": round(security_base, 1),
                "tag_bonus":     round(tag_bonus, 1),
            },
        },
        "operational_health": {
            "label": "Operational Health",
            "weight": 0.10,
            "value": round(ops_pillar, 1),
            "description": "SLO proxy (% instances below 85% CPU) weighted with alert noise ratio.",
            "sub": {
                "slo_proxy":   round(slo_score,   1),
                "noise_score": round(noise_score, 1),
            },
        },
    }

    return {
        "overall_confidence": round(overall, 1),
        "metrics": metrics,
    }



@app.post("/api/run-cycle")
async def trigger_cycle():
    """Manually trigger one agent cycle (Observation -> Planning)."""
    global _cycle_thread
    if _agent is None:
        return {"error": "Agent not initialized"}
    if _cycle_thread and _cycle_thread.is_alive():
        return {"status": "already_running"}
    _cycle_thread = threading.Thread(target=_run_agent_cycle, daemon=True)
    _cycle_thread.start()
    return {"status": "cycle_triggered"}


@app.post("/api/approve-action")
async def approve_action(request: Request):
    """Execute a single specific action from the current plan."""
    data = await request.json()
    tool_name = data.get("tool_name")
    resource_id = data.get("resource_id")
    action_type = data.get("action_type")
    
    if not _agent or not _current_plan:
        return {"error": "No active agent or plan"}

    # Find the action in the current plan
    match = None
    for action in _current_plan.actions:
        if (action.tool_name == tool_name and 
            action.resource_id == resource_id and 
            action.action_type == action_type):
            match = action
            break
    
    if not match:
        return {"error": "Action not found in plan"}

    # Execute it live (bypass dry_run for this explicit manual approval)
    logger.info("[cyan]Dashboard: Manual approval for %s on %s[/cyan]", tool_name, resource_id)
    try:
        # We manually register the result to results feed
        results = _agent.act(Plan(actions=[match], summary="Manual approval"))
        
        # Log the action individually so it appears in history
        if results:
            _agent.action_logger.log_action(results[0])

        # Determine the resulting status from the execution
        new_status = "executed"
        if results and "status" in results[0]:
            new_status = results[0]["status"]

        # Update the UI state so it doesn't show "pending_approval" anymore
        for act in _latest_state.get("actions", []):
            if (act.get("tool_name") == tool_name and 
                act.get("resource_id") == resource_id and 
                act.get("action_type") == action_type):
                act["status"] = new_status
                break

        _broadcast(_latest_state)
        return {"status": "executed", "results": results}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/allocate-instance")
async def allocate_instance(request: Request):
    """Proactively provision a new compute instance."""
    data = await request.json()
    instance_type = data.get("instance_type", "t3.micro")
    region = data.get("region")
    tags = data.get("tags", [
        {"Key": "Name", "Value": f"auto-allocated-{int(time.time())}"},
        {"Key": "Provisioner", "Value": "CloudAgentDashboard"}
    ])
    
    if not _agent:
        return {"error": "Agent not initialized"}
        
    try:
        result = _agent.provider.create_instance(instance_type, region, tags)
        # Update state so it appears immediately
        observation = _agent.observe()
        _latest_state["instances"] = observation.instances
        _broadcast(_latest_state)
        return result
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/allocate-volume")
async def allocate_volume(request: Request):
    """Proactively provision a new block storage volume."""
    data = await request.json()
    size_gb = int(data.get("size_gb", 10))
    region = data.get("region")
    tags = data.get("tags", [
        {"Key": "Name", "Value": f"auto-volume-{int(time.time())}"},
        {"Key": "Provisioner", "Value": "CloudAgentDashboard"}
    ])
    
    if not _agent:
        return {"error": "Agent not initialized"}
        
    try:
        result = _agent.provider.create_volume(size_gb, region, tags)
        # Update state so it appears immediately
        observation = _agent.observe()
        _latest_state["volumes"] = observation.disks
        _broadcast(_latest_state)
        return result
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    """Handle natural language chat queries and route to appropriate tools."""
    from cloud_agent.chat_interface import ChatInterface
    
    data = await request.json()
    query = data.get("query", "")
    
    if not query:
        return {"error": "No query provided", "response": "Please ask a question."}
    
    try:
        # Create chat interface instance with the agent's provider
        chat = ChatInterface(_agent.provider if _agent else None)
        
        # Process the query
        result = chat.process_query(query)
        
        # Format response based on result type
        if isinstance(result, dict):
            response_text = result.get("summary", "Query processed.")
            response_data = result.get("data", [])
        elif isinstance(result, list):
            response_data = result
            response_text = f"Found {len(result)} items matching your query."
        else:
            response_text = str(result)
            response_data = []
        
        return {
            "response": response_text,
            "data": response_data
        }
    except Exception as e:
        logger.exception("Chat endpoint error")
        return {
            "response": f"I encountered an error: {str(e)}",
            "data": []
        }



# ------------------------------------------------------------------
# WebSocket
# ------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global _main_loop
    if not _main_loop:
        try:
            _main_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    await ws.accept()
    _connected_clients.append(ws)
    logger.info("[green]WebSocket client connected[/green] (total: %d)", len(_connected_clients))

    # Send current state immediately
    try:
        await ws.send_json(_latest_state)
    except Exception:
        pass

    try:
        while True:
            # Keep connection alive, ignore incoming messages
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in _connected_clients:
            _connected_clients.remove(ws)
        logger.info("[yellow]WebSocket client disconnected[/yellow] (total: %d)", len(_connected_clients))


def _broadcast(data: dict[str, Any]) -> None:
    """Push data to all connected WebSocket clients safely from any thread."""
    if not _connected_clients or not _main_loop:
        return

    async def _send_to_all(payload):
        disconnected = []
        for ws in _connected_clients:
            try:
                await ws.send_json(payload)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            if ws in _connected_clients:
                _connected_clients.remove(ws)

    # Schedule the broadcast on the main event loop
    _main_loop.call_soon_threadsafe(
        lambda: asyncio.create_task(_send_to_all(data))
    )


# ------------------------------------------------------------------
# Background agent loop
# ------------------------------------------------------------------

def _run_agent_cycle() -> None:
    """Run one cycle and update global state."""
    global _latest_state, _current_plan
    if _agent is None:
        return
    if not _cycle_lock.acquire(blocking=False):
        logger.info("Dashboard cycle skipped because another cycle is already running")
        return

    try:
        _latest_state.pop("error", None)
        _latest_state["status"] = "running"
        _latest_state["thoughts"] = [] # Clear previous cycle logic
        _latest_state["security_findings"] = []
        _broadcast(_latest_state)
        _emit_thought("Starting secure handshake with cloud environment...")
        time.sleep(0.5)

        # Collect observation
        _emit_thought("Observing infrastructure telemetry and instance states...")
        time.sleep(0.5)
        observation = _agent.observe()
        _latest_state["instances"] = observation.instances
        _latest_state["volumes"] = observation.disks
        _latest_state["costs"] = observation.costs
        _emit_thought(f"Analyzing {len(observation.instances)} instances and {len(observation.disks)} volumes.")
        time.sleep(0.5)

        # Planning
        _emit_thought("Reasoning through optimization and risk signals...")
        time.sleep(0.8)
        plan = _agent.think(observation)
        _current_plan = plan
        
        _emit_thought(f"Planning complete with {len(plan.actions)} optimization item(s).")
        time.sleep(0.5)
        
        # In dashboard mode, we post the planned actions for review
        _latest_state["cycle_count"] += 1
        _latest_state["last_cycle"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _latest_state["reasoning_summary"] = plan.summary
        
        # Map actions to dashboard view
        planned_actions = []
        for a in plan.actions:
            _emit_thought(f"Correlating {a.action_type} strategy for {a.resource_id}...")
            time.sleep(0.4)

            # ----------------------------------------------------------
            # Per-action confidence: data-driven, replaces Math.random()
            # resource_sub: how clearly the resource telemetry justifies
            #               this specific action (CPU/memory signals)
            # security_sub: no security risk introduced by this action
            # cost_sub:     clear financial benefit expected
            # ----------------------------------------------------------
            target_inst = next(
                (i for i in observation.instances if i.get("instance_id") == a.resource_id),
                None,
            )
            t_cpu = float((target_inst or {}).get("cpu_percent",
                          (target_inst or {}).get("cpu", 50)) or 50)
            t_mem = float((target_inst or {}).get("memory_percent",
                          (target_inst or {}).get("mem", 50)) or 50)

            # resource_sub: idle tools score high when CPU is near 0;
            #               diagnose/security tools score high when CPU is high
            if a.tool_name in ("idle_server", "rightsizer", "scheduler"):
                resource_sub = max(0, 1.0 - t_cpu / 30)      # CPU < 30 → high confidence
            elif a.tool_name in ("diagnose_server", "security_auditor"):
                resource_sub = min(1.0, t_cpu / 80)           # CPU > 80 → high confidence
            else:
                resource_sub = 0.65                            # neutral for cost/tag tools

            # security_sub: tag/security tools boost; destructive actions penalise slightly
            destructive = a.action_type in ("terminate", "snapshot_delete", "freeze")
            security_sub = 0.55 if destructive else 0.85

            # cost_sub: cost-saving tools get high marks; pure alerts are neutral
            savings_tools = {"idle_server", "rightsizer", "disk_cleanup",
                             "cost_monitor", "scheduler"}
            cost_sub = 0.90 if a.tool_name in savings_tools else 0.60

            action_confidence = round(
                (resource_sub * 0.40 + security_sub * 0.30 + cost_sub * 0.30) * 100, 1
            )

            entry = {
                "tool_name": a.tool_name,
                "resource_id": a.resource_id,
                "action_type": a.action_type,
                "reason": a.reason,
                "status": "pending_approval",
                "confidence": action_confidence,
            }
            
            # Pre-fetch insights for specific tools (Security and Diagnosis)
            if a.tool_name == "security_auditor":
                _emit_thought("Auditing cloud security posture...")
                res = _agent.act(Plan(actions=[a], summary="Security scan for dashboard"))
                _latest_state["security_findings"] = res[0].get("findings", []) if res else []
            
            elif a.tool_name == "diagnose_server":
                _emit_thought(f"Diagnosing root cause for {a.resource_id}...")
                res = _agent.act(Plan(actions=[a], summary="Pre-diagnosis for dashboard"))
                if res and "diagnosis" in res[0]:
                    entry["diagnosis"] = res[0]["diagnosis"]

            planned_actions.append(entry)
            
        _latest_state["actions"] = planned_actions
        _emit_thought("Ready for review.")
        _latest_state["status"] = "idle"

        # Log this cycle to the persistent audit trail
        import uuid
        cycle_id = str(uuid.uuid4())[:8]
        _agent.action_logger.log_cycle(
            cycle_id=cycle_id,
            plan_summary=plan.summary,
            results=[], 
            observation_summary={
                "instances": len(observation.instances),
                "disks": len(observation.disks),
                "costs": observation.costs,
            }
        )
    except Exception as exc:
        logger.exception("Dashboard agent cycle failed")
        _latest_state["status"] = "error"
        _latest_state["error"] = str(exc)
        _emit_thought(f"Agent logic failure: {str(exc)}")
    finally:
        _cycle_lock.release()

    _broadcast(_latest_state)



def _agent_loop(interval: int) -> None:
    """Continuous background loop."""
    while True:
        _run_agent_cycle()
        time.sleep(interval)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def run_dashboard(agent: "CloudOpsAgent", port: int = 8080) -> None:
    """Start the dashboard server with the given agent."""
    global _agent
    _agent = agent

    # Start agent loop in background thread
    interval = agent.config.get("agent", {}).get("loop_interval_seconds", 60)
    bg_thread = threading.Thread(target=_agent_loop, args=(interval,), daemon=True)
    bg_thread.start()

    # Run first cycle immediately
    threading.Thread(target=_run_agent_cycle, daemon=True).start()

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
