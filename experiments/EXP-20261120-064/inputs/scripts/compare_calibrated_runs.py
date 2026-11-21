#!/usr/bin/env python3
"""Paired descriptive comparison of preregistered development arms; no tuning."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(refs):
    runs = {}
    for ref in refs:
        root = Path(ref['path'])
        if sha(root / 'manifest.json') != ref['manifest_sha256']:
            raise ValueError('manifest changed: ' + str(root))
        manifest = json.loads((root / 'manifest.json').read_text())
        if not manifest['evidence_valid'] or manifest['status'] != 'completed':
            raise ValueError('incomplete or invalid source run')
        for name, digest in manifest['outputs'].items():
            if sha(root / name) != digest:
                raise ValueError('source output changed: ' + name)
        required = {'config.json', 'case_results.jsonl', 'metrics.json', 'model_provenance.json'}
        if not required.issubset(manifest['outputs']):
            raise ValueError('required read artifact missing from output inventory')
        for section, directory in (('inputs', root / 'inputs'), ('outputs', root)):
            for name, digest in manifest[section].items():
                candidate = directory / name
                candidate.resolve().relative_to(directory.resolve())
                if not candidate.is_file() or sha(candidate) != digest:
                    raise ValueError('source ' + section + ' changed: ' + name)
            actual = {str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file() and
                      (section == 'inputs' or (p.relative_to(directory).parts[0] != 'inputs' and p.name != 'manifest.json'))}
            if actual != set(manifest[section]):
                raise ValueError('source ' + section + ' inventory mismatch')
        config = json.loads((root / 'config.json').read_text())
        arm = config['comparison_arm']
        if arm in runs: raise ValueError('duplicate arm')
        rows = [json.loads(line) for line in (root / 'case_results.jsonl').read_text().splitlines()]
        if [row['case_id'] for row in rows] != config['case_ids']:
            raise ValueError('incomplete or reordered source results')
        if any(row['case_seed'] != config['case_seeds'][row['case_id']] for row in rows):
            raise ValueError('result case seed mismatch')
        runs[arm] = {'config': config, 'rows': {r['case_id']: r for r in rows},
            'metrics': json.loads((root / 'metrics.json').read_text())['metrics'],
            'provenance': json.loads((root / 'model_provenance.json').read_text()),
            'cases_sha256': manifest['inputs'][config['cases_path']],
            'runtime_inputs': {k: v for k, v in manifest['inputs'].items() if k.startswith('toolloop_fix/')},
            'source': ref}
    if set(runs) != {'B0', 'B1', 'B2'}: raise ValueError('all three arms required')
    base = runs['B0']
    def common(c): return {k: v for k, v in c.items() if k not in ('policy_name', 'comparison_arm')}
    for arm, run in runs.items():
        if common(run['config']) != common(base['config']): raise ValueError('unpaired config: ' + arm)
        if run['runtime_inputs'] != base['runtime_inputs']: raise ValueError('inference code changed: ' + arm)
        if run['cases_sha256'] != base['cases_sha256']: raise ValueError('case data changed: ' + arm)
        for key in ('runtime', 'model', 'sampling', 'chat_template_sha256', 'effective_generation',
                    'actual_parameter_device', 'actual_parameter_dtype', 'actual_model_class',
                    'model_manifest_sha256', 'model_files_verified', 'guard_version', 'protocol_version',
                    'score_version', 'case_seeds'):
            if run['provenance'][key] != base['provenance'][key]:
                raise ValueError('actual provenance changed: ' + key)
        for cid, row in run['rows'].items():
            for key in ('domain', 'category', 'template_group', 'completable', 'case_seed'):
                if row[key] != base['rows'][cid][key]: raise ValueError('case metadata changed: ' + key)
    if runs['B2']['provenance']['system_prompt_sha256'] != base['provenance']['system_prompt_sha256']:
        raise ValueError('B0/B2 system prompt mismatch')
    paired = {}
    for arm in ('B1', 'B2'):
        paired[arm] = {}
        for metric in ('tool_goal_attainment', 'exact_grounded_structured_completion'):
            changes = {'wins': [], 'ties': [], 'losses': []}
            for cid, row in base['rows'].items():
                if not row['completable']: continue
                delta = int(runs[arm]['rows'][cid][metric]) - int(row[metric])
                changes['wins' if delta > 0 else 'losses' if delta < 0 else 'ties'].append(cid)
            paired[arm][metric] = {k: {'count': len(v), 'case_ids': v} for k, v in changes.items()}
    return {'evidence_class': 'analysis', 'comparison': 'ITER-003 paired E2E dev, one explicit seed schedule',
        'source_runs': {arm: run['source'] for arm, run in runs.items()},
        'checks': {'shared_runtime_code_hashes': True, 'shared_common_config': True,
                   'paired_case_seeds': True, 'all_source_output_hashes': True,
                   'all_source_input_hashes': True, 'shared_actual_provenance': True,
                   'shared_case_data_and_metadata': True, 'identical_B0_B2_prompt': True},
        'metrics_by_arm': {arm: run['metrics'] for arm, run in runs.items()},
        'paired_against_B0': paired,
        'case_rows': {cid: {arm: run['rows'][cid] for arm, run in runs.items()} for cid in base['rows']},
        'limits': ['Known15development fixtures; no heldout or statistical significance claim.',
                   'Tool-goal attainment is observed evidence, not overall task success.',
                   'Exact structured completion does not grade natural-language answer correctness.',
                   'Native terminal semantics unknown; stopping after unavailable does not prove truthful explanation.',
                   'B2 executed-loop suppression can be mechanical; proposal repetition must also be reported.',
                   'Old EXP013 scores preserved and not used as a calibrated B0 control.',
                   'No training or fixed-error-prefix model experiments in this iteration.']}


def markdown(result):
    def fmt(value):
        if isinstance(value, dict):
            return str(value['numerator']) + '/' + str(value['denominator']) if value['denominator'] else 'N/A'
        return str(value)
    lines = ['# ITER-003 B0/B1/B2 开发对照', '',
             '同一校准协议、固定权重、15 个已知开发案例、相同显式 case seeds。非封闭测试；不训练。', '',
             '| 指标 | B0 | B1 通用提示 | B2 公开历史 guard |', '|---|---:|---:|---:|']
    keys = [('tool_goal_attainment', '取得目标证据（不是任务完成率）'),
            ('exact_grounded_structured_completion', '精确有依据的结构化完成'),
            ('normal_tool_goal_attainment', '正常案例目标证据'),
            ('retry_required_tool_goal_attainment', '必要重试案例目标证据'),
            ('natural_termination_semantics_unknown', '自然终止语义待审'),
            ('unsupported_finish_wrapper', '不受支持的完成包装'),
            ('stopped_after_public_unavailable', '公开不可用后停止'),
            ('exact_stop_reason_code', '精确停止原因码'),
            ('has_invalid_loop', '实际工具历史原型循环'),
            ('proposed_repeat_count', '符合 guard 的重复提案数'),
            ('guard_blocked_proposals', '实际阻断提案数'),
            ('budget_exhausted', '预算耗尽'), ('turns', '模型生成轮数'),
            ('actual_tool_attempts', '真实工具调用'), ('proposal_attempts', '预算提案次数'),
            ('parser_error_turns', '解析错误轮数'), ('input_tokens', '输入 token'), ('output_tokens', '输出 token')]
    for key, title in keys:
        lines.append('| ' + title + ' | ' + ' | '.join(fmt(result['metrics_by_arm'][arm][key]) for arm in ('B0', 'B1', 'B2')) + ' |')
    lines += ['', '## 相对 B0 的逐例配对（胜/平/负）']
    for arm, metrics in result['paired_against_B0'].items():
        for key, delta in metrics.items():
            lines.append('- {} {}: {}/{}/{}。'.format(arm, key, *(delta[k]['count'] for k in ('wins', 'ties', 'losses'))))
    lines += ['', '## 解释边界',
        '- 目标证据与最终回答正确性分开；自然终止全部保留 unknown，不把其当错误或正确回答。',
        '- B2 拦截减少实际执行可以是机制结果，不证明模型学会恢复；须同时看重复提案与目标证据。',
        '- 单一小样本不做显著性/总体泛化声明；旧 EXP-013 没有重打分，不能当新 B0。',
        '- 固定错误前缀对照、多种子、新开发模板及人工语义/检测器复核尚未完成。训练继续 hold。', '']
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--config', required=True); p.add_argument('--output', required=True)
    args = p.parse_args(); config = json.loads(Path(args.config).read_text())
    result = compare(config['run_dirs']); output = Path(args.output)
    with (output / 'comparison.json').open('x') as f: json.dump(result, f, ensure_ascii=False, indent=2); f.write('\n')
    with (output / 'comparison.md').open('x') as f: f.write(markdown(result))
    print(json.dumps({'checks': result['checks'], 'paired_against_B0': result['paired_against_B0']}, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
