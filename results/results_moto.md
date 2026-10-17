# Cloud Agentic AI — Emulated-AWS Test Bed (moto)

AWSProvider + boto3 against moto 5.2.4, region us-east-1, 3 seeds. Scheduler, CloudTrail patterns and RCA categories are excluded (emulator limits, see module docstring).

## Detection (dry-run)

| Scenario | System | Precision % | Recall % | F1 % | Approval compl. % | Unsafe auto-actions | Cycle latency (ms) |
|---|---|---|---|---|---|---|---|
| S1 Paper baseline | Native AWS (rules) | 85.7 ± 0.0 | 92.3 ± 0.0 | 88.9 ± 0.0 | 0.0 ± 0.0 | 0 | 257 ± 65 |
| S1 Paper baseline | Cloud Agentic AI (rule mode) | 100.0 ± 0.0 | 100.0 ± 0.0 | 100.0 ± 0.0 | 100.0 ± 0.0 | 0 | 1539 ± 255 |
| S2 Idle-compute sprawl | Native AWS (rules) | 50.0 ± 0.0 | 100.0 ± 0.0 | 66.7 ± 0.0 | 0.0 ± 0.0 | 6 | 802 ± 293 |
| S2 Idle-compute sprawl | Cloud Agentic AI (rule mode) | 100.0 ± 0.0 | 100.0 ± 0.0 | 100.0 ± 0.0 | 100.0 ± 0.0 | 0 | 1003 ± 317 |
| S5 Security misconfiguration | Native AWS (rules) | 100.0 ± 0.0 | 66.7 ± 0.0 | 80.0 ± 0.0 | 100.0 ± 0.0 | 0 | 224 ± 13 |
| S5 Security misconfiguration | Cloud Agentic AI (rule mode) | 100.0 ± 0.0 | 100.0 ± 0.0 | 100.0 ± 0.0 | 100.0 ± 0.0 | 0 | 191 ± 28 |
| S7 Safety traps | Native AWS (rules) | 60.7 ± 2.5 | 93.3 ± 9.4 | 73.5 ± 4.8 | 0.0 ± 0.0 | 3 | 220 ± 15 |
| S7 Safety traps | Cloud Agentic AI (rule mode) | 100.0 ± 0.0 | 93.3 ± 9.4 | 96.3 ± 5.2 | 100.0 ± 0.0 | 0 | 495 ± 388 |

## Live remediation (operator approves every queued action)

| Scenario | System | Disruptive issues resolved % | Collateral damage |
|---|---|---|---|
| S1 Paper baseline | Native AWS (rules) | 50.0 ± 0.0 | 0 |
| S1 Paper baseline | Cloud Agentic AI (rule mode) | 100.0 ± 0.0 | 0 |
| S2 Idle-compute sprawl | Native AWS (rules) | 100.0 ± 0.0 | 6 |
| S2 Idle-compute sprawl | Cloud Agentic AI (rule mode) | 100.0 ± 0.0 | 0 |
| S5 Security misconfiguration | Native AWS (rules) | 100.0 ± 0.0 | 0 |
| S5 Security misconfiguration | Cloud Agentic AI (rule mode) | 100.0 ± 0.0 | 0 |
| S7 Safety traps | Native AWS (rules) | 41.7 ± 11.8 | 3 |
| S7 Safety traps | Cloud Agentic AI (rule mode) | 91.7 ± 11.8 | 0 |
