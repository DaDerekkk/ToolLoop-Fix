import copy
import json
from pathlib import Path
import unittest

from toolloop_fix.calibrated_run import calibrated_metrics, dialogue_text, run_calibrated_case, validate_config
from test_model_adapter import FakeBackend, fixture, xml

ROOT = Path(__file__).resolve().parents[1]


def config(arm='B0'):
    return json.loads((ROOT / ('configs/calibrated_' + arm.lower() + '.json')).read_text())


class CalibratedRunnerTests(unittest.TestCase):
    def test_native_terminal_is_one_turn_not_error_or_tool(self):
        backend = FakeBackend(['The order is shipped.'])
        result, trace = run_calibrated_case(fixture(), backend, config())
        self.assertEqual(result['turns'], 1)
        self.assertEqual(result['actual_tool_attempts'], 0)
        self.assertEqual(result['proposal_attempts'], 0)
        self.assertFalse(result['tool_goal_attainment'])
        self.assertTrue(result['natural_termination_semantics_unknown'])
        self.assertEqual(result['parser_error_turns'], 0)
        event = trace['events'][0]
        self.assertEqual(event['event_kind'], 'internal_terminal_lifecycle')
        self.assertFalse(event['actual_tool_dispatch'])
        self.assertFalse(event['model_generated_terminal_tool'])

    def test_obtained_evidence_with_native_answer_not_structured_completion(self):
        result, _ = run_calibrated_case(fixture(), FakeBackend([
            xml('get_order', identifier='ORD-101'), 'The order has shipped.']), config())
        self.assertTrue(result['tool_goal_attainment'])
        self.assertFalse(result['exact_grounded_structured_completion'])
        self.assertTrue(result['natural_termination_semantics_unknown'])

    def test_grounded_finish(self):
        result, trace = run_calibrated_case(fixture(), FakeBackend([
            xml('get_order', identifier='ORD-101'), xml('finish_task', identifier='ORD-101', answer='shipped')]), config())
        self.assertTrue(result['exact_grounded_structured_completion'])
        self.assertEqual(result['actual_tool_attempts'], 1)
        self.assertEqual(result['proposal_attempts'], 1)
        self.assertTrue(trace['events'][-1]['model_generated_terminal_tool'])

    def test_b2_repeated_blocks_do_not_mutate_history_and_charge_budget(self):
        call = xml('get_order', identifier='bad')
        cfg = config('B2'); cfg['tool_budget'] = 3
        backend = FakeBackend([call] * 3)
        result, trace = run_calibrated_case(fixture('recoverable'), backend, cfg)
        self.assertEqual(result['guard_blocked_proposals'], 2)
        self.assertEqual(result['proposed_repeat_count'], 2)
        self.assertEqual(result['actual_tool_attempts'], 1)
        self.assertEqual(result['proposal_attempts'], 3)
        self.assertTrue(result['budget_exhausted'])
        self.assertFalse(result['has_invalid_loop'])
        self.assertEqual(len(trace['final_snapshot']['history']), 1)
        for event in trace['events'][1:]:
            self.assertEqual(event['snapshot_before_sha256'], event['snapshot_after_sha256'])
            self.assertIsNone(event['response'])
            self.assertFalse(event['executed'])
        last_messages = backend.requests[-1]['messages']
        self.assertEqual(sum(m['role'] == 'tool' for m in last_messages), 1)
        self.assertEqual(last_messages[-1]['role'], 'user')
        self.assertIn('not a tool result', last_messages[-1]['content'])
        self.assertEqual(last_messages[-2]['content'], dialogue_text(call))
        self.assertNotIn('<|im_end|>', last_messages[-2]['content'])

    def test_internal_parse_error_is_not_guard_progress(self):
        cfg = config('B2'); cfg['tool_budget'] = 3
        call = xml('get_order', identifier='bad')
        result, trace = run_calibrated_case(fixture(), FakeBackend([
            call, '<tool_call><function=get_order>', call]), cfg)
        self.assertEqual(result['guard_blocked_proposals'], 1)
        self.assertEqual(result['actual_tool_attempts'], 1)
        self.assertTrue(trace['events'][-1]['guard_blocked'])

    def test_repeated_parse_errors_are_not_executed_tool_loops(self):
        cfg = config(); cfg['tool_budget'] = 3
        result, _ = run_calibrated_case(fixture(), FakeBackend(['<tool_call>'] * 3), cfg)
        self.assertEqual(result['parser_error_turns'], 3)
        self.assertFalse(result['has_invalid_loop'])

    def test_only_trailing_eos_removed_for_dialogue(self):
        self.assertEqual(dialogue_text('  A<|im_end|><|endoftext|>\n'), '  A')
        self.assertEqual(dialogue_text('A<|im_end|>B'), 'A<|im_end|>B')

    def test_b0_and_b1_observe_but_do_not_enforce_guard(self):
        for arm in ('B0', 'B1'):
            cfg = config(arm); cfg['tool_budget'] = 3
            result, trace = run_calibrated_case(fixture('recoverable'), FakeBackend([
                xml('get_order', identifier='bad')] * 3), cfg)
            self.assertEqual(result['actual_tool_attempts'], 3)
            self.assertEqual(result['guard_blocked_proposals'], 0)
            self.assertEqual(result['proposed_repeat_count'], 2)
            self.assertTrue(result['has_invalid_loop'])
            self.assertEqual(len(trace['final_snapshot']['history']), 3)

    def test_legal_retries_never_blocked(self):
        case = fixture('retry_required')
        backend = FakeBackend([xml('get_order', identifier=case['hidden']['valid_identifier'])] * 3 + ['Done.'])
        result, _ = run_calibrated_case(case, backend, config('B2'))
        self.assertTrue(result['tool_goal_attainment'])
        self.assertEqual(result['guard_blocked_proposals'], 0)

    def test_parser_error_does_not_fabricate_tool_result(self):
        backend = FakeBackend(['<tool_call><function=get_order>', 'Cannot proceed.'])
        result, _ = run_calibrated_case(fixture(), backend, config())
        self.assertEqual(result['parser_error_turns'], 1)
        self.assertEqual(result['proposal_attempts'], 1)
        self.assertEqual(result['actual_tool_attempts'], 0)
        self.assertFalse(any(m['role'] == 'tool' for m in backend.requests[-1]['messages']))

    def test_truncation_stops_without_execution(self):
        result, _ = run_calibrated_case(fixture(), FakeBackend([xml('get_order', identifier='ORD-101')], True), config())
        self.assertTrue(result['budget_exhausted'])
        self.assertEqual(result['actual_tool_attempts'], 0)
        self.assertEqual(result['terminal_form'], 'none')

    def test_case_seed_independent_of_order(self):
        cfg = config(); cfg['case_ids'].reverse()
        validate_config(cfg)
        case = fixture('recoverable')
        backend = FakeBackend([xml('get_order', identifier='bad'), 'Cannot proceed.'])
        run_calibrated_case(case, backend, cfg)
        self.assertEqual([r['seed'] for r in backend.requests], [16, 17])

    def test_config_requires_explicit_nonoverlapping_seeds(self):
        for mutation in ('missing', 'overlap', 'negative', 'bool', 'extra', 'duplicate'):
            cfg = config(); key = cfg['case_ids'][0]
            if mutation == 'missing': del cfg['case_seeds'][key]
            elif mutation == 'overlap': cfg['case_seeds'][key] = 16
            elif mutation == 'negative': cfg['case_seeds'][key] = -1
            elif mutation == 'bool': cfg['case_seeds'][key] = True
            elif mutation == 'extra': cfg['case_seeds']['extra'] = 1000
            else: cfg['case_ids'].append(key)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): validate_config(cfg)

    def test_model_only_receives_public_inputs(self):
        case = fixture('recoverable'); case['hidden']['value'] = 'PRIVATE_SENTINEL_123'
        backend = FakeBackend(['Unable.'])
        run_calibrated_case(case, backend, config())
        self.assertNotIn('PRIVATE_SENTINEL_123', json.dumps(backend.requests))
        self.assertNotIn('template_group', json.dumps(backend.requests))

    def test_b0_b2_same_messages_before_intervention(self):
        inputs = []
        for arm in ('B0', 'B1', 'B2'):
            backend = FakeBackend(['Done.'])
            run_calibrated_case(fixture(), backend, config(arm))
            inputs.append(backend.requests[0])
        self.assertEqual(inputs[0], inputs[2])
        self.assertEqual(inputs[0]['messages'][1:], inputs[1]['messages'][1:])
        self.assertTrue(inputs[1]['messages'][0]['content'].startswith(inputs[0]['messages'][0]['content']))

    def test_costs_include_all_blocked_generations_and_unknown_metrics(self):
        cfg = config('B2'); cfg['tool_budget'] = 3
        result, _ = run_calibrated_case(fixture(), FakeBackend([xml('get_order', identifier='bad')] * 3), cfg)
        metrics = calibrated_metrics([result])['metrics']
        self.assertEqual(metrics['input_tokens'], 30)
        self.assertEqual(metrics['output_tokens'], 15)
        self.assertNotIn('success', metrics)
        self.assertEqual(metrics['terminal_forms']['none'], 1)

    def test_generation_failure_retains_partial(self):
        rows, partial = [], []
        with self.assertRaisesRegex(RuntimeError, 'test failure'):
            run_calibrated_case(fixture(), FakeBackend([RuntimeError('test failure')]), config(),
                                emit_generation=rows.append, emit_partial=partial.append)
        self.assertEqual(rows[0]['generation_error']['type'], 'RuntimeError')
        self.assertEqual(partial[0]['status'], 'generation_failed')


if __name__ == '__main__':
    unittest.main()
