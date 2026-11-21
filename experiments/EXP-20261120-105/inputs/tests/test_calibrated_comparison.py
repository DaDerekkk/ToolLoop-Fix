"""Synthetic temporary artifacts only: no model inference or historical run edits."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('calibrated_comparison_script', ROOT / 'scripts/compare_calibrated_runs.py')
COMPARISON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARISON)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def ratio(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'value': numerator / denominator if denominator else None}


class CalibratedComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.refs = []
        ids = ['case-win', 'case-tie', 'case-loss', 'case-unavailable']
        self.seeds = {case_id: index * 16 for index, case_id in enumerate(ids)}
        self.base_config = {'evidence_class': 'model_inference', 'artifact_contract': 'model_evaluation',
            'mode': 'end_to_end', 'cases_path': 'data/dev/pilot_cases.jsonl', 'case_ids': ids,
            'case_seeds': self.seeds, 'tool_budget': 12, 'turn_budget': 16,
            'protocol_version': 'calibrated-fixture-v1',
            'model': {'model_id': 'synthetic/unit-test-double', 'revision': 'a' * 40,
                'backend': 'transformers', 'model_path': '/synthetic/not-a-real-model',
                'device': 'mps', 'dtype': 'bfloat16'},
            'sampling': {'seed': 0, 'do_sample': True, 'temperature': 1.0, 'top_p': 1.0,
                'top_k': 20, 'min_p': 0.0, 'presence_penalty': 2.0, 'repetition_penalty': 1.0,
                'max_new_tokens': 256, 'enable_thinking': False}}
        goals = {'B0': [False, True, True, False], 'B1': [True, True, False, True], 'B2': [False, True, True, True]}
        exact = {'B0': [False, True, False, False], 'B1': [True, True, False, True], 'B2': [False, False, True, True]}
        for arm in ('B0', 'B1', 'B2'):
            root = self.root / arm
            root.mkdir()
            cfg = {**copy.deepcopy(self.base_config), 'comparison_arm': arm, 'policy_name': 'fixture-' + arm}
            dump(root / 'config.json', cfg)
            rows = [{'case_id': cid, 'case_seed': self.seeds[cid], 'completable': index != 3,
                'domain': 'fixture', 'category': ['normal', 'recoverable', 'retry_required', 'unrecoverable'][index],
                'template_group': cid + '-template', 'evidence_class': 'model_inference', 'comparison_arm': arm,
                'tool_goal_attainment': goals[arm][index], 'exact_grounded_structured_completion': exact[arm][index]}
                for index, cid in enumerate(ids)]
            self.write_rows(root, rows)
            metrics = {key: ratio(0, 4) for key in (
                'natural_termination_semantics_unknown', 'unsupported_finish_wrapper',
                'stopped_after_public_unavailable', 'exact_stop_reason_code', 'has_invalid_loop', 'budget_exhausted')}
            metrics.update(tool_goal_attainment=ratio(sum(goals[arm][:3]), 3),
                exact_grounded_structured_completion=ratio(sum(exact[arm][:3]), 3),
                normal_tool_goal_attainment=ratio(int(goals[arm][0]), 1),
                retry_required_tool_goal_attainment=ratio(int(goals[arm][2]), 1))
            metrics.update({key: 4 for key in ('proposed_repeat_count', 'guard_blocked_proposals', 'turns',
                'actual_tool_attempts', 'proposal_attempts', 'parser_error_turns', 'input_tokens', 'output_tokens')})
            dump(root / 'metrics.json', {'evidence_class': 'model_inference', 'comparison_arm': arm, 'metrics': metrics})
            provenance = {'evidence_class': 'model_inference', 'model': cfg['model'], 'sampling': cfg['sampling'],
                'runtime': {'executable': '/synthetic/venv/bin/python', 'python_version': '3.12.fixture',
                    'packages': {'torch': 'fixture-1', 'transformers': 'fixture-1'},
                    'pytorch_enable_mps_fallback': '0', 'mps_is_built': True, 'mps_is_available': True},
                'chat_template_sha256': 'b' * 64, 'model_manifest_sha256': 'c' * 64,
                'model_files_verified': True, 'effective_generation': {'temperature': 1.0, 'eos_token_id': [1, 2]},
                'actual_parameter_device': 'mps:0', 'actual_parameter_dtype': 'torch.bfloat16',
                'actual_model_class': 'FixtureCausalLM', 'guard_version': 'guard-fixture-v2',
                'protocol_version': 'calibrated-fixture-v1', 'score_version': 'score-fixture-v1',
                'case_seeds': self.seeds, 'comparison_arm': arm,
                'system_prompt_sha256': ('e' if arm == 'B1' else 'd') * 64}
            dump(root / 'model_provenance.json', provenance)
            for name, content in {'toolloop_fix/calibrated_run.py': '# frozen synthetic test runtime\n',
                                  'data/dev/pilot_cases.jsonl': '{"unit_test_fixture": true}\n'}.items():
                path = root / 'inputs' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            manifest = {'schema_version': 3, 'status': 'completed', 'evidence_valid': True,
                'inputs': {str(path.relative_to(root / 'inputs')): sha(path)
                           for path in (root / 'inputs').rglob('*') if path.is_file()},
                'outputs': {name: sha(root / name) for name in (
                    'config.json', 'case_results.jsonl', 'metrics.json', 'model_provenance.json')}}
            dump(root / 'manifest.json', manifest)
            self.refs.append({'path': str(root), 'manifest_sha256': sha(root / 'manifest.json')})

    def source(self, arm):
        return self.root / arm

    def write_rows(self, root, rows):
        (root / 'case_results.jsonl').write_text(''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows))

    def reseal(self, arm, outputs=(), inputs=(), manifest_mutator=None):
        root = self.source(arm)
        manifest = json.loads((root / 'manifest.json').read_text())
        for name in outputs:
            manifest['outputs'][name] = sha(root / name)
        for name in inputs:
            manifest['inputs'][name] = sha(root / 'inputs' / name)
        if manifest_mutator:
            manifest_mutator(manifest)
        dump(root / 'manifest.json', manifest)
        next(ref for ref in self.refs if ref['path'] == str(root))['manifest_sha256'] = sha(root / 'manifest.json')

    def test_paired_wins_ties_losses_and_unreachable_exclusion(self):
        result = COMPARISON.compare(self.refs)
        goal = result['paired_against_B0']['B1']['tool_goal_attainment']
        self.assertEqual(goal, {'wins': {'count': 1, 'case_ids': ['case-win']},
            'ties': {'count': 1, 'case_ids': ['case-tie']}, 'losses': {'count': 1, 'case_ids': ['case-loss']}})
        exact = result['paired_against_B0']['B2']['exact_grounded_structured_completion']
        self.assertEqual(exact['wins']['case_ids'], ['case-loss'])
        self.assertEqual(exact['ties']['case_ids'], ['case-win'])
        self.assertEqual(exact['losses']['case_ids'], ['case-tie'])
        self.assertNotIn('case-unavailable', json.dumps(result['paired_against_B0']))
        self.assertEqual(result['evidence_class'], 'analysis')
        self.assertTrue(all(result['checks'].values()))

    def test_comparison_is_read_only_and_preserves_source_hash_references(self):
        before = {str(path): sha(path) for path in self.root.rglob('*') if path.is_file()}
        refs = copy.deepcopy(self.refs)
        result = COMPARISON.compare(self.refs)
        self.assertEqual(self.refs, refs)
        self.assertEqual({str(path): sha(path) for path in self.root.rglob('*') if path.is_file()}, before)
        self.assertEqual(result['source_runs']['B0'], refs[0])

    def test_all_three_unique_arms_required(self):
        with self.assertRaises(ValueError):
            COMPARISON.compare(self.refs[:2])
        with self.assertRaises(ValueError):
            COMPARISON.compare(self.refs + [self.refs[0]])

    def test_manifest_and_output_tampering_rejected(self):
        root = self.source('B1')
        (root / 'metrics.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'source output changed'):
            COMPARISON.compare(self.refs)
        (root / 'manifest.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            COMPARISON.compare(self.refs)

    def test_failed_or_invalid_source_run_rejected(self):
        for field, value in (('status', 'failed'), ('evidence_valid', False)):
            with self.subTest(field=field):
                self.reseal('B1', manifest_mutator=lambda manifest: manifest.update(
                    {'status': 'completed', 'evidence_valid': True, field: value}))
                with self.assertRaises(ValueError):
                    COMPARISON.compare(self.refs)

    def test_unpaired_common_config_rejected(self):
        root = self.source('B1')
        cfg = json.loads((root / 'config.json').read_text())
        cfg['sampling']['temperature'] = 0.5
        dump(root / 'config.json', cfg)
        self.reseal('B1', outputs=['config.json'])
        with self.assertRaisesRegex(ValueError, 'unpaired config'):
            COMPARISON.compare(self.refs)

    def test_result_order_and_case_seed_mismatch_rejected(self):
        root = self.source('B1')
        rows = [json.loads(line) for line in (root / 'case_results.jsonl').read_text().splitlines()]
        self.write_rows(root, list(reversed(rows)))
        self.reseal('B1', outputs=['case_results.jsonl'])
        with self.assertRaisesRegex(ValueError, 'reordered'):
            COMPARISON.compare(self.refs)
        rows[0]['case_seed'] += 1
        self.write_rows(root, rows)
        self.reseal('B1', outputs=['case_results.jsonl'])
        with self.assertRaisesRegex(ValueError, 'seed mismatch'):
            COMPARISON.compare(self.refs)

    def test_changed_declared_inference_code_hash_rejected(self):
        name = 'toolloop_fix/calibrated_run.py'
        (self.source('B1') / 'inputs' / name).write_text('# different runtime\n')
        self.reseal('B1', inputs=[name])
        with self.assertRaisesRegex(ValueError, 'inference code changed'):
            COMPARISON.compare(self.refs)

    def test_tampered_frozen_code_is_verified_not_only_compared_by_claimed_hash(self):
        (self.source('B1') / 'inputs/toolloop_fix/calibrated_run.py').write_text('# unrecorded tampering\n')
        with self.assertRaises(ValueError):
            COMPARISON.compare(self.refs)

    def test_changed_dataset_hash_rejected_even_when_path_and_config_match(self):
        name = 'data/dev/pilot_cases.jsonl'
        (self.source('B1') / 'inputs' / name).write_text('{"different_fixture":true}\n')
        self.reseal('B1', inputs=[name])
        with self.assertRaises(ValueError):
            COMPARISON.compare(self.refs)

    def test_read_artifacts_must_be_in_output_hash_inventory(self):
        self.reseal('B1', manifest_mutator=lambda manifest: manifest['outputs'].pop('metrics.json'))
        with self.assertRaises(ValueError):
            COMPARISON.compare(self.refs)

    def test_actual_provenance_must_match_across_arms_not_just_config(self):
        root = self.source('B1')
        original = json.loads((root / 'model_provenance.json').read_text())
        changes = [('runtime', {'executable': '/different/python', 'packages': {'torch': 'different'}}),
            ('model', {**original['model'], 'revision': 'f' * 40}),
            ('sampling', {**original['sampling'], 'temperature': 0.1}),
            ('chat_template_sha256', 'f' * 64), ('effective_generation', {'temperature': 0.1}),
            ('actual_parameter_device', 'cpu'), ('actual_parameter_dtype', 'torch.float32'),
            ('model_manifest_sha256', 'f' * 64), ('guard_version', 'different-guard'),
            ('protocol_version', 'different-protocol'), ('score_version', 'different-score'),
            ('case_seeds', {**self.seeds, 'case-win': 999})]
        for field, value in changes:
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                changed[field] = value
                dump(root / 'model_provenance.json', changed)
                self.reseal('B1', outputs=['model_provenance.json'])
                with self.assertRaises(ValueError):
                    COMPARISON.compare(self.refs)

    def test_b0_and_b2_system_prompts_must_match_but_b1_can_differ(self):
        COMPARISON.compare(self.refs)  # setup deliberately gives B1 a different prompt
        root = self.source('B2')
        value = json.loads((root / 'model_provenance.json').read_text())
        value['system_prompt_sha256'] = 'f' * 64
        dump(root / 'model_provenance.json', value)
        self.reseal('B2', outputs=['model_provenance.json'])
        with self.assertRaises(ValueError):
            COMPARISON.compare(self.refs)

    def test_completable_and_case_metadata_must_not_shift_between_arms(self):
        root = self.source('B1')
        original = [json.loads(line) for line in (root / 'case_results.jsonl').read_text().splitlines()]
        for field, value in (('completable', False), ('category', 'unrecoverable'), ('template_group', 'new-template')):
            with self.subTest(field=field):
                rows = copy.deepcopy(original)
                rows[0][field] = value
                self.write_rows(root, rows)
                self.reseal('B1', outputs=['case_results.jsonl'])
                with self.assertRaises(ValueError):
                    COMPARISON.compare(self.refs)

    def test_markdown_table_and_paired_counts_are_well_formed(self):
        result = COMPARISON.compare(self.refs)
        text = COMPARISON.markdown(result)
        self.assertTrue(text.startswith('# ITER-003'))
        self.assertIn('| 指标 | B0 | B1 通用提示 | B2 公开历史 guard |', text)
        self.assertIn('B1 tool_goal_attainment: 1/1/1', text)
        self.assertIn('B2 exact_grounded_structured_completion: 1/1/1', text)
        table_lines = [line for line in text.splitlines() if line.startswith('|')]
        self.assertEqual(len(table_lines), 20)
        self.assertTrue(all(line.count('|') == 5 for line in table_lines))
        self.assertIn('不是任务完成率', text)
        self.assertIn('训练继续 hold', text)

    def test_zero_denominator_markdown_is_na_not_zero_over_zero(self):
        result = COMPARISON.compare(self.refs)
        for arm in ('B0', 'B1', 'B2'):
            result['metrics_by_arm'][arm]['exact_stop_reason_code'] = ratio(0, 0)
        text = COMPARISON.markdown(result)
        stop_line = next(line for line in text.splitlines() if '精确停止原因码' in line)
        self.assertIn('N/A', stop_line)
        self.assertNotIn('0/0', stop_line)

    def test_cli_writes_readable_json_markdown_and_refuses_overwrite(self):
        cfg = self.root / 'comparison_config.json'
        dump(cfg, {'run_dirs': self.refs})
        output = self.root / 'output'
        output.mkdir()
        command = [sys.executable, str(ROOT / 'scripts/compare_calibrated_runs.py'),
                   '--config', str(cfg), '--output', str(output)]
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads((output / 'comparison.json').read_text())['evidence_class'], 'analysis')
        self.assertIn('# ITER-003', (output / 'comparison.md').read_text())
        repeated = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(repeated.returncode, 0)


if __name__ == '__main__':
    unittest.main()
