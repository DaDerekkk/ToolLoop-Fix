#!/usr/bin/env python3
"""Extract hash-pinned authentic tool prefixes, never synthetic model outputs."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from toolloop_fix.calibrated_protocol import parse_calibrated_output
from toolloop_fix.harness import Environment, state_hash


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def build_entry(selection):
    source = Path(selection['source_run'])
    if sha(source / 'manifest.json') != selection['source_manifest_sha256']:
        raise ValueError('source manifest changed')
    manifest = json.loads((source / 'manifest.json').read_text())
    if not manifest['evidence_valid'] or manifest['status'] != 'completed':
        raise ValueError('source must be completed valid model inference')
    names = ('generations.jsonl', 'trajectories.jsonl', 'execution_events.jsonl')
    for name in names:
        if sha(source / name) != manifest['outputs'][name]:
            raise ValueError('source changed: ' + name)
    cfg = json.loads((source / 'config.json').read_text())
    case_path = source / 'inputs' / cfg['cases_path']
    if sha(case_path) != manifest['inputs'][cfg['cases_path']]:
        raise ValueError('source frozen cases changed')
    # Shared simulator semantics must be identical, not silently replayed under new rules.
    here = Path(__file__).resolve().parents[1]
    if sha(here / 'toolloop_fix/harness.py') != manifest['inputs']['toolloop_fix/harness.py']:
        raise ValueError('environment implementation differs from prefix source')
    cid = selection['source_case_id']
    case = next(row for row in rows(case_path) if row['case_id'] == cid)
    trace_line, trace = next((i + 1, row) for i, row in enumerate(rows(source / 'trajectories.jsonl')) if row['case_id'] == cid)
    count = selection['prefix_turns']
    if type(count) is not int or count < 1:
        raise ValueError('positive prefix length required')
    indexed_generations = [(i + 1, row) for i, row in enumerate(rows(source / 'generations.jsonl')) if row['case_id'] == cid][:count]
    if len(indexed_generations) != count or len(trace['events']) < count:
        raise ValueError('requested prefix not present')
    events = trace['events'][:count]
    env = Environment(case)
    for turn, ((_, generation), event) in enumerate(zip(indexed_generations, events), 1):
        decision = parse_calibrated_output(generation['raw_text'], generation['token_limit_reached'])
        if not decision['valid'] or decision['is_terminal'] or decision['action']['type'] != 'tool':
            raise ValueError('prefix must contain only authentic valid business tool calls')
        if generation['turn'] != turn or event['turn'] != turn or event.get('guard_blocked'):
            raise ValueError('nonconsecutive or blocked source prefix')
        if decision['action'] != event['action']:
            raise ValueError('source raw call differs from executed action')
        actual = env.execute(decision['action'])
        if any(actual[key] != event[key] for key in ('action', 'response', 'state_before', 'state_after')):
            raise ValueError('source prefix did not replay exactly')
    global_events = [(i + 1, row) for i, row in enumerate(rows(source / 'execution_events.jsonl')) if row['case_id'] == cid][:count]
    entry = {'prefix_schema_version': 'authentic_tool_prefix_v1', 'case_id': selection['case_id'],
        'role': selection['role'], 'original_case': copy.deepcopy(case),
        'prefix_generations': [row for _, row in indexed_generations], 'prefix_events': events,
        'prefix_snapshot': env.snapshot(),
        'provenance': {'source_run': str(source), 'source_case_id': cid,
            'source_manifest_sha256': selection['source_manifest_sha256'],
            'source_generations_sha256': sha(source / 'generations.jsonl'),
            'source_trajectories_sha256': sha(source / 'trajectories.jsonl'),
            'source_execution_events_sha256': sha(source / 'execution_events.jsonl'),
            'source_generation_lines_1based': [i for i, _ in indexed_generations],
            'source_event_indices_1based': list(range(1, count + 1)),
            'source_trajectory_line_1based': trace_line,
            'source_execution_event_lines_1based': [i for i, _ in global_events],
            'source_case_sha256': state_hash(case), 'source_cases_path': str(case_path),
            'source_cases_sha256': sha(case_path), 'source_prefix_turns': count,
            'transplant_note': 'Authentic model-generated tool prefix transplanted into a controlled continuation context. No claim that new wording/arm naturally produced this prefix.'}}
    return entry


def main():
    p = argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();cfg=json.loads(Path(args.config).read_text());output=Path(args.output)
    entries=[build_entry(item) for item in cfg['selections']]
    if len({row['case_id'] for row in entries}) != len(entries):raise ValueError('duplicate prefix IDs')
    target=output/'fixed_prefix_cases_iter4.jsonl'
    with target.open('x') as f:
        for row in entries:f.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n')
    report={'evidence_class':'analysis','prefix_schema_version':'authentic_tool_prefix_v1',
        'prefix_count':len(entries),'total_historical_tool_turns':sum(len(e['prefix_events']) for e in entries),
        'dataset_sha256':sha(target),'selection_config':cfg,
        'prefixes':[{'case_id':e['case_id'],'role':e['role'],'snapshot_sha256':state_hash(e['prefix_snapshot']),
                     'public_history_sha256':state_hash(e['prefix_snapshot']['history']),
                     'last_response':e['prefix_events'][-1]['response'],'provenance':e['provenance']} for e in entries],
        'note':'No model generation/training. Historical tokens are not fresh inference. Selection is known-development conditioning, not a natural failure rate.'}
    with (output/'prefix_build_report.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'prefix_count':len(entries),'historical_tool_turns':report['total_historical_tool_turns'],'dataset_sha256':report['dataset_sha256']}))


if __name__ == '__main__':main()
