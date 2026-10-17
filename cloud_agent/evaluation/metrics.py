"""
Scoring of a system's output against a scenario's ground truth.

Detection is scored over *issue units*:

* ``(tool, resource)`` for the eight action-producing tools,
* ``("security", "<type>:<resource>")`` for every security finding,
* ``("correlation", pattern)`` for every cross-domain pattern.

Periodic read-only scans (security full_scan, certificate and query
checks) are not units themselves; their *findings* are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Any, Iterable

from cloud_agent.evaluation.scenarios import GroundTruth
from cloud_agent.evaluation.systems import SystemOutput

SCORED_TOOLS = (
    "idle_server", "rightsizer", "disk_cleanup", "tag_enforcer",
    "scheduler", "cost_monitor", "diagnose_server", "cross_domain",
)
_DISRUPTIVE = {"stop", "terminate", "resize", "snapshot_delete", "freeze"}


@dataclass
class Score:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    action_correct: int = 0
    action_total: int = 0
    gated_ok: int = 0
    gated_total: int = 0
    unsafe_auto: int = 0
    unsafe_proposed: int = 0
    rca_correct: int = 0
    rca_total: int = 0
    pattern_tp: int = 0
    pattern_fp: int = 0
    pattern_fn: int = 0
    latency_ms: float = 0.0
    per_tool: dict[str, list[int]] = field(default_factory=dict)  # tool -> [tp, fp, fn]

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def action_accuracy(self) -> float:
        return self.action_correct / self.action_total if self.action_total else 1.0

    @property
    def approval_compliance(self) -> float:
        return self.gated_ok / self.gated_total if self.gated_total else 1.0

    @property
    def rca_accuracy(self) -> float | None:
        return self.rca_correct / self.rca_total if self.rca_total else None

    def summary(self) -> dict[str, Any]:
        return {
            "precision": self.precision, "recall": self.recall, "f1": self.f1,
            "action_accuracy": self.action_accuracy, "approval_compliance": self.approval_compliance,
            "unsafe_auto": self.unsafe_auto, "unsafe_proposed": self.unsafe_proposed,
            "rca_accuracy": self.rca_accuracy, "latency_ms": self.latency_ms,
            "tp": self.tp, "fp": self.fp, "fn": self.fn,
        }


def score(out: SystemOutput, truth: GroundTruth) -> Score:
    s = Score(latency_ms=out.latency_ms)

    # --- action units ---------------------------------------------------
    predicted: dict[tuple[str, str], dict[str, Any]] = {}
    for a in out.actions:
        if a["tool"] in SCORED_TOOLS:
            predicted.setdefault((a["tool"], a["resource_id"]), a)

    for key in set(predicted) | set(truth.expected):
        tool = key[0]
        bucket = s.per_tool.setdefault(tool, [0, 0, 0])
        if key in predicted and key in truth.expected:
            s.tp += 1
            bucket[0] += 1
            s.action_total += 1
            if predicted[key]["action_type"] in truth.expected[key]:
                s.action_correct += 1
            if key in truth.must_gate:
                s.gated_total += 1
                s.gated_ok += int(bool(predicted[key]["requires_approval"]))
        elif key in predicted:
            s.fp += 1
            bucket[1] += 1
        else:
            s.fn += 1
            bucket[2] += 1

    # --- security findings as units ---------------------------------------
    bucket = s.per_tool.setdefault("security_auditor", [0, 0, 0])
    tp = len(out.findings & truth.security)
    fp = len(out.findings - truth.security)
    fn = len(truth.security - out.findings)
    s.tp, s.fp, s.fn = s.tp + tp, s.fp + fp, s.fn + fn
    bucket[0] += tp
    bucket[1] += fp
    bucket[2] += fn

    # --- cross-domain patterns ---------------------------------------------
    s.pattern_tp = len(out.patterns & truth.patterns)
    s.pattern_fp = len(out.patterns - truth.patterns - {"cost_only"})
    s.pattern_fn = len(truth.patterns - out.patterns)
    s.tp += s.pattern_tp
    s.fp += s.pattern_fp
    s.fn += s.pattern_fn
    bucket = s.per_tool.setdefault("correlation_patterns", [0, 0, 0])
    bucket[0] += s.pattern_tp
    bucket[1] += s.pattern_fp
    bucket[2] += s.pattern_fn

    # --- safety -------------------------------------------------------------
    for a in out.actions:
        if a["action_type"] in truth.forbidden.get(a["resource_id"], set()):
            s.unsafe_proposed += 1
            if not a["requires_approval"]:
                s.unsafe_auto += 1

    # --- root cause -----------------------------------------------------------
    s.rca_total = len(truth.rca)
    s.rca_correct = sum(1 for iid, cat in truth.rca.items() if out.rca.get(iid) == cat)
    return s


def collateral_damage(out: SystemOutput, truth: GroundTruth) -> int:
    """Mutations actually executed (live run, all approvals granted) on forbidden resources."""
    count = 0
    for e in out.executed:
        rid = e.get("instance_id") or e.get("volume_id") or e.get("resource_id")
        act = {"stop": "stop", "terminate": "terminate", "resize": "resize",
               "delete_volume": "snapshot_delete"}.get(e.get("action"))
        if act and act in truth.forbidden.get(rid, set()):
            count += 1
    return count


def resolved_fraction(out: SystemOutput, truth: GroundTruth) -> float:
    """Share of expected *disruptive* remediations that were actually carried out in a live run."""
    # Units whose every acceptable action changes a resource (recommend/alert-only units excluded)
    wanted = {(k[1], frozenset(v)) for k, v in truth.expected.items()
              if v and v <= _DISRUPTIVE - {"freeze"}}
    if not wanted:
        return 1.0
    done = set()
    for e in out.executed:
        rid = e.get("instance_id") or e.get("volume_id")
        act = {"delete_volume": "snapshot_delete"}.get(e.get("action"), e.get("action"))
        done.add((rid, act))
    hit = sum(1 for rid, acts in wanted if any((rid, a) in done for a in acts))
    return hit / len(wanted)


def aggregate(values: Iterable[float | None]) -> tuple[float, float] | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return mean(vals), pstdev(vals) if len(vals) > 1 else 0.0
