"""Calibrated E2E model controls; blocked proposals never enter tool history."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

from .baselines import GUARD_VERSION, guard_action, prompt_for
from .calibrated_protocol import (CALIBRATED_PROTOCOL_VERSION, CALIBRATED_SCORE_VERSION,
    messages_for_calibrated_view, parse_calibrated_output, score_calibrated_episode)
from .harness import Environment, detect_loops, ratio, state_hash
from .model_backend import TransformersBackend
from .model_protocol import tool_schemas
from .model_run import ARTIFACTS, validate_config as validate_model_config


def validate_config(config):
    validate_model_config(config)
    prompt_for(config['comparison_arm'])
    if config['protocol_version'] != CALIBRATED_PROTOCOL_VERSION:
        raise ValueError('calibrated protocol version mismatch')
    ids, seeds = config['case_ids'], config['case_seeds']
    if not ids or len(ids) != len(set(ids)) or set(ids) != set(seeds):
        raise ValueError('unique case_ids and an exact explicit case_seeds map required')
    if any(type(seed) is not int or seed < 0 for seed in seeds.values()):
        raise ValueError('case seeds must be nonnegative integers')
    ranges = [set(range(seed, seed + config['turn_budget'])) for seed in seeds.values()]
    if any(a & b for i, a in enumerate(ranges) for b in ranges[i + 1:]):
        raise ValueError('per-case seed ranges must not overlap')


def dialogue_text(raw_text):
    """Remove only generated EOS suffixes that the chat template will reinsert."""
    text = raw_text.rstrip()
    while text.endswith(('<|im_end|>', '<|endoftext|>')):
        suffix = '<|im_end|>' if text.endswith('<|im_end|>') else '<|endoftext|>'
        text = text[:-len(suffix)].rstrip()
    return text


def public_tool_view(env):
    """Internal parse rejections/lifecycle markers are not new tool information."""
    view = env.public_view()
    view['history'] = [event for event in view['history'] if event['action'].get('type') == 'tool']
    return view


def append_feedback(messages, decision, event, turn):
    """Separate runtime notices from genuine tool responses, without action repair."""
    if decision.get('is_terminal'):
        return
    if event['guard_blocked']:
        messages.append({'role': 'assistant', 'content': dialogue_text(decision['raw_text'])})
        messages.append({'role': 'user', 'content': 'Runtime guard notice (not a tool result): ' +
                         event['guard']['feedback']})
    elif not decision['valid']:
        messages.append({'role': 'assistant', 'content': dialogue_text(decision['raw_text'])})
        messages.append({'role': 'user', 'content': 'Adapter protocol error; no tool was executed. ' +
                         json.dumps({'parser_error': decision['error']}, ensure_ascii=False)})
    else:
        call_id = 'call_{}'.format(turn)
        call = decision['call']
        messages.append({'role': 'assistant', 'content': decision['assistant_content'],
            'tool_calls': [{'id': call_id, 'type': 'function', 'function': copy.deepcopy(call)}]})
        messages.append({'role': 'tool', 'name': call['name'], 'tool_call_id': call_id,
                         'content': json.dumps(event['response'], ensure_ascii=False)})


def run_calibrated_case(case, backend, config, emit_generation=None, emit_event=None, emit_partial=None):
    env = Environment(case)
    initial = env.snapshot()
    messages = messages_for_calibrated_view(env.public_view(), [], prompt_for(config['comparison_arm']))
    tools = tool_schemas(env.public_view())
    decisions, traces = [], []
    attempts = turns = actual_tools = blocks = repeats = parser_errors = input_tokens = output_tokens = 0
    seconds = 0.0
    exhausted = output_limit = False
    identity = {'case_id': case['case_id'], 'domain': case['domain'], 'category': case['category'],
        'template_group': case['template_group'], 'policy': config['policy_name'],
        'comparison_arm': config['comparison_arm'], 'mode': 'end_to_end',
        'evidence_class': 'model_inference', 'protocol_version': CALIBRATED_PROTOCOL_VERSION,
        'case_seed': config['case_seeds'][case['case_id']]}
    while env.snapshot()['terminal'] is None:
        if attempts >= config['tool_budget'] or turns >= config['turn_budget']:
            exhausted = True
            break
        seed = identity['case_seed'] + turns
        try:
            generation = backend.generate(copy.deepcopy(messages), copy.deepcopy(tools), seed)
        except Exception as exc:
            if emit_generation:
                emit_generation({**identity, 'turn': turns + 1, 'messages': copy.deepcopy(messages),
                    'tools': tools, 'seed': seed, 'raw_text': None, 'input_tokens': None, 'output_tokens': None,
                    'generation_error': {'type': type(exc).__name__, 'message': str(exc)}})
            if emit_partial:
                emit_partial({**identity, 'state': env.snapshot(), 'events': traces,
                    'tool_attempts': attempts, 'turns': turns, 'status': 'generation_failed'})
            raise
        decision = parse_calibrated_output(generation['raw_text'], generation['token_limit_reached'])
        action = decision['action'] if decision['valid'] else {
            'type': 'invalid_model_output', 'raw_text': generation['raw_text'], 'parser_error': decision['error']}
        guard = guard_action(public_tool_view(env), action)
        blocked = config['comparison_arm'] == 'B2' and guard['blocked']
        if emit_generation:
            emit_generation({**identity, 'turn': turns + 1, 'messages': copy.deepcopy(messages),
                'tools': tools, **generation, 'parse': copy.deepcopy(decision),
                'guard_observation': guard, 'guard_enforced': blocked})
        turns += 1
        attempts += int(action.get('type') not in {'final', 'stop'})
        actual_dispatch = not blocked and action.get('type') == 'tool'
        actual_tools += int(actual_dispatch)
        blocks += int(blocked)
        repeats += int(guard['blocked'])
        parser_errors += int(not decision['valid'])
        input_tokens += generation['input_tokens']
        output_tokens += generation['output_tokens']
        seconds += generation['elapsed_seconds']
        before = env.snapshot()
        if blocked:
            event = {'action': copy.deepcopy(action), 'response': None,
                'state_before': state_hash(before['state']), 'state_after': state_hash(before['state'])}
            event_kind = 'guard_blocked_proposal'
        else:
            event = env.execute(action)
            event_kind = ('actual_tool_response' if actual_dispatch else
                          'internal_terminal_lifecycle' if decision['is_terminal'] else 'internal_protocol_rejection')
        event.update(turn=turns, event_kind=event_kind, executed=not blocked,
            actual_tool_dispatch=actual_dispatch, model_generated_terminal_tool=bool(
                decision['is_terminal'] and decision['call'] is not None),
            guard_blocked=blocked, guard=guard, parser_error=decision['error'],
            snapshot_before_sha256=state_hash(before), snapshot_after_sha256=state_hash(env.snapshot()))
        traces.append(event)
        decisions.append(decision)
        if emit_event:
            emit_event({**identity, **event})
        if emit_partial:
            emit_partial({**identity, 'state': env.snapshot(), 'events': traces,
                'tool_attempts': attempts, 'turns': turns, 'status': 'running'})
        append_feedback(messages, decision, event, turns)
        if generation['token_limit_reached']:
            exhausted = output_limit = True
            break
    outcome = score_calibrated_episode(case, env.snapshot(), decisions, exhausted)
    flags = detect_loops(public_tool_view(env)['history'])
    result = {**identity, **outcome, 'tool_attempts': attempts, 'proposal_attempts': attempts,
        'actual_tool_attempts': actual_tools, 'guard_blocked_proposals': blocks,
        'proposed_repeat_count': repeats, 'turns': turns, 'parser_error_turns': parser_errors,
        'has_invalid_loop': bool(flags), 'loop_types': sorted({flag['type'] for flag in flags}),
        'output_token_limit_reached': output_limit, 'input_tokens': input_tokens,
        'output_tokens': output_tokens, 'generation_seconds': seconds,
        'budget_scope': 'all_proposals_including_blocked_and_invalid; terminal_excluded_from_tool_budget'}
    trajectory = {**identity, 'public_initial_request': case['request'], 'initial_snapshot': initial,
        'events': traces, 'loop_flags': flags, 'loop_index_scope': 'actual_tool_history_only',
        'actual_tool_history': public_tool_view(env)['history'], 'final_snapshot': env.snapshot()}
    return result, trajectory


def calibrated_metrics(results):
    group = {'case_count': len(results)}
    for key in ('tool_goal_attainment', 'exact_grounded_structured_completion'):
        rows = [row for row in results if row['completable']]
        group[key] = ratio(sum(row[key] for row in rows), len(rows))
        for category in ('normal', 'retry_required'):
            subset = [row for row in rows if row['category'] == category]
            group[category + '_' + key] = ratio(sum(row[key] for row in subset), len(subset))
    for key in ('unsupported_finish_wrapper', 'natural_termination_semantics_unknown',
                'premature_stop_without_goal_evidence', 'budget_exhausted', 'has_invalid_loop'):
        group[key] = ratio(sum(row[key] for row in results), len(results))
    unavailable = [row for row in results if not row['completable']]
    for key in ('stopped_after_public_unavailable', 'exact_stop_reason_code', 'stop_reason_semantics_unknown'):
        group[key] = ratio(sum(row[key] for row in unavailable), len(unavailable))
    for key in ('turns', 'proposal_attempts', 'actual_tool_attempts', 'guard_blocked_proposals',
                'proposed_repeat_count', 'parser_error_turns', 'input_tokens', 'output_tokens', 'generation_seconds'):
        group[key] = sum(row[key] for row in results)
    group['parser_error_turn_rate'] = ratio(group['parser_error_turns'], group['turns'])
    group['proposed_repeat_rate'] = ratio(group['proposed_repeat_count'], group['proposal_attempts'])
    group['terminal_forms'] = {form: sum(row['terminal_form'] == form for row in results)
                              for form in ('natural_text', 'structured_finish', 'structured_stop', 'none')}
    return {'evidence_class': 'model_inference', 'protocol_version': CALIBRATED_PROTOCOL_VERSION,
        'score_version': CALIBRATED_SCORE_VERSION, 'comparison_arm': results[0]['comparison_arm'],
        'metrics': group,
        'warning': '15 known hand-authored development fixtures, one seed schedule; not held-out or training. '
                   'Tool-goal evidence is not task completion. Natural-language semantics remain unknown. '
                   'B2 may mechanically suppress execution while model proposals still repeat.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    validate_config(config)
    root = Path(__file__).resolve().parents[1]
    by_id = {case['case_id']: case for case in (json.loads(line) for line in
              (root / config['cases_path']).read_text().splitlines() if line.strip())}
    cases = [by_id[case_id] for case_id in config['case_ids']]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ARTIFACTS):
        raise FileExistsError('refusing to overwrite model artifacts')
    def write_json(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    handles = {}
    def emit(name, value):
        if name not in handles:
            handles[name] = (output / name).open('x')
        handles[name].write(json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n')
        handles[name].flush()
    stage = 'model_load'
    try:
        backend = TransformersBackend(config, tool_schemas(Environment(cases[0]).public_view()))
        provenance = backend.provenance()
        prompt = messages_for_calibrated_view(Environment(cases[0]).public_view(), [], prompt_for(config['comparison_arm']))[0]['content']
        provenance.update(protocol_version=CALIBRATED_PROTOCOL_VERSION, score_version=CALIBRATED_SCORE_VERSION,
            comparison_arm=config['comparison_arm'], guard_version=GUARD_VERSION,
            system_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(), case_seeds=config['case_seeds'])
        write_json('model_provenance.json', provenance)
        results = []
        for case in cases:
            stage = 'case:' + case['case_id']
            result, trajectory = run_calibrated_case(case, backend, config,
                emit_generation=lambda row: emit('generations.jsonl', row),
                emit_event=lambda row: emit('execution_events.jsonl', row),
                emit_partial=lambda row: write_json('partial_case.json', row))
            emit('case_results.jsonl', result)
            emit('trajectories.jsonl', trajectory)
            results.append(result)
            print(json.dumps({key: result[key] for key in ('case_id', 'tool_goal_attainment',
                'exact_grounded_structured_completion', 'terminal_form', 'turns', 'guard_blocked_proposals')}), flush=True)
        write_json('metrics.json', calibrated_metrics(results))
        write_json('partial_case.json', {'evidence_class': 'model_inference', 'status': 'completed',
                                       'completed_case_count': len(results)})
    except Exception as exc:
        write_json('model_failure.json', {'evidence_class': 'model_inference', 'stage': stage,
            'error_type': type(exc).__name__, 'message': str(exc), 'executable': sys.executable,
            'note': 'Failure retained; no scripted fallback or unrecorded retries.'})
        raise
    finally:
        for handle in handles.values():
            handle.close()


if __name__ == '__main__':
    main()
