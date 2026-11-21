"""FakeBackend/temp-archive analysis tests; no real inference or old-run writes."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_model_adapter import FakeBackend, xml
import test_prefix_run as prefix_test_helpers
from toolloop_fix.harness import state_hash
from toolloop_fix.prefix_run import prefix_metrics, run_prefix_case

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('prefix_comparison_script', ROOT / 'scripts/compare_prefix_runs.py')
COMPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARE)


def dump(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n')


def lines(path, values):
    Path(path).write_text(''.join(json.dumps(value, sort_keys=True) + '\n' for value in values))


class PrefixComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.helper = prefix_test_helpers.PrefixRuntimeTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.data = [self.helper.make_prefix('orders', 'recoverable'),
                     self.helper.make_prefix('orders', 'retry_required'),
                     self.helper.make_prefix('files', 'retry_required'),
                     self.helper.make_prefix('jobs', 'retry_required'),
                     self.helper.make_prefix('orders', 'normal', role='normal_completion_control'),
                     self.helper.make_prefix('orders', 'unrecoverable', role='unavailable_stop_control')]
        self.ids = [row['case_id'] for row in self.data]
        patterns = {('legacy', 'B0'): [0, 1, 1, 0], ('legacy', 'B1'): [1, 1, 0, 0],
                    ('legacy', 'B2'): [0, 1, 1, 0], ('explicit', 'B0'): [1, 1, 1, 0],
                    ('explicit', 'B1'): [1, 1, 1, 0], ('explicit', 'B2'): [0, 1, 0, 1]}
        self.refs = []
        for wording, arm in COMPARE.CELLS:
            root = self.root / (wording + '-' + arm)
            root.mkdir()
            cfg = self.helper.config(self.data[0], arm, wording)
            cfg.update(cases_path='data/dev/fixed_prefix_cases_iter4.jsonl', case_ids=self.ids,
                       case_seeds={cid: 1000 + 32 * i for i, cid in enumerate(self.ids)})
            rows, trajectories, contexts, generations, events = [], [], [], [], []
            for index, data in enumerate(self.data):
                successful = index >= 4 or bool(patterns[(wording, arm)][index])
                output = self.responses(data, successful)
                if arm == 'B2' and index == 0:
                    output = [xml('get_order', identifier='ORD-OLD'), 'Cannot proceed.']
                result, trace = run_prefix_case(data, FakeBackend(output), cfg,
                    emit_generation=generations.append, emit_event=events.append, emit_prefix_context=contexts.append)
                rows.append(result)
                trajectories.append(trace)
            dump(root / 'config.json', cfg)
            lines(root / 'case_results.jsonl', rows)
            lines(root / 'trajectories.jsonl', trajectories)
            lines(root / 'prefix_contexts.jsonl', contexts)
            lines(root / 'generations.jsonl', generations)
            lines(root / 'execution_events.jsonl', events)
            dump(root / 'metrics.json', prefix_metrics(rows))
            data_path = root / 'inputs' / cfg['cases_path']
            data_path.parent.mkdir(parents=True)
            lines(data_path, self.data)
            for name in ('harness.py', 'prefix_run.py'):
                path = root / 'inputs/toolloop_fix' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / 'toolloop_fix' / name).read_bytes())
            provenance = {'evidence_class': 'model_inference', 'runtime': {'executable': '/fake/python',
                'packages': {'torch': 'unit-test', 'transformers': 'unit-test'}},
                'model': cfg['model'], 'sampling': cfg['sampling'], 'chat_template_sha256': 'a' * 64,
                'effective_generation': {'temperature': 1.0, 'eos_token_id': [1, 2]},
                'actual_parameter_device': 'mps:0', 'actual_parameter_dtype': 'torch.bfloat16',
                'actual_model_class': 'FakeBackend', 'model_manifest_sha256': 'b' * 64,
                'model_files_verified': True, 'guard_version': 'guard-unit-test',
                'protocol_version': cfg['protocol_version'], 'score_version': 'score-unit-test',
                'case_seeds': cfg['case_seeds'], 'mode': cfg['mode'], 'prefix_runtime_version': 'prefix-unit-test',
                'prefix_schema_version': cfg['prefix_schema_version'], 'task_wording_version': cfg['task_wording_version'],
                'prefix_dataset_sha256': COMPARE.sha(data_path),
                'prefix_source_rows': {row['case_id']: state_hash(row) for row in self.data},
                'wording_profile': wording, 'comparison_arm': arm,
                'system_prompt_sha256': hashlib.sha256(generations[0]['messages'][0]['content'].encode()).hexdigest()}
            dump(root / 'model_provenance.json', provenance)
            manifest = {'kind': 'model_inference', 'status': 'completed', 'evidence_valid': True,
                'unit_test_fixture': True,
                'inputs': {str(path.relative_to(root / 'inputs')): COMPARE.sha(path)
                           for path in (root / 'inputs').rglob('*') if path.is_file()},
                'outputs': {path.name: COMPARE.sha(path) for path in root.iterdir() if path.is_file()}}
            dump(root / 'manifest.json', manifest)
            self.refs.append({'path': str(root), 'manifest_sha256': COMPARE.sha(root / 'manifest.json')})

    def responses(self, row, successful):
        case = row['original_case']
        if row['role'] == 'unavailable_stop_control': return [xml('stop_task', reason='RESOURCE_UNAVAILABLE')]
        if not successful: return ['Cannot proceed.']
        value, identifier = case['hidden']['value'], case['hidden']['valid_identifier']
        final = xml('finish_task', answer=value, identifier=identifier)
        if row['role'] == 'normal_completion_control': return [final]
        if case['domain'] == 'jobs':
            return [xml('poll_job', identifier=identifier)] * 2 + [xml('get_result', identifier=identifier), final]
        primary = 'get_order' if case['domain'] == 'orders' else 'read_file'
        before = [xml('find_order', target=case['request']['target'])] if case['category'] == 'recoverable' else []
        return before + [xml(primary, identifier=identifier), final]

    def cell(self, name='legacy-B1'):
        return self.root / name

    def reseal(self, cell, outputs=(), inputs=(), mutator=None):
        root = self.cell(cell)
        manifest = json.loads((root / 'manifest.json').read_text())
        for name in outputs: manifest['outputs'][name] = COMPARE.sha(root / name)
        for name in inputs: manifest['inputs'][name] = COMPARE.sha(root / 'inputs' / name)
        if mutator: mutator(manifest)
        dump(root / 'manifest.json', manifest)
        next(ref for ref in self.refs if ref['path'] == str(root))['manifest_sha256'] = COMPARE.sha(root / 'manifest.json')

    def test_six_cells_four_recovery_two_controls_and_correct_paired_results(self):
        result = COMPARE.compare(self.refs)
        self.assertEqual(len(result['cells']), 6)
        self.assertEqual(len(result['recovery_case_ids']), 4)
        base = result['cells']['legacy/B0']
        self.assertEqual(base['recovery_new_goal_evidence']['numerator'], 2)
        self.assertEqual(base['recovery_new_goal_evidence']['denominator'], 4)
        self.assertEqual(base['controls']['normal_completion_control']['goal_already_at_prefix'], 1)
        self.assertEqual(base['controls']['normal_completion_control']['new_goal_evidence']['numerator'], 0)
        within = result['paired_arms_within_wording']['legacy']['B1']['new_goal_evidence']
        self.assertEqual([within[k]['count'] for k in ('wins', 'ties', 'losses')], [1, 2, 1])
        wording = result['paired_explicit_vs_legacy']['B2']['new_goal_evidence']
        self.assertEqual([wording[k]['count'] for k in ('wins', 'ties', 'losses')], [1, 2, 1])
        self.assertNotIn(self.ids[4], json.dumps(result['paired_arms_within_wording']))
        self.assertNotIn(self.ids[5], json.dumps(result['paired_explicit_vs_legacy']))

    def test_fresh_historical_costs_and_mechanical_blocks_separate(self):
        result = COMPARE.compare(self.refs)
        cell = result['cells']['legacy/B2']
        self.assertEqual(cell['historical_input_tokens'], 18)
        self.assertEqual(cell['historical_output_tokens'], 12)
        self.assertEqual(cell['input_tokens'], 10 * cell['continuation_turns'])
        self.assertEqual(cell['output_tokens'], 5 * cell['continuation_turns'])
        self.assertEqual(cell['turns'], cell['prefix_turns'] + cell['continuation_turns'])
        self.assertEqual(cell['guard_blocked_proposals'], 1)
        self.assertEqual(cell['proposed_repeat_count'], 1)
        self.assertEqual(cell['has_invalid_loop']['numerator'], 0)

    def test_source_inventory_and_hashes_preserved_read_only(self):
        before = {str(path): COMPARE.sha(path) for path in self.root.rglob('*') if path.is_file()}
        refs = copy.deepcopy(self.refs)
        result = COMPARE.compare(self.refs)
        self.assertTrue(all(result['checks'].values()))
        self.assertEqual(self.refs, refs)
        self.assertEqual(before, {str(path): COMPARE.sha(path) for path in self.root.rglob('*') if path.is_file()})

    def test_missing_or_duplicate_cell_rejected(self):
        for refs in (self.refs[:5], self.refs + self.refs[:1]):
            with self.subTest(count=len(refs)), self.assertRaises(ValueError): COMPARE.compare(refs)

    def test_tampered_input_output_and_omitted_inventory_rejected(self):
        root = self.cell()
        (root / 'inputs/toolloop_fix/harness.py').write_text('tampered')
        with self.assertRaises(ValueError): COMPARE.compare(self.refs)
        (root / 'inputs/toolloop_fix/harness.py').write_bytes((ROOT / 'toolloop_fix/harness.py').read_bytes())
        self.reseal('legacy-B1', mutator=lambda manifest: manifest['outputs'].pop('metrics.json'))
        with self.assertRaises(ValueError): COMPARE.compare(self.refs)

    def test_actual_provenance_drift_rejected(self):
        root = self.cell()
        original = json.loads((root / 'model_provenance.json').read_text())
        for key, value in [('runtime', {'packages': {'torch': 'different'}}), ('actual_parameter_device', 'cpu'),
                           ('chat_template_sha256', 'f' * 64), ('effective_generation', {'temperature': 0.1}),
                           ('guard_version', 'other'), ('prefix_runtime_version', 'other')]:
            with self.subTest(key=key):
                changed = copy.deepcopy(original)
                changed[key] = value
                dump(root / 'model_provenance.json', changed)
                self.reseal('legacy-B1', outputs=['model_provenance.json'])
                with self.assertRaises(ValueError): COMPARE.compare(self.refs)

    def test_common_configuration_drift_rejected(self):
        root = self.cell()
        cfg = json.loads((root / 'config.json').read_text())
        cfg['max_context_tokens'] += 1
        dump(root / 'config.json', cfg)
        self.reseal('legacy-B1', outputs=['config.json'])
        with self.assertRaisesRegex(ValueError, 'common config'): COMPARE.compare(self.refs)

    def test_snapshot_history_budget_and_seed_mismatches_rejected(self):
        root = self.cell()
        original = COMPARE.jsonl(root / 'case_results.jsonl')
        for key, value in [('prefix_snapshot_sha256', 'f' * 64), ('prefix_public_history_sha256', 'f' * 64),
                           ('remaining_tool_budget_at_start', 0), ('case_seed', 999), ('prefix_turns', 7)]:
            with self.subTest(key=key):
                rows = copy.deepcopy(original)
                rows[0][key] = value
                lines(root / 'case_results.jsonl', rows)
                self.reseal('legacy-B1', outputs=['case_results.jsonl'])
                with self.assertRaises(ValueError): COMPARE.compare(self.refs)

    def test_preexisting_control_goal_cannot_become_new_recovery(self):
        root = self.cell()
        rows = COMPARE.jsonl(root / 'case_results.jsonl')
        rows[4]['new_goal_evidence'] = True
        lines(root / 'case_results.jsonl', rows)
        self.reseal('legacy-B1', outputs=['case_results.jsonl'])
        with self.assertRaisesRegex(ValueError, 'pre-existing goal'): COMPARE.compare(self.refs)

    def test_context_budget_and_source_binding_checked(self):
        root = self.cell()
        original = COMPARE.jsonl(root / 'prefix_contexts.jsonl')
        for key, value in [('remaining_tool_budget', 0), ('total_turn_budget', 99),
                           ('source_provenance', {'unbound': True})]:
            with self.subTest(key=key):
                contexts = copy.deepcopy(original)
                contexts[0][key] = value
                lines(root / 'prefix_contexts.jsonl', contexts)
                self.reseal('legacy-B1', outputs=['prefix_contexts.jsonl'])
                with self.assertRaises(ValueError): COMPARE.compare(self.refs)

    def test_fresh_tokens_and_source_historical_costs_not_interchangeable(self):
        root = self.cell()
        rows = COMPARE.jsonl(root / 'case_results.jsonl')
        rows[0]['input_tokens'] += rows[0]['historical_input_tokens']
        rows[0]['continuation_input_tokens'] = rows[0]['input_tokens']
        lines(root / 'case_results.jsonl', rows)
        self.reseal('legacy-B1', outputs=['case_results.jsonl'])
        with self.assertRaisesRegex(ValueError, 'fresh token'): COMPARE.compare(self.refs)

    def test_generated_seed_schedule_is_checked_not_only_result_seed(self):
        root = self.cell()
        generated = COMPARE.jsonl(root / 'generations.jsonl')
        generated[0]['seed'] += 1
        lines(root / 'generations.jsonl', generated)
        self.reseal('legacy-B1', outputs=['generations.jsonl'])
        with self.assertRaisesRegex(ValueError, 'seed/turn'): COMPARE.compare(self.refs)

    def test_saved_aggregate_must_match_case_rows(self):
        root = self.cell()
        value = json.loads((root / 'metrics.json').read_text())
        value['metrics']['new_goal_evidence_recovery']['numerator'] += 1
        dump(root / 'metrics.json', value)
        self.reseal('legacy-B1', outputs=['metrics.json'])
        with self.assertRaisesRegex(ValueError, 'recovery aggregates'): COMPARE.compare(self.refs)

    def test_b0_b2_prompt_and_same_wording_user_identity_checked(self):
        root = self.cell('explicit-B2')
        value = json.loads((root / 'model_provenance.json').read_text())
        value['system_prompt_sha256'] = 'f' * 64
        dump(root / 'model_provenance.json', value)
        self.reseal('explicit-B2', outputs=['model_provenance.json'])
        with self.assertRaises(ValueError): COMPARE.compare(self.refs)

    def test_markdown_has_six_friendly_rows_per_table_and_clear_boundaries(self):
        text = COMPARE.markdown(COMPARE.compare(self.refs))
        main = text.split('## 成本')[0]
        table = [line for line in main.splitlines() if line.startswith('|')]
        self.assertEqual(len(table), 8)
        self.assertTrue(all(line.count('|') == 9 for line in table))
        self.assertIn('1个已取得证据', text)
        self.assertIn('未重跑', text)
        self.assertIn('不是恢复收益', text)
        self.assertIn('不做显著性', text)
        self.assertIn('仅4个恢复前缀', text)

    def test_cli_outputs_json_markdown_and_refuses_either_existing_file(self):
        config = self.root / 'comparison_config.json'
        dump(config, {'run_dirs': self.refs})
        output = self.root / 'comparison-output'
        command = [sys.executable, str(ROOT / 'scripts/compare_prefix_runs.py'),
                   '--config', str(config), '--output', str(output)]
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads((output / 'prefix_comparison.json').read_text())['evidence_class'], 'analysis')
        before = COMPARE.sha(output / 'prefix_comparison.json')
        repeated = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertEqual(before, COMPARE.sha(output / 'prefix_comparison.json'))


if __name__ == '__main__':
    unittest.main()
