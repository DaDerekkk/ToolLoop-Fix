"""Describe the first archived decision after a matched error type; do not run models."""
import argparse
import hashlib
import json
from pathlib import Path


def require(ok, message):
    if not ok:
        raise ValueError(message)


def select(rows, cid, first=False):
    matches = [(n, r) for n, r in enumerate(rows, 1)
               if r["case_id"] == cid and (not first or r["continuation_turn"] == 1)]
    require(len(matches) == 1, "expected exactly one matching row")
    return matches[0]


def analyze(cfg):
    cells, prefix_hash, seen = [], {}, set()
    for ref in cfg["source_runs"]:
        path = Path(ref["path"])
        manifest_bytes = (path / "manifest.json").read_bytes()
        require(hashlib.sha256(manifest_bytes).hexdigest() == ref["manifest_sha256"], "source manifest changed")
        m = json.loads(manifest_bytes)
        require(m["status"] == "completed" and m["evidence_valid"], "invalid inference source")
        tables = {}
        for name, pinned in ref["artifact_sha256"].items():
            raw = (path / name).read_bytes()
            require(hashlib.sha256(raw).hexdigest() == pinned == m["outputs"][name], "evidence bytes changed")
            tables[name] = [json.loads(line) for line in raw.splitlines()]
        pair = []
        for cid in cfg["case_ids"]:
            p_line, p = select(tables["prefix_contexts.jsonl"], cid)
            r_line, r = select(tables["case_results.jsonl"], cid)
            e_line, e = select(tables["execution_events.jsonl"], cid, first=True)
            g_line, g = select(tables["generations.jsonl"], cid, first=True)
            key = (r["wording_profile"], r["comparison_arm"])
            require(all((x["wording_profile"], x["comparison_arm"]) == key for x in (p, e, g)), "cell mismatch")
            if pair:
                require(cell_key == key, "mixed pair")
            cell_key = key
            prefix = p["replayed_prefix_events"]
            require(len(prefix) == 1 and p["goal_at_prefix"] is False and r["recovery_eligible"], "not an initial recovery prefix")
            prev = prefix[0]
            require(prev["response"] == {"code": "TIMEOUT", "retryable": True} and prev["actual_tool_dispatch"], "not a retryable TIMEOUT")
            h = p["prefix_snapshot_sha256"]
            require(prefix_hash.setdefault(cid, h) == h == r["prefix_snapshot_sha256"] == e["snapshot_before_sha256"],
                    "source prefix mismatch")
            require(e["turn"] == g["turn"] == prev["turn"]+1 and e["phase"] == g["phase"] == "fresh_continuation",
                    "first fresh turn mismatch")
            proposed = e["action"]
            require(g["parse"]["valid"] and g["parse"]["action"] == proposed, "first parse/event mismatch")
            same = proposed == prev["action"]
            dispatch = e["actual_tool_dispatch"]
            pair.append({"case_id": cid, "prefix_call": prev["action"], "prefix_source_run": p["source_provenance"]["source_run"],
                         "prefix_snapshot_sha256": h, "first_fresh_seed": g["seed"], "first_proposed_action": proposed,
                         "terminal_form": g["parse"]["terminal_form"], "same_tool_and_arguments": same,
                         "actual_business_dispatch": dispatch, "same_call_executed": bool(same and dispatch),
                         "first_business_response_code": e["response"]["code"] if dispatch else None,
                         "first_business_response_OK": bool(dispatch and e["response"]["code"] == "OK"),
                         "guard_blocked": e["guard_blocked"], "final_new_goal_evidence_archived": r["new_goal_evidence"],
                         "source_lines_1based": {"prefix_contexts": p_line, "generations": g_line,
                                                 "execution_events": e_line, "case_results": r_line}})
        require(cell_key not in seen, "duplicate cell")
        seen.add(cell_key)
        cells.append({"wording": cell_key[0], "arm": cell_key[1], "source_run": path.name, "paired_cases": pair})
    require(seen == {(w, a) for w in ("legacy", "explicit") for a in ("B0", "B1", "B2")}, "not six cells")
    return {"evidence_class":"analysis","scope":"retrospective_first_action_only","hypothesis_tested":False,
            "unique_original_prefixes":2,"paired_conditions":6,"new_model_generations":0,"rescored":False,
            "cells":cells,"warning":"Two selected fixtures reused across conditions; different tools, identifiers, source prompts and case-specific seeds. Not 12 independent tasks, nor a causal effect of resource domain."}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    args=p.parse_args();out=analyze(json.loads(args.config.read_text()))
    with (args.output/"timeout_pair.json").open("x") as f:
        json.dump(out,f,ensure_ascii=False,indent=2);f.write("\n")
    for cell in out["cells"]:
        print(cell["wording"],cell["arm"],[(r["case_id"],r["same_call_executed"],r["first_business_response_code"],r["final_new_goal_evidence_archived"]) for r in cell["paired_cases"]])


if __name__ == "__main__":
    main()
