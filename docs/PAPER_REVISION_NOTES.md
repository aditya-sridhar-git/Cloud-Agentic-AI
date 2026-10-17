# Paper revision notes

Working notes for revising *“Cloud Agentic AI: An Autonomous Framework for Intelligent Cloud
Infrastructure Management”* against the current code. Numbers come from
[`results/results.md`](../results/results.md) (mock test bed, 10 seeds) and
[`results/results_moto.md`](../results/results_moto.md) (emulated AWS, 3 seeds). Regenerate them
before submission if anything changes.

## 1. Reviewer comments → what now exists

| Comment | Implemented | Where in the paper |
|---|---|---|
| 1. Different scenarios for evaluation | 8 scenarios with injected faults and ground truth (S1 paper baseline … S8 generated large fleets), each run over 10 seeds with telemetry noise | Replace §VII-A with the scenario table from `EVALUATION.md` §2; new Table "Scenario results" (E1) |
| 2. Baseline comparison with experimental evidence | Native-AWS rule baseline (CloudWatch alarms, Compute Optimizer, Trusted Advisor, AWS Config rules, Instance Scheduler, Cost Anomaly Detection) run on the same scenarios; live-effect comparison (E7) | New §VII "Baseline comparison" with E2 + E3; replace qualitative Table 5 with E2 numbers (keep Table 5 as a capability summary) |
| 3. Ablation approach | Ablation study removing one component at a time (E4) and a guardrail ablation under injected reasoning errors (E5); see §3 below for the literature-survey ablation table | New §VII "Ablation study"; restructure §II with the capability matrix |
| 4. More test beds | T1 scenario simulator, T2 emulated AWS (moto, real `AWSProvider` code path), T3 generated fleets up to 2,000 instances, T4 real-AWS sandbox script | New §VII-A "Test beds" table; E6 scalability; moto results table |

## 2. Paper statements that changed or must be updated

### Now true in the code (keep the text, optionally cite the mechanism)

| Paper claim | Code change |
|---|---|
| Human approval for high-impact actions via Slack (§V-B, Fig. 3) | Approval queue + Slack Approve/Reject buttons (`/api/slack/actions`, signed with `SLACK_SIGNING_SECRET`) + dashboard approvals. High-impact = terminate, resize, volume deletion, cost freeze, stopping `Environment=prod`. |
| Idle instances stopped/terminated per instance tag policy (§IV-A1) | `IdlePolicy=stop|terminate` tag overrides the default; protection tag `PreserveInstance=true`. |
| Cost monitor uses a rolling 30-day baseline and freezes `priority=low` instances (§IV-A6) | `baseline_window_days: 30`; freeze stops running `priority=low` instances, never production. |
| Security auditor ports 22, 3389, 5432, 1521 (§IV-B2) | Configurable list now includes 1521 (also 3306, 6379). |
| Correlator uses CloudTrail + Cost Explorer + Security Hub/GuardDuty (§IV-B3) | Security Hub and GuardDuty findings added; rule-based correlation detects `sg_exposure`, `unauthorised_provisioning`, `compromise_cost`. |
| Diagnoser triggers on high CPU **or memory** (§IV-B1) | Memory trigger added (`memory_high_threshold: 90`, CloudWatch agent metric on AWS). |
| Scheduler evaluates UTC against per-environment windows (§IV-A5) | `environment_hours` per `Environment` tag, evaluated in UTC. |
| VM right-sizer (Table 1) | The rule engine never proposed right-sizing before (detection was 0 in practice); rule added (7-day CPU < 20 %). |
| Reasoning model GPT-4o, prompt with examples of prior successful interventions (§V-A1) | `llm.model: gpt-4o`; prompt includes static examples plus operator-approved interventions from the approval memory. |
| Rule-based fallback on network failure / rate limit / missing key (§V-A2) | Unchanged, now also used when `llm.enabled: false`. |
| Listing 1 `run(self, continuous)` | `BaseAgent.run(continuous=True)` added; `--once` maps to `continuous=False`. |
| Dashboard chat via `POST /api/chat` (§VI-C) | Unchanged; chat is read-only by design (see below). |
| 15 chat-interface tests passing | All 15 pass again (they were failing: intent detection was broken). |

### Text to change (the code intentionally differs)

| Paper | Change the text to |
|---|---|
| §VII-A: “five EC2 instances spanning 1–95 % … one at 97 %” | S1 has **five instances spanning 1–97 %, one at 97 %**, plus one over-provisioned staging host (14 %). Use the S1 row in `EVALUATION.md`. |
| Table 2: Certificates → Security Auditor (TLS sub-scan); Backups → Disk Cleanup (snapshot mode) | Certificates → **Certificate Monitor** (`cert_monitor.py`); Backups → **Backup Manager** (`backup_manager.py`). Also list Rightsizer, Scheduler, Tag Enforcer, Correlator, Query Optimizer intents. |
| §VI-B “response generation through LLM” | Responses are template-based; optional LLM rephrasing (`chat.llm_responses: true`) keeps every number and ID. |
| §VI chat issuing directives | The chat is **read-only**: action requests are acknowledged but executed only through the agent's plan and approval flow. State this as a safety design choice. |
| Listing 2 diagnostic commands | Also runs `journalctl -p err`, `uptime`, `last`. |
| Table 4 (61 tests, 58 pass, 3 need AWS) | **125 tests, all passing**, including 1 emulated-AWS test (moto); CI on Python 3.10 and 3.12 (`.github/workflows/ci.yml`). |
| Table 3 (100 % detection, 95.7 % action correctness, percentages from 1–3 cases) | Replace with E3 (per-tool recall/precision with counts) and E1/E2. Do not report percentages that cannot arise from the stated number of cases. |
| §VII-D latency “1.2 s without LLM, 3–8 s with LLM” | Measured decision latency: ≈ 1–2 ms per cycle on T1 (no API time), 0.2–1.5 s per cycle on T2 including emulated AWS API calls, ≈ 0.1–0.2 ms per instance up to 2,000 instances (E6). LLM latency is not measured in the committed results. |
| §VIII-B4 Safety and blast radius | Describe guardrails + approval gate + dry-run default; cite E5 (0 % of injected unsafe actions reach the plan with guardrails vs. 82–98 % without; 15–21 % would auto-execute) and E7 (collateral damage 0 for the agent on S1/S3–S7 vs. 22 and 10 for the native baseline on S2/S7). Keep remaining limitations: rubber-stamped approvals (E7 S2: 2 borderline prod stops when an operator approves everything). |
| Abstract / conclusion numbers (100 % detection, 95.7 %, 95 % pass rate) | Use E2: agent F1 99.0 % vs. native 79.7 %; approval compliance 100 % vs. 0 %; 0 vs. 32 unsafe auto-actions; RCA 100 % vs. 0 %; correlation recall 100 % vs. 0 %. Say “rule mode” explicitly unless you add LLM-mode runs. |
| Header placeholders (VOLUME 4, 2016, DOI xxxx) | Fill in or remove for the conference template. |

### Additions worth one sentence each

* Optional adversarial multi-agent consensus (`consensus.py`): FinOps/SecOps/DevOps personas + judge; **escalates to a human** when unavailable (fail-safe), disabled by default.
* Supplementary tools: certificate monitor, backup manager, RDS query optimizer.
* Dashboard security: localhost binding, token for state-changing endpoints, append-only audit trail.

## 3. Literature survey as an ablation (capability matrix)

Reviewers asked for an ablation-style survey. Structure §II around capabilities and show which
ones each class of prior work lacks. The paper's experiment E4 then removes the same capabilities
from Cloud Agentic AI and measures the loss, which links the survey to the evidence.
**Verify each cell against the cited papers before submission.** The cells below follow the
paper's own characterisation of the related work.

| Capability (E4 variant that removes it) | Native AWS tools | SaaS FinOps (CloudHealth, Cloudability) | AIOps anomaly detection [2,3] | RL autoscaling [9,10,15,16] | ChatOps [11] | LLM agents (ReAct, Toolformer) [7,8] | **Cloud Agentic AI** |
|---|---|---|---|---|---|---|---|
| Multi-signal detection | per-service | cost only | ✓ | load only | – | – | ✓ |
| Autonomous remediation | limited | – | – | scaling only | manual | generic | ✓ |
| Root-cause diagnosis (− diagnoser: F1 −8.5 pp, RCA 100→0 %) | – | – | partial | – | – | – | ✓ |
| Cross-service correlation (− correlator: F1 −5.5 pp, correlation 100→0 %) | manual | – | – | – | – | – | ✓ |
| Security posture (− auditor: F1 −10.6 pp) | separate service | – | – | – | – | – | ✓ |
| Natural-language interface | – | – | – | – | commands | ✓ | ✓ |
| Human-approval gate (− gate: approval compliance 100→0 %) | – | ✓ (manual) | – | – | ✓ | – | ✓ |
| Action guardrails vs. erroneous reasoning (− guardrails: 82–98 % of injected errors reach the plan) | n/a | n/a | n/a | n/a | n/a | – | ✓ |

## 4. Suggested new evaluation section outline

1. **Test beds** (T1–T4 table) and **scenarios** (S1–S8 table).
2. **Systems and metrics** (`EVALUATION.md` §3–4).
3. **Scenario results** — E1, discuss S2/S7 (safety) and S3/S4 (diagnosis, correlation).
4. **Baseline comparison** — E2 + E3; E7 live effect (resolved vs. collateral).
5. **Ablation** — E4; E5 robustness of guardrails.
6. **Scalability and sensitivity** — E6, E8.
7. **Emulated-AWS validation** — moto table.
8. **Threats to validity** — `EVALUATION.md` §6 (rule-mode results partly reflect shared thresholds; baseline is an emulation; LLM mode not yet measured).

To add LLM-mode rows, run `python -m cloud_agent.evaluation --with-llm` with `OPENAI_API_KEY` set. Then
compare rule vs. LLM mode in E1/E2 and report E5 with real (not injected) LLM errors.
