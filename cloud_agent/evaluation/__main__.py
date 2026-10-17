"""CLI: ``python -m cloud_agent.evaluation [--quick] [--seeds N] [--only E ...] [--testbed moto]``."""

from __future__ import annotations

import argparse
import sys

from cloud_agent.utils.logger import set_global_level


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Cloud Agentic AI evaluation experiments")
    parser.add_argument("--seeds", type=int, default=10, help="Repetitions per configuration (default 10)")
    parser.add_argument("--quick", action="store_true", help="3 seeds and a smaller scalability sweep")
    parser.add_argument("--only", nargs="*", choices=["scenarios", "ablation", "robustness", "scalability", "live", "noise"],
                        help="Run a subset of the experiments")
    parser.add_argument("--with-llm", action="store_true",
                        help="Also evaluate LLM reasoning (needs OPENAI_API_KEY; costs API calls)")
    parser.add_argument("--testbed", choices=["mock", "moto"], default="mock",
                        help="mock = scenario simulator (default); moto = AWSProvider against emulated AWS APIs")
    parser.add_argument("--out", default="results", help="Output directory (default: results/)")
    parser.add_argument("--list", action="store_true", help="List scenarios and exit")
    args = parser.parse_args()

    set_global_level("CRITICAL")
    from cloud_agent.evaluation.scenarios import SCENARIOS
    from cloud_agent.utils.config import load_config

    if args.list:
        for s in SCENARIOS.values():
            print(f"{s.name:20} {s.title} — {s.description.strip()}")
        return

    config = load_config()
    set_global_level("CRITICAL")

    if args.testbed == "moto":
        from cloud_agent.evaluation.testbeds import run_moto_testbed

        path = run_moto_testbed(config, out_dir=args.out, seeds=3 if args.quick else args.seeds)
    else:
        from cloud_agent.evaluation.runner import Runner

        runner = Runner(config, seeds=3 if args.quick else args.seeds, out_dir=args.out,
                        with_llm=args.with_llm, quick=args.quick)
        path = runner.run(args.only)
    print(f"Results written to {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
