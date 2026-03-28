# ☁️ Cloud Agentic AI

**Autonomous cloud operations powered by agentic AI.**

This system continuously monitors your cloud infrastructure, detects inefficiencies, and takes corrective action — automatically.

## What It Automates

| Operation | Trigger | Action |
|---|---|---|
| Shut down idle servers | CPU < 5 % for 30 min | Stop / terminate instance |
| Right-size VMs | Avg CPU < 20 % over 7 days | Downgrade instance type |
| Clean up orphaned disks | Unattached > 7 days | Snapshot → delete |
| Enforce tag compliance | Missing required tags | Auto-apply default tags |
| Schedule dev environments | Outside business hours | Stop non-prod instances |
| Cost anomaly detection | Daily spend > 120 % baseline | Alert + freeze non-critical |

## Architecture

The agent follows an **Observe → Think → Act** loop:

```
┌─────────────────────────────────────────────────────┐
│                   Agent Loop                        │
│                                                     │
│   ┌──────────┐   ┌──────────┐   ┌──────────┐       │
│   │ OBSERVE  │──▶│  THINK   │──▶│   ACT    │       │
│   │ (metrics)│   │  (LLM)   │   │ (tools)  │       │
│   └──────────┘   └──────────┘   └──────────┘       │
│        ▲                              │             │
│        └──────────────────────────────┘             │
└─────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Clone & install
git clone https://github.com/<your-org>/Cloud-Agentic-AI.git
cd Cloud-Agentic-AI
pip install -r requirements.txt

# 2. Configure
cp .env.example .env        # Add your API keys
nano config/settings.yaml   # Tune thresholds

# 3. Dry-run (no changes applied)
python -m cloud_agent.main --dry-run

# 4. Live run
python -m cloud_agent.main
```

## Project Structure

```
Cloud-Agentic-AI/
├── config/settings.yaml        # Thresholds & schedules
├── cloud_agent/
│   ├── main.py                 # Entry-point orchestrator
│   ├── agent/                  # Core agent (reasoning + planning)
│   ├── cloud/                  # Cloud provider abstraction (AWS)
│   ├── tools/                  # One tool per automation
│   ├── monitor/                # Metrics collection & evaluation
│   └── utils/                  # Logger, config loader
└── tests/                      # Unit tests
```

## Configuration

Edit `config/settings.yaml` to tune thresholds, schedules, and tag policies. Environment variables go in `.env` (see `.env.example`).

## Requirements

- Python 3.10+
- AWS credentials (if using AWS provider)
- OpenAI API key (for LLM reasoning)

## License

MIT
