"""Run deterministic synthetic development fixtures; writes new output files only."""
import argparse
import json
from pathlib import Path
from .harness import EVIDENCE_CLASS, run_case, summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text())
    if config["evidence_class"] != EVIDENCE_CLASS:
        raise ValueError("this runner is synthetic_harness only")
    root = Path(__file__).resolve().parents[1]
    data_path = root / config["cases_path"]
    cases = [json.loads(line) for line in data_path.read_text().splitlines() if line.strip()]
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("duplicate case IDs")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    names = ("trajectories.jsonl", "case_results.jsonl", "metrics.json")
    if any((output / name).exists() for name in names):
        raise FileExistsError("refusing to overwrite finished or partial experiment artifacts")
    results, trajectories = [], []
    for mode in config["modes"]:
        selected = cases if mode == "end_to_end" else [case for case in cases if case.get("fixed_prefix")]
        for policy in config["policies"]:
            for case in selected:
                result, trajectory = run_case(case, policy, mode, config["tool_budget"], config["turn_budget"])
                results.append(result)
                trajectories.append(trajectory)
    for filename, rows in (("trajectories.jsonl", trajectories), ("case_results.jsonl", results)):
        with (output / filename).open("x") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    metrics = summarize(results)
    with (output / "metrics.json").open("x") as handle:
        json.dump(metrics, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({"evidence_class": EVIDENCE_CLASS, "case_count": len(cases),
                      "evaluation_rows": len(results), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
