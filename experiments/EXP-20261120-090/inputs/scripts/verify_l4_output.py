"""Offline consistency audit of one public Colab receipt, not another model run."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts import colab_inference_probe as probe
from toolloop_fix.model_protocol import ADAPTER_VERSION, parse_tool_output

EOS = [248044, 248046]


class AuditError(ValueError):
    """Message is an authored safe field label, never a remote value."""


def require(condition, field):
    if not condition:
        raise AuditError(field)


def verify(r, tokenizer):
    require(r['schema_version'] == 1 and r['scope'] == 'single_public_tool_call_proposal_only'
            and r['status'] == r['stage'] == 'completed', 'not_completed_public_probe')
    require(r['training_performed'] is False and r['tool_executed'] is False, 'probe_scope')
    require(r['model_id'] == probe.MODEL_ID and r['revision'] == probe.REVISION
            and r['verified_files'] == probe.FILES and len(r['verified_files']) == 9, 'model_snapshot')
    require(r['model_class'] == 'Qwen3_5ForCausalLM' and r['parameter_device'] == 'cuda:0'
            and r['parameter_dtype'] == 'torch.bfloat16', 'model_placement')
    gpu = r['gpu']
    require('L4' in gpu['name'].split() and gpu['compute_capability'] == [8, 9]
            and type(gpu['memory_bytes']) is int and gpu['memory_bytes'] > 0, 'gpu_metadata')
    runtime = r['runtime']
    require(all(isinstance(x, str) and x for x in [runtime['python'], runtime['cuda_version']] +
                [runtime['packages'].get(k) for k in ('torch','transformers','huggingface-hub','tokenizers','safetensors')]),
            'runtime_metadata')
    require(r['messages'] == probe.MESSAGES and r['tools'] == probe.TOOLS, 'public_task')
    g = r['generation_config']
    require(type(r['seed']) is int and r['seed'] == probe.SEED and g['do_sample'] is False
            and type(g['num_beams']) is int and g['num_beams'] == 1 and g['use_cache'] is True
            and g['max_new_tokens'] == probe.MAX_NEW_TOKENS and g['eos_token_id'] == EOS
            and g['pad_token_id'] == tokenizer.pad_token_id, 'generation_setup')
    prompt = tokenizer.apply_chat_template(probe.MESSAGES, tools=probe.TOOLS, tokenize=False,
                                           add_generation_prompt=True, enable_thinking=False)
    ids = tokenizer(prompt, add_special_tokens=False)['input_ids']
    require(r['prompt_text'] == prompt and r['prompt_sha256'] == hashlib.sha256(prompt.encode()).hexdigest()
            and r['prompt_token_ids'] == ids and type(r['input_tokens']) is int
            and r['input_tokens'] == len(ids) and 0 < len(ids) <= 2048, 'prompt_rebuild')
    generated = r['generated_token_ids']
    require(type(generated) is list and 1 <= len(generated) <= probe.MAX_NEW_TOKENS and
            type(r['output_tokens']) is int and len(generated) == r['output_tokens'] and
            all(type(t) is int and 0 <= t < len(tokenizer) for t in generated), 'generated_ids')
    limit = len(generated) == probe.MAX_NEW_TOKENS and generated[-1] not in EOS
    require(not any(t in EOS for t in generated[:-1]) and (limit or generated[-1] in EOS)
            and r['token_limit_reached'] is limit, 'termination')
    require(type(r['generation_seconds']) in (float, int) and math.isfinite(r['generation_seconds'])
            and r['generation_seconds'] > 0, 'generation_seconds')
    require(r['raw_text'] == tokenizer.decode(generated, skip_special_tokens=False), 'token_decode')
    parsed = parse_tool_output(r['raw_text'], token_limit_reached=limit)
    expected = {'name':'lookup_demo','arguments':{'identifier':'DEMO-001'}}
    return {'verified': True, 'scope': 'receipt_consistency_and_tokenizer_replay_only',
            'gpu_reported':gpu['name'], 'dtype_reported':r['parameter_dtype'],
            'model_id':probe.MODEL_ID, 'revision':probe.REVISION, 'verified_files_reported':9,
            'input_tokens':len(ids), 'output_tokens':len(generated), 'generation_seconds':r['generation_seconds'],
            'stop_reason':'token_limit' if limit else 'eos', 'parser_version': ADAPTER_VERSION,
            'parse_valid':parsed['valid'], 'parse_error':parsed['error'], 'parsed_call':parsed['call'],
            'expected_call':parsed['valid'] and parsed['call'] == expected, 'tool_executed':False,
            'training_performed':False, 'local_model_generations':0}


def load_tokenizer(directory):
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'
    # Verify the inputs used for the independent tokenizer replay. No weight load/generation.
    files = {k:v for k,v in probe.FILES.items() if k != 'LICENSE' and 'safetensors' not in k}
    probe.verify_files(directory, files)
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(str(directory.resolve()), trust_remote_code=False, local_files_only=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--model-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True, help='new JSON file, not a directory')
    p.add_argument('--expected-sha256', help='optional digest pinned by the lifecycle collector')
    args = p.parse_args()
    meta = {'evidence_kind':'analysis', 'probe_source_sha256':hashlib.sha256(Path(probe.__file__).read_bytes()).hexdigest()}
    try:
        with args.receipt.open('rb') as f:
            data = f.read(65537)
        require(0 < len(data) <= 65536, 'receipt_size')
        meta['receipt_sha256'] = hashlib.sha256(data).hexdigest()
        if args.expected_sha256 is not None:
            require(re.fullmatch('[0-9a-f]{64}', args.expected_sha256)
                    and meta['receipt_sha256'] == args.expected_sha256, 'receipt_digest')
        r = json.loads(data)
        require(isinstance(r, dict) and r.get('status') == 'completed', 'no_completed_receipt')
        audit = verify(r, load_tokenizer(args.model_dir))
    except Exception as error:
        audit = {'verified':False, 'reason':str(error) if isinstance(error, AuditError) else type(error).__name__}
    result = {**meta, **audit}
    try:
        with args.output.open('x') as f:
            json.dump(result, f, indent=2, ensure_ascii=False, allow_nan=False)
            f.write('\n')
    except OSError as error:
        print(type(error).__name__, file=sys.stderr)
        return 2
    print(json.dumps({k:result[k] for k in ('verified','expected_call','reason') if k in result}))
    return 0 if result['verified'] else 1


if __name__ == '__main__':
    sys.exit(main())
