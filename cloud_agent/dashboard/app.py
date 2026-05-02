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

from cloud_agent.agent.baseagent import Plan
if TYPE_CHECKING:
    from cloud_agent.main import CloudOpsAgent

from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Cloud Agentic AI — Dashboard")

# Global state
_agent: CloudOpsAgent | None = None
_connected_clients: list[WebSocket] = []
_current_plan: Plan | None = None
_main_loop = None
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


@app.get("/dashboard.css")
async def dashboard_style():
    return FileResponse(_STATIC_DIR / "dashboard.css", media_type="text/css")


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


@app.post("/api/run-cycle")
async def trigger_cycle():
    """Manually trigger one agent cycle (Observation -> Planning)."""
    if _agent is None:
        return {"error": "Agent not initialized"}
    threading.Thread(target=_run_agent_cycle, daemon=True).start()
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
            # Fallback if not in a loop yet
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

    _latest_state["status"] = "running"
    _latest_state["thoughts"] = [] # Clear previous cycle logic
    _emit_thought("▶ INITIALIZING: Establishing secure handshake with cloud environment...")
    time.sleep(0.5) 
    
    try:
        # Collect observation
        _emit_thought("▶ OBSERVING: Scanning infrastructure telemetry and instance states...")
        time.sleep(0.5)
        observation = _agent.observe()
        _latest_state["instances"] = observation.instances
        _latest_state["volumes"] = observation.disks
        _latest_state["costs"] = observation.costs
        _emit_thought(f"▶ ANALYZING: {len(observation.instances)} instances and {len(observation.disks)} volumes ingested.")
        time.sleep(0.5)

        # Planning
        _emit_thought("▶ REASONING: Simulating optimization logic via Reasoning Engine...")
        time.sleep(0.8)
        plan = _agent.think(observation)
        _current_plan = plan
        
        _emit_thought(f"▶ PLANNING: Developed intelligence model containing {len(plan.actions)} optimization items.")
        time.sleep(0.5)
        
        # In dashboard mode, we post the planned actions for review
        _latest_state["cycle_count"] += 1
        _latest_state["last_cycle"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _latest_state["reasoning_summary"] = plan.summary
        
        # Map actions to dashboard view
        planned_actions = []
        for a in plan.actions:
            _emit_thought(f"▶ CORRELATING: Auditing {a.action_type} strategy for {a.resource_id}...")
            time.sleep(0.4)
            entry = {
                "tool_name": a.tool_name,
                "resource_id": a.resource_id,
                "action_type": a.action_type,
                "reason": a.reason,
                "status": "pending_approval"
            }
            
            # Pre-fetch insights for specific tools (Security and Diagnosis)
            if a.tool_name == "security_auditor":
                _emit_thought("▶ AUDITING: Performing deep security vulnerability scan...")
                res = _agent.act(Plan(actions=[a], summary="Security scan for dashboard"))
                _latest_state["security_findings"] = res[0].get("findings", [])
            
            elif a.tool_name == "diagnose_server":
                _emit_thought(f"▶ DIAGNOSING: Analyzing root cause for {a.resource_id}...")
                res = _agent.act(Plan(actions=[a], summary="Pre-diagnosis for dashboard"))
                if res and "diagnosis" in res[0]:
                    entry["diagnosis"] = res[0]["diagnosis"]

            planned_actions.append(entry)
            
        _latest_state["actions"] = planned_actions
        _emit_thought("▶ READY: Orchestration strategy ready for review.")
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
        _emit_thought(f"▶ CRITICAL: Agent logic failure — {str(exc)}")

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
