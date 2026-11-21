"""Comprehensive stdlib unit tests for ITER-007 runners, comparator, and CPU replay auditor."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from scripts import audit_iter7_runs, compare_iter7_runs, validate_records
from toolloop_fix.baselines import GUARD_VERSION, prompt_for
from toolloop_fix.calibrated_protocol import (
    CALIBRATED_PROTOCOL_VERSION,
    CALIBRATED_SCORE_VERSION,
    CALIBRATED_SYSTEM_PROMPT,
    parse_calibrated_output,
)
from toolloop_fix.harness import DOMAIN_TOOLS, Environment, state_hash
from toolloop_fix.iter7_runners import (
    CATEGORIES,
    DOMAINS,
    EXPLICIT_TASK_PREFIX,
    ITER7_RUNNER_VERSION,
    PREFIX_RUNTIME_VERSION,
    PREFIX_SCHEMA_VERSION,
    TASK_WORDING_VERSION,
    main as iter7_main,
    prepare_iter7_prefix,
    resolve_decoding_regime,
    validate_explicit_e2e_config,
    validate_iter7_prefix_config,
    validate_prefix_multiseed_config,
)
from toolloop_fix.model_backend import sha256_file

ROOT = Path(__file__).resolve().parents[1]
PILOT_CASES = [json.loads(line) for line in (ROOT / "data/dev/pilot_cases.jsonl").read_text().splitlines() if line.strip()]


def xml(name, **arguments):
    return (
        "<tool_call>\n<function="
        + name
        + ">\n"
        + "".join("<parameter={}>\n{}\n</parameter>\n".format(k, v) for k, v in arguments.items())
        + "</function>\n</tool_call><|im_end|>"
    )


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows))


class DeterministicTestBackend:
    """Pure-stdlib test double satisfying model_artifact_errors without loading torch/transformers."""

    def __init__(self, config, initial_tools, responder=None):
        self.config = copy.deepcopy(config)
        self.initial_tools = copy.deepcopy(initial_tools)
        self.responder = responder
        self.calls = []
        model_ref = next(ref for ref in config["external_inputs"] if ref["name"] == "model_manifest")
        self.manifest_sha256 = model_ref["sha256"]
        self.chat_template_sha256 = hashlib.sha256(json.dumps(initial_tools, sort_keys=True).encode()).hexdigest()
        self.effective_generation = {
            key: config["sampling"][key]
            for key in ("do_sample", "temperature", "top_p", "top_k", "min_p", "repetition_penalty", "max_new_tokens")
        }
        self.effective_generation.update(
            eos_token_id=[248044, 248046],
            pad_token_id=248044,
            presence_penalty=config["sampling"]["presence_penalty"],
            presence_penalty_implementation="additive_unique_current_completion_tokens_v1",
            presence_penalty_scope="generated_tokens_since_current_prompt_end",
            enable_thinking=False,
            attn_implementation="eager",
            use_cache=True,
        )

    def provenance(self):
        return {
            "schema_version": 1,
            "evidence_class": "model_inference",
            "model": copy.deepcopy(self.config["model"]),
            "sampling": copy.deepcopy(self.config["sampling"]),
            "runtime": {
                "executable": sys.executable,
                "python_version": sys.version,
                "platform": "unit-test-stdlib",
                "packages": {
                    "torch": "0.0-testdouble",
                    "transformers": "0.0-testdouble",
                    "tokenizers": "0.0-testdouble",
                    "safetensors": "0.0-testdouble",
                },
            },
            "model_manifest_sha256": self.manifest_sha256,
            "model_files_verified": True,
            "chat_template_sha256": self.chat_template_sha256,
            "effective_generation": copy.deepcopy(self.effective_generation),
            "actual_model_class": "DeterministicTestBackend",
            "actual_parameter_device": self.config["model"]["device"],
            "actual_parameter_dtype": self.config["model"]["dtype"],
            "checkpoint_loading_info": {"missing_keys": [], "unexpected_keys": [], "mismatched_keys": [], "error_msgs": []},
        }

    def generate(self, messages, tools, seed):
        self.calls.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools), "seed": seed})
        raw_text = self.responder(messages, tools, seed, self.config)
        if isinstance(raw_text, Exception):
            raise raw_text
        prompt_text = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False, sort_keys=True)
        prompt_ids = [101, 102, (seed % 97) + 1]
        gen_ids = [201 + (index % 17) for index in range(max(1, min(8, len(raw_text) // 12 + 1)))]
        return {
            "raw_text": raw_text,
            "prompt_text": prompt_text,
            "prompt_sha256": hashlib.sha256(prompt_text.encode()).hexdigest(),
            "prompt_token_ids": prompt_ids,
            "generated_token_ids": gen_ids,
            "input_tokens": len(prompt_ids),
            "output_tokens": len(gen_ids),
            "elapsed_seconds": 0.05,
            "token_limit_reached": False,
            "seed": seed,
            "effective_generation": copy.deepcopy(self.effective_generation),
        }


def build_six_authentic_style_prefixes(temp_root):
    """Build 6 verifiable synthetic source-backed prefixes matching the 6 authentic roles/IDs."""
    specs = [
        ("pfx-orders-timeout", "orders", "retry_required", "recovery", [xml("get_order", identifier="ORD-101")]),
        ("pfx-files-timeout", "files", "retry_required", "recovery", [xml("read_file", identifier="/records/report.txt")]),
        (
            "pfx-jobs-precondition",
            "jobs",
            "retry_required",
            "recovery",
            [xml("get_result", identifier="JOB-101"), xml("get_result", identifier="JOB-101")],
        ),
        ("pfx-orders-invalid", "orders", "recoverable", "recovery", [xml("get_order", identifier="ORD-OLD")]),
        (
            "pfx-orders-unavailable",
            "orders",
            "unrecoverable",
            "unavailable_stop_control",
            [xml("get_order", identifier="ORD-101"), xml("find_order", target="latest order")],
        ),
        ("pfx-orders-ok", "orders", "normal", "normal_completion_control", [xml("get_order", identifier="ORD-101")]),
    ]
    source = temp_root / "synthetic_prefix_source_run"
    source.mkdir(parents=True, exist_ok=True)
    cases_rel = "data/dev/pilot_cases.jsonl"
    cases_path = source / "inputs" / cases_rel
    write_rows(cases_path, PILOT_CASES)
    harness_target = source / "inputs/toolloop_fix/harness.py"
    harness_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "toolloop_fix/harness.py", harness_target)
    write_json(source / "model_provenance.json", {"evidence_class": "model_inference", "unit_test_double": True})

    all_source_gens = []
    all_source_trajs = []
    prefix_rows = []
    for prefix_id, domain, category, role, raw_calls in specs:
        case = copy.deepcopy(
            next(c for c in PILOT_CASES if c["domain"] == domain and c["category"] == category)
        )
        env = Environment(case)
        gens, events = [], []
        gen_line_numbers = []
        for turn, raw in enumerate(raw_calls, 1):
            decision = parse_calibrated_output(raw, False)
            before = env.snapshot()
            event = env.execute(decision["action"])
            event.update(
                turn=turn,
                guard_blocked=False,
                executed=True,
                actual_tool_dispatch=True,
                snapshot_before_sha256=state_hash(before),
                snapshot_after_sha256=state_hash(env.snapshot()),
            )
            events.append(event)
            gen_record = {
                "evidence_class": "model_inference",
                "case_id": case["case_id"],
                "turn": turn,
                "raw_text": raw,
                "token_limit_reached": False,
                "parse": decision,
                "input_tokens": 4,
                "output_tokens": 2,
                "prompt_token_ids": [1, 2, 3, 4],
                "generated_token_ids": [5, 6],
                "elapsed_seconds": 0.1,
            }
            gens.append(gen_record)
            all_source_gens.append(gen_record)
            gen_line_numbers.append(len(all_source_gens))
        all_source_trajs.append(
            {"case_id": case["case_id"], "evidence_class": "model_inference", "events": events}
        )
        traj_line = len(all_source_trajs)
        prefix_rows.append(
            {
                "case_id": prefix_id,
                "prefix_schema_version": PREFIX_SCHEMA_VERSION,
                "role": role,
                "original_case": case,
                "prefix_generations": gens,
                "prefix_events": events,
                "prefix_snapshot": env.snapshot(),
                "provenance": {
                    "source_run": str(source),
                    "source_case_id": case["case_id"],
                    "source_generation_lines_1based": gen_line_numbers,
                    "source_event_indices_1based": list(range(1, len(raw_calls) + 1)),
                    "source_trajectory_line_1based": traj_line,
                    "source_prefix_turns": len(raw_calls),
                    "source_cases_path": str(cases_path),
                    "source_case_sha256": state_hash(case),
                    "transplant_note": "Synthetic unit-test source archive.",
                },
            }
        )

    write_rows(source / "generations.jsonl", all_source_gens)
    write_rows(source / "trajectories.jsonl", all_source_trajs)
    outputs = {
        name: sha256_file(source / name)
        for name in ("generations.jsonl", "trajectories.jsonl", "model_provenance.json")
    }
    inputs = {
        cases_rel: sha256_file(cases_path),
        "toolloop_fix/harness.py": sha256_file(harness_target),
    }
    write_json(
        source / "manifest.json",
        {
            "run_id": "SYNTH-SOURCE-001",
            "kind": "model_inference",
            "status": "completed",
            "exit_code": 0,
            "evidence_valid": True,
            "inputs": inputs,
            "outputs": outputs,
        },
    )
    manifest_sha = sha256_file(source / "manifest.json")
    for row in prefix_rows:
        row["provenance"].update(
            source_manifest_sha256=manifest_sha,
            source_generations_sha256=outputs["generations.jsonl"],
            source_trajectories_sha256=outputs["trajectories.jsonl"],
            source_cases_sha256=inputs[cases_rel],
        )
    return prefix_rows


def build_unseen_e2e_cases():
    """Build a 15-case unseen-template E2E fixture suite covering all 3 domains x 5 categories."""
    cases = []
    for index, base in enumerate(PILOT_CASES, 1):
        clone = copy.deepcopy(base)
        domain = clone["domain"]
        category = clone["category"]
        clone["case_id"] = "unseen-{}-{}-{:03d}".format(domain, category, index)
        clone["template_group"] = "iter7-unseen-{}-{}-g1".format(domain, category)
        clone["request"]["target"] = "unseen target " + clone["request"]["target"]
        if category == "retry_required" and domain == "jobs":
            clone["fault_subtype"] = "job_poll_precondition_2"
        elif category == "retry_required":
            clone["fault_subtype"] = "transient_timeout_1"
        elif category == "recoverable":
            clone["fault_subtype"] = "stale_identifier_lookup"
        cases.append(clone)
    return cases


def smart_policy_responder(messages, tools, seed, config):
    """Deterministic seed-sensitive policy responder depending ONLY on public messages/tools/seed."""
    request_str = messages[1]["content"].split("Request: ", 1)[-1]
    if messages[1]["content"].startswith("Complete this request: "):
        request_str = messages[1]["content"].split("Complete this request: ", 1)[-1]
    request = json.loads(request_str)
    domain = request["domain"]
    primary, locate = DOMAIN_TOOLS[domain]
    arm_is_b1 = prompt_for("B1") in messages[0]["content"]
    do_sample = config["sampling"]["do_sample"]
    penalty = config["sampling"]["presence_penalty"]

    tool_msgs = [m for m in messages if m["role"] == "tool"]
    guard_notices = [
        m for m in messages if m["role"] == "user" and m["content"].startswith("Runtime guard notice")
    ]
    last_tool_resp = json.loads(tool_msgs[-1]["content"]) if tool_msgs else None

    if last_tool_resp and last_tool_resp.get("code") == "OK":
        return xml("finish_task", answer=last_tool_resp["value"], identifier=last_tool_resp["identifier"])

    if last_tool_resp and last_tool_resp.get("code") == "RESOURCE_UNAVAILABLE":
        if guard_notices:
            return xml("stop_task", reason="RESOURCE_UNAVAILABLE")
        # Repeat non-retryable call twice if unblocked so B0 loops while B2 blocks and recovers to stop_task!
        if len(tool_msgs) < 4 and not arm_is_b1:
            ident = request.get("identifier", "ORD-101")
            return xml(primary, identifier=ident)
        return "Resource is unavailable.<|im_end|>"

    if last_tool_resp and last_tool_resp.get("code") == "TIMEOUT":
        # Disentangle greedy_pp0 vs greedy_pp2 vs sampled seeds!
        if not do_sample:
            if penalty == 0:
                return "Timeout occurred without retry.<|im_end|>"
            return xml(primary, identifier=request["identifier"])
        if domain == "orders" or (seed % 2 == 0) or arm_is_b1:
            return xml(primary, identifier=request["identifier"])
        return "Stopped on file timeout.<|im_end|>"

    if last_tool_resp and last_tool_resp.get("code") == "INVALID_IDENTIFIER":
        if guard_notices or arm_is_b1 or (do_sample and seed % 3 == 0):
            return xml(locate, target=request["target"])
        if len(tool_msgs) == 1:
            return xml(primary, identifier=request["identifier"])
        return "Could not find valid identifier.<|im_end|>"

    if last_tool_resp and last_tool_resp.get("code") in {"LOCATED", "USER_INFORMATION"}:
        return xml(primary, identifier=last_tool_resp["identifier"])

    if last_tool_resp and last_tool_resp.get("code") in {"PRECONDITION", "RUNNING"}:
        if arm_is_b1 and (seed % 5 == 0):
            return xml("poll_job", identifier=request["identifier"])
        return xml("stop_task", reason="PRECONDITION")

    if last_tool_resp and last_tool_resp.get("code") == "READY":
        return xml(primary, identifier=request["identifier"])

    # Initial E2E turn (no tool history yet)
    if not request.get("identifier"):
        if arm_is_b1 or (seed % 2 == 0):
            return xml("ask_user", field="identifier")
        return "Need identifier from user.<|im_end|>"
    return xml(primary, identifier=request["identifier"])


class Iter7WorkspaceHarness:
    """Builds frozen-snapshot experiment directories matching record_run.py / validate_records.py."""

    def __init__(self, temp_dir):
        self.base = Path(temp_dir)
        self.fake_model_dir = self.base / "fake_model_weights"
        self.fake_model_dir.mkdir(parents=True, exist_ok=True)
        weight_file = self.fake_model_dir / "model.safetensors"
        weight_file.write_bytes(b"unit-test-safetensors-stub")
        self.model_manifest_path = self.base / "fake_model_manifest.json"
        self.model_spec = {
            "model_id": "Qwen/Qwen3.5-0.8B",
            "revision": "2fc06364715b967f1860aea9cf38778875588b17",
            "backend": "transformers",
            "model_path": str(self.fake_model_dir.resolve()),
            "device": "cpu",
            "dtype": "float32",
        }
        write_json(
            self.model_manifest_path,
            {
                "model_id": self.model_spec["model_id"],
                "revision": self.model_spec["revision"],
                "model_path": self.model_spec["model_path"],
                "files": [
                    {
                        "path": "model.safetensors",
                        "sha256": sha256_file(weight_file),
                        "size_bytes": weight_file.stat().st_size,
                    }
                ],
            },
        )
        self.model_manifest_sha = sha256_file(self.model_manifest_path)
        self.prefix_rows = build_six_authentic_style_prefixes(self.base)
        self.e2e_cases = build_unseen_e2e_cases()

        # Build workspace mirror for inputs
        self.ws = self.base / "workspace"
        for rel in (
            "toolloop_fix/__init__.py",
            "toolloop_fix/harness.py",
            "toolloop_fix/model_protocol.py",
            "toolloop_fix/model_backend.py",
            "toolloop_fix/model_run.py",
            "toolloop_fix/calibrated_protocol.py",
            "toolloop_fix/baselines.py",
            "toolloop_fix/calibrated_run.py",
            "toolloop_fix/prefix_run.py",
            "toolloop_fix/iter7_runners.py",
        ):
            dst = self.ws / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, dst)
        write_rows(self.ws / "data/dev/fixed_prefix_cases_iter4.jsonl", self.prefix_rows)
        write_rows(self.ws / "data/dev/grouped_dev_suite_iter7.jsonl", self.e2e_cases)
        shutil.copyfile(ROOT / "data/dev/pilot_cases.jsonl", self.ws / "data/dev/pilot_cases.jsonl")

    def base_sampling(self, do_sample=True, presence_penalty=2.0, top_k=20, temperature=1.0):
        return {
            "enable_thinking": False,
            "do_sample": do_sample,
            "temperature": temperature,
            "top_p": 1.0,
            "top_k": top_k,
            "min_p": 0.0,
            "presence_penalty": presence_penalty,
            "repetition_penalty": 1.0,
            "max_new_tokens": 128,
            "seed": 0,
        }

    def external_inputs(self):
        return [
            {
                "name": "model_manifest",
                "path": str(self.model_manifest_path.resolve()),
                "sha256": self.model_manifest_sha,
            }
        ]

    def seal_run(self, run_id, subcommand, config, responder=smart_policy_responder, backend_instances=None):
        run_dir = self.base / "experiments" / run_id
        inputs_dir = run_dir / "inputs"
        shutil.copytree(self.ws, inputs_dir)
        cfg_rel = "configs/{}.json".format(run_id.lower())
        write_json(inputs_dir / cfg_rel, config)
        shutil.copyfile(inputs_dir / cfg_rel, run_dir / "config.json")
        ext_target = run_dir / "external_inputs/model_manifest.json"
        ext_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.model_manifest_path, ext_target)

        def factory(cfg, tools):
            inst = DeterministicTestBackend(cfg, tools, responder=responder)
            if backend_instances is not None:
                backend_instances.append(inst)
            return inst

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            iter7_main(
                [subcommand, "--config", str(run_dir / "config.json"), "--output", str(run_dir)],
                backend_factory=factory,
                root=inputs_dir,
            )
        (run_dir / "stdout.log").write_text(buf.getvalue())
        (run_dir / "stderr.log").write_text("")
        manifest = {
            "schema_version": 3,
            "run_id": run_id,
            "kind": "model_inference",
            "owner": "runner7",
            "status": "completed",
            "hypothesis": "unit test",
            "acceptance_criteria": "unit test",
            "started_at_utc": "2026-11-19T09:00:00+00:00",
            "ended_at_utc": "2026-11-19T09:00:01+00:00",
            "cwd": str(inputs_dir.resolve()),
            "execution_mode": "frozen_snapshot",
            "artifact_contract": "model_inference",
            "config_input_path": cfg_rel,
            "config_sha256": sha256_file(run_dir / "config.json"),
            "command": [sys.executable, "-m", "toolloop_fix.iter7_runners", subcommand],
            "child_executable": sys.executable,
            "environment": {"python": sys.version, "executable": sys.executable},
            "exit_code": 0,
            "evidence_valid": True,
            "inputs_changed_during_run": [],
            "external_inputs_changed_during_run": [],
            "contract_errors": [],
            "external_inputs": [
                {**ref, "snapshot_path": "external_inputs/{}.json".format(ref["name"])}
                for ref in config["external_inputs"]
            ],
            "inputs": {},
            "outputs": {},
        }
        return self.refresh_manifest(run_dir, manifest)

    def refresh_manifest(self, run_dir, manifest=None):
        if manifest is None:
            manifest = json.loads((run_dir / "manifest.json").read_text())
        inputs_dir = run_dir / "inputs"
        manifest["config_sha256"] = sha256_file(run_dir / "config.json")
        manifest["inputs"] = {
            str(p.relative_to(inputs_dir)): sha256_file(p)
            for p in sorted(inputs_dir.rglob("*"))
            if p.is_file()
        }
        manifest["outputs"] = {
            str(p.relative_to(run_dir)): sha256_file(p)
            for p in sorted(run_dir.rglob("*"))
            if p.is_file() and p.relative_to(run_dir).parts[0] != "inputs" and p.name != "manifest.json"
        }
        write_json(run_dir / "manifest.json", manifest)
        return {"path": str(run_dir.resolve()), "manifest_sha256": sha256_file(run_dir / "manifest.json")}


class Iter7RunnersUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="iter7-runners-test-")
        self.addCleanup(self.temp.cleanup)
        self.harness = Iter7WorkspaceHarness(self.temp.name)
        self.prefix_ids = [r["case_id"] for r in self.harness.prefix_rows]
        self.e2e_ids = [c["case_id"] for c in self.harness.e2e_cases]

    def make_prefix_decoding_config(self, arm="B0", do_sample=False, presence_penalty=0.0, regime=None):
        cfg = {
            "schema_version": 1,
            "evidence_class": "model_inference",
            "evidence_kind": "model_inference",
            "policy_name": "qwen_iter7_decoding_" + arm,
            "mode": "fixed_failure_prefix",
            "cases_path": "data/dev/fixed_prefix_cases_iter4.jsonl",
            "case_ids": list(self.prefix_ids),
            "case_seeds": {cid: 200 + idx * 32 for idx, cid in enumerate(self.prefix_ids)},
            "tool_budget": 12,
            "turn_budget": 16,
            "max_context_tokens": 8192,
            "model": copy.deepcopy(self.harness.model_spec),
            "sampling": self.harness.base_sampling(
                do_sample=do_sample,
                presence_penalty=presence_penalty,
                top_k=0 if not do_sample else 20,
                temperature=1.0,
            ),
            "external_inputs": self.harness.external_inputs(),
            "comparison_arm": arm,
            "protocol_version": CALIBRATED_PROTOCOL_VERSION,
            "wording_profile": "explicit",
            "task_wording_version": TASK_WORDING_VERSION,
            "prefix_schema_version": PREFIX_SCHEMA_VERSION,
        }
        if regime is not None:
            cfg["decoding_regime"] = regime
        return cfg

    def make_prefix_multiseed_config(self, arm="B0", schedule_ids=("s1", "s2", "s3", "s4"), base_offset=2000):
        schedules = []
        for s_idx, sid in enumerate(schedule_ids):
            schedules.append(
                {
                    "schedule_id": sid,
                    "case_seeds": {
                        cid: base_offset + s_idx * 256 + c_idx * 32
                        for c_idx, cid in enumerate(self.prefix_ids)
                    },
                }
            )
        return {
            "schema_version": 1,
            "evidence_class": "model_inference",
            "evidence_kind": "model_inference",
            "policy_name": "qwen_iter7_multiseed_" + arm,
            "mode": "fixed_failure_prefix",
            "cases_path": "data/dev/fixed_prefix_cases_iter4.jsonl",
            "case_ids": list(self.prefix_ids),
            "seed_schedules": schedules,
            "tool_budget": 12,
            "turn_budget": 16,
            "max_context_tokens": 8192,
            "model": copy.deepcopy(self.harness.model_spec),
            "sampling": self.harness.base_sampling(do_sample=True, presence_penalty=2.0, top_k=20),
            "external_inputs": self.harness.external_inputs(),
            "comparison_arm": arm,
            "protocol_version": CALIBRATED_PROTOCOL_VERSION,
            "wording_profile": "explicit",
            "task_wording_version": TASK_WORDING_VERSION,
            "prefix_schema_version": PREFIX_SCHEMA_VERSION,
        }

    def make_explicit_e2e_config(self, arm="B0"):
        return {
            "schema_version": 1,
            "evidence_class": "model_inference",
            "evidence_kind": "model_inference",
            "policy_name": "qwen_iter7_e2e_" + arm,
            "mode": "end_to_end",
            "cases_path": "data/dev/grouped_dev_suite_iter7.jsonl",
            "case_ids": list(self.e2e_ids),
            "case_seeds": {cid: 5000 + idx * 32 for idx, cid in enumerate(self.e2e_ids)},
            "tool_budget": 12,
            "turn_budget": 16,
            "max_context_tokens": 8192,
            "model": copy.deepcopy(self.harness.model_spec),
            "sampling": self.harness.base_sampling(do_sample=True, presence_penalty=2.0, top_k=20),
            "external_inputs": self.harness.external_inputs(),
            "comparison_arm": arm,
            "protocol_version": CALIBRATED_PROTOCOL_VERSION,
            "wording_profile": "explicit",
            "task_wording_version": TASK_WORDING_VERSION,
            "notes": "ITER-007 unseen-template E2E arm " + arm,
        }

    def test_prefix_decoding_supports_greedy_and_sampled_and_validates_records(self):
        for regime, do_sample, pp in (
            ("greedy_pp0", False, 0.0),
            ("greedy_pp2", False, 2.0),
            ("sampled_pp2", True, 2.0),
        ):
            with self.subTest(regime=regime):
                cfg = self.make_prefix_decoding_config(do_sample=do_sample, presence_penalty=pp, regime=regime)
                if not do_sample:
                    cfg["sampling"]["temperature"] = 0.0
                self.assertEqual(validate_records.config_errors(cfg, "model_inference"), [])
                ref = self.harness.seal_run("EXP-DEC-" + regime, "prefix-decoding", cfg)
                run_dir = Path(ref["path"])
                manifest = json.loads((run_dir / "manifest.json").read_text())
                self.assertEqual(validate_records.model_artifact_errors(run_dir, cfg, manifest), [])
                metrics = json.loads((run_dir / "metrics.json").read_text())
                prov = json.loads((run_dir / "model_provenance.json").read_text())
                self.assertEqual(metrics["decoding_regime"], regime)
                self.assertEqual(prov["decoding_regime"], regime)
                self.assertEqual(prov["runner_subcommand"], "prefix-decoding")

    def test_prefix_decoding_validation_rejects_bad_configs(self):
        bad_thinking = self.make_prefix_decoding_config()
        bad_thinking["sampling"]["enable_thinking"] = True
        with self.assertRaisesRegex(ValueError, "enable_thinking"):
            validate_iter7_prefix_config(bad_thinking)

        bad_bool = self.make_prefix_decoding_config()
        bad_bool["sampling"]["do_sample"] = 0
        with self.assertRaisesRegex(ValueError, "boolean"):
            validate_iter7_prefix_config(bad_bool)

        bad_regime = self.make_prefix_decoding_config(do_sample=False, presence_penalty=2.0, regime="greedy_pp0")
        with self.assertRaisesRegex(ValueError, "greedy_pp0"):
            validate_iter7_prefix_config(bad_regime)

        overlap = self.make_prefix_decoding_config()
        overlap["case_seeds"][self.prefix_ids[1]] = overlap["case_seeds"][self.prefix_ids[0]] + 15
        with self.assertRaisesRegex(ValueError, "disjoint"):
            validate_iter7_prefix_config(overlap)

    def test_real_workspace_authentic_six_prefixes_pass_prepare_with_greedy_configs(self):
        real_rows = [
            json.loads(line)
            for line in (ROOT / "data/dev/fixed_prefix_cases_iter4.jsonl").read_text().splitlines()
            if line.strip()
        ]
        self.assertEqual(len(real_rows), 6)
        for pp, regime in ((0.0, "greedy_pp0"), (2.0, "greedy_pp2")):
            cfg = self.make_prefix_decoding_config(do_sample=False, presence_penalty=pp, regime=regime)
            for row in real_rows:
                prepared = prepare_iter7_prefix(row, cfg)
                self.assertEqual(prepared["context"]["prefix_snapshot_sha256"], state_hash(row["prefix_snapshot"]))

    def test_prefix_multiseed_single_model_load_tags_and_breakdowns(self):
        cfg = self.make_prefix_multiseed_config(arm="B2")
        self.assertEqual(validate_records.config_errors(cfg, "model_inference"), [])
        backends = []
        ref = self.harness.seal_run("EXP-MS-B2", "prefix-multiseed", cfg, backend_instances=backends)
        self.assertEqual(len(backends), 1, "prefix-multiseed must load the backend exactly once")
        run_dir = Path(ref["path"])
        manifest = json.loads((run_dir / "manifest.json").read_text())
        self.assertEqual(validate_records.model_artifact_errors(run_dir, cfg, manifest), [])

        results = [json.loads(l) for l in (run_dir / "case_results.jsonl").read_text().splitlines()]
        traces = [json.loads(l) for l in (run_dir / "trajectories.jsonl").read_text().splitlines()]
        contexts = [json.loads(l) for l in (run_dir / "prefix_contexts.jsonl").read_text().splitlines()]
        gens = [json.loads(l) for l in (run_dir / "generations.jsonl").read_text().splitlines()]
        events = [json.loads(l) for l in (run_dir / "execution_events.jsonl").read_text().splitlines()]
        self.assertEqual(len(results), 24)
        self.assertEqual(len(traces), 24)
        self.assertEqual(len(contexts), 24)

        for collection in (results, traces, contexts, gens, events):
            for item in collection:
                self.assertIn("@", item["case_id"])
                self.assertEqual(item["case_id"], item["trial_id"])
                self.assertEqual(item["trial_id"], "{}@{}".format(item["base_prefix_id"], item["schedule_id"]))
                self.assertIn(item["schedule_id"], {"s1", "s2", "s3", "s4"})
                self.assertIn(item["base_prefix_id"], set(self.prefix_ids))
                self.assertTrue(item["original_case_id"])

        metrics = json.loads((run_dir / "metrics.json").read_text())
        self.assertEqual(metrics["schedule_ids"], ["s1", "s2", "s3", "s4"])
        self.assertEqual(set(metrics["by_schedule"]), {"s1", "s2", "s3", "s4"})
        self.assertEqual(set(metrics["by_prefix"]), set(self.prefix_ids))
        self.assertEqual(metrics["metrics"]["new_goal_evidence_recovery"]["denominator"], 16)

    def test_prefix_multiseed_rejects_cross_schedule_seed_overlap_and_greedy(self):
        overlap_cfg = self.make_prefix_multiseed_config()
        overlap_cfg["seed_schedules"][2]["case_seeds"][self.prefix_ids[3]] = (
            overlap_cfg["seed_schedules"][0]["case_seeds"][self.prefix_ids[1]] + 8
        )
        with self.assertRaisesRegex(ValueError, "disjoint"):
            validate_prefix_multiseed_config(overlap_cfg)

        dup_sched = self.make_prefix_multiseed_config(schedule_ids=("s1", "s1"))
        with self.assertRaisesRegex(ValueError, "duplicate schedule_id"):
            validate_prefix_multiseed_config(dup_sched)

        greedy_ms = self.make_prefix_multiseed_config()
        greedy_ms["sampling"]["do_sample"] = False
        with self.assertRaisesRegex(ValueError, "do_sample=true"):
            validate_prefix_multiseed_config(greedy_ms)

    def test_explicit_e2e_wording_and_slice_breakdowns(self):
        cfg = self.make_explicit_e2e_config(arm="B1")
        self.assertEqual(validate_records.config_errors(cfg, "model_inference"), [])
        ref = self.harness.seal_run("EXP-E2E-B1", "explicit-e2e", cfg)
        run_dir = Path(ref["path"])
        manifest = json.loads((run_dir / "manifest.json").read_text())
        self.assertEqual(validate_records.model_artifact_errors(run_dir, cfg, manifest), [])

        gens = [json.loads(l) for l in (run_dir / "generations.jsonl").read_text().splitlines()]
        first_gen = gens[0]
        self.assertTrue(first_gen["messages"][1]["content"].startswith(EXPLICIT_TASK_PREFIX))
        self.assertTrue(first_gen["messages"][0]["content"].startswith(CALIBRATED_SYSTEM_PROMPT))
        self.assertTrue(first_gen["messages"][0]["content"].endswith(prompt_for("B1")))

        metrics = json.loads((run_dir / "metrics.json").read_text())
        self.assertEqual(set(metrics["by_category"]), set(CATEGORIES))
        self.assertEqual(set(metrics["by_domain"]), set(DOMAINS))
        required_slice_metrics = {
            "tool_goal_attainment",
            "exact_grounded_structured_completion",
            "has_invalid_loop",
            "premature_stop_without_goal_evidence",
            "stopped_after_public_unavailable",
            "exact_stop_reason_code",
            "natural_termination_semantics_unknown",
        }
        for cat in CATEGORIES:
            self.assertTrue(required_slice_metrics.issubset(metrics["by_category"][cat]))
        for dom in DOMAINS:
            self.assertTrue(required_slice_metrics.issubset(metrics["by_domain"][dom]))
        self.assertIsNone(metrics["by_category"]["unrecoverable"]["tool_goal_attainment"]["value"])
        self.assertIsNone(metrics["by_category"]["normal"]["stopped_after_public_unavailable"]["value"])

        legacy_cfg = self.make_explicit_e2e_config()
        legacy_cfg["wording_profile"] = "legacy"
        with self.assertRaisesRegex(ValueError, "explicit"):
            validate_explicit_e2e_config(legacy_cfg)

    def test_failure_retention_and_overwrite_protection(self):
        cfg = self.make_explicit_e2e_config(arm="B0")
        out_dir = Path(self.temp.name) / "fail_run"
        cfg_path = Path(self.temp.name) / "fail_cfg.json"
        write_json(cfg_path, cfg)

        def boom_factory(c, t):
            return DeterministicTestBackend(c, t, responder=lambda *_: RuntimeError("simulated backend crash"))

        with self.assertRaisesRegex(RuntimeError, "simulated backend crash"):
            iter7_main(
                ["explicit-e2e", "--config", str(cfg_path), "--output", str(out_dir)],
                backend_factory=boom_factory,
                root=self.harness.ws,
            )
        failure = json.loads((out_dir / "model_failure.json").read_text())
        self.assertEqual(failure["error_type"], "RuntimeError")
        with self.assertRaises(FileExistsError):
            iter7_main(
                ["explicit-e2e", "--config", str(cfg_path), "--output", str(out_dir)],
                backend_factory=boom_factory,
                root=self.harness.ws,
            )

    def test_stdlib_import_without_ml_packages(self):
        import subprocess

        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, toolloop_fix.iter7_runners, scripts.compare_iter7_runs, scripts.audit_iter7_runs; "
                "assert 'torch' not in sys.modules and 'transformers' not in sys.modules",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_prefix_multiseed_environment_branch_isolation_across_schedules(self):
        # In s1, retry consumes transient_remaining (1 -> 0 -> OK). In s2, stopping immediately must NOT see OK!
        cfg = self.make_prefix_multiseed_config(arm="B0", schedule_ids=("s1", "s2"))

        def schedule_specific_responder(messages, tools, seed, _cfg):
            tool_msgs = [m for m in messages if m["role"] == "tool"]
            last = json.loads(tool_msgs[-1]["content"])
            if last.get("code") == "OK":
                return xml("finish_task", answer=last["value"], identifier=last["identifier"])
            # s1 seeds are in [2000, 2256); s2 seeds are in [2256, 2512)
            if seed < 2256 and last.get("code") == "TIMEOUT":
                req = json.loads(messages[1]["content"].split("Request: ", 1)[-1])
                primary = DOMAIN_TOOLS[req["domain"]][0]
                return xml(primary, identifier=req["identifier"])
            return "No retry on s2.<|im_end|>"

        ref = self.harness.seal_run("EXP-MS-ISOLATION", "prefix-multiseed", cfg, responder=schedule_specific_responder)
        results = {
            r["trial_id"]: r
            for r in (json.loads(l) for l in (Path(ref["path"]) / "case_results.jsonl").read_text().splitlines())
        }
        self.assertTrue(results["pfx-orders-timeout@s1"]["new_goal_evidence"])
        self.assertFalse(results["pfx-orders-timeout@s2"]["new_goal_evidence"])
        self.assertEqual(
            results["pfx-orders-timeout@s1"]["prefix_snapshot_sha256"],
            results["pfx-orders-timeout@s2"]["prefix_snapshot_sha256"],
        )

    def test_explicit_e2e_matches_calibrated_run_guard_and_scoring_semantics(self):
        from toolloop_fix.calibrated_run import run_calibrated_case
        from toolloop_fix.iter7_runners import run_explicit_e2e_case

        case = copy.deepcopy(next(c for c in self.harness.e2e_cases if c["category"] == "recoverable"))
        raw_sequence = [
            xml(DOMAIN_TOOLS[case["domain"]][0], identifier=case["request"]["identifier"]),
            xml(DOMAIN_TOOLS[case["domain"]][0], identifier=case["request"]["identifier"]),
            xml(DOMAIN_TOOLS[case["domain"]][1], target=case["request"]["target"]),
            xml(DOMAIN_TOOLS[case["domain"]][0], identifier=case["hidden"]["valid_identifier"]),
            xml("finish_task", answer=case["hidden"]["value"], identifier=case["hidden"]["valid_identifier"]),
        ]
        for arm in ("B0", "B1", "B2"):
            cfg = self.make_explicit_e2e_config(arm=arm)
            seq_a, seq_b = iter(raw_sequence), iter(raw_sequence)
            b_exp = DeterministicTestBackend(cfg, [], responder=lambda *_: next(seq_a))
            b_cal = DeterministicTestBackend(cfg, [], responder=lambda *_: next(seq_b))
            res_exp, _ = run_explicit_e2e_case(case, b_exp, cfg)
            res_cal, _ = run_calibrated_case(case, b_cal, cfg)
            for key in (
                "tool_goal_attainment",
                "exact_grounded_structured_completion",
                "guard_blocked_proposals",
                "proposed_repeat_count",
                "turns",
                "actual_tool_attempts",
                "has_invalid_loop",
            ):
                self.assertEqual(res_exp[key], res_cal[key], "mismatch on {} for {}".format(key, arm))
            self.assertEqual(b_exp.calls[0]["messages"][0], b_cal.calls[0]["messages"][0])
            self.assertNotEqual(b_exp.calls[0]["messages"][1], b_cal.calls[0]["messages"][1])

    def test_grouped_dev_suite_iter7_compatibility_when_present(self):
        suite_path = ROOT / "data/dev/grouped_dev_suite_iter7.jsonl"
        if not suite_path.is_file():
            self.skipTest("grouped_dev_suite_iter7.jsonl not yet generated")
        rows = [json.loads(l) for l in suite_path.read_text().splitlines() if l.strip()]
        selected = rows[:6]
        shutil.copyfile(suite_path, self.harness.ws / "data/dev/grouped_dev_suite_iter7.jsonl")
        cfg = self.make_explicit_e2e_config(arm="B0")
        cfg["case_ids"] = [r["case_id"] for r in selected]
        cfg["case_seeds"] = {r["case_id"]: 8000 + i * 32 for i, r in enumerate(selected)}
        ref = self.harness.seal_run("EXP-GDEV7-CHECK", "explicit-e2e", cfg)
        audited = audit_iter7_runs.audit_run(ref)
        self.assertTrue(audited["summary"]["all_cases_replayed"])


class Iter7CompareAndAuditIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="iter7-full-suite-")
        self.addCleanup(self.temp.cleanup)
        self.harness = Iter7WorkspaceHarness(self.temp.name)
        self.prefix_ids = [r["case_id"] for r in self.harness.prefix_rows]
        self.e2e_ids = [c["case_id"] for c in self.harness.e2e_cases]

        # Build complete 11-run ITER-007 fixture:
        # 3 s0 prefix runs + 3 s1..s4 multiseed prefix runs + 2 greedy decoding runs + 3 unseen E2E runs
        self.s0_refs = {}
        self.ms_refs = {}
        self.dec_refs = {}
        self.e2e_refs = {}

        for arm in ("B0", "B1", "B2"):
            s0_cfg = {
                "schema_version": 1,
                "evidence_class": "model_inference",
                "evidence_kind": "model_inference",
                "policy_name": "qwen_s0_" + arm,
                "mode": "fixed_failure_prefix",
                "cases_path": "data/dev/fixed_prefix_cases_iter4.jsonl",
                "case_ids": list(self.prefix_ids),
                "case_seeds": {cid: 1000 + idx * 32 for idx, cid in enumerate(self.prefix_ids)},
                "tool_budget": 12,
                "turn_budget": 16,
                "max_context_tokens": 8192,
                "model": copy.deepcopy(self.harness.model_spec),
                "sampling": self.harness.base_sampling(do_sample=True, presence_penalty=2.0, top_k=20),
                "external_inputs": self.harness.external_inputs(),
                "comparison_arm": arm,
                "protocol_version": CALIBRATED_PROTOCOL_VERSION,
                "wording_profile": "explicit",
                "task_wording_version": TASK_WORDING_VERSION,
                "prefix_schema_version": PREFIX_SCHEMA_VERSION,
            }
            self.s0_refs[arm] = self.harness.seal_run("EXP-S0-" + arm, "prefix-decoding", s0_cfg)

            ms_schedules = [
                {
                    "schedule_id": sid,
                    "case_seeds": {
                        cid: 2000 + s_idx * 256 + c_idx * 32
                        for c_idx, cid in enumerate(self.prefix_ids)
                    },
                }
                for s_idx, sid in enumerate(("s1", "s2", "s3", "s4"))
            ]
            ms_cfg = {
                **{k: copy.deepcopy(v) for k, v in s0_cfg.items() if k != "case_seeds"},
                "policy_name": "qwen_ms_" + arm,
                "seed_schedules": ms_schedules,
            }
            self.ms_refs[arm] = self.harness.seal_run("EXP-MS-" + arm, "prefix-multiseed", ms_cfg)

            e2e_cfg = {
                "schema_version": 1,
                "evidence_class": "model_inference",
                "evidence_kind": "model_inference",
                "policy_name": "qwen_e2e_" + arm,
                "mode": "end_to_end",
                "cases_path": "data/dev/grouped_dev_suite_iter7.jsonl",
                "case_ids": list(self.e2e_ids),
                "case_seeds": {cid: 6000 + idx * 32 for idx, cid in enumerate(self.e2e_ids)},
                "tool_budget": 12,
                "turn_budget": 16,
                "max_context_tokens": 8192,
                "model": copy.deepcopy(self.harness.model_spec),
                "sampling": self.harness.base_sampling(do_sample=True, presence_penalty=2.0, top_k=20),
                "external_inputs": self.harness.external_inputs(),
                "comparison_arm": arm,
                "protocol_version": CALIBRATED_PROTOCOL_VERSION,
                "wording_profile": "explicit",
                "task_wording_version": TASK_WORDING_VERSION,
                "notes": "E2E arm " + arm,
            }
            self.e2e_refs[arm] = self.harness.seal_run("EXP-E2E-" + arm, "explicit-e2e", e2e_cfg)

        for regime, pp in (("greedy_pp0", 0.0), ("greedy_pp2", 2.0)):
            dec_cfg = {
                "schema_version": 1,
                "evidence_class": "model_inference",
                "evidence_kind": "model_inference",
                "policy_name": "qwen_dec_" + regime,
                "mode": "fixed_failure_prefix",
                "cases_path": "data/dev/fixed_prefix_cases_iter4.jsonl",
                "case_ids": list(self.prefix_ids),
                "case_seeds": {cid: 500 + idx * 32 for idx, cid in enumerate(self.prefix_ids)},
                "tool_budget": 12,
                "turn_budget": 16,
                "max_context_tokens": 8192,
                "model": copy.deepcopy(self.harness.model_spec),
                "sampling": self.harness.base_sampling(do_sample=False, presence_penalty=pp, top_k=0),
                "external_inputs": self.harness.external_inputs(),
                "comparison_arm": "B0",
                "protocol_version": CALIBRATED_PROTOCOL_VERSION,
                "wording_profile": "explicit",
                "task_wording_version": TASK_WORDING_VERSION,
                "prefix_schema_version": PREFIX_SCHEMA_VERSION,
                "decoding_regime": regime,
            }
            self.dec_refs[regime] = self.harness.seal_run("EXP-DEC-" + regime, "prefix-decoding", dec_cfg)

    def full_comparison_config(self):
        return {
            "prefix_s0_runs": [self.s0_refs[arm] for arm in ("B0", "B1", "B2")],
            "prefix_multiseed_runs": [self.ms_refs[arm] for arm in ("B0", "B1", "B2")],
            "prefix_decoding_runs": [self.dec_refs["greedy_pp0"], self.dec_refs["greedy_pp2"]],
            "e2e_runs": [self.e2e_refs[arm] for arm in ("B0", "B1", "B2")],
        }

    def test_compare_iter7_cli_and_statistical_outputs(self):
        cfg = self.full_comparison_config()
        cfg_path = Path(self.temp.name) / "compare_cfg.json"
        out_dir = Path(self.temp.name) / "compare_out"
        write_json(cfg_path, cfg)
        rc = compare_iter7_runs.main(["--config", str(cfg_path), "--output", str(out_dir)])
        self.assertEqual(rc, 0)
        self.assertTrue((out_dir / "iter7_comparison.json").is_file())
        self.assertTrue((out_dir / "iter7_comparison.md").is_file())

        report = json.loads((out_dir / "iter7_comparison.json").read_text())
        md_text = (out_dir / "iter7_comparison.md").read_text()
        self.assertFalse(report["training_eligible"])
        self.assertTrue(all(report["checks"].values()))

        # Decoding disentanglement checks
        dec = report["decoding_disentanglement"]
        self.assertEqual(
            dec["regimes"]["greedy_pp0"]["timeout_first_turn"]["pfx-orders-timeout"]["same_call_executed"],
            False,
        )
        self.assertEqual(
            dec["regimes"]["greedy_pp2"]["timeout_first_turn"]["pfx-orders-timeout"]["same_call_executed"],
            True,
        )
        self.assertEqual(
            dec["timeout_first_turn_disentanglement"]["pfx-orders-timeout"]["sampled_b0_first_turn_retry_count"]["denominator"],
            5,
        )

        # 5-seed prefix recovery checks (5 seeds x 6 prefixes x 3 arms = 90 episodes)
        pfx = report["prefix_5seed_recovery"]
        self.assertEqual(pfx["schedule_ids"], ["s0", "s1", "s2", "s3", "s4"])
        for arm in ("B0", "B1", "B2"):
            self.assertEqual(pfx["arm_summaries"][arm]["total_episodes"], 30)
            self.assertEqual(pfx["arm_summaries"][arm]["recovery_trials"], 20)
        self.assertGreater(pfx["arm_summaries"]["B2"]["guard_blocked_proposals"], 0)
        self.assertEqual(pfx["arm_summaries"]["B0"]["guard_blocked_proposals"], 0)
        for arm in ("B1", "B2"):
            wtl = pfx["paired_against_B0"][arm]["new_goal_evidence"]["trial_win_tie_loss_20"]
            self.assertEqual(wtl["wins"]["count"] + wtl["ties"]["count"] + wtl["losses"]["count"], 20)
            self.assertEqual(
                pfx["paired_against_B0"][arm]["new_goal_evidence"]["clustered_by_4_recovery_prefixes"]["n"],
                4,
            )
            self.assertEqual(
                pfx["paired_against_B0"][arm]["new_goal_evidence"]["paired_across_5_seed_schedules"]["n"],
                5,
            )
        self.assertIn("n=4", pfx["diagnostic_scope_note"])
        self.assertIn("n=4", md_text)

        # Unseen E2E + fault_subtype + B0/B2 pre-intervention match checks
        e2e = report["unseen_template_e2e"]
        self.assertEqual(e2e["case_count"], 15)
        self.assertEqual(e2e["completable_count"], 12)
        self.assertIn("job_poll_precondition_2", e2e["fault_subtypes"])
        self.assertTrue(e2e["b0_vs_b2_pre_intervention_check"]["all_pre_intervention_generations_identical"])
        self.assertGreater(len(e2e["b0_vs_b2_pre_intervention_check"]["b2_intervened_episodes"]), 0)

        # Same-snapshot public-only validate_alternative checks (including 2-poll RUNNING progress!)
        alt = report["same_snapshot_public_alternatives"]
        self.assertFalse(alt["training_eligible"])
        for section_name in ("unseen_e2e", "prefix_5seed"):
            sec = alt[section_name]
            self.assertFalse(sec["training_eligible"])
            self.assertGreater(sec["failed_completable_episode_count"], 0)
            self.assertTrue(sec["all_public_arguments_only"])
            self.assertEqual(
                sec["at_failure_point_valid_public_recovery_step"]["numerator"],
                sec["failed_completable_episode_count"],
            )
            self.assertGreater(sec["at_failure_point_legal_retry_or_poll_progress"]["numerator"], 0)
            self.assertTrue(all(ep["training_eligible"] is False for ep in sec["episodes"]))

        with self.assertRaises(FileExistsError):
            compare_iter7_runs.main(["--config", str(cfg_path), "--output", str(out_dir)])

    def test_audit_iter7_replays_all_11_runs_and_real_s0_archives(self):
        cfg = self.full_comparison_config()
        cfg_path = Path(self.temp.name) / "audit_cfg.json"
        out_dir = Path(self.temp.name) / "audit_out"
        write_json(cfg_path, cfg)
        rc = audit_iter7_runs.main(["--config", str(cfg_path), "--output", str(out_dir)])
        self.assertEqual(rc, 0)
        audit_report = json.loads((out_dir / "iter7_audit.json").read_text())
        self.assertTrue(audit_report["accepted"])
        self.assertFalse(audit_report["ml_libraries_loaded"])
        self.assertEqual(audit_report["run_count"], 11)
        # 3*6 (s0) + 3*24 (multiseed) + 2*6 (decoding) + 3*15 (e2e) = 18 + 72 + 12 + 45 = 147 episodes!
        self.assertEqual(audit_report["episodes_replayed"], 147)
        self.assertEqual(len(audit_report["b0_b2_pre_intervention_checks"]), 3)

        # Also verify real historical EXP-028/029/030 archives pass audit_iter7_runs on CPU!
        archive_root = ROOT
        if not (archive_root / "experiments").is_dir():
            parent_manifest = ROOT.parent / "manifest.json"
            if parent_manifest.is_file():
                src = json.loads(parent_manifest.read_text()).get("source_workspace")
                if src and (Path(src) / "experiments").is_dir():
                    archive_root = Path(src)
        real_s0_refs = [
            {
                "path": str((archive_root / "experiments" / run_id).resolve()),
                "manifest_sha256": sha256_file(archive_root / "experiments" / run_id / "manifest.json"),
            }
            for run_id in ("EXP-20261116-028", "EXP-20261116-029", "EXP-20261116-030")
        ]
        real_audit = audit_iter7_runs.audit_runs({"run_dirs": real_s0_refs})
        self.assertTrue(real_audit["accepted"])
        self.assertEqual(real_audit["episodes_replayed"], 18)

    def test_audit_rejects_prompt_leakage_and_rehashed_tampering(self):
        # 1. Manifest pin mismatch
        bad_pin = {**self.e2e_refs["B0"], "manifest_sha256": "0" * 64}
        with self.assertRaisesRegex(ValueError, "pin mismatch"):
            audit_iter7_runs.audit_run(bad_pin)

        # Helper to clone a run, mutate a JSONL/JSON file, and reseal manifest hashes
        def clone_and_mutate(ref, name_suffix, filename, mutator):
            src = Path(ref["path"])
            dst = Path(self.temp.name) / "tampered" / name_suffix
            shutil.copytree(src, dst)
            target = dst / filename
            if filename.endswith(".jsonl"):
                rows = [json.loads(l) for l in target.read_text().splitlines() if l.strip()]
                mutator(rows)
                write_rows(target, rows)
            else:
                obj = json.loads(target.read_text())
                mutator(obj)
                write_json(target, obj)
            manifest = json.loads((dst / "manifest.json").read_text())
            manifest["run_id"] = dst.name
            return self.harness.refresh_manifest(dst, manifest)

        # 2. Evaluation label leaked into prompt_text (even when prompt_sha256 & manifest are resealed!)
        def leak_label(rows):
            leaked = rows[0]["prompt_text"] + ' {"fault_subtype": "secret"}'
            rows[0]["prompt_text"] = leaked
            rows[0]["prompt_sha256"] = hashlib.sha256(leaked.encode()).hexdigest()

        leaked_label_ref = clone_and_mutate(self.e2e_refs["B0"], "leak_label", "generations.jsonl", leak_label)
        with self.assertRaisesRegex(ValueError, "evaluation label leaked"):
            audit_iter7_runs.audit_run(leaked_label_ref)

        # 3. Unseen hidden value leaked into prompt_text
        def leak_hidden_value(rows):
            leaked = rows[0]["prompt_text"] + " shipped"
            rows[0]["prompt_text"] = leaked
            rows[0]["prompt_sha256"] = hashlib.sha256(leaked.encode()).hexdigest()

        leaked_val_ref = clone_and_mutate(self.e2e_refs["B0"], "leak_val", "generations.jsonl", leak_hidden_value)
        with self.assertRaisesRegex(ValueError, "hidden value leaked"):
            audit_iter7_runs.audit_run(leaked_val_ref)

        # 4. Trial ID leaked into multiseed prompt_text
        def leak_trial_id(rows):
            leaked = rows[0]["prompt_text"] + " " + rows[0]["trial_id"]
            rows[0]["prompt_text"] = leaked
            rows[0]["prompt_sha256"] = hashlib.sha256(leaked.encode()).hexdigest()

        leaked_tid_ref = clone_and_mutate(self.ms_refs["B0"], "leak_tid", "generations.jsonl", leak_trial_id)
        with self.assertRaisesRegex(ValueError, "identifier leaked"):
            audit_iter7_runs.audit_run(leaked_tid_ref)

        # 5. Tampered case_results.jsonl (falsified goal attainment, resealed manifest)
        falsified_res_ref = clone_and_mutate(
            self.ms_refs["B0"],
            "falsified_res",
            "case_results.jsonl",
            lambda rows: rows[1].update(new_goal_evidence=True, tool_goal_attainment=True),
        )
        with self.assertRaisesRegex(ValueError, "replay mismatch"):
            audit_iter7_runs.audit_run(falsified_res_ref)

        # 6. Tampered core harness.py in frozen inputs
        bad_harness_dir = Path(self.temp.name) / "tampered/bad_harness"
        shutil.copytree(Path(self.e2e_refs["B0"]["path"]), bad_harness_dir)
        (bad_harness_dir / "inputs/toolloop_fix/harness.py").write_text("# tampered\n")
        bad_harness_manifest = json.loads((bad_harness_dir / "manifest.json").read_text())
        bad_harness_manifest["run_id"] = "bad_harness"
        bad_harness_ref = self.harness.refresh_manifest(bad_harness_dir, bad_harness_manifest)
        with self.assertRaisesRegex(ValueError, "harness.py SHA mismatch"):
            audit_iter7_runs.audit_run(bad_harness_ref)

        # 7. B0 vs B2 pre-intervention generation divergence rejected in both audit and compare!
        b2_diverged_cfg = {
            **json.loads((Path(self.e2e_refs["B2"]["path"]) / "config.json").read_text()),
            "policy_name": "qwen_e2e_B2",
        }
        b2_diverged_ref = self.harness.seal_run(
            "EXP-E2E-B2-DIV",
            "explicit-e2e",
            b2_diverged_cfg,
            responder=lambda m, t, s, c: "Diverged on turn 1.<|im_end|>",
        )
        with self.assertRaisesRegex(ValueError, "B0/B2 pre-intervention generation mismatch"):
            audit_iter7_runs.audit_runs({"run_dirs": [self.e2e_refs["B0"], b2_diverged_ref]})

        bad_compare_cfg = self.full_comparison_config()
        bad_compare_cfg["e2e_runs"] = [self.e2e_refs["B0"], self.e2e_refs["B1"], b2_diverged_ref]
        with self.assertRaisesRegex(ValueError, "pre-intervention generation mismatch"):
            compare_iter7_runs.compare_iter7(bad_compare_cfg)

        # 8. CLI failure writes accepted=False and returns exit code 1
        bad_audit_cfg_path = Path(self.temp.name) / "bad_audit_cfg.json"
        bad_audit_out = Path(self.temp.name) / "bad_audit_out"
        write_json(bad_audit_cfg_path, {"run_dirs": [falsified_res_ref]})
        rc = audit_iter7_runs.main(["--config", str(bad_audit_cfg_path), "--output", str(bad_audit_out)])
        self.assertEqual(rc, 1)
        rejected = json.loads((bad_audit_out / "iter7_audit.json").read_text())
        self.assertFalse(rejected["accepted"])

    def test_compare_rejects_s0_multiseed_seed_overlap_and_pilot_reuse(self):
        # Overlapping s1 seed with s0 (1000..1176)
        overlapping_ms_refs = {}
        for arm in ("B0", "B1", "B2"):
            base_cfg = json.loads((Path(self.ms_refs[arm]["path"]) / "config.json").read_text())
            base_cfg["seed_schedules"][0]["case_seeds"][self.prefix_ids[0]] = 1005
            overlapping_ms_refs[arm] = self.harness.seal_run("EXP-MS-OVERLAP-" + arm, "prefix-multiseed", base_cfg)
        bad_cfg = self.full_comparison_config()
        bad_cfg["prefix_multiseed_runs"] = [overlapping_ms_refs[arm] for arm in ("B0", "B1", "B2")]
        with self.assertRaisesRegex(ValueError, "disjoint"):
            compare_iter7_runs.compare_iter7(bad_cfg)

        # Reusing pilot_cases.jsonl as unseen E2E suite must be rejected
        pilot_ids = [c["case_id"] for c in PILOT_CASES]
        pilot_e2e_refs = {}
        for arm in ("B0", "B1", "B2"):
            e2e_cfg = json.loads((Path(self.e2e_refs[arm]["path"]) / "config.json").read_text())
            e2e_cfg["cases_path"] = "data/dev/pilot_cases.jsonl"
            e2e_cfg["case_ids"] = pilot_ids
            e2e_cfg["case_seeds"] = {cid: 7000 + idx * 32 for idx, cid in enumerate(pilot_ids)}
            pilot_e2e_refs[arm] = self.harness.seal_run("EXP-E2E-PILOT-" + arm, "explicit-e2e", e2e_cfg)
        bad_pilot_cfg = self.full_comparison_config()
        bad_pilot_cfg["e2e_runs"] = [pilot_e2e_refs[arm] for arm in ("B0", "B1", "B2")]
        with self.assertRaisesRegex(ValueError, "distinct from the original 15 pilot_cases.jsonl"):
            compare_iter7_runs.compare_iter7(bad_pilot_cfg)

    def test_compound_fault_multi_stage_alternative_validation(self):
        # Test stale_id_locate_plus_1poll where model resolves LOCATED, hits PRECONDITION, then stops
        compound_case = {
            "case_id": "compound-jobs-rec-poll-01",
            "category": "recoverable",
            "domain": "jobs",
            "evidence_class": "synthetic_harness",
            "fault_subtype": "stale_id_locate_plus_1poll",
            "hidden": {
                "polls_remaining": 1,
                "transient_remaining": 0,
                "unavailable": False,
                "valid_identifier": "JOB-NEW-901",
                "value": "batch_done_901",
            },
            "request": {"domain": "jobs", "identifier": "JOB-STALE-001", "target": "lookup_key:job_901"},
            "schema_version": 1,
            "split": "dev",
            "template_group": "iter7-jobs-compound-test",
        }
        from toolloop_fix.iter7_runners import run_explicit_e2e_case

        cfg = json.loads((Path(self.e2e_refs["B0"]["path"]) / "config.json").read_text())
        cfg["case_ids"] = [compound_case["case_id"]]
        cfg["case_seeds"] = {compound_case["case_id"]: 9100}
        turns = iter(
            [
                xml("get_result", identifier="JOB-STALE-001"),
                xml("find_job", target="lookup_key:job_901"),
                xml("get_result", identifier="JOB-NEW-901"),
                "Stopping prematurely after precondition.<|im_end|>",
            ]
        )
        backend = DeterministicTestBackend(cfg, [], responder=lambda *_: next(turns))
        result, trajectory = run_explicit_e2e_case(compound_case, backend, cfg)
        self.assertFalse(result["tool_goal_attainment"])
        alt_check = compare_iter7_runs._check_episode_public_alternative(
            compound_case, trajectory, result, prefix_turns=0, label="B0:compound-jobs-rec-poll-01", arm="B0"
        )
        self.assertEqual(alt_check["primary_failure_point"], "active_public_error_snapshot")
        self.assertEqual(
            alt_check["at_failure_point"]["candidate"],
            {"type": "tool", "tool": "poll_job", "arguments": {"identifier": "JOB-NEW-901"}},
        )
        self.assertTrue(alt_check["at_failure_point"]["public_arguments_only"])
        self.assertTrue(alt_check["at_failure_point"]["locally_validated"])
        self.assertFalse(alt_check["training_eligible"])


if __name__ == "__main__":
    unittest.main()
