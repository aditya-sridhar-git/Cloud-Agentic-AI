# ☁️ Cloud Agentic AI

**Autonomous cloud operations powered by agentic AI.**

This system continuously monitors your cloud infrastructure, detects inefficiencies and security risks, **diagnoses root causes**, and takes corrective action — automatically.

> **What makes this different from AWS native tools?** This agent doesn't just fire `IF metric > threshold THEN action` rules. It SSMs into troubled servers to explain *why* they're failing, correlates events across CloudTrail + Costs + Security, and makes LLM-powered decisions that span AWS service boundaries.

## What It Automates

| Operation | Trigger | Action |
|---|---|---|
| Shut down idle servers | CPU < 5% for 30 min | Stop / terminate instance |
| Right-size VMs | Avg CPU < 20% over 7 days | Downgrade instance type |
| Clean up orphaned disks | Unattached > 7 days | Snapshot → delete |
| Enforce tag compliance | Missing required tags | Auto-apply default tags |
| Schedule dev environments | Outside business hours | Stop ↔ start instances |
| Cost anomaly detection | Daily spend > 120% baseline | Alert + freeze non-critical |
| **🔍 Root cause diagnosis** | High CPU / anomaly | SSM into server → LLM explains why |
| **🛡️ Security audit** | Periodic scan | Open SGs, public S3, unencrypted EBS |
| **🔗 Cross-domain correlation** | Cost spike / anomaly | CloudTrail + costs + security → LLM finds connections |

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        Agent Loop                                │
│                                                                  │
│   ┌──────────┐    ┌──────────────┐    ┌──────────┐              │
│   │ OBSERVE  │───►│    THINK     │───►│   ACT    │              │
│   │ (metrics)│    │ (LLM / rules)│    │ (9 tools)│              │
│   └──────────┘    └──────────────┘    └──────────┘              │
│        ▲                                    │                    │
│        └────────────────────────────────────┘                    │
│                                                                  │
│   ┌────────────┐  ┌────────────┐  ┌────────────┐               │
│   │ 📋 Logger  │  │ 🔔 Notify  │  │ 🌐 Dashbrd │               │
│   └────────────┘  └────────────┘  └────────────┘               │
└──────────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Clone & install
git clone https://github.com/<your-org>/Cloud-Agentic-AI.git
cd Cloud-Agentic-AI
pip install -r requirements.txt

# 2. Demo mode (no AWS credentials needed!)
python -m cloud_agent.main --mock --once

# 3. Launch the web dashboard
python -m cloud_agent.main --mock --dashboard

# 4. Real AWS (dry-run)
cp .env.example .env        # Add your API keys
python -m cloud_agent.main --dry-run

# 5. Real AWS (live — actions will execute!)
python -m cloud_agent.main --live
```

## Project Structure

```
Cloud-Agentic-AI/
├── config/settings.yaml           # Thresholds, schedules, tool configs
├── cloud_agent/
│   ├── main.py                    # Entry-point orchestrator
│   ├── agent/                     # Core agent (reasoning + planning)
│   │   ├── baseagent.py           # Observe → Think → Act loop
│   │   ├── reasoningagent.py      # LLM reasoning + rule-based fallback
│   │   └── planningagent.py       # Action planner with human approval
│   ├── cloud/                     # Cloud provider abstraction
│   │   ├── provider.py            # Abstract interface
│   │   ├── aws_provider.py        # AWS (boto3) implementation
│   │   └── mock_provider.py       # Mock data for demos (no creds needed)
│   ├── tools/                     # 9 automation tools
│   │   ├── idle_server.py         # Shut down idle servers
│   │   ├── rightsizer.py          # Right-size VMs
│   │   ├── disk_cleanup.py        # Orphaned disk cleanup
│   │   ├── tag_enforcer.py        # Tag compliance
│   │   ├── scheduler.py           # Dev env scheduling (bidirectional)
│   │   ├── cost_monitor.py        # Cost anomaly + freeze
│   │   ├── diagnose_server.py     # 🔍 SSM root cause diagnosis (NEW)
│   │   ├── security_auditor.py    # 🛡️ Security posture audit (NEW)
│   │   └── cross_domain.py        # 🔗 Cross-domain correlation (NEW)
│   ├── monitor/                   # Metrics collection & evaluation
│   │   ├── collector.py           # Fetch metrics from CloudWatch
│   │   └── evaluator.py           # Threshold-based evaluation
│   ├── dashboard/                 # 🌐 Web dashboard (NEW)
│   │   ├── app.py                 # FastAPI + WebSocket server
│   │   └── static/                # HTML, CSS, JS
│   └── utils/
│       ├── logger.py              # Rich structured logging
│       ├── config.py              # YAML + .env config loader
│       ├── action_log.py          # 📋 JSON audit trail (NEW)
│       └── notifier.py            # 🔔 Slack + SNS notifications (NEW)
├── tests/                         # Unit + integration tests
└── logs/                          # Action audit trail (auto-created)
```

## Configuration

Edit `config/settings.yaml` to tune thresholds, schedules, and tool policies. Secrets go in `.env` (see `.env.example`).

### Key CLI Flags

| Flag | Description |
|---|---|
| `--mock` | Use simulated data (no AWS credentials needed) |
| `--once` | Run a single cycle then exit |
| `--dashboard` | Launch the web dashboard on port 8080 |
| `--live` | Execute actions for real (default is dry-run) |
| `--port N` | Set dashboard port (default: 8080) |

## Requirements

- Python 3.10+
- AWS credentials (if using AWS provider — not needed for `--mock`)
- OpenAI API key (optional — falls back to rule-based analysis)

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run with mock provider (no AWS needed)
python -m cloud_agent.main --mock --once
```

## License

MIT
