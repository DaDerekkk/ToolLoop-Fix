#!/usr/bin/env python3
"""Read-only six-cell descriptive fixed-prefix comparison; no new inference."""
import argparse
import hashlib
import json
import math
from pathlib import Path

CELLS = [(wording, arm) for wording in ('legacy', 'explicit') for arm in ('B0', 'B1', 'B2')]
PROVENANCE_KEYS = ('runtime', 'model', 'sampling', 'chat_template_sha256', 'effective_generation',
    'actual_parameter_device', 'actual_parameter_dtype', 'actual_model_class', 'model_manifest_sha256',
    'model_files_verified', 'guard_version', 'protocol_version', 'score_version', 'case_seeds',
    'mode', 'prefix_runtime_version', 'prefix_schema_version', 'task_wording_version',
    'prefix_dataset_sha256', 'prefix_source_rows')
PAIRED_ROW_KEYS = ('original_case_id', 'domain', 'category', 'template_group', 'prefix_role', 'completable',
    'case_seed', 'prefix_turns', 'prefix_tool_attempts', 'prefix_actual_tool_attempts',
    'prefix_snapshot_sha256', 'prefix_public_history_sha256', 'prefix_row_sha256',
    'remaining_tool_budget_at_start', 'remaining_turn_budget_at_start', 'goal_at_prefix',
    'recovery_eligible', 'historical_input_tokens', 'historical_output_tokens',
    'historical_generation_seconds', 'prefix_proposed_repeat_count', 'prefix_fresh_generations')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def object_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def ratio(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'value': numerator / denominator if denominator else None}


def _verify_inventory(root, manifest):
    for section, directory in (('inputs', root / 'inputs'), ('outputs', root)):
        for name, expected in manifest[section].items():
            candidate = directory / name
            candidate.resolve().relative_to(directory.resolve())
            if not candidate.is_file() or sha(candidate) != expected:
                raise ValueError('source ' + section + ' changed: ' + name)
        actual = {str(path.relative_to(directory)) for path in directory.rglob('*') if path.is_file() and
            (section == 'inputs' or (path != root / 'manifest.json' and path.relative_to(root).parts[0] != 'inputs'))}
        if actual != set(manifest[section]):
            raise ValueError('source ' + section + ' inventory mismatch')
    required = {'config.json', 'model_provenance.json', 'case_results.jsonl', 'metrics.json',
                'generations.jsonl', 'trajectories.jsonl', 'prefix_contexts.jsonl'}
    if not required.issubset(manifest['outputs']):
        raise ValueError('required fixed-prefix artifact missing from hash inventory')


def _summarize(rows):
    recovery = [row for row in rows if row['recovery_eligible']]
    controls = {}
    for role in ('normal_completion_control', 'unavailable_stop_control'):
        selected = [row for row in rows if row['prefix_role'] == role]
        controls[role] = {'case_ids': [row['case_id'] for row in selected],
            'goal_already_at_prefix': sum(row['goal_at_prefix'] for row in selected),
            'new_goal_evidence': ratio(sum(row['new_goal_evidence'] for row in selected), len(selected)),
            'exact_structured_completion': ratio(sum(row['exact_grounded_structured_completion'] for row in selected), len(selected)),
            'stopped_after_public_unavailable': ratio(sum(row['stopped_after_public_unavailable'] for row in selected), len(selected)),
            'exact_stop_reason_code': ratio(sum(row['exact_stop_reason_code'] for row in selected), len(selected)),
            'natural_semantics_unknown': ratio(sum(row['natural_termination_semantics_unknown'] for row in selected), len(selected))}
    result = {'case_count': len(rows), 'recovery_case_ids': [row['case_id'] for row in recovery],
        'recovery_new_goal_evidence': ratio(sum(row['new_goal_evidence'] for row in recovery), len(recovery)),
        'recovery_exact_structured_completion': ratio(sum(row['new_goal_evidence'] and
            row['exact_grounded_structured_completion'] for row in recovery), len(recovery)), 'controls': controls}
    for field in ('natural_termination_semantics_unknown', 'unsupported_finish_wrapper', 'budget_exhausted',
                  'has_inherited_loop', 'has_invalid_loop', 'premature_stop_without_goal_evidence'):
        result[field] = ratio(sum(row[field] for row in rows), len(rows))
    for field in ('prefix_turns', 'continuation_turns', 'turns', 'prefix_tool_attempts', 'proposal_attempts',
                  'continuation_proposal_attempts', 'actual_tool_attempts', 'continuation_actual_tool_attempts',
                  'proposed_repeat_count', 'guard_blocked_proposals', 'parser_error_turns',
                  'input_tokens', 'output_tokens', 'generation_seconds', 'historical_input_tokens',
                  'historical_output_tokens', 'historical_generation_seconds'):
        result[field] = sum(row[field] for row in rows)
    result['fresh_token_cost_per_new_goal'] = ratio(sum(row['input_tokens'] + row['output_tokens'] for row in recovery),
                                                   sum(row['new_goal_evidence'] for row in recovery))
    result['cost_scope'] = 'input/output_tokens and generation_seconds are fresh continuation only; historical costs were not rerun'
    return result


def _load(ref):
    root = Path(ref['path']).resolve()
    if sha(root / 'manifest.json') != ref['manifest_sha256']:
        raise ValueError('source manifest changed')
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest.get('kind') != 'model_inference' or manifest.get('status') != 'completed' or manifest.get('evidence_valid') is not True:
        raise ValueError('source is not completed valid model inference')
    _verify_inventory(root, manifest)
    config = json.loads((root / 'config.json').read_text())
    if config['mode'] != 'fixed_failure_prefix': raise ValueError('wrong source evaluation mode')
    cell = (config['wording_profile'], config['comparison_arm'])
    if cell not in CELLS: raise ValueError('unknown factorial cell')
    ids = config['case_ids']
    if len(ids) != 6 or len(set(ids)) != 6 or set(config['case_seeds']) != set(ids):
        raise ValueError('six unique paired prefix IDs and exact seed map required')
    dataset = jsonl(root / 'inputs' / config['cases_path'])
    if {row['case_id'] for row in dataset} != set(ids) or len(dataset) != 6:
        raise ValueError('prefix dataset must contain exactly the configured six prefixes')
    dataset = {row['case_id']: row for row in dataset}
    rows = jsonl(root / 'case_results.jsonl')
    contexts = jsonl(root / 'prefix_contexts.jsonl')
    if [row['case_id'] for row in rows] != ids or [row['case_id'] for row in contexts] != ids:
        raise ValueError('incomplete, duplicated or reordered prefix results/context')
    provenance = json.loads((root / 'model_provenance.json').read_text())
    for field in ('model', 'sampling', 'case_seeds', 'mode', 'comparison_arm', 'wording_profile',
                  'task_wording_version', 'prefix_schema_version'):
        if provenance[field] != config[field]: raise ValueError('provenance/config mismatch: ' + field)
    data_hash = manifest['inputs'][config['cases_path']]
    if provenance['prefix_dataset_sha256'] != data_hash or provenance['model_files_verified'] is not True:
        raise ValueError('unverified model or prefix data provenance')
    generated = jsonl(root / 'generations.jsonl')
    if not generated or any(g['case_id'] not in ids or g.get('evidence_class') != 'model_inference' for g in generated):
        raise ValueError('fresh generation records missing or belong to another case')
    for row, context in zip(rows, contexts):
        cid = row['case_id']
        data = dataset[cid]
        if row.get('evidence_class') != 'model_inference' or context.get('evidence_class') != 'model_inference':
            raise ValueError('wrong row evidence class')
        if (row['wording_profile'], row['comparison_arm']) != cell or (context['wording_profile'], context['comparison_arm']) != cell:
            raise ValueError('row/context belongs to another cell')
        if row['case_seed'] != config['case_seeds'][cid]: raise ValueError('case seed mismatch')
        if row['prefix_role'] != data['role'] or context['role'] != data['role']:
            raise ValueError('prefix role mismatch')
        if context['source_provenance'] != data['provenance']:
            raise ValueError('context/source provenance mismatch')
        if object_hash(data) != row['prefix_row_sha256'] or row['prefix_row_sha256'] != provenance['prefix_source_rows'][cid]:
            raise ValueError('prefix row not bound to frozen data')
        snapshot = context['actual_prefix_snapshot']
        if (object_hash(snapshot) != row['prefix_snapshot_sha256'] or
                object_hash(snapshot['history']) != row['prefix_public_history_sha256'] or
                snapshot != data['prefix_snapshot']):
            raise ValueError('prefix snapshot/history binding mismatch')
        for field in ('prefix_snapshot_sha256', 'prefix_public_history_sha256', 'prefix_row_sha256',
                      'prefix_turns', 'prefix_tool_attempts', 'historical_input_tokens', 'historical_output_tokens'):
            if context[field] != row[field]: raise ValueError('context/result mismatch: ' + field)
        n = len(data['prefix_events'])
        if row['prefix_turns'] != n or row['prefix_tool_attempts'] != n or row['prefix_actual_tool_attempts'] != n:
            raise ValueError('prefix counts mismatch')
        if row['prefix_fresh_generations'] != 0 or context['prefix_fresh_generations'] != 0:
            raise ValueError('historical prefix falsely charged as fresh generation')
        if (row['remaining_tool_budget_at_start'] != config['tool_budget'] - n or
                row['remaining_turn_budget_at_start'] != config['turn_budget'] - n):
            raise ValueError('remaining prefix budget mismatch')
        if (context['remaining_tool_budget'] != row['remaining_tool_budget_at_start'] or
                context['remaining_turn_budget'] != row['remaining_turn_budget_at_start'] or
                context['total_tool_budget'] != config['tool_budget'] or
                context['total_turn_budget'] != config['turn_budget']):
            raise ValueError('context budget mismatch')
        if (row['turns'] != n + row['continuation_turns'] or
                row['proposal_attempts'] != n + row['continuation_proposal_attempts'] or
                row['tool_attempts'] != row['proposal_attempts'] or
                row['actual_tool_attempts'] != n + row['continuation_actual_tool_attempts'] or
                row['turns'] > config['turn_budget'] or row['proposal_attempts'] > config['tool_budget']):
            raise ValueError('prefix/continuation total budget mismatch')
        if row['continuation_proposal_attempts'] != (row['continuation_actual_tool_attempts'] +
                row['guard_blocked_proposals'] + row['parser_error_turns']):
            raise ValueError('proposal accounting mismatch')
        for field in ('tool_goal_attainment', 'new_goal_evidence', 'goal_at_prefix',
                      'exact_grounded_structured_completion', 'recovery_eligible', 'completable'):
            if type(row[field]) is not bool: raise ValueError('binary outcome required: ' + field)
        if row['new_goal_evidence'] != (row['tool_goal_attainment'] and not row['goal_at_prefix']):
            raise ValueError('pre-existing goal incorrectly counted as new recovery')
        eligible = row['prefix_role'] == 'recovery' and row['completable'] and not row['goal_at_prefix']
        if row['recovery_eligible'] != eligible: raise ValueError('recovery eligibility mismatch')
        if row['prefix_role'] == 'normal_completion_control' and not row['goal_at_prefix']:
            raise ValueError('normal control must start with goal evidence')
        if row['prefix_role'] == 'unavailable_stop_control' and row['completable']:
            raise ValueError('unavailable control must remain separate from completable recovery')
        case_generations = [g for g in generated if g['case_id'] == cid]
        if len(case_generations) != row['continuation_turns']:
            raise ValueError('fresh generation count mismatch')
        for index, generation in enumerate(case_generations):
            if generation['seed'] != row['case_seed'] + n + index or generation['turn'] != n + index + 1:
                raise ValueError('fresh continuation seed/turn mismatch')
        for field in ('input_tokens', 'output_tokens'):
            if sum(g[field] for g in case_generations) != row[field] or row['continuation_' + field] != row[field]:
                raise ValueError('fresh token accounting mismatch')
            if sum(g[field] for g in data['prefix_generations']) != row['historical_' + field]:
                raise ValueError('historical token accounting mismatch')
        if not math.isclose(sum(g['elapsed_seconds'] for g in case_generations), row['generation_seconds'], abs_tol=1e-9):
            raise ValueError('fresh elapsed time mismatch')
    counts = {role: sum(row['prefix_role'] == role for row in rows) for role in
              ('recovery', 'normal_completion_control', 'unavailable_stop_control')}
    if counts != {'recovery': 4, 'normal_completion_control': 1, 'unavailable_stop_control': 1}:
        raise ValueError('expected four recovery prefixes and two separate controls')
    summary = _summarize(rows)
    saved = json.loads((root / 'metrics.json').read_text())
    if saved.get('evidence_class') != 'model_inference': raise ValueError('metrics evidence class mismatch')
    group = saved['metrics']
    if (group['new_goal_evidence_recovery'] != summary['recovery_new_goal_evidence'] or
            group['exact_structured_completion_recovery'] != summary['recovery_exact_structured_completion']):
        raise ValueError('saved recovery aggregates differ from paired case rows')
    for field in ('prefix_turns', 'continuation_turns', 'turns', 'proposal_attempts', 'actual_tool_attempts',
                  'proposed_repeat_count', 'guard_blocked_proposals', 'parser_error_turns', 'input_tokens',
                  'output_tokens', 'historical_input_tokens', 'historical_output_tokens'):
        if group[field] != summary[field]: raise ValueError('saved aggregate accounting mismatch: ' + field)
    return cell, {'config': config, 'provenance': provenance, 'rows': {r['case_id']: r for r in rows},
        'contexts': {c['case_id']: c for c in contexts}, 'summary': summary, 'source': ref,
        'runtime_inputs': {key: value for key, value in manifest['inputs'].items() if key.startswith('toolloop_fix/')},
        'data_sha256': data_hash}


def _paired(base, candidate, ids):
    result = {}
    for metric in ('new_goal_evidence', 'exact_structured_completion_with_new_goal'):
        groups = {'wins': [], 'ties': [], 'losses': []}
        for cid in ids:
            def value(row):
                return row['new_goal_evidence'] if metric == 'new_goal_evidence' else (
                    row['new_goal_evidence'] and row['exact_grounded_structured_completion'])
            delta = int(value(candidate['rows'][cid])) - int(value(base['rows'][cid]))
            groups['wins' if delta > 0 else 'losses' if delta < 0 else 'ties'].append(cid)
        result[metric] = {key: {'count': len(values), 'case_ids': values} for key, values in groups.items()}
    return result


def compare(refs):
    runs = {}
    for ref in refs:
        cell, run = _load(ref)
        if cell in runs: raise ValueError('duplicate factorial cell')
        runs[cell] = run
    if set(runs) != set(CELLS): raise ValueError('all six wording-by-arm cells are required')
    base = runs[('legacy', 'B0')]
    excluded = {'policy_name', 'comparison_arm', 'wording_profile'}
    common = lambda cfg: {key: value for key, value in cfg.items() if key not in excluded}
    for cell, run in runs.items():
        if common(run['config']) != common(base['config']): raise ValueError('unpaired common config')
        if not run['runtime_inputs'] or run['runtime_inputs'] != base['runtime_inputs']:
            raise ValueError('inference code hashes differ')
        if run['data_sha256'] != base['data_sha256']: raise ValueError('prefix data differs')
        for field in PROVENANCE_KEYS:
            if run['provenance'][field] != base['provenance'][field]:
                raise ValueError('actual provenance differs: ' + field)
        for cid, row in run['rows'].items():
            for field in PAIRED_ROW_KEYS:
                if row[field] != base['rows'][cid][field]: raise ValueError('unpaired case metadata: ' + field)
            if run['contexts'][cid]['source_provenance'] != base['contexts'][cid]['source_provenance']:
                raise ValueError('source prefix provenance differs')
    for wording in ('legacy', 'explicit'):
        if runs[(wording, 'B0')]['provenance']['system_prompt_sha256'] != runs[(wording, 'B2')]['provenance']['system_prompt_sha256']:
            raise ValueError('B0/B2 prompt identity violated')
        for cid in base['rows']:
            if len({runs[(wording, arm)]['contexts'][cid]['initial_user_content'] for arm in ('B0', 'B1', 'B2')}) != 1:
                raise ValueError('task wording differs across arms within the same wording profile')
    for arm in ('B0', 'B1', 'B2'):
        if runs[('legacy', arm)]['provenance']['system_prompt_sha256'] != runs[('explicit', arm)]['provenance']['system_prompt_sha256']:
            raise ValueError('wording change also changed system prompt')
    recovery_ids = base['summary']['recovery_case_ids']
    labels = lambda cell: cell[0] + '/' + cell[1]
    return {'evidence_class': 'analysis', 'comparison': 'ITER-004 fixed-prefix 2 wording x 3 arm descriptive comparison',
        'checks': {'complete_six_cells': True, 'all_source_inventories_hashes': True, 'shared_common_config': True,
            'shared_inference_code_data_provenance': True, 'matched_snapshot_history_budgets_seeds': True,
            'four_recovery_two_separate_controls': True, 'fresh_and_historical_costs_separated': True,
            'paired_prompt_and_task_wording': True},
        'recovery_case_ids': recovery_ids, 'source_runs': {labels(cell): runs[cell]['source'] for cell in CELLS},
        'cells': {labels(cell): runs[cell]['summary'] for cell in CELLS},
        'paired_arms_within_wording': {wording: {arm: _paired(runs[(wording, 'B0')], runs[(wording, arm)], recovery_ids)
            for arm in ('B1', 'B2')} for wording in ('legacy', 'explicit')},
        'paired_explicit_vs_legacy': {arm: _paired(runs[('legacy', arm)], runs[('explicit', arm)], recovery_ids)
            for arm in ('B0', 'B1', 'B2')},
        'case_rows': {cid: {labels(cell): runs[cell]['rows'][cid] for cell in CELLS} for cid in base['rows']},
        'limits': ['Four selected recovery prefixes, reused across conditions; 36 episodes are not 36 independent tasks.',
            'New goal evidence is not answer correctness; natural-language terminal semantics remain unknown.',
            'The normal control already has evidence at prefix and never contributes to recovery numerator/denominator.',
            'B2 executed-loop suppression can be mechanical; proposed repeats, blocks, costs and recovery are separate.',
            'Historical prefix generations were not rerun and are not new compute/token costs.',
            'One explicit seed schedule, known development sources; no significance, generalization or natural failure-rate claim.',
            'No training; all historical runs and earlier scores remain unchanged.']}


def markdown(result):
    def fmt(value):
        if isinstance(value, dict):
            return str(value['numerator']) + '/' + str(value['denominator']) if value['denominator'] else 'N/A'
        return str(value)
    lines = ['# ITER-004 固定前缀续跑对照', '',
        '每格4个恢复前缀；另列1个已取得证据的正常完成控制、1个不可用停止控制。单一种子安排，不训练。', '',
        '| 条件 | 新目标证据/恢复 | 精确结构化完成/恢复 | 正常控制精确完成 | 不可用控制停止 | 自然终止待审 | 新执行循环 | 重复提案/阻断 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for label, cell in result['cells'].items():
        fields = [label, fmt(cell['recovery_new_goal_evidence']), fmt(cell['recovery_exact_structured_completion']),
            fmt(cell['controls']['normal_completion_control']['exact_structured_completion']),
            fmt(cell['controls']['unavailable_stop_control']['stopped_after_public_unavailable']),
            fmt(cell['natural_termination_semantics_unknown']), fmt(cell['has_invalid_loop']),
            str(cell['proposed_repeat_count']) + '/' + str(cell['guard_blocked_proposals'])]
        lines.append('| ' + ' | '.join(fields) + ' |')
    lines += ['', '## 成本与预算（历史不计作本次新生成）', '',
        '| 条件 | 历史轮/新生成轮 | 总提案/总执行 | 新输入/输出token | 历史输入/输出token（未重跑） | 解析错/预算耗尽 |',
        '|---|---:|---:|---:|---:|---:|']
    for label, cell in result['cells'].items():
        lines.append('| {} | {}/{} | {}/{} | {}/{} | {}/{} | {}/{} |'.format(label,
            cell['prefix_turns'], cell['continuation_turns'], cell['proposal_attempts'], cell['actual_tool_attempts'],
            cell['input_tokens'], cell['output_tokens'], cell['historical_input_tokens'], cell['historical_output_tokens'],
            cell['parser_error_turns'], fmt(cell['budget_exhausted'])))
    lines += ['', '## 逐前缀配对：胜/平/负（仅4个恢复前缀）']
    def append_pair(label, metrics):
        for metric, groups in metrics.items():
            lines.append('- {} {}：{}/{}/{}。'.format(label, metric, *(groups[key]['count'] for key in ('wins', 'ties', 'losses'))))
    for wording, arms in result['paired_arms_within_wording'].items():
        for arm, values in arms.items(): append_pair(wording + ' ' + arm + ' vs B0', values)
    for arm, values in result['paired_explicit_vs_legacy'].items(): append_pair(arm + ' explicit vs legacy', values)
    lines += ['', '## 解释边界',
        '- 取得目标证据不等于回答正确；自然终止语义仍 unknown。正常控制已在前缀取得的证据不是恢复收益。',
        '- 预算包含历史前缀；新token/耗时只来自续跑（含每轮重新输入历史），历史生成成本单列且未重跑。',
        '- B2机械阻断可减少执行循环，不等于模型学会恢复；不能只看循环下降。',
        '- 同4个恢复前缀跨条件复用，不视为独立36样本；不做显著性、总体泛化或自然故障率声明。',
        '- 所有旧run及成绩保留；本轮不训练。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    names = ('prefix_comparison.json', 'prefix_comparison.md')
    if any((output / name).exists() for name in names): raise FileExistsError('refusing to overwrite prefix comparison')
    result = compare(config['run_dirs'])
    with (output / names[0]).open('x') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    with (output / names[1]).open('x') as handle: handle.write(markdown(result))
    print(json.dumps({'checks': result['checks'], 'cells': result['cells']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
