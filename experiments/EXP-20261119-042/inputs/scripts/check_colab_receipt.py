"""Archive a pinned client lifecycle: successful cleanup does not prove model generation."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path


def require(ok, message):
    if not ok:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    source_dir = args.output / "operation_sources"
    source_dir.mkdir(exist_ok=False)
    for ref in config["sources"]:
        src = Path(ref["path"])
        name = ref["archive_name"]
        require(Path(name).name == name, "invalid archive basename")
        content = src.read_bytes()
        require(hashlib.sha256(content).hexdigest() == ref["sha256"], "source changed: " + name)
        with (source_dir / name).open("xb") as f:
            f.write(content)

    rows = [json.loads(line) for line in (source_dir / "lifecycle.jsonl").read_text().splitlines()]
    step = {r["step"]: r for r in rows}
    require(len(rows) == len(step), "duplicate lifecycle stage")
    require([r["elapsed_s"] for r in rows] == sorted(r["elapsed_s"] for r in rows), "reordered time")
    observed = [r["step"] for r in rows]
    phases = ["reservation", "baseline_usage_observation", "new", "new_endpoint_verified", "allocated_usage_observation",
              "status", "single_inference", "download_receipt", "work_failed", "stop_owned_only", "unassign_ack",
              "final_usage_observation", "cleanup_observed", "finished"]
    require([key for key in observed if key in phases] == phases, "missing or reordered required phases")
    payload_sha = hashlib.sha256((source_dir / "colab_inference_probe.py").read_bytes()).hexdigest()
    require(step["reservation"]["allowlist_sha256"] == payload_sha, "attempted payload not pinned")
    require(step["reservation"]["training"] is False and step["single_inference"]["exit"] == 0,
            "unexpected attempt status")
    require(step["new"]["exit"] == 0 and step["status"]["exit"] == 0, "allocation/status failed")
    require(step["download_receipt"]["error"] == "client_wall_timeout" and
            step["work_failed"]["error"] == "client_wall_timeout" and "remote_receipt" not in step and
            step["finished"]["fresh_inference_receipt"] is False, "receipt status mismatch")
    require(not Path(config["expected_local_receipt"]).exists(), "local receipt present: requires separate review")
    before, during, after = (step[name + "_usage_observation"] for name in ("baseline", "allocated", "final"))
    require(during["assignments"] == before["assignments"] + 1 and after["assignments"] == before["assignments"],
            "assignment count not restored")
    require(step["stop_owned_only"]["exit"] == 0 and step["unassign_ack"]["confirmed"] is True and
            step["cleanup_observed"]["new_assignment_absent"] is True and
            step["cleanup_observed"]["initial_assignments_unchanged"] is True and
            step["finished"]["baseline_assignments_restored"] is True, "cleanup unconfirmed")
    report = {"evidence_class": "analysis", "scope": "offline audit of sanitized Colab client observations only",
              "source_count": len(config["sources"]), "probe_sha256": payload_sha,
              "provider_session_type_observed": "L4", "remote_exec_cli_exit_code": 0,
              "remote_output_verified": False, "model_generation_verified": False, "local_model_runs": 0,
              "verified_generation_count": 0, "actual_model_generation_count": None,
              "parse_result": None, "outcome": "model_evidence_incomplete_download_timed_out",
              "receipt_transfer_error": step["download_receipt"]["error"],
              "owned_assignment_release_observed": True, "baseline_assignments_restored": True,
              "account_assignments_before_during_after": [before["assignments"], during["assignments"], after["assignments"]],
              "account_rates_cu_per_hour_before_during_after": [before["rate_cu_hr"], during["rate_cu_hr"], after["rate_cu_hr"]],
              "observed_account_rate_increment_cu_per_hour":
                  float(Decimal(str(during["rate_cu_hr"])) - Decimal(str(before["rate_cu_hr"]))),
              "exact_per_session_cu_billed": None, "training_performed_by_probe": False,
              "gross_client_elapsed_seconds": step["finished"]["elapsed_s"],
              "limitations": "No remote JSON: CUDA runtime, weight verification, generated tokens, parse, task correctness and per-instance bill are unverified. Zero verified is not zero generated."}
    with (args.output / "receipt_audit.json").open("x") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
