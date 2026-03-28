# Cloud Agentic AI — Automated Cloud Operations Agent

An agentic AI system that **monitors, diagnoses, and autonomously remediates** common cloud infrastructure problems. The agent continuously evaluates the state of your cloud environment and takes corrective actions — like shutting down idle servers, right-sizing instances, cleaning up orphaned resources, and responding to cost/performance anomalies.

## What Gets Automated

| Operation | Trigger | Agent Action |
|---|---|---|
| **Shut down idle servers** | CPU < 5 % for 30 min | Stop / terminate the instance |
| **Right-size over-provisioned VMs** | Avg CPU < 20 % over 7 days | Recommend / apply downgrade |
| **Clean up orphaned disks** | Unattached for > 7 days | Snapshot → delete |
| **Enforce tag compliance** | Missing required tags | Auto-tag with defaults |
| **Scale-down dev environments** | Outside business hours | Stop non-prod instances |
| **Cost spike alerts** | Daily spend > 120 % of baseline | Alert + freeze non-critical |

---

## Proposed Project Structure

```
Cloud-Agentic-AI/
├── README.md
├── requirements.txt
├── .env.example
├── config/
│   └── settings.yaml          # Thresholds, schedules, provider config
├── cloud_agent/
│   ├── __init__.py
│   ├── main.py                # Entry-point / orchestrator loop
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── base.py            # BaseAgent ABC
│   │   ├── reasoning.py       # LLM-backed decision engine
│   │   └── planner.py         # Action planner (plan → approve → execute)
│   ├── cloud/
│   │   ├── __init__.py
│   │   ├── provider.py        # Abstract CloudProvider interface
│   │   └── aws_provider.py    # AWS (boto3) concrete implementation
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── base_tool.py       # BaseTool ABC + registry decorator
│   │   ├── idle_server.py     # Shut down idle servers
│   │   ├── rightsizer.py      # Right-size VMs
│   │   ├── disk_cleanup.py    # Orphaned disk cleanup
│   │   ├── tag_enforcer.py    # Tag compliance
│   │   ├── scheduler.py       # Dev env scheduling
│   │   └── cost_monitor.py    # Cost anomaly detection
│   ├── monitor/
│   │   ├── __init__.py
│   │   ├── collector.py       # Fetch metrics from CloudWatch / etc.
│   │   └── evaluator.py       # Evaluate metrics against thresholds
│   └── utils/
│       ├── __init__.py
│       ├── logger.py          # Structured logging
│       └── config.py          # YAML / env config loader
├── tests/
│   ├── __init__.py
│   ├── test_idle_server.py
│   └── test_evaluator.py
```

---

## Proposed Changes

### Project Root

#### [NEW] [README.md](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/README.md)
Project overview, setup instructions, architecture diagram.

#### [NEW] [requirements.txt](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/requirements.txt)
`boto3`, `openai`, `pyyaml`, `python-dotenv`, `schedule`, `rich`

#### [NEW] [.env.example](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/.env.example)
Template for `OPENAI_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`.

#### [NEW] [config/settings.yaml](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/config/settings.yaml)
Thresholds (CPU idle %, hours), schedules, tag policies, cost baselines.

---

### Core Agent (`cloud_agent/agent/`)

#### [NEW] [base.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/agent/base.py)
Abstract `BaseAgent` with `observe() → think() → act()` loop.

#### [NEW] [reasoning.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/agent/reasoning.py)
Calls OpenAI to interpret metrics and decide which tool to invoke.

#### [NEW] [planner.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/agent/planner.py)
Generates an execution plan, optionally waits for human approval, then dispatches tool calls.

---

### Cloud Provider (`cloud_agent/cloud/`)

#### [NEW] [provider.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/cloud/provider.py)
Abstract interface: `list_instances()`, `stop_instance()`, `get_metrics()`, `list_disks()`, `delete_disk()`, `get_cost()`, etc.

#### [NEW] [aws_provider.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/cloud/aws_provider.py)
Concrete AWS implementation using `boto3`.

---

### Automation Tools (`cloud_agent/tools/`)

Each tool inherits `BaseTool` and implements `evaluate()` + `execute()`.

#### [NEW] [base_tool.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/tools/base_tool.py)
#### [NEW] [idle_server.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/tools/idle_server.py)
#### [NEW] [rightsizer.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/tools/rightsizer.py)
#### [NEW] [disk_cleanup.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/tools/disk_cleanup.py)
#### [NEW] [tag_enforcer.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/tools/tag_enforcer.py)
#### [NEW] [scheduler.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/tools/scheduler.py)
#### [NEW] [cost_monitor.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/tools/cost_monitor.py)

---

### Monitor (`cloud_agent/monitor/`)

#### [NEW] [collector.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/monitor/collector.py)
Pulls CloudWatch / provider metrics on a schedule.

#### [NEW] [evaluator.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/monitor/evaluator.py)
Compares live metrics to YAML thresholds; emits findings.

---

### Utilities (`cloud_agent/utils/`)

#### [NEW] [logger.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/utils/logger.py)
#### [NEW] [config.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/utils/config.py)

---

### Entry Point

#### [NEW] [main.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/cloud_agent/main.py)
CLI entry point; loads config, instantiates the agent, and starts the observe-think-act loop.

---

### Tests

#### [NEW] [test_idle_server.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/tests/test_idle_server.py)
Unit tests for idle-server tool using mocked provider.

#### [NEW] [test_evaluator.py](file:///c:/Users/adity/OneDrive/Documents/GitHub/Cloud-Agentic-AI/tests/test_evaluator.py)
Unit tests for threshold evaluator.

---

## Verification Plan

### Automated Tests
```bash
cd c:\Users\adity\OneDrive\Documents\GitHub\Cloud-Agentic-AI
python -m pytest tests/ -v
```

### Import / Lint Check
```bash
cd c:\Users\adity\OneDrive\Documents\GitHub\Cloud-Agentic-AI
python -c "from cloud_agent.main import main; print('All imports OK')"
```

### Manual Verification
- Review generated project tree structure
- Confirm `python cloud_agent/main.py --dry-run` runs the observe-think-act loop once and prints planned actions without executing them
