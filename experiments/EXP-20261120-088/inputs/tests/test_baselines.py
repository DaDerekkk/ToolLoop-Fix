import copy
import json
from pathlib import Path
import unittest

from toolloop_fix.baselines import B1_GUIDANCE, GUARD_VERSION, guard_action, prompt_for
from toolloop_fix.harness import Environment, tool_call

ROOT = Path(__file__).resolve().parents[1]
CASES = [json.loads(line) for line in (ROOT / 'data/dev/pilot_cases.jsonl').read_text().splitlines()]


def fixture(domain='orders', category='recoverable'):
    return copy.deepcopy(next(case for case in CASES if case['domain'] == domain and case['category'] == category))


def view_with(history):
    return {'tools': {'get_order': {}, 'find_order': {}, 'poll_job': {}, 'read_file': {}},
            'history': copy.deepcopy(history)}


def event(action, code='INVALID_IDENTIFIER', retryable=False, **fields):
    return {'action': action, 'response': {'code': code, 'retryable': retryable, **fields}}


class BaselinePromptTests(unittest.TestCase):
    def test_b0_and_b2_have_identical_no_extra_guidance(self):
        self.assertEqual(prompt_for('B0'), '')
        self.assertEqual(prompt_for('B2'), prompt_for('B0'))
        self.assertEqual(prompt_for('B1'), B1_GUIDANCE)

    def test_b1_is_generic_and_preserves_legal_retry(self):
        prompt = prompt_for('B1')
        self.assertIn('within its limits', prompt)
        self.assertIn('Preserve identifiers', prompt)
        self.assertIn('prerequisite', prompt)
        for value in ('ORD-', 'JOB-', 'report.txt', 'get_order', 'poll_job', 'case_id', 'recoverable-001'):
            self.assertNotIn(value, prompt)

    def test_unknown_arm_rejected(self):
        for arm in ('B1+B2', 'B3', 'b0', ''):
            with self.assertRaises(ValueError):
                prompt_for(arm)


class GuardTests(unittest.TestCase):
    def test_first_attempt_unblocked(self):
        env = Environment(fixture())
        verdict = guard_action(env.public_view(), tool_call('get_order', identifier='ORD-OLD'))
        self.assertFalse(verdict['blocked'])
        self.assertEqual(verdict['rule_version'], GUARD_VERSION)

    def test_adjacent_known_nonretryable_repeat_blocked_without_replacement(self):
        action = tool_call('get_order', identifier='ORD-OLD')
        env = Environment(fixture())
        env.execute(action)
        verdict = guard_action(env.public_view(), action)
        self.assertTrue(verdict['blocked'])
        self.assertEqual(verdict['matched_history_index'], 0)
        self.assertEqual(verdict['prior_response_code'], 'INVALID_IDENTIFIER')
        self.assertIn('not executed', verdict['feedback'])
        for key in ('replacement_action', 'success', 'stop', 'chosen'):
            self.assertNotIn(key, verdict)
        self.assertNotIn('ORD-101', json.dumps(verdict))

    def test_equivalent_whitespace_path_and_ignored_metadata_repeat(self):
        env = Environment(fixture('files'))
        env.execute({**tool_call('read_file', identifier='/old/report.txt'), 'trace_label': 'a'})
        verdict = guard_action(env.public_view(),
            {**tool_call('read_file', identifier=' /old/../old/report.txt '), 'trace_label': 'b'})
        self.assertTrue(verdict['blocked'])

    def test_changed_arguments_are_not_substituted_or_blocked(self):
        first = tool_call('get_order', identifier='bad-a')
        second = tool_call('get_order', identifier='bad-b')
        self.assertFalse(guard_action(view_with([event(first)]), second)['blocked'])

    def test_legal_timeout_retry_and_polling_not_blocked(self):
        for domain, action in (('orders', tool_call('get_order', identifier='ORD-101')),
                               ('files', tool_call('read_file', identifier='/records/report.txt'))):
            env = Environment(fixture(domain, 'retry_required'))
            env.execute(action)
            self.assertFalse(guard_action(env.public_view(), action)['blocked'])
        env = Environment(fixture('jobs', 'retry_required'))
        poll = tool_call('poll_job', identifier='JOB-101')
        env.execute(poll)
        self.assertFalse(guard_action(env.public_view(), poll)['blocked'])

    def test_public_retry_limit_errors_are_eligible(self):
        action = tool_call('get_order', identifier='ORD-101')
        for code in ('RETRY_LIMIT_EXCEEDED', 'TIMEOUT', 'RUNNING'):
            with self.subTest(code=code):
                self.assertTrue(guard_action(view_with([event(action, code=code)]), action)['blocked'])

    def test_unknown_or_nonboolean_retry_flag_is_not_assumed_nonretryable(self):
        action = tool_call('get_order', identifier='ORD-OLD')
        for retryable in (True, None, 0, 'false', 'unknown'):
            self.assertFalse(guard_action(view_with([event(action, retryable=retryable)]), action)['blocked'])
        absent = {'action': action, 'response': {'code': 'INVALID_IDENTIFIER'}}
        self.assertFalse(guard_action(view_with([absent]), action)['blocked'])

    def test_unknown_error_code_not_interpreted_as_permanent(self):
        action = tool_call('get_order', identifier='ORD-OLD')
        for code in ('NEW_UNKNOWN_ERROR', 'OK', 'READY', 'GUARD_BLOCKED'):
            self.assertFalse(guard_action(view_with([event(action, code=code)]), action)['blocked'])

    def test_new_public_information_allows_retry_of_old_action(self):
        action = tool_call('get_order', identifier='ORD-OLD')
        lookup = tool_call('find_order', target='latest order')
        history = [event(action), event(lookup, code='LOCATED', identifier='ORD-101')]
        self.assertFalse(guard_action(view_with(history), action)['blocked'])

    def test_abab_without_new_response_information_detected_at_fourth_proposal(self):
        a = tool_call('get_order', identifier='bad-a')
        b = tool_call('find_order', target='bad-target')
        first_two = [event(a), event(b, code='NOT_FOUND')]
        self.assertFalse(guard_action(view_with(first_two), a)['blocked'])
        self.assertTrue(guard_action(view_with(first_two + [event(a)]), b)['blocked'])

    def test_new_failed_action_is_information_even_if_response_payload_matches(self):
        a = tool_call('get_order', identifier='bad-a')
        b = tool_call('get_order', identifier='bad-b')
        first_two = [event(a), event(b)]
        self.assertFalse(guard_action(view_with(first_two), a)['blocked'])
        self.assertTrue(guard_action(view_with(first_two + [event(a)]), b)['blocked'])

    def test_successful_observation_resets_guard_even_if_identical_information_was_seen(self):
        a = tool_call('get_order', identifier='ORD-OLD')
        lookup = tool_call('find_order', target='latest order')
        for code in ('LOCATED', 'USER_INFORMATION', 'OK', 'READY'):
            with self.subTest(code=code):
                progress = event(lookup, code=code, identifier='ORD-101')
                history = [progress, event(a), progress]
                self.assertFalse(guard_action(view_with(history), a)['blocked'])

    def test_permitted_intervening_poll_is_not_ignored_even_if_response_seen_before(self):
        a = tool_call('get_order', identifier='ORD-101')
        poll = tool_call('poll_job', identifier='JOB-101')
        running = event(poll, code='RUNNING', retryable=True)
        history = [running, event(a, code='PRECONDITION'), running]
        self.assertFalse(guard_action(view_with(history), a)['blocked'])

    def test_latest_equivalent_response_supersedes_older_failure(self):
        action = tool_call('get_order', identifier='ORD-OLD')
        history = [event(action), event(action, code='TIMEOUT', retryable=True)]
        self.assertFalse(guard_action(view_with(history), action)['blocked'])

    def test_guard_is_pure_and_does_not_advance_snapshot(self):
        env = Environment(fixture())
        action = tool_call('get_order', identifier='ORD-OLD')
        env.execute(action)
        snapshot, view = env.snapshot(), env.public_view()
        view_copy, action_copy = copy.deepcopy(view), copy.deepcopy(action)
        first = guard_action(view, action)
        second = guard_action(view, action)
        self.assertEqual(first, second)
        self.assertTrue(first['blocked'])
        self.assertEqual(snapshot, env.snapshot())
        self.assertEqual(view, view_copy)
        self.assertEqual(action, action_copy)

    def test_terminal_malformed_unknown_tool_actions_pass_to_existing_protocol(self):
        view = Environment(fixture()).public_view()
        for action in ({'type': 'stop', 'reason': 'NATURAL_LANGUAGE_TERMINAL'},
                       {'type': 'final', 'answer': 'invented'}, {'type': 'invalid_model_output'},
                       tool_call('unknown_tool', identifier='x'), 'not a parsed action'):
            self.assertFalse(guard_action(view, action)['blocked'])

    def test_guard_does_not_read_hidden_labels_or_request(self):
        action = tool_call('get_order', identifier='ORD-OLD')
        view = view_with([event(action)])
        view['hidden'] = object()
        view['request'] = object()
        view['category'] = object()
        self.assertTrue(guard_action(view, action)['blocked'])


if __name__ == '__main__':
    unittest.main()
