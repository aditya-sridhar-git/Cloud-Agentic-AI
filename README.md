#  Cloud Agentic AI

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
| **🔗 Cross-domain correlation** | Cost spike / anomaly | CloudTrail + costs + Security Hub/GuardDuty → LLM finds connections |

Supplementary tools: certificate expiry monitor, EBS backup manager and RDS query optimizer.

## Safety Model

* **Dry-run by default.** Nothing changes unless you pass `--live`. In dry-run, read-only tools
  (diagnosis, security scan, correlation) still run so you can see their findings.
* **Guardrails.** Every proposed action — from the LLM or the rules — is checked for a valid
  tool/action, an existing resource in a sensible state, protection tags (`PreserveInstance=true`)
  and supporting telemetry before it can be planned.
* **Human approval.** Termination, resizing, volume deletion, cost freezes and stopping
  production instances wait in an approval queue. Approve them in the dashboard or with the
  Slack buttons (set `SLACK_SIGNING_SECRET` and point the Slack app's interactivity URL at
  `/api/slack/actions`). Approved interventions become few-shot examples for the LLM.
* **Auto-remediation is opt-in** (`tools.diagnose_server.auto_remediate`).
* **Dashboard** binds to `127.0.0.1`; set `DASHBOARD_TOKEN` before exposing it with `--host`.

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

# 3. Launch the web dashboard (add --live to let approvals execute against the mock)
python -m cloud_agent.main --mock --dashboard

# 3b. Load an evaluation scenario into the demo
python -m cloud_agent.main --mock --scenario paper_baseline --once

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
│   │   ├── guardrails.py          # Action validation (schema, inventory, protection, grounding)
│   │   ├── planningagent.py       # Planner: flags high-impact actions for approval
│   │   ├── approval.py            # Approval queue + operator feedback memory
│   │   └── consensus.py           # Optional multi-agent LLM debate
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
│   └── evaluation/                # Scenarios, baselines, metrics, experiment runner, moto test bed
├── scripts/live_aws_testbed.py    # Real-AWS sandbox test bed (provision / evaluate / teardown)
├── results/                       # Generated evaluation tables
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
| `--host ADDR` | Dashboard bind address (default: 127.0.0.1; requires `DASHBOARD_TOKEN` otherwise) |
| `--scenario NAME` | With `--mock`, load an evaluation scenario (`python -m cloud_agent.evaluation --list`) |

## Requirements

- Python 3.10+
- AWS credentials (if using AWS provider — not needed for `--mock`)
- OpenAI API key (optional — falls back to rule-based analysis; model set in `llm.model`)

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

## Evaluation

Scenario-based evaluation with ground truth, a native-AWS baseline, an ablation study,
robustness to reasoning errors, scalability, a telemetry-noise sweep and an emulated-AWS test bed:

```bash
python -m cloud_agent.evaluation                         # writes results/results.md
python -m cloud_agent.evaluation --testbed moto --seeds 3   # writes results/results_moto.md
```

See [EVALUATION.md](EVALUATION.md) for the methodology and [results/](results/) for the latest numbers.

## License

MIT
