import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from toolloop_fix.harness import (Environment, canonical_action, detect_loops, run_case,
                                  scripted_policy, summarize, tool_call, validate_alternative)

ROOT = Path(__file__).resolve().parents[1]
CASES = [json.loads(line) for line in (ROOT / 'data/dev/pilot_cases.jsonl').read_text().splitlines()]


def case(domain='orders', category='normal'):
    return copy.deepcopy(next(c for c in CASES if c['domain'] == domain and c['category'] == category))


class HarnessTests(unittest.TestCase):
    def test_fifteen_dev_cases_cover_domains_categories(self):
        self.assertEqual(len(CASES), 15)
        self.assertEqual(len({c['case_id'] for c in CASES}), 15)
        self.assertEqual({c['split'] for c in CASES}, {'dev'})
        self.assertEqual(len({(c['domain'], c['category']) for c in CASES}), 15)

    def test_reference_completes_all_completable_and_stops_unreachable(self):
        for fixture in CASES:
            with self.subTest(case=fixture['case_id']):
                result, _ = run_case(fixture, 'scripted_reference', 'end_to_end')
                self.assertEqual(result['success'], fixture['category'] != 'unrecoverable')
                self.assertEqual(result['correct_termination'], fixture['category'] == 'unrecoverable')
                self.assertFalse(result['has_invalid_loop'])
                self.assertFalse(result['budget_exhausted'])

    def test_naive_produces_detectable_development_failures(self):
        result, _ = run_case(case(category='recoverable'), 'scripted_naive', 'end_to_end')
        self.assertTrue(result['has_invalid_loop'])
        self.assertTrue(result['budget_exhausted'])
        self.assertEqual(result['tool_attempts'], 12)
        self.assertFalse(result['success'])

    def test_transient_retry_not_loop(self):
        for domain in ('orders', 'files'):
            result, _ = run_case(case(domain, 'retry_required'), 'scripted_naive', 'end_to_end')
            self.assertTrue(result['success'])
            self.assertFalse(result['has_invalid_loop'])
            self.assertEqual(result['tool_attempts'], 2)

    def test_legitimate_polling_not_loop_and_state_changes(self):
        result, trace = run_case(case('jobs', 'retry_required'), 'scripted_reference', 'end_to_end')
        self.assertTrue(result['success'])
        self.assertFalse(result['has_invalid_loop'])
        polls = [event for event in trace['events'] if event['action'].get('tool') == 'poll_job']
        self.assertEqual(len(polls), 2)
        self.assertTrue(all(event['state_before'] != event['state_after'] for event in polls))

    def test_equivalent_action_normalization(self):
        left = tool_call('read_file', identifier=' /records/../old/report.txt ')
        right = {'arguments': {'identifier': '/old/report.txt'}, 'tool': 'read_file', 'type': 'tool'}
        self.assertEqual(canonical_action(left), canonical_action(right))
        env = Environment(case('files', 'recoverable'))
        env.execute(left)
        env.execute(right)
        self.assertEqual(detect_loops(env.public_view()['history'])[0]['type'], 'identical_or_equivalent_repeat')

    def test_abab_short_cycle(self):
        a = tool_call('get_order', identifier='bad-a')
        b = tool_call('find_order', target='bad-target')
        env = Environment(case())
        for action in (a, b, a, b):
            env.execute(action)
        self.assertEqual(detect_loops(env.public_view()['history']), [{'index': 3, 'type': 'short_cycle_abab'}])

    def test_first_error_not_loop(self):
        env = Environment(case(category='recoverable'))
        env.execute(tool_call('get_order', identifier='ORD-OLD'))
        self.assertEqual(detect_loops(env.public_view()['history']), [])

    def test_progress_between_identical_calls_not_loop(self):
        result, trajectory = run_case(case('jobs', 'retry_required'), 'scripted_reference', 'end_to_end')
        self.assertFalse(result['has_invalid_loop'])
        self.assertEqual(trajectory['events'][0]['action'], trajectory['events'][-2]['action'])

    def test_snapshot_restore_and_copy_isolation(self):
        env = Environment(case(category='retry_required'))
        snapshot = env.snapshot()
        env.execute(tool_call('get_order', identifier='ORD-101'))
        self.assertNotEqual(snapshot, env.snapshot())
        env.restore(snapshot)
        self.assertEqual(snapshot, env.snapshot())
        snapshot['state']['value'] = 'corrupted external object'
        self.assertNotEqual(snapshot, env.snapshot())
        other = Environment(case(category='retry_required'))
        self.assertEqual(env.snapshot(), other.snapshot())

    def test_public_view_has_no_hidden_labels_or_answer_and_is_detached(self):
        env = Environment(case(category='recoverable'))
        view = env.public_view()
        self.assertEqual(set(view), {'request', 'tools', 'history'})
        serialized = json.dumps(view)
        for forbidden in ('hidden', 'category', 'recoverable', 'valid_identifier', 'shipped', 'ORD-101'):
            self.assertNotIn(forbidden, serialized)
        view['request']['target'] = 'external mutation'
        self.assertNotEqual(view, env.public_view())

    def test_policy_works_with_public_view_alone(self):
        env = Environment(case(category='recoverable'))
        first = scripted_policy(env.public_view(), 'scripted_reference')
        env.execute(first)
        self.assertEqual(scripted_policy(env.public_view(), 'scripted_reference'),
                         tool_call('find_order', target='latest order'))

    def test_alternative_validation_restores_state_and_rejects_hidden_identifier(self):
        env = Environment(case(category='recoverable'))
        env.execute(tool_call('get_order', identifier='ORD-OLD'))
        before = env.snapshot()
        chosen = validate_alternative(env, tool_call('find_order', target='latest order'))
        self.assertTrue(chosen['locally_validated'])
        self.assertEqual(before, env.snapshot())
        hidden_guess = validate_alternative(env, tool_call('get_order', identifier='ORD-101'))
        self.assertFalse(hidden_guess['public_arguments_only'])
        self.assertFalse(hidden_guess['locally_validated'])
        self.assertEqual(before, env.snapshot())
        rejected = validate_alternative(env, tool_call('get_order', identifier='ORD-OLD'))
        self.assertFalse(rejected['locally_validated'])
        self.assertEqual(before, env.snapshot())

    def test_same_fixed_prefix_snapshot_for_both_policies(self):
        for fixture in CASES:
            if fixture.get('fixed_prefix'):
                first, _ = run_case(fixture, 'scripted_naive', 'fixed_failure_prefix')
                second, trace = run_case(fixture, 'scripted_reference', 'fixed_failure_prefix')
                self.assertEqual(first['prefix_snapshot_sha256'], second['prefix_snapshot_sha256'])
                self.assertEqual(first['prefix_tool_attempts'], 1)
                self.assertTrue(second['success'])
                self.assertEqual(trace['prefix_provenance'], 'hand_authored_fixture_not_model_failure')

    def test_candidate_repeated_information_is_not_progress(self):
        env = Environment(case(category='recoverable'))
        candidate = tool_call('find_order', target='latest order')
        env.execute(candidate)
        verdict = validate_alternative(env, candidate)
        self.assertFalse(verdict['new_information'])
        self.assertFalse(verdict['state_progress'])
        self.assertFalse(verdict['locally_validated'])

    def test_malformed_attempts_consume_budget(self):
        with patch('toolloop_fix.harness.scripted_policy', return_value='invalid json action'):
            result, _ = run_case(case(), 'scripted_naive', 'end_to_end', tool_budget=3)
        self.assertEqual(result['tool_attempts'], 3)
        self.assertTrue(result['budget_exhausted'])
        self.assertFalse(result['success'])

    def test_turn_budget_is_independent(self):
        result, _ = run_case(case(category='recoverable'), 'scripted_naive', 'end_to_end', turn_budget=2)
        self.assertEqual(result['turns'], 2)
        self.assertTrue(result['budget_exhausted'])

    def test_last_budgeted_tool_result_not_success_without_final_answer(self):
        result, _ = run_case(case(), 'scripted_reference', 'end_to_end', tool_budget=1)
        self.assertTrue(result['budget_exhausted'])
        self.assertFalse(result['success'])

    def test_false_success_requires_evidence_not_just_correct_guess(self):
        env = Environment(case())
        env.execute({'type': 'final', 'answer': 'shipped', 'identifier': 'ORD-101'})
        self.assertFalse(env.evaluate(False)['success'])
        self.assertTrue(env.evaluate(False)['false_success'])

    def test_answer_and_identifier_must_match_read_evidence(self):
        for answer, identifier in [('invented', 'ORD-101'), ('shipped', 'ORD-wrong')]:
            env = Environment(case())
            env.execute(tool_call('get_order', identifier='ORD-101'))
            env.execute({'type': 'final', 'answer': answer, 'identifier': identifier})
            self.assertTrue(env.evaluate(False)['false_success'])
            self.assertFalse(env.evaluate(False)['success'])

    def test_premature_stop_and_unsupported_stop_not_correct(self):
        env = Environment(case())
        env.execute({'type': 'stop', 'reason': 'RESOURCE_UNAVAILABLE'})
        self.assertTrue(env.evaluate(False)['premature_abandonment'])
        env = Environment(case(category='unrecoverable'))
        env.execute({'type': 'stop', 'reason': 'RESOURCE_UNAVAILABLE'})
        self.assertFalse(env.evaluate(False)['correct_termination'])

    def test_terminal_action_cannot_be_followed_by_tool(self):
        env = Environment(case())
        env.execute({'type': 'stop', 'reason': 'unknown'})
        with self.assertRaises(ValueError):
            env.execute(tool_call('get_order', identifier='ORD-101'))

    def test_unknown_policy_and_mode_rejected(self):
        with self.assertRaises(ValueError):
            run_case(case(), 'qwen', 'end_to_end')
        with self.assertRaises(ValueError):
            run_case(case(), 'scripted_reference', 'invented')
        with self.assertRaises(ValueError):
            run_case(case(), 'scripted_reference', 'fixed_failure_prefix')

    def test_metrics_separate_unreachable_and_cost_includes_failures(self):
        rows = [run_case(c, 'scripted_naive', 'end_to_end')[0] for c in CASES]
        metrics = summarize(rows)['groups'][0]
        self.assertEqual(metrics['task_completion']['denominator'], 12)
        self.assertEqual(metrics['correct_termination']['denominator'], 3)
        self.assertEqual(metrics['tool_attempts_per_success']['numerator'],
                         sum(row['tool_attempts'] for row in rows if row['completable']))
        self.assertEqual(metrics['tool_attempts_per_success']['denominator'], 5)
        self.assertIsNone(metrics['error_recovery']['value'])

    def test_zero_success_cost_is_null_not_zero(self):
        result, _ = run_case(case(category='recoverable'), 'scripted_naive', 'end_to_end')
        cost = summarize([result])['groups'][0]['tool_attempts_per_success']
        self.assertIsNone(cost['value'])
        self.assertEqual(cost['denominator'], 0)

    def test_fixed_prefix_consumes_total_tool_and_turn_budgets(self):
        fixture = case(category='recoverable')
        result, trace = run_case(fixture, 'scripted_naive', 'fixed_failure_prefix', tool_budget=4)
        self.assertEqual(len(trace['events']), 4)
        self.assertEqual(result['tool_attempts'], 4)
        self.assertEqual(result['continuation_tool_attempts'], 3)
        self.assertEqual(result['prefix_tool_attempts'], 1)
        self.assertEqual(result['turns'], 4)
        self.assertEqual(result['continuation_turns'], 3)
        self.assertEqual(result['remaining_tool_budget_at_start'], 3)
        result, _ = run_case(fixture, 'scripted_naive', 'fixed_failure_prefix', turn_budget=2)
        self.assertEqual(result['turns'], 2)
        self.assertEqual(result['continuation_turns'], 1)

    def test_prefix_exceeding_either_budget_rejected(self):
        fixture = case(category='recoverable')
        fixture['fixed_prefix'] *= 3
        with self.assertRaises(ValueError):
            run_case(fixture, 'scripted_reference', 'fixed_failure_prefix', tool_budget=2)
        with self.assertRaises(ValueError):
            run_case(fixture, 'scripted_reference', 'fixed_failure_prefix', turn_budget=2)

    def test_prefix_exactly_exhausting_budget_cannot_continue(self):
        result, trace = run_case(case(category='recoverable'), 'scripted_reference',
                                 'fixed_failure_prefix', tool_budget=1)
        self.assertEqual(len(trace['events']), 1)
        self.assertEqual(result['continuation_tool_attempts'], 0)
        self.assertTrue(result['budget_exhausted'])
        self.assertFalse(result['success'])

    def test_timeout_cannot_exceed_two_retries(self):
        fixture = case(category='retry_required')
        fixture['hidden']['transient_remaining'] = 5
        env = Environment(fixture)
        action = tool_call('get_order', identifier='ORD-101')
        responses = [env.execute(action)['response'] for _ in range(4)]
        self.assertEqual([row['code'] for row in responses],
                         ['TIMEOUT', 'TIMEOUT', 'TIMEOUT', 'RETRY_LIMIT_EXCEEDED'])
        self.assertFalse(responses[2]['retryable'])
        self.assertEqual(env.snapshot()['state']['transient_remaining'], 2)
        self.assertTrue(detect_loops(env.public_view()['history']))

    def test_retry_limit_respects_equivalent_arguments_and_snapshot(self):
        fixture = case(category='retry_required')
        fixture['hidden']['transient_remaining'] = 5
        env = Environment(fixture)
        for identifier in ('ORD-101', ' ORD-101 ', 'ORD-101'):
            env.execute(tool_call('get_order', identifier=identifier))
        snapshot = env.snapshot()
        result = env.execute(tool_call('get_order', identifier=' ORD-101 '))
        self.assertEqual(result['response']['code'], 'RETRY_LIMIT_EXCEEDED')
        env.restore(snapshot)
        self.assertEqual(env.execute(tool_call('get_order', identifier='ORD-101'))['response'],
                         result['response'])

    def test_three_poll_cap_prevents_business_state_progress_after_limit(self):
        fixture = case('jobs', 'retry_required')
        fixture['hidden']['polls_remaining'] = 5
        env = Environment(fixture)
        action = tool_call('poll_job', identifier='JOB-101')
        responses = [env.execute(action)['response'] for _ in range(4)]
        self.assertEqual([row['code'] for row in responses],
                         ['RUNNING', 'RUNNING', 'RUNNING', 'RETRY_LIMIT_EXCEEDED'])
        self.assertFalse(responses[2]['retryable'])
        self.assertEqual(env.snapshot()['state']['polls_remaining'], 2)
        self.assertTrue(detect_loops(env.public_view()['history']))

    def test_irrelevant_action_metadata_cannot_reset_retry_counter(self):
        fixture = case(category='retry_required')
        fixture['hidden']['transient_remaining'] = 5
        env = Environment(fixture)
        for index in range(4):
            action = {**tool_call('get_order', identifier='ORD-101'), 'trace_label': str(index)}
            response = env.execute(action)['response']
        self.assertEqual(response['code'], 'RETRY_LIMIT_EXCEEDED')
        self.assertEqual(env.snapshot()['state']['transient_remaining'], 2)

    def test_ignored_tool_metadata_cannot_hide_equivalent_error_repeat(self):
        first = {**tool_call('get_order', identifier='ORD-OLD'), 'trace_label': 'first'}
        second = {**tool_call('get_order', identifier='ORD-OLD'), 'trace_label': 'second'}
        self.assertEqual(canonical_action(first), canonical_action(second))
        env = Environment(case(category='recoverable'))
        env.execute(first)
        env.execute(second)
        history = env.public_view()['history']
        self.assertEqual(detect_loops(history),
                         [{'index': 1, 'type': 'identical_or_equivalent_repeat'}])
        self.assertEqual(history[0]['action']['trace_label'], 'first')
        self.assertEqual(history[1]['action']['trace_label'], 'second')

    def test_retries_and_polls_can_succeed_at_the_documented_limit(self):
        fixture = case(category='retry_required')
        fixture['hidden']['transient_remaining'] = 2
        result, _ = run_case(fixture, 'scripted_reference', 'end_to_end')
        self.assertTrue(result['success'])
        self.assertFalse(result['has_invalid_loop'])
        fixture = case('jobs', 'retry_required')
        fixture['hidden']['polls_remaining'] = 3
        result, _ = run_case(fixture, 'scripted_reference', 'end_to_end')
        self.assertTrue(result['success'])
        self.assertFalse(result['has_invalid_loop'])

    def test_inherited_loop_is_not_attributed_to_continuation(self):
        fixture = case(category='recoverable')
        fixture['fixed_prefix'] *= 3
        result, trace = run_case(fixture, 'scripted_reference', 'fixed_failure_prefix')
        self.assertTrue(result['success'])
        self.assertFalse(result['has_invalid_loop'])
        self.assertEqual([item['index'] for item in trace['prefix_loop_flags']], [1, 2])
        self.assertEqual(trace['continuation_loop_flags'], [])
        self.assertTrue(result['has_inherited_loop'])

    def test_new_repeat_crossing_prefix_boundary_is_attributed_to_continuation(self):
        result, trace = run_case(case(category='recoverable'), 'scripted_naive', 'fixed_failure_prefix')
        self.assertTrue(result['has_invalid_loop'])
        self.assertEqual(trace['prefix_loop_flags'], [])
        self.assertEqual(trace['continuation_loop_flags'][0]['index'], 1)

    def test_unnecessary_help_has_own_metric_even_if_task_eventually_succeeds(self):
        actions = [tool_call('ask_user', field='identifier'), tool_call('get_order', identifier='ORD-101'),
                   {'type': 'final', 'answer': 'shipped', 'identifier': 'ORD-101'}]
        with patch('toolloop_fix.harness.scripted_policy', side_effect=actions):
            result, _ = run_case(case(), 'scripted_reference', 'end_to_end')
        self.assertTrue(result['success'])
        self.assertTrue(result['premature_abandonment'])
        self.assertTrue(result['unnecessary_clarification'])
        self.assertEqual(result['unnecessary_clarification_count'], 1)
        metric = summarize([result])['groups'][0]['unnecessary_clarification']
        self.assertEqual(metric, {'numerator': 1, 'denominator': 1, 'value': 1.0})

    def test_required_help_excluded_but_repeated_help_after_reply_is_unnecessary(self):
        env = Environment(case(category='clarification'))
        ask = tool_call('ask_user', field='identifier')
        self.assertEqual(env.execute(ask)['response']['code'], 'USER_INFORMATION')
        self.assertFalse(env.evaluate(False)['unnecessary_clarification'])
        self.assertEqual(env.execute(ask)['response']['code'], 'UNNECESSARY_CLARIFICATION')
        self.assertTrue(env.evaluate(False)['unnecessary_clarification'])
        self.assertTrue(env.evaluate(False)['premature_abandonment'])

    def test_fixed_prefix_metrics_cost_uses_total_and_exposes_continuation_cost(self):
        result, _ = run_case(case(category='recoverable'), 'scripted_reference', 'fixed_failure_prefix')
        group = summarize([result])['groups'][0]
        self.assertEqual(group['tool_attempts_per_success']['numerator'], 3)
        self.assertEqual(group['continuation_tool_attempts_per_success']['numerator'], 2)
        self.assertEqual(group['cost_scope'], 'total_including_prefix; tool_attempts_not_tokens_or_money')

    def test_cli_produces_42_rows_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            command = [sys.executable, '-m', 'toolloop_fix.run', '--config',
                       str(ROOT / 'configs/smoke.json'), '--output', folder]
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(len((Path(folder) / 'case_results.jsonl').read_text().splitlines()), 42)
            self.assertEqual(len((Path(folder) / 'trajectories.jsonl').read_text().splitlines()), 42)
            self.assertEqual(json.loads((Path(folder) / 'metrics.json').read_text())['evidence_class'],
                             'synthetic_harness')
            repeated = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertIn('refusing to overwrite', repeated.stderr)


if __name__ == '__main__':
    unittest.main()
