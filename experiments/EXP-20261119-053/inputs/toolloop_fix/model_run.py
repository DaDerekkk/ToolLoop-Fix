"""Real model preflight on the existing simulated environment; no policy fallback."""
import argparse
import json
from pathlib import Path
import re
import sys

from .harness import Environment, detect_loops, ratio, state_hash, summarize
from .model_backend import TransformersBackend
from .model_protocol import ADAPTER_VERSION, messages_for_view, parse_tool_output, tool_schemas


ARTIFACTS = ("model_provenance.json", "generations.jsonl", "trajectories.jsonl",
             "case_results.jsonl", "metrics.json", "execution_events.jsonl",
             "partial_case.json", "model_failure.json")


def validate_config(config):
    if config.get("evidence_class") != "model_inference" or config.get("evidence_kind") != "model_inference":
        raise ValueError("real runner requires model_inference evidence labels")
    if config.get("mode") != "end_to_end":
        raise ValueError("first model adapter supports end_to_end only")
    model = config["model"]
    if model["backend"] != "transformers" or not re.fullmatch(r"[0-9a-f]{40}", model["revision"]):
        raise ValueError("requires transformers and a pinned 40-hex model revision")
    if config["sampling"]["enable_thinking"] is not False:
        raise ValueError("this preflight is explicitly nonthinking")
    if config["sampling"]["do_sample"] is not True:
        raise ValueError("this preflight uses the official nonthinking sampling configuration")
    for field in ("tool_budget", "turn_budget", "max_context_tokens"):
        if not isinstance(config[field], int) or config[field] <= 0:
            raise ValueError("positive integer required: " + field)


def run_model_case(case, backend, config, case_index=0, emit_generation=None, emit_event=None, emit_partial=None):
    """Only messages derived from public_view reach backend.generate."""
    env = Environment(case)
    initial_snapshot = env.snapshot()
    traces, decisions = [], []
    calls = turns = input_tokens = output_tokens = parser_errors = 0
    exhausted = output_limit = False
    generation_seconds = 0.0
    identity = {"case_id": case["case_id"], "domain": case["domain"], "category": case["category"],
                "template_group": case["template_group"], "policy": config["policy_name"],
                "mode": "end_to_end", "evidence_class": "model_inference", "adapter_version": ADAPTER_VERSION}
    while env.snapshot()["terminal"] is None:
        if calls >= config["tool_budget"] or turns >= config["turn_budget"]:
            exhausted = True
            break
        view = env.public_view()
        messages, tools = messages_for_view(view, decisions), tool_schemas(view)
        seed = config["sampling"]["seed"] + case_index * config["turn_budget"] + turns
        # The backend gets no case/category/hidden snapshot; only public protocol inputs.
        try:
            generation = backend.generate(messages, tools, seed)
        except Exception as exc:
            if emit_generation:
                emit_generation({**identity, "turn": turns + 1, "messages": messages, "tools": tools,
                    "seed": seed, "generation_error": {"type": type(exc).__name__, "message": str(exc)},
                    "input_tokens": None, "output_tokens": None, "raw_text": None})
            if emit_partial:
                emit_partial({**identity, "state": env.snapshot(), "events": traces,
                              "tool_attempts": calls, "turns": turns, "status": "generation_failed"})
            raise
        parsed = parse_tool_output(generation["raw_text"], generation["token_limit_reached"])
        parsed["raw_text"] = generation["raw_text"]
        record = {**identity, "turn": turns + 1, "messages": messages, "tools": tools,
                  **generation, "parse": parsed}
        if emit_generation:
            emit_generation(record)
        input_tokens += generation["input_tokens"]
        output_tokens += generation["output_tokens"]
        generation_seconds += generation["elapsed_seconds"]
        if parsed["valid"]:
            action = parsed["action"]
        else:
            # Raw content retained: distinct malformed outputs are not collapsed to one action.
            action = {"type": "invalid_model_output", "raw_text": generation["raw_text"],
                      "parser_error": parsed["error"]}
            parser_errors += 1
        is_attempt = action.get("type") not in {"final", "stop"}
        calls += int(is_attempt)
        turns += 1
        event = {**env.execute(action), "phase": "continuation", "turn": turns,
                 "parser_error": parsed["error"], "requested_tool_call_count": parsed["requested_tool_call_count"]}
        traces.append(event)
        decisions.append(parsed)
        if emit_event:
            emit_event({**identity, **event})
        if emit_partial:
            emit_partial({**identity, "state": env.snapshot(), "events": traces,
                          "tool_attempts": calls, "turns": turns, "status": "running"})
        if generation["token_limit_reached"]:
            exhausted = output_limit = True
            break
    outcome = env.evaluate(exhausted)
    flags = detect_loops(env.public_view()["history"])
    result = {**identity, **outcome, "tool_attempts": calls, "turns": turns,
        "prefix_tool_attempts": 0, "prefix_turns": 0,
        "continuation_tool_attempts": calls, "continuation_turns": turns,
        "remaining_tool_budget_at_start": config["tool_budget"],
        "remaining_turn_budget_at_start": config["turn_budget"],
        "budget_scope": "total_including_prefix", "has_invalid_loop": bool(flags),
        "has_inherited_loop": False, "loop_types": sorted({flag["type"] for flag in flags}),
        "prefix_snapshot_sha256": state_hash(initial_snapshot),
        "parser_error_turns": parser_errors, "output_token_limit_reached": output_limit,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "generation_seconds": generation_seconds}
    trajectory = {**identity, "public_initial_request": case["request"],
        "prefix_provenance": None, "prefix_snapshot": initial_snapshot,
        "events": traces, "loop_flags": flags, "prefix_loop_flags": [],
        "continuation_loop_flags": flags, "final_snapshot": env.snapshot()}
    return result, trajectory


def model_metrics(results):
    metrics = summarize(results)
    metrics.update(evidence_class="model_inference", adapter_version=ADAPTER_VERSION,
        warning="Actual raw-model inference on hand-authored development fixtures with a custom single-call XML adapter; not a native benchmark, held-out result, training result or validated model-loop attribution.")
    for group in metrics["groups"]:
        rows = [row for row in results if row["policy"] == group["policy"] and row["mode"] == group["mode"]]
        completable = [row for row in rows if row["completable"]]
        success_count = sum(row["success"] for row in completable)
        group.update(
            parser_error_turn_rate=ratio(sum(row["parser_error_turns"] for row in rows), sum(row["turns"] for row in rows)),
            input_tokens=sum(row["input_tokens"] for row in rows),
            output_tokens=sum(row["output_tokens"] for row in rows),
            generation_seconds=sum(row["generation_seconds"] for row in rows),
            tokens_per_success=ratio(sum(row["input_tokens"] + row["output_tokens"] for row in completable), success_count))
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    validate_config(config)
    root = Path(__file__).resolve().parents[1]
    cases = [json.loads(line) for line in (root / config["cases_path"]).read_text().splitlines() if line.strip()]
    selected_ids = config.get("case_ids")
    if selected_ids is not None:
        by_id = {case["case_id"]: case for case in cases}
        if len(set(selected_ids)) != len(selected_ids) or any(case_id not in by_id for case_id in selected_ids):
            raise ValueError("case_ids must be unique known fixtures")
        cases = [by_id[case_id] for case_id in selected_ids]
    if not cases:
        raise ValueError("model run must select at least one case")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ARTIFACTS):
        raise FileExistsError("refusing to overwrite model artifacts")
    def write_json(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    handles = {}
    def emit(name, value):
        if name not in handles:
            handles[name] = (output / name).open("x")
        handles[name].write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handles[name].flush()
    stage = "model_load"
    try:
        backend = TransformersBackend(config, tool_schemas(Environment(cases[0]).public_view()))
        write_json("model_provenance.json", backend.provenance())
        results = []
        for case_index, case in enumerate(cases):
            stage = "case:" + case["case_id"]
            result, trajectory = run_model_case(case, backend, config, case_index,
                emit_generation=lambda row: emit("generations.jsonl", row),
                emit_event=lambda row: emit("execution_events.jsonl", row),
                emit_partial=lambda row: write_json("partial_case.json", row))
            emit("case_results.jsonl", result)
            emit("trajectories.jsonl", trajectory)
            results.append(result)
            print(json.dumps({"case_id": case["case_id"], "success": result["success"],
                              "turns": result["turns"], "parser_errors": result["parser_error_turns"]}), flush=True)
        write_json("metrics.json", model_metrics(results))
        write_json("partial_case.json", {"evidence_class": "model_inference", "status": "completed",
                                         "completed_case_count": len(results)})
    except Exception as exc:
        write_json("model_failure.json", {"evidence_class": "model_inference", "stage": stage,
            "error_type": type(exc).__name__, "message": str(exc), "executable": sys.executable,
            "note": "Failure retained; no scripted fallback or success artifact fabricated."})
        raise
    finally:
        for handle in handles.values():
            handle.close()


if __name__ == "__main__":
    main()
