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
_latest_state: dict[str, Any] = {
    "status": "idle",
    "cycle_count": 0,
    "last_cycle": None,
    "instances": [],
    "volumes": [],
    "costs": {},
    "actions": [],
    "security_findings": [],
}


# ------------------------------------------------------------------
# REST endpoints
# ------------------------------------------------------------------


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



# ------------------------------------------------------------------
# WebSocket
# ------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
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
    """Push data to all connected WebSocket clients."""
    disconnected = []
    for ws in _connected_clients:
        try:
            asyncio.run(ws.send_json(data))
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        if ws in _connected_clients:
            _connected_clients.remove(ws)


# ------------------------------------------------------------------
# Background agent loop
# ------------------------------------------------------------------

def _run_agent_cycle() -> None:
    """Run one cycle and update global state."""
    global _latest_state, _current_plan
    if _agent is None:
        return

    _latest_state["status"] = "running"
    _broadcast(_latest_state)

    try:
        # Collect observation
        observation = _agent.observe()
        _latest_state["instances"] = observation.instances
        _latest_state["volumes"] = observation.disks
        _latest_state["costs"] = observation.costs

        # Planning
        plan = _agent.think(observation)
        _current_plan = plan
        
        # In dashboard mode, we post the planned actions for review
        # The user can then click "Approve" via the API
        _latest_state["cycle_count"] += 1
        _latest_state["last_cycle"] = time.strftime("%Y-%m-%d %H:%M:%S")
        
        # Map actions to dashboard view
        planned_actions = []
        for a in plan.actions:
            planned_actions.append({
                "tool_name": a.tool_name,
                "resource_id": a.resource_id,
                "action_type": a.action_type,
                "reason": a.reason,
                "status": "pending_approval"
            })
        _latest_state["actions"] = planned_actions

        # Extract security findings
        # Manual run of security auditor results if planned
        for a in plan.actions:
            if a.tool_name == "security_auditor":
                # Quickly dry-run security auditor to get findings without wait
                res = _agent.act(Plan(actions=[a], summary="Security scan for dashboard"))
                _latest_state["security_findings"] = res[0].get("findings", [])

        _latest_state["status"] = "idle"
    except Exception as exc:
        logger.exception("Dashboard agent cycle failed")
        _latest_state["status"] = "error"
        _latest_state["error"] = str(exc)

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
