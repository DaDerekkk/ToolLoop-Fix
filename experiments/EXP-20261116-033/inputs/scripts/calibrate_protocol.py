#!/usr/bin/env python3
"""Offline parsing diagnosis only; never counterfactually score old continuations."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from toolloop_fix.calibrated_protocol import CALIBRATED_PROTOCOL_VERSION, parse_calibrated_output
from toolloop_fix.model_protocol import parse_tool_output


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def diagnose(rows):
    transitions, first_terminals, details = Counter(), {}, []
    for row in rows:
        old = parse_tool_output(row['raw_text'], row['token_limit_reached'])
        if any(old[key] != row['parse'][key] for key in ('valid', 'error', 'action', 'call')):
            raise ValueError('recorded legacy parse does not replay')
        new = parse_calibrated_output(row['raw_text'], row['token_limit_reached'])
        label = ('valid_tool_or_wrapper' if old['valid'] else old['error']) + ' -> ' + (
            new['terminal_form'] or ('valid_tool' if new['valid'] else new['error']))
        transitions[label] += 1
        cid = row['case_id']
        previous_terminal = cid in first_terminals
        if new['is_terminal'] and not previous_terminal:
            first_terminals[cid] = {'turn': row['turn'], 'form': new['terminal_form']}
        details.append({'case_id': cid, 'turn': row['turn'], 'legacy_error': old['error'],
            'new_valid': new['valid'], 'new_terminal_form': new['terminal_form'],
            'after_first_calibrated_terminal': previous_terminal})
    return {'evidence_class': 'analysis', 'protocol_version': CALIBRATED_PROTOCOL_VERSION,
        'row_count': len(rows), 'transitions': dict(transitions),
        'first_calibrated_terminal': first_terminals, 'rows': details,
        'warning': 'All rows reparsed as diagnostics. After the first calibrated terminal, later old rows '
                   'cannot be used as counterfactual calibrated outcomes. No new model inference, semantic '
                   'grading, action repair, old score overwrite or training.'}


def check_local_template(config):
    """Render only: official tokenizer, no model construction or inference."""
    from transformers import AutoTokenizer
    from toolloop_fix.baselines import guard_action
    from toolloop_fix.calibrated_run import append_feedback, public_tool_view
    from toolloop_fix.calibrated_protocol import messages_for_calibrated_view
    from toolloop_fix.harness import Environment
    from toolloop_fix.model_backend import verify_model_manifest
    from toolloop_fix.model_protocol import tool_schemas
    verify_model_manifest(config)
    tokenizer = AutoTokenizer.from_pretrained(config['model']['model_path'], local_files_only=True,
                                             trust_remote_code=False)
    root = Path(__file__).resolve().parents[1]
    case = next(json.loads(line) for line in (root / config['cases_path']).read_text().splitlines())
    env = Environment(case)
    raw = '<tool_call>\n<function=get_order>\n<parameter=identifier>bad</parameter>\n</function>\n</tool_call><|im_end|>'
    decision = parse_calibrated_output(raw)
    messages = messages_for_calibrated_view(env.public_view(), [])
    event = env.execute(decision['action']); event['guard_blocked'] = False
    append_feedback(messages, decision, event, 1)
    guard = guard_action(public_tool_view(env), decision['action'])
    assert guard['blocked']
    append_feedback(messages, decision, {'guard_blocked': True, 'guard': guard}, 2)
    tools = tool_schemas(env.public_view())
    rendered = tokenizer.apply_chat_template(messages, tools=tools, tokenize=False,
                                            add_generation_prompt=True, enable_thinking=False)
    blocked_segment = rendered.split('<|im_start|>assistant\n')[-2].split('<|im_start|>user')[0]
    assert blocked_segment.count('<|im_end|>') == 1, repr(blocked_segment)
    assert '<tool_response>' not in blocked_segment
    assert raw.endswith('<|im_end|>') and decision['raw_text'] == raw
    return {'evidence_class': 'analysis', 'type': 'local_tokenizer_template_render_only',
            'blocked_assistant_eos_count': blocked_segment.count('<|im_end|>'),
            'chat_template_sha256': hashlib.sha256(tokenizer.get_chat_template(tools=tools).encode()).hexdigest(),
            'rendered_prompt_sha256': hashlib.sha256(rendered.encode()).hexdigest(),
            'raw_generation_unchanged': True, 'no_model_loaded': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True); parser.add_argument('--output', required=True)
    args = parser.parse_args(); config = json.loads(Path(args.config).read_text())
    for ref in config['source_refs'] + config['preserved_manifests']:
        if digest(ref['path']) != ref['sha256']:
            raise ValueError('source hash changed: ' + ref['path'])
    source = config['source_refs'][0]
    rows = [json.loads(line) for line in Path(source['path']).read_text().splitlines() if line.strip()]
    result = diagnose(rows)
    result.update(source_refs=config['source_refs'], preserved_manifest_count=len(config['preserved_manifests']))
    root = Path(__file__).resolve().parents[1]
    result['template_render_check'] = check_local_template(json.loads((root / config['template_config']).read_text()))
    with (Path(args.output) / 'calibration.json').open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps({key: result[key] for key in ('row_count', 'transitions', 'preserved_manifest_count')}, indent=2))


if __name__ == '__main__': main()
