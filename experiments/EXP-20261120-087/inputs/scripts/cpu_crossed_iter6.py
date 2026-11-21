"""CPU only. 'generate' creates a one-step primary record; 'audit' never calls generate."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import time

def now():
    return datetime.now(timezone.utc).isoformat()

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def require(ok, message):
    if not ok:
        raise ValueError(message)

def write(path, data):
    with path.open("x") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

def pins(c):
    root = Path(c["source_run"])
    for name, sha in c["input_sha256"].items():
        require(digest(root / name) == sha, "source hash mismatch: " + name)
    return root

def generate(c, out):
    # No model parameter or batch selection depends on evaluator labels.
    from toolloop_fix.model_backend import TransformersBackend
    import torch
    from transformers import GenerationConfig
    public = (out / "public_payloads.jsonl").read_text().splitlines()
    require(len(public) == 12 and digest(out / "public_payloads.jsonl") == c["input_sha256"]["public_payloads.jsonl"], "source coverage")
    tools = json.loads(public[0])["tools"]
    torch.set_num_threads(4); torch.set_num_interop_threads(1)
    b = TransformersBackend(c, tools)
    require(c["model"]["device"] == "cpu" and all(str(p.device) == "cpu" and p.dtype == torch.float32 for p in b.model.parameters()), "actual CPU fp32 mismatch")
    tok = b.tokenizer
    tok.padding_side = "left"
    gen = GenerationConfig(do_sample=False, num_beams=1, max_new_tokens=96, use_cache=True,
        eos_token_id=b.eos_ids, pad_token_id=tok.pad_token_id, repetition_penalty=1.0)
    prov = b.provenance()
    prov.update(contract="single_proposal_no_dispatch", actual_threads=torch.get_num_threads(),
        interop_threads=torch.get_num_interop_threads(), all_parameters_cpu_fp32=True,
        batch_generation=gen.to_dict(), seed=0, enable_thinking=False, weights_updated=False,
        effective_generation_note="The explicit batch_generation config is authoritative; no logits processor or penalty.")
    write(out / "model_provenance.json", prov)
    with (out / "raw_generations.jsonl").open("x") as f:
        for offset in range(3):
            indexes = list(range(offset,12,3))
            inp = [json.loads(public[i]) for i in indexes]
            require(all(set(item)=={"messages","tools"} for item in inp),"nonpublic field in input")
            prompts = [tok.apply_chat_template(x["messages"], tools=x["tools"], tokenize=False, add_generation_prompt=True, enable_thinking=False) for x in inp]
            enc = tok(prompts, return_tensors="pt", add_special_tokens=False, padding=True)
            base = enc.input_ids.shape[1]
            require(base + 96 <= c["max_context_tokens"],"context limit")
            torch.manual_seed(0)
            print("starting batch",offset+1,now(),flush=True)
            t = time.monotonic()
            with torch.inference_mode():
                results = b.model.generate(**enc, generation_config=gen)
            s = time.monotonic()-t
            for j, i in enumerate(indexes):
                all_ids = results[j, base:].tolist()
                cut = next((k+1 for k,v in enumerate(all_ids) if v in b.eos_ids),len(all_ids))
                ids=all_ids[:cut]
                require(0 < len(ids) <=96 and all(x==tok.pad_token_id for x in all_ids[cut:]),"unexpected EOS/padding")
                row = dict(source_line=i+1, payload_sha256=hashlib.sha256(public[i].encode()).hexdigest(),
                    batch=offset+1,batch_elapsed_seconds=s,seed=0,prompt_text=prompts[j],
                    prompt_sha256=hashlib.sha256(prompts[j].encode()).hexdigest(),
                    prompt_token_ids=enc.input_ids[j][enc.attention_mask[j].bool()].tolist(),
                    padded_batch_prompt_tokens=base, generated_token_ids=ids,
                    raw_text=tok.decode(ids,skip_special_tokens=False),token_limit_reached=len(ids)==96 and ids[-1] not in b.eos_ids)
                f.write(json.dumps(row,ensure_ascii=False)+"\n")
            f.flush()
            print("finished batch",offset+1,"elapsed_s",round(s,3),now(),flush=True)

def audit(c, op, out, config_path, manifest_digest):
    from toolloop_fix.calibrated_protocol import parse_calibrated_output
    from transformers import AutoTokenizer
    root=pins(c)
    require(manifest_digest and digest(op/"operation_manifest.json")==manifest_digest,"unbound operation manifest")
    opm=json.loads((op/"operation_manifest.json").read_text())
    for path, sha in opm["artifacts"].items():
        require(digest(op/path)==sha,"operation evidence hash mismatch: "+path)
    for path, sha in json.loads((op/"inputs_sha256.json").read_text()).items():
        require(digest(op/path)==sha,"frozen operation input changed: "+path)
    require(digest(op/"public_payloads.jsonl")==c["input_sha256"]["public_payloads.jsonl"] and
            digest(op/"weight_manifest.json")==c["external_inputs"][0]["sha256"],"source or weights not bound")
    record=json.loads((op/"status.json").read_text())
    require(record["evidence_kind"]=="model_inference" and record["exit_code"]==0,"operation incomplete")
    require(digest(op/"config.json")==digest(config_path),"operation/analysis config mismatch")
    tokenizer=AutoTokenizer.from_pretrained(c["model"]["model_path"], local_files_only=True, trust_remote_code=False)
    prov=json.loads((op/"model_provenance.json").read_text())
    require(prov["actual_parameter_device"]=="cpu" and prov["actual_parameter_dtype"]=="torch.float32" and prov["model"]==c["model"] and prov["model_manifest_sha256"]==c["external_inputs"][0]["sha256"],"provenance mismatch")
    require(prov["batch_generation"]["do_sample"] is False and prov["batch_generation"]["num_beams"]==1 and prov["batch_generation"]["max_new_tokens"]==96 and prov["all_parameters_cpu_fp32"],"sampling/device mismatch")
    rows=[json.loads(l) for l in (op/"raw_generations.jsonl").read_text().splitlines()]
    require(sorted(x["source_line"] for x in rows)==list(range(1,13)),"incomplete or duplicate source rows")
    public=(root/"public_payloads.jsonl").read_text().splitlines()
    labels=[json.loads(l) for l in (root/"synthetic_trajectories.jsonl").read_text().splitlines()]
    reports=[]; groups={}
    for r in sorted(rows,key=lambda x:x["source_line"]):
        idx=r["source_line"]-1; item=json.loads(public[idx]); ref=labels[idx]
        prompt=tokenizer.apply_chat_template(item["messages"],tools=item["tools"],tokenize=False,add_generation_prompt=True,enable_thinking=False)
        require(r["batch"]==idx%3+1,"unexpected batch allocation")
        ids=r["generated_token_ids"]
        require(isinstance(ids,list) and 1<=len(ids)<=96 and all(type(v)==int and 0<=v<len(tokenizer) for v in ids),"output IDs invalid")
        require(r["seed"]==0 and r["payload_sha256"]==hashlib.sha256(public[idx].encode()).hexdigest()==ref["payload_sha256"],"payload binding")
        require(prompt==r["prompt_text"] and hashlib.sha256(prompt.encode()).hexdigest()==r["prompt_sha256"] and tokenizer.encode(prompt,add_special_tokens=False)==r["prompt_token_ids"],"prompt binding")
        require(tokenizer.decode(ids,skip_special_tokens=False)==r["raw_text"],"decode mismatch")
        eos=prov["batch_generation"]["eos_token_id"]
        require(not any(v in eos for v in ids[:-1]) and (ids[-1] in eos or len(ids)==96) and r["token_limit_reached"]==(ids[-1] not in eos),"EOS mismatch")
        p=parse_calibrated_output(r["raw_text"],r["token_limit_reached"])
        exp=ref["scripted_actions"][0]
        action=p["action"]
        result="invalid_or_truncated" if not p["valid"] else "natural_semantics_unknown" if p["terminal_form"]=="natural_text" else "exact_proposal" if action==exp else "other_structured"
        code=ref["prefix_response"]["code"]
        group=groups.setdefault(ref["tool"]+"/"+code,dict(n=0,exact_proposal=0,invalid_or_truncated=0,natural_semantics_unknown=0,other_structured=0))
        group["n"]+=1; group[result]+=1
        reports.append(dict(source_line=idx+1,case_id=ref["case_id"],tool=ref["tool"],identifier=ref["identifier"],code=code,observed_form=p["terminal_form"],action=action,parser_error=p["error"],result=result))
    require(len(groups)==6 and all(g["n"]==2 for g in groups.values()),"pairing failed")
    copy=out/"operation_sources"; copy.mkdir()
    for name in list(opm["artifacts"])+["operation_manifest.json"]:
        target=copy/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(op/name,target)
    write(out/"first_step_decisions.json",reports)
    write(out/"first_step_summary.json",{"source_evidence_kind":"model_inference","analysis_command_model_generations":0,
          "source_generations_verified":len(rows),"business_tool_dispatches":0,"groups":groups,"e2e_recovery_not_measured":True})
    print(json.dumps(groups,sort_keys=True))

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode",choices=["generate","audit"]); p.add_argument("--config",type=Path,required=True)
    p.add_argument("--operation",type=Path,required=True); p.add_argument("--output",type=Path)
    p.add_argument("--operation-manifest-sha256")
    args=p.parse_args(); c=json.loads(args.config.read_text())
    for key in ("HF_HUB_OFFLINE","TRANSFORMERS_OFFLINE","HF_HUB_DISABLE_TELEMETRY","HF_HUB_DISABLE_IMPLICIT_TOKEN"):
        os.environ[key]="1"
    os.environ["TOKENIZERS_PARALLELISM"]="false"
    if args.mode=="generate":
        start=now(); t=time.monotonic(); rc=1
        def expiry(*_):
            raise TimeoutError("CPU budget exhausted; keep any completed batches")
        signal.signal(signal.SIGALRM,expiry); signal.alarm(c["wall_limit_seconds"])
        try:
            generate(c,args.operation); rc=0
        finally:
            signal.alarm(0)
            write(args.operation/"status.json",{"evidence_kind":"model_inference","exit_code":rc,"started_at_utc":start,"ended_at_utc":now(),"elapsed_seconds":time.monotonic()-t,"training":False,"business_dispatches":0})
    else:
        audit(c,args.operation,args.output,args.config,args.operation_manifest_sha256)
