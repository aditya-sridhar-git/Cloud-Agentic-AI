"""
Experiment runner — produces every table used in the paper's evaluation.

    python -m cloud_agent.evaluation                 # all experiments, 10 seeds
    python -m cloud_agent.evaluation --quick         # 3 seeds, smaller scale sweep
    python -m cloud_agent.evaluation --only ablation

Results are written to ``results/`` as JSON (raw), CSV and Markdown tables.
"""

from __future__ import annotations

import csv
import json
import platform
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from cloud_agent.evaluation.metrics import (
    SCORED_TOOLS, Score, aggregate, collateral_damage, resolved_fraction, score,
)
from cloud_agent.evaluation.scenarios import SCENARIOS
from cloud_agent.evaluation.systems import (
    AgentSystem, NativeAWSBaseline, agent_config, inject_reasoning_errors, llm_available,
)

CORE_SCENARIOS = [n for n in SCENARIOS if n != "large_fleet"]


def _fmt(pair: tuple[float, float] | None, pct: bool = True, digits: int = 1) -> str:
    if pair is None:
        return "—"
    m, sd = pair
    if pct:
        return f"{m * 100:.{digits}f} ± {sd * 100:.{digits}f}"
    return f"{m:.{digits}f} ± {sd:.{digits}f}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


class Runner:
    def __init__(self, base_config: dict[str, Any], seeds: int = 10, out_dir: str | Path = "results",
                 with_llm: bool = False, quick: bool = False) -> None:
        self.base = base_config
        self.seeds = list(range(seeds))
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.with_llm = with_llm and llm_available()
        self.quick = quick
        self.raw: dict[str, Any] = {}
        self.md: list[str] = []

    # ------------------------------------------------------------------

    def systems(self) -> dict[str, Callable[[], Any]]:
        systems: dict[str, Callable[[], Any]] = {
            "Native AWS (rules)": lambda: NativeAWSBaseline(self.base),
            "Cloud Agentic AI (rule mode)": lambda: AgentSystem(agent_config(self.base)),
        }
        if self.with_llm:
            systems["Cloud Agentic AI (LLM mode)"] = lambda: AgentSystem(agent_config(self.base, llm=True))
        return systems

    def _runs(self, make_system: Callable[[], Any], scenario: str, seeds: list[int],
              scale: int | None = None, noise: float | None = None) -> list[tuple[Score, Any, Any]]:
        out = []
        for seed in seeds:
            inst = SCENARIOS[scenario].build(seed=seed, scale=scale, noise=noise)
            result = make_system().run(inst)
            out.append((score(result, inst.truth), result, inst.truth))
        return out

    # ------------------------------------------------------------------
    # E1 / E2: scenarios × systems, per-tool detection
    # ------------------------------------------------------------------

    def exp_scenarios(self) -> None:
        rows, raw = [], {}
        per_tool: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
        overall: dict[str, list[Score]] = defaultdict(list)
        for sname in CORE_SCENARIOS:
            for sysname, make in self.systems().items():
                runs = self._runs(make, sname, self.seeds)
                scores = [r[0] for r in runs]
                overall[sysname] += scores
                for sc in scores:
                    for tool, (tp, fp, fn) in sc.per_tool.items():
                        acc = per_tool[sysname][tool]
                        acc[0] += tp
                        acc[1] += fp
                        acc[2] += fn
                raw[f"{sname}/{sysname}"] = [sc.summary() for sc in scores]
                rows.append([
                    SCENARIOS[sname].title, sysname,
                    _fmt(aggregate(s.precision for s in scores)),
                    _fmt(aggregate(s.recall for s in scores)),
                    _fmt(aggregate(s.f1 for s in scores)),
                    _fmt(aggregate(s.action_accuracy for s in scores)),
                    _fmt(aggregate(s.approval_compliance for s in scores)),
                    _fmt(aggregate(s.unsafe_auto for s in scores), pct=False),
                    _fmt(aggregate(s.rca_accuracy for s in scores)),
                    _fmt(aggregate(s.latency_ms for s in scores), pct=False, digits=2),
                ])
        self.raw["scenarios"] = raw
        self.md.append("## E1. Scenario evaluation (mock test bed, mean ± std over "
                       f"{len(self.seeds)} seeds)\n")
        self.md.append(_table(
            ["Scenario", "System", "Precision %", "Recall %", "F1 %", "Action acc. %",
             "Approval compl. %", "Unsafe auto-actions", "RCA acc. %", "Cycle latency (ms)"], rows))

        # Overall comparison
        rows = []
        for sysname, scores in overall.items():
            tp = sum(s.tp for s in scores)
            fp = sum(s.fp for s in scores)
            fn = sum(s.fn for s in scores)
            p = tp / (tp + fp) if tp + fp else 1.0
            r = tp / (tp + fn) if tp + fn else 1.0
            f1 = 2 * p * r / (p + r) if p + r else 0.0
            act = sum(s.action_correct for s in scores) / max(sum(s.action_total for s in scores), 1)
            gate = sum(s.gated_ok for s in scores) / max(sum(s.gated_total for s in scores), 1)
            rca_t = sum(s.rca_total for s in scores)
            rca = sum(s.rca_correct for s in scores) / rca_t if rca_t else 0.0
            pat_t = sum(s.pattern_tp + s.pattern_fn for s in scores)
            pat = sum(s.pattern_tp for s in scores) / pat_t if pat_t else 0.0
            rows.append([sysname, f"{p * 100:.1f}", f"{r * 100:.1f}", f"{f1 * 100:.1f}", f"{act * 100:.1f}",
                         f"{gate * 100:.1f}", str(sum(s.unsafe_auto for s in scores)), f"{rca * 100:.1f}",
                         f"{pat * 100:.1f}"])
        self.raw["overall"] = rows
        self.md.append("\n\n## E2. Baseline comparison — micro-averaged over all scenarios and seeds\n")
        self.md.append(_table(["System", "Precision %", "Recall %", "F1 %", "Action acc. %",
                               "Approval compl. %", "Unsafe auto-actions (total)", "RCA acc. %",
                               "Correlation recall %"], rows))

        # Per-tool detection (paper Table 3)
        tools = list(SCORED_TOOLS) + ["security_auditor", "correlation_patterns"]
        names = list(per_tool)
        rows = []
        for tool in tools:
            row = [tool]
            for sysname in names:
                tp, fp, fn = per_tool[sysname].get(tool, [0, 0, 0])
                det = f"{tp / (tp + fn) * 100:.1f}% ({tp}/{tp + fn})" if tp + fn else "—"
                prec = f"{tp / (tp + fp) * 100:.1f}%" if tp + fp else "—"
                row += [det, prec]
            rows.append(row)
        headers = ["Tool"]
        for n in names:
            headers += [f"{n} recall", f"{n} precision"]
        self.raw["per_tool"] = {k: dict(v) for k, v in per_tool.items()}
        self.md.append("\n\n## E3. Per-tool detection (all scenarios × seeds)\n")
        self.md.append(_table(headers, rows))

    # ------------------------------------------------------------------
    # E4: ablation
    # ------------------------------------------------------------------

    def exp_ablation(self) -> None:
        b = self.base
        variants = {
            "Full system": agent_config(b),
            "− Guardrails (validation + grounding)": agent_config(b, safety__guardrails=False),
            "− Grounding checks only": agent_config(b, safety__grounding_checks=False),
            "− Human-approval gate": agent_config(b, agent__require_approval=False),
            "− Root-cause diagnoser": agent_config(b, tools__diagnose_server__enabled=False),
            "− Memory trigger (CPU only)": agent_config(b, tools__diagnose_server__memory_high_threshold=1000),
            "− Cross-domain correlator": agent_config(b, tools__cross_domain__enabled=False),
            "− Security auditor": agent_config(b, tools__security_auditor__enabled=False),
            "− Per-instance policy & protection tags": agent_config(
                b, safety__protected_tags=[], tools__idle_server__exclude_tags=[],
                tools__idle_server__policy_tag="__none__"),
        }
        rows, raw = [], {}
        for vname, cfg in variants.items():
            scores: list[Score] = []
            for sname in CORE_SCENARIOS:
                scores += [r[0] for r in self._runs(lambda c=cfg: AgentSystem(c), sname, self.seeds)]
            tp, fp, fn = (sum(getattr(s, k) for s in scores) for k in ("tp", "fp", "fn"))
            p = tp / (tp + fp) if tp + fp else 1.0
            r = tp / (tp + fn) if tp + fn else 1.0
            rca_t = sum(s.rca_total for s in scores)
            pat_t = sum(s.pattern_tp + s.pattern_fn for s in scores)
            row = {
                "f1": 2 * p * r / (p + r) if p + r else 0.0,
                "action": sum(s.action_correct for s in scores) / max(sum(s.action_total for s in scores), 1),
                "gate": sum(s.gated_ok for s in scores) / max(sum(s.gated_total for s in scores), 1),
                "unsafe_auto": sum(s.unsafe_auto for s in scores),
                "unsafe_proposed": sum(s.unsafe_proposed for s in scores),
                "rca": sum(s.rca_correct for s in scores) / rca_t if rca_t else 0.0,
                "patterns": sum(s.pattern_tp for s in scores) / pat_t if pat_t else 0.0,
            }
            raw[vname] = row
            rows.append([vname, f"{row['f1'] * 100:.1f}", f"{row['action'] * 100:.1f}", f"{row['gate'] * 100:.1f}",
                         f"{row['rca'] * 100:.1f}", f"{row['patterns'] * 100:.1f}", str(row["unsafe_proposed"]),
                         str(row["unsafe_auto"])])
        self.raw["ablation"] = raw
        self.md.append("\n\n## E4. Ablation study (rule mode, all core scenarios × seeds)\n")
        self.md.append(_table(["Variant", "F1 %", "Action acc. %", "Approval compl. %", "RCA acc. %",
                               "Correlation recall %", "Unsafe proposals", "Unsafe auto-actions"], rows))

    # ------------------------------------------------------------------
    # E5: robustness to reasoning errors
    # ------------------------------------------------------------------

    def exp_robustness(self) -> None:
        rates = [0.1, 0.25, 0.5, 1.0]
        rows, raw = [], {}
        for guard in (True, False):
            for rate in rates:
                injected = reached = auto = 0
                for sname in ("paper_baseline", "idle_waste", "safety_traps", "cost_compromise"):
                    for seed in self.seeds:
                        cfg = agent_config(self.base, safety__guardrails=guard)
                        inst = SCENARIOS[sname].build(seed=seed)
                        out = AgentSystem(cfg, injector=inject_reasoning_errors(rate), injector_seed=seed).run(inst)
                        keys = {(a["tool"], a["resource_id"], a["action_type"]): a for a in out.actions}
                        for k in out.injected:
                            injected += 1
                            if k in keys:
                                reached += 1
                                auto += int(not keys[k]["requires_approval"])
                label = "with guardrails" if guard else "without guardrails"
                raw[f"{label}/{rate}"] = {"injected": injected, "reached_plan": reached, "auto_executable": auto}
                rows.append([label, f"{rate:.2f}", str(injected), f"{reached / max(injected, 1) * 100:.1f}",
                             f"{auto / max(injected, 1) * 100:.1f}"])
        self.raw["robustness"] = raw
        self.md.append("\n\n## E5. Robustness to erroneous reasoning (injected hallucinated / unsafe actions)\n")
        self.md.append(_table(["Configuration", "Injection rate", "Injected actions",
                               "Reached plan %", "Would auto-execute %"], rows))

    # ------------------------------------------------------------------
    # E6: scalability (test bed 3)
    # ------------------------------------------------------------------

    def exp_scalability(self) -> None:
        sizes = [10, 50, 100, 250, 500] if self.quick else [10, 50, 100, 250, 500, 1000, 2000]
        seeds = self.seeds[:3]
        rows, raw = [], {}
        for n in sizes:
            for sysname, make in self.systems().items():
                if "LLM" in sysname:
                    continue
                runs = self._runs(make, "large_fleet", seeds, scale=n)
                scores = [r[0] for r in runs]
                lat = aggregate(s.latency_ms for s in scores)
                f1 = aggregate(s.f1 for s in scores)
                raw[f"{n}/{sysname}"] = {"latency_ms": lat, "f1": f1}
                rows.append([str(n), sysname, _fmt(lat, pct=False, digits=1),
                             f"{(lat[0] / n):.3f}" if lat else "—", _fmt(f1)])
        self.raw["scalability"] = raw
        self.md.append("\n\n## E6. Scalability (generated fleets, decision latency excl. cloud API time)\n")
        self.md.append(_table(["Instances", "System", "Cycle latency (ms)", "ms / instance", "F1 %"], rows))

    # ------------------------------------------------------------------
    # E8: sensitivity to telemetry noise
    # ------------------------------------------------------------------

    def exp_noise(self) -> None:
        levels = [0.0, 1.0, 2.5, 5.0, 10.0]
        rows, raw = [], {}
        for level in levels:
            for sysname, make in self.systems().items():
                scores: list[Score] = []
                for sname in ("paper_baseline", "idle_waste", "safety_traps", "large_fleet"):
                    scores += [r[0] for r in self._runs(make, sname, self.seeds, noise=level,
                                                         scale=100 if sname == "large_fleet" else None)]
                tp, fp, fn = (sum(getattr(s, k) for s in scores) for k in ("tp", "fp", "fn"))
                p = tp / (tp + fp) if tp + fp else 1.0
                r = tp / (tp + fn) if tp + fn else 1.0
                f1 = 2 * p * r / (p + r) if p + r else 0.0
                unsafe = sum(s.unsafe_auto for s in scores)
                raw[f"{level}/{sysname}"] = {"precision": p, "recall": r, "f1": f1, "unsafe_auto": unsafe}
                rows.append([f"±{level:g} pp", sysname, f"{p * 100:.1f}", f"{r * 100:.1f}", f"{f1 * 100:.1f}",
                             str(unsafe)])
        self.raw["noise"] = raw
        self.md.append("\n\n## E8. Sensitivity to CPU telemetry noise (S1, S2, S7, S8@100)\n")
        self.md.append(_table(["Noise amplitude", "System", "Precision %", "Recall %", "F1 %",
                               "Unsafe auto-actions"], rows))

    # ------------------------------------------------------------------
    # E7: live remediation effect
    # ------------------------------------------------------------------

    def exp_live(self) -> None:
        rows, raw = [], {}
        for sname in CORE_SCENARIOS:
            for sysname, make in (
                ("Native AWS (rules)", lambda: NativeAWSBaseline(self.base, live=True)),
                ("Cloud Agentic AI (rule mode)", lambda: AgentSystem(agent_config(self.base), live=True)),
            ):
                resolved, damage = [], []
                for seed in self.seeds:
                    inst = SCENARIOS[sname].build(seed=seed)
                    out = make().run(inst)
                    resolved.append(resolved_fraction(out, inst.truth))
                    damage.append(collateral_damage(out, inst.truth))
                raw[f"{sname}/{sysname}"] = {"resolved": resolved, "collateral": damage}
                rows.append([SCENARIOS[sname].title, sysname, _fmt(aggregate(resolved)),
                             f"{sum(damage)}"])
        self.raw["live"] = raw
        self.md.append("\n\n## E7. Live remediation on the mock test bed (operator approves every queued action)\n")
        self.md.append(_table(["Scenario", "System", "Disruptive issues resolved %",
                               "Collateral damage (total, forbidden resources hit)"], rows))

    # ------------------------------------------------------------------

    def run(self, only: list[str] | None = None) -> Path:
        started = time.time()
        experiments = {
            "scenarios": self.exp_scenarios,
            "ablation": self.exp_ablation,
            "robustness": self.exp_robustness,
            "scalability": self.exp_scalability,
            "live": self.exp_live,
            "noise": self.exp_noise,
        }
        for name, fn in experiments.items():
            if only and name not in only:
                continue
            print(f"[evaluation] running {name} …", file=sys.stderr)
            fn()
        meta = {
            "seeds": len(self.seeds), "llm_mode": self.with_llm, "python": platform.python_version(),
            "platform": platform.platform(), "duration_s": round(time.time() - started, 1),
        }
        self.raw["meta"] = meta
        header = [
            "# Cloud Agentic AI — Evaluation Results",
            "",
            f"Generated by `python -m cloud_agent.evaluation` · seeds={meta['seeds']} · "
            f"LLM mode {'included' if self.with_llm else 'not run (no OPENAI_API_KEY)'} · "
            f"Python {meta['python']} · {meta['duration_s']} s",
            "",
        ]
        (self.out / "results.md").write_text("\n".join(header + self.md) + "\n", encoding="utf-8")
        (self.out / "results.json").write_text(json.dumps(self.raw, indent=2, default=str), encoding="utf-8")
        if "scenarios" in self.raw:
            with open(self.out / "scenario_runs.csv", "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["scenario", "system", "seed", "precision", "recall", "f1", "action_accuracy",
                                 "approval_compliance", "unsafe_auto", "rca_accuracy", "latency_ms"])
                for key, runs in self.raw["scenarios"].items():
                    scen, system = key.split("/", 1)
                    for seed, r in enumerate(runs):
                        writer.writerow([scen, system, seed, r["precision"], r["recall"], r["f1"],
                                         r["action_accuracy"], r["approval_compliance"], r["unsafe_auto"],
                                         r["rca_accuracy"], r["latency_ms"]])
        return self.out / "results.md"
