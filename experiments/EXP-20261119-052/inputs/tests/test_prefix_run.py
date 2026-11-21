"""No inference: synthetic source archives exercise authentic-prefix validation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from toolloop_fix.baselines import prompt_for
from toolloop_fix.calibrated_protocol import parse_calibrated_output
from toolloop_fix.harness import Environment, state_hash
from toolloop_fix.model_backend import sha256_file
from toolloop_fix.prefix_run import (EXPLICIT_TASK_PREFIX, PREFIX_SCHEMA_VERSION, TASK_WORDING_VERSION,
    prefix_metrics, prepare_prefix, run_prefix_case, validate_config)
from test_model_adapter import FakeBackend, xml

ROOT = Path(__file__).resolve().parents[1]
CASES = [json.loads(line) for line in (ROOT / 'data/dev/pilot_cases.jsonl').read_text().splitlines()]


class PrefixRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_prefix(self, domain='orders', category='recoverable', calls=None, role='recovery'):
        case = copy.deepcopy(next(case for case in CASES if case['domain'] == domain and case['category'] == category))
        primary = {'orders': 'get_order', 'files': 'read_file', 'jobs': 'get_result'}[domain]
        if calls is None:
            calls = [xml(primary, identifier=case['request']['identifier'])]
        env = Environment(case)
        generations, events = [], []
        for turn, raw in enumerate(calls, 1):
            decision = parse_calibrated_output(raw)
            before = env.snapshot()
            event = env.execute(decision['action'])
            event.update(turn=turn, guard_blocked=False, executed=True, actual_tool_dispatch=True,
                snapshot_before_sha256=state_hash(before), snapshot_after_sha256=state_hash(env.snapshot()))
            events.append(event)
            generations.append({'evidence_class': 'model_inference', 'case_id': case['case_id'], 'turn': turn,
                'raw_text': raw, 'token_limit_reached': False, 'parse': decision,
                'input_tokens': 3, 'output_tokens': 2, 'prompt_token_ids': [1, 2, 3],
                'generated_token_ids': [4, 5], 'elapsed_seconds': 0.5,
                'messages': [{'role': 'system', 'content': 'PRIVATE_SOURCE_SYSTEM_MUST_NOT_BE_REUSED'}]})
        source = self.root / ('source-' + str(len(list(self.root.iterdir()))))
        source.mkdir()
        cases_path = source / 'inputs/data/dev/pilot_cases.jsonl'
        cases_path.parent.mkdir(parents=True)
        cases_path.write_text(json.dumps(case) + '\n')
        harness_path = source / 'inputs/toolloop_fix/harness.py'
        harness_path.parent.mkdir(parents=True)
        harness_path.write_bytes((ROOT / 'toolloop_fix/harness.py').read_bytes())
        (source / 'model_provenance.json').write_text(json.dumps({'evidence_class': 'model_inference', 'unit_test_double': True}))
        row = {'case_id': 'prefix-' + domain + '-' + category, 'prefix_schema_version': PREFIX_SCHEMA_VERSION,
            'role': role, 'original_case': case, 'prefix_generations': generations,
            'prefix_events': events, 'prefix_snapshot': env.snapshot(),
            'provenance': {'source_run': str(source), 'source_case_id': case['case_id'],
                'source_generation_lines_1based': list(range(1, len(calls) + 1)),
                'source_event_indices_1based': list(range(1, len(calls) + 1)),
                'source_trajectory_line_1based': 1, 'source_prefix_turns': len(calls),
                'source_cases_path': str(cases_path), 'source_case_sha256': state_hash(case),
                'transplant_note': 'Synthetic unit-test archive, not actual model data.'}}
        self.reseal_source(row)
        return row

    def reseal_source(self, row, kind='model_inference'):
        source = Path(row['provenance']['source_run'])
        (source / 'generations.jsonl').write_text(''.join(json.dumps(item) + '\n' for item in row['prefix_generations']))
        (source / 'trajectories.jsonl').write_text(json.dumps({'case_id': row['original_case']['case_id'],
            'evidence_class': 'model_inference', 'events': row['prefix_events']}) + '\n')
        cases_path = Path(row['provenance']['source_cases_path'])
        outputs = {name: sha256_file(source / name) for name in ('generations.jsonl', 'trajectories.jsonl', 'model_provenance.json')}
        manifest = {'kind': kind, 'status': 'completed', 'evidence_valid': True,
            'outputs': outputs, 'inputs': {'data/dev/pilot_cases.jsonl': sha256_file(cases_path),
                'toolloop_fix/harness.py': sha256_file(source / 'inputs/toolloop_fix/harness.py')}}
        (source / 'manifest.json').write_text(json.dumps(manifest))
        row['provenance'].update(source_manifest_sha256=sha256_file(source / 'manifest.json'),
            source_generations_sha256=outputs['generations.jsonl'], source_trajectories_sha256=outputs['trajectories.jsonl'],
            source_cases_sha256=sha256_file(cases_path))

    def config(self, row, arm='B0', wording='legacy'):
        cfg = json.loads((ROOT / 'configs/calibrated_b0.json').read_text())
        cfg.update(mode='fixed_failure_prefix', prefix_schema_version=PREFIX_SCHEMA_VERSION,
            task_wording_version=TASK_WORDING_VERSION, wording_profile=wording, comparison_arm=arm,
            policy_name='prefix-fixture-' + arm, case_ids=[row['case_id']], case_seeds={row['case_id']: 100})
        return cfg

    def test_prepare_replays_original_prefix_and_keeps_source_prompt_private(self):
        row = self.make_prefix()
        original = copy.deepcopy(row)
        prepared = prepare_prefix(row, self.config(row))
        self.assertEqual(prepared['env'].snapshot(), row['prefix_snapshot'])
        self.assertEqual(prepared['context']['prefix_snapshot_sha256'], state_hash(row['prefix_snapshot']))
        self.assertEqual(prepared['context']['historical_input_tokens'], 3)
        self.assertEqual(prepared['context']['historical_output_tokens'], 2)
        self.assertEqual(prepared['context']['prefix_fresh_generations'], 0)
        self.assertNotIn('PRIVATE_SOURCE_SYSTEM', json.dumps(prepared['messages']))
        self.assertNotIn('hidden', json.dumps(prepared['messages']))
        self.assertNotIn('shipped', json.dumps(prepared['messages']))
        self.assertEqual(row, original)

    def test_explicit_wording_changes_only_initial_user_content(self):
        row = self.make_prefix()
        legacy = prepare_prefix(row, self.config(row))
        explicit = prepare_prefix(row, self.config(row, wording='explicit'))
        self.assertEqual(legacy['messages'][0], explicit['messages'][0])
        self.assertEqual(legacy['messages'][2:], explicit['messages'][2:])
        self.assertEqual(legacy['tools'], explicit['tools'])
        self.assertEqual(legacy['env'].snapshot(), explicit['env'].snapshot())
        self.assertEqual(explicit['messages'][1]['content'], EXPLICIT_TASK_PREFIX +
                         json.dumps(row['original_case']['request'], ensure_ascii=False))
        self.assertEqual(legacy['context']['prefix_public_history_sha256'], explicit['context']['prefix_public_history_sha256'])
        self.assertNotIn('objective', row['original_case']['request'])

    def test_b0_b2_same_dialogue_and_b1_only_additive_guidance(self):
        row = self.make_prefix()
        prepared = [prepare_prefix(row, self.config(row, arm)) for arm in ('B0', 'B1', 'B2')]
        self.assertEqual(prepared[0]['messages'], prepared[2]['messages'])
        self.assertEqual(prepared[0]['messages'][1:], prepared[1]['messages'][1:])
        self.assertTrue(prepared[1]['messages'][0]['content'].endswith(prompt_for('B1')))
        self.assertEqual(len({p['context']['prefix_snapshot_sha256'] for p in prepared}), 1)

    def test_source_hash_tampering_rejected_before_any_generation(self):
        row = self.make_prefix()
        Path(row['provenance']['source_run'], 'generations.jsonl').write_text('{}\n')
        with self.assertRaisesRegex(ValueError, 'artifact hash mismatch'):
            prepare_prefix(row, self.config(row))

    def test_embedded_raw_record_cannot_differ_from_source(self):
        row = self.make_prefix()
        row['prefix_generations'][0]['raw_text'] = xml('find_order', target='latest order')
        with self.assertRaisesRegex(ValueError, 'embedded prefix differs'):
            prepare_prefix(row, self.config(row))

    def test_supplied_snapshot_not_trusted(self):
        row = self.make_prefix()
        row['prefix_snapshot']['state']['value'] = 'tampered'
        with self.assertRaisesRegex(ValueError, 'snapshot differs'):
            prepare_prefix(row, self.config(row))

    def test_replay_action_response_and_business_hash_must_match(self):
        for field, value in (('response', {'code': 'OK'}), ('state_after', 'f' * 64)):
            with self.subTest(field=field):
                row = self.make_prefix()
                row['prefix_events'][0][field] = value
                self.reseal_source(row)
                with self.assertRaisesRegex(ValueError, 'replay mismatch'):
                    prepare_prefix(row, self.config(row))

    def test_raw_action_mismatch_rejected_even_when_source_hashes_updated(self):
        row = self.make_prefix()
        row['prefix_generations'][0]['raw_text'] = xml('get_order', identifier='different')
        self.reseal_source(row)
        with self.assertRaisesRegex(ValueError, 'generation/action mismatch'):
            prepare_prefix(row, self.config(row))

    def test_prefix_blocks_invalid_or_terminal_records_rejected(self):
        for mutation in ('blocked', 'invalid', 'terminal', 'truncated'):
            with self.subTest(mutation=mutation):
                row = self.make_prefix()
                if mutation == 'blocked': row['prefix_events'][0]['guard_blocked'] = True
                elif mutation == 'invalid': row['prefix_generations'][0]['raw_text'] = '<tool_call>'
                elif mutation == 'terminal': row['prefix_generations'][0]['raw_text'] = 'Done.'
                else: row['prefix_generations'][0]['token_limit_reached'] = True
                self.reseal_source(row)
                with self.assertRaisesRegex(ValueError, 'only valid actually executed'):
                    prepare_prefix(row, self.config(row))

    def test_source_must_be_model_inference_not_synthetic_claim(self):
        row = self.make_prefix()
        self.reseal_source(row, kind='synthetic_harness')
        with self.assertRaisesRegex(ValueError, 'originate in model_inference'):
            prepare_prefix(row, self.config(row))

    def test_historical_token_counts_checked_against_actual_recorded_ids(self):
        for field in ('input_tokens', 'output_tokens'):
            with self.subTest(field=field):
                row = self.make_prefix()
                row['prefix_generations'][0][field] = 999
                self.reseal_source(row)
                with self.assertRaisesRegex(ValueError, 'token accounting mismatch'):
                    prepare_prefix(row, self.config(row))

    def test_original_case_bound_to_source_fixture_and_hash(self):
        row = self.make_prefix()
        row['original_case']['hidden']['value'] = 'altered'
        with self.assertRaisesRegex(ValueError, 'pinned source fixture'):
            prepare_prefix(row, self.config(row))

    def test_noncontiguous_prefix_indices_rejected(self):
        row = self.make_prefix(calls=[xml('get_order', identifier='ORD-OLD')] * 2)
        row['provenance']['source_event_indices_1based'] = [1, 3]
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            prepare_prefix(row, self.config(row))

    def test_prefix_budget_consumed_and_continuation_seed_offset(self):
        row = self.make_prefix(category='retry_required')
        cfg = self.config(row)
        backend = FakeBackend([xml('get_order', identifier='ORD-101'),
                               xml('finish_task', answer='shipped', identifier='ORD-101')])
        generations, contexts = [], []
        result, trace = run_prefix_case(row, backend, cfg, emit_generation=generations.append,
                                       emit_prefix_context=contexts.append)
        self.assertTrue(result['new_goal_evidence'])
        self.assertTrue(result['exact_grounded_structured_completion'])
        self.assertEqual(result['prefix_turns'], 1)
        self.assertEqual(result['continuation_turns'], 2)
        self.assertEqual(result['turns'], 3)
        self.assertEqual(result['proposal_attempts'], 2)
        self.assertEqual(result['continuation_proposal_attempts'], 1)
        self.assertEqual(result['actual_tool_attempts'], 2)
        self.assertEqual([request['seed'] for request in backend.requests], [101, 102])
        self.assertEqual([generation['continuation_turn'] for generation in generations], [1, 2])
        self.assertEqual(result['input_tokens'], 20)
        self.assertEqual(result['historical_input_tokens'], 3)
        self.assertEqual(len(contexts), 1)
        self.assertEqual(trace['events'][0]['phase'], 'historical_prefix')

    def test_equal_budget_prefix_has_zero_fresh_generation_and_exhausted_result(self):
        row = self.make_prefix()
        for budget in ('tool_budget', 'turn_budget'):
            with self.subTest(budget=budget):
                cfg = self.config(row)
                cfg[budget] = 1
                backend = FakeBackend([])
                result, _ = run_prefix_case(row, backend, cfg)
                self.assertEqual(backend.requests, [])
                self.assertEqual(result['continuation_turns'], 0)
                self.assertEqual(result['input_tokens'], 0)
                self.assertEqual(result['output_tokens'], 0)
                self.assertTrue(result['budget_exhausted'])
                self.assertFalse(result['new_goal_evidence'])

    def test_overbudget_prefix_rejected(self):
        row = self.make_prefix(calls=[xml('get_order', identifier='ORD-OLD')] * 3)
        for budget in ('tool_budget', 'turn_budget'):
            cfg = self.config(row)
            cfg[budget] = 2
            with self.subTest(budget=budget), self.assertRaisesRegex(ValueError, 'exceeds total'):
                prepare_prefix(row, cfg)

    def test_b2_blocked_boundary_proposal_preserves_prefix_and_charges_total_budget(self):
        row = self.make_prefix()
        cfg = self.config(row, 'B2')
        cfg['tool_budget'] = 3
        backend = FakeBackend([xml('get_order', identifier='ORD-OLD')] * 2)
        result, trace = run_prefix_case(row, backend, cfg)
        self.assertEqual(result['guard_blocked_proposals'], 2)
        self.assertEqual(result['proposed_repeat_count'], 2)
        self.assertEqual(result['actual_tool_attempts'], 1)
        self.assertEqual(result['proposal_attempts'], 3)
        self.assertEqual(result['continuation_actual_tool_attempts'], 0)
        self.assertFalse(result['has_invalid_loop'])
        self.assertTrue(result['budget_exhausted'])
        self.assertEqual(trace['final_snapshot'], row['prefix_snapshot'])
        for event in trace['events'][1:]:
            self.assertEqual(event['snapshot_before_sha256'], event['snapshot_after_sha256'])
            self.assertFalse(event['executed'])
        self.assertIn('not a tool result', backend.requests[-1]['messages'][-1]['content'])

    def test_inherited_loops_not_counted_as_new_recovery_failure(self):
        row = self.make_prefix(calls=[xml('get_order', identifier='ORD-OLD')] * 3)
        backend = FakeBackend([xml('find_order', target='latest order'), xml('get_order', identifier='ORD-101'),
                               xml('finish_task', answer='shipped', identifier='ORD-101')])
        result, trace = run_prefix_case(row, backend, self.config(row))
        self.assertTrue(result['has_inherited_loop'])
        self.assertFalse(result['has_invalid_loop'])
        self.assertEqual([flag['index'] for flag in trace['prefix_loop_flags']], [1, 2])
        self.assertEqual(trace['continuation_loop_flags'], [])
        self.assertTrue(result['new_goal_evidence'])

    def test_new_loop_across_prefix_boundary_is_counted(self):
        row = self.make_prefix()
        backend = FakeBackend([xml('get_order', identifier='ORD-OLD'), 'Cannot continue.'])
        result, trace = run_prefix_case(row, backend, self.config(row))
        self.assertTrue(result['has_invalid_loop'])
        self.assertFalse(result['has_inherited_loop'])
        self.assertEqual(trace['continuation_loop_flags'][0]['index'], 1)

    def test_prefix_snapshot_reused_for_independent_branches_without_mutation(self):
        row = self.make_prefix(category='retry_required')
        cfg = self.config(row)
        prepared = prepare_prefix(row, cfg)
        before = copy.deepcopy(prepared['context'])
        before_env = prepared['env'].snapshot()
        outputs = [xml('get_order', identifier='ORD-101'), xml('finish_task', answer='shipped', identifier='ORD-101')]
        first = run_prefix_case(row, FakeBackend(outputs), cfg, prepared=prepared)
        second = run_prefix_case(row, FakeBackend(outputs), cfg, prepared=prepared)
        self.assertEqual(first, second)
        self.assertEqual(prepared['env'].snapshot(), before_env)
        self.assertEqual(prepared['context'], before)

    def test_prepared_context_cannot_be_reused_with_different_budgets(self):
        row = self.make_prefix()
        cfg = self.config(row)
        prepared = prepare_prefix(row, cfg)
        for field in ('tool_budget', 'turn_budget'):
            with self.subTest(field=field):
                changed = copy.deepcopy(cfg)
                changed[field] += 1
                with self.assertRaises(ValueError):
                    run_prefix_case(row, FakeBackend(['Cannot proceed.']), changed, prepared=prepared)

    def test_source_and_current_environment_code_must_match(self):
        row = self.make_prefix()
        source = Path(row['provenance']['source_run'])
        (source / 'inputs/toolloop_fix/harness.py').write_text('# different historical environment\n')
        self.reseal_source(row)
        with self.assertRaisesRegex(ValueError, 'environment code'):
            prepare_prefix(row, self.config(row))

    def test_goal_already_at_prefix_is_completion_control_not_recovery(self):
        row = self.make_prefix(category='normal', role='normal_completion_control')
        result, _ = run_prefix_case(row, FakeBackend([
            xml('finish_task', answer='shipped', identifier='ORD-101')]), self.config(row))
        self.assertTrue(result['goal_at_prefix'])
        self.assertTrue(result['tool_goal_attainment'])
        self.assertTrue(result['exact_grounded_structured_completion'])
        self.assertFalse(result['new_goal_evidence'])
        self.assertFalse(result['recovery_eligible'])
        metrics = prefix_metrics([result])['metrics']
        self.assertEqual(metrics['new_goal_evidence_recovery']['denominator'], 0)
        self.assertIsNone(metrics['new_goal_evidence_recovery']['value'])
        self.assertEqual(metrics['normal_completion_control']['case_count'], 1)

    def test_unavailable_control_stops_without_fabricated_success(self):
        row = self.make_prefix(category='unrecoverable', role='unavailable_stop_control')
        result, _ = run_prefix_case(row, FakeBackend([xml('stop_task', reason='RESOURCE_UNAVAILABLE')]), self.config(row))
        self.assertTrue(result['stopped_after_public_unavailable'])
        self.assertTrue(result['exact_stop_reason_code'])
        self.assertFalse(result['tool_goal_attainment'])
        self.assertFalse(result['recovery_eligible'])

    def test_native_final_semantics_remain_unknown(self):
        row = self.make_prefix(category='normal', role='normal_completion_control')
        result, _ = run_prefix_case(row, FakeBackend(['The resource result is shipped.']), self.config(row))
        self.assertTrue(result['tool_goal_attainment'])
        self.assertFalse(result['exact_grounded_structured_completion'])
        self.assertTrue(result['natural_termination_semantics_unknown'])

    def test_generation_error_retains_partial_and_no_fallback(self):
        row = self.make_prefix()
        records, partials = [], []
        backend = FakeBackend([RuntimeError('prefix backend failure')])
        with self.assertRaisesRegex(RuntimeError, 'prefix backend failure'):
            run_prefix_case(row, backend, self.config(row), emit_generation=records.append, emit_partial=partials.append)
        self.assertEqual(len(backend.requests), 1)
        self.assertEqual(records[0]['turn'], 2)
        self.assertIsNone(records[0]['output_tokens'])
        self.assertEqual(partials[0]['state'], row['prefix_snapshot'])

    def test_invalid_and_blocked_outputs_both_consume_remaining_budget(self):
        row = self.make_prefix()
        cfg = self.config(row, 'B2')
        cfg['tool_budget'] = 3
        result, _ = run_prefix_case(row, FakeBackend(['<tool_call>', xml('get_order', identifier='ORD-OLD')]), cfg)
        self.assertEqual(result['proposal_attempts'], 3)
        self.assertEqual(result['parser_error_turns'], 1)
        self.assertEqual(result['guard_blocked_proposals'], 1)
        self.assertTrue(result['budget_exhausted'])

    def test_truncated_generation_ends_continuation_without_tool_execution(self):
        row = self.make_prefix()
        result, trace = run_prefix_case(row, FakeBackend([xml('get_order', identifier='ORD-101')], True), self.config(row))
        self.assertTrue(result['output_token_limit_reached'])
        self.assertEqual(result['continuation_actual_tool_attempts'], 0)
        self.assertFalse(result['new_goal_evidence'])
        self.assertEqual(trace['final_snapshot']['state'], row['prefix_snapshot']['state'])

    def test_jobs_legal_polling_after_prefix_not_blocked(self):
        row = self.make_prefix('jobs', 'retry_required')
        outputs = [xml('poll_job', identifier='JOB-101'), xml('poll_job', identifier='JOB-101'),
                   xml('get_result', identifier='JOB-101'),
                   xml('finish_task', answer='processed 25 records', identifier='JOB-101')]
        result, _ = run_prefix_case(row, FakeBackend(outputs), self.config(row, 'B2'))
        self.assertTrue(result['new_goal_evidence'])
        self.assertTrue(result['exact_grounded_structured_completion'])
        self.assertEqual(result['guard_blocked_proposals'], 0)

    def test_config_mode_wording_versions_and_seed_map_checked(self):
        row = self.make_prefix()
        cfg = self.config(row)
        validate_config(cfg)
        for key, value in (('mode', 'end_to_end'), ('wording_profile', 'unknown'),
                           ('prefix_schema_version', 'other'), ('task_wording_version', 'other')):
            with self.subTest(key=key):
                changed = copy.deepcopy(cfg)
                changed[key] = value
                with self.assertRaises(ValueError):
                    validate_config(changed)


if __name__ == '__main__':
    unittest.main()
