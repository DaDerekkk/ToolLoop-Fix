import copy
import json
import unittest

from toolloop_fix.calibrated_protocol import (
    CALIBRATED_PROTOCOL_VERSION, CALIBRATED_SYSTEM_PROMPT, NATURAL_TERMINAL_REASON,
    messages_for_calibrated_view, parse_calibrated_output, score_calibrated_episode,
)
from toolloop_fix.harness import Environment, tool_call
from toolloop_fix.model_protocol import parse_tool_output


def fixture(category="normal"):
    return {"domain": "orders", "category": category,
            "request": {"domain": "orders", "target": "latest order", "identifier": "ORD-101"},
            "hidden": {"valid_identifier": "ORD-101", "value": "shipped", "polls_remaining": 0,
                       "transient_remaining": 0, "unavailable": category == "unrecoverable"}}


def xml(name, **args):
    return "<tool_call><function={}>".format(name) + "".join(
        "<parameter={}>{}</parameter>".format(key, value) for key, value in args.items()
    ) + "</function></tool_call><|im_end|>"


class CalibratedParserTests(unittest.TestCase):
    def test_plain_text_is_ungraded_terminal_not_retry_error(self):
        raw = 'The order could not be found.<|im_end|>'
        parsed = parse_calibrated_output(raw)
        self.assertTrue(parsed["valid"])
        self.assertTrue(parsed["is_terminal"])
        self.assertEqual(parsed["terminal_form"], "natural_text")
        self.assertIsNone(parsed["call"])
        self.assertEqual(parsed["action"], {"type": "stop", "reason": NATURAL_TERMINAL_REASON,
                                           "raw_text": raw})
        self.assertEqual(parsed["legacy_strict_parse"],
                         {"valid": False, "error": "EXPECTED_EXACTLY_ONE_TOOL_CALL"})
        self.assertEqual(parsed["protocol_version"], CALIBRATED_PROTOCOL_VERSION)

    def test_xml_tool_action_is_unchanged(self):
        raw = xml("get_order", identifier="ORD-101")
        parsed = parse_calibrated_output(raw)
        strict = parse_tool_output(raw)
        for key in ("action", "call", "valid", "assistant_content"):
            self.assertEqual(parsed[key], strict[key])
        self.assertFalse(parsed["is_terminal"])

    def test_structured_finish_and_stop_keep_distinct_forms(self):
        self.assertEqual(parse_calibrated_output(xml("finish_task", answer="shipped", identifier="ORD-101"))["terminal_form"], "structured_finish")
        self.assertEqual(parse_calibrated_output(xml("stop_task", reason="unavailable"))["terminal_form"], "structured_stop")

    def test_partial_multiple_and_conversation_markup_remain_invalid(self):
        for raw in ("<tool_call>", "<function=get_order>", "</tool_call>",
                    "<TOOL_CALL>", "<|im_start|>assistant", "<tool_response>OK</tool_response>",
                    xml("get_order", identifier="ORD-101") * 2):
            with self.subTest(raw=raw):
                self.assertFalse(parse_calibrated_output(raw)["valid"])

    def test_empty_and_truncated_plain_text_not_accepted(self):
        for raw in ("", "   ", "<|im_end|>", "<|im_end|><|endoftext|>"):
            self.assertFalse(parse_calibrated_output(raw)["valid"])
        parsed = parse_calibrated_output("unfinished prose", token_limit_reached=True)
        self.assertFalse(parsed["valid"])
        self.assertEqual(parsed["error"], "OUTPUT_TOKEN_LIMIT")

    def test_plain_json_is_not_executed_or_repaired_as_a_tool(self):
        raw = '{"name":"get_order","arguments":{"identifier":"ORD-101"}}'
        parsed = parse_calibrated_output(raw)
        self.assertEqual(parsed["terminal_form"], "natural_text")
        self.assertEqual(parsed["action"]["raw_text"], raw)
        self.assertIsNone(parsed["call"])

    def test_shared_messages_only_change_by_explicit_extra_prompt(self):
        env = Environment(fixture())
        parsed = parse_calibrated_output(xml("get_order", identifier="ORD-101"))
        env.execute(parsed["action"])
        view = env.public_view()
        original = copy.deepcopy(view)
        baseline = messages_for_calibrated_view(view, [parsed])
        prompted = messages_for_calibrated_view(view, [parsed], extra_prompt="Generic recovery advice.")
        self.assertEqual(baseline[0]["content"], CALIBRATED_SYSTEM_PROMPT)
        self.assertEqual(prompted[1:], baseline[1:])
        self.assertEqual(prompted[0]["content"], CALIBRATED_SYSTEM_PROMPT + "\n\nGeneric recovery advice.")
        self.assertEqual(view, original)
        self.assertNotIn('"category"', json.dumps(baseline))

    def test_native_terminal_cannot_generate_error_feedback(self):
        env = Environment(fixture())
        parsed = parse_calibrated_output("I cannot proceed.<|im_end|>")
        event = env.execute(parsed["action"])
        self.assertEqual(event["response"]["code"], "STOP_RECORDED")
        with self.assertRaises(ValueError):
            messages_for_calibrated_view(env.public_view(), [parsed])
        with self.assertRaises(ValueError):
            env.execute(tool_call("get_order", identifier="ORD-101"))


class CalibratedScoreTests(unittest.TestCase):
    def score(self, case, raw, with_goal=False, exhausted=False, middle=None):
        env = Environment(case)
        decisions = list(middle or [])
        if with_goal:
            prior = parse_calibrated_output(xml("get_order", identifier="ORD-101"))
            env.execute(prior["action"])
            decisions.append(prior)
        decision = parse_calibrated_output(raw)
        env.execute(decision["action"])
        decisions.append(decision)
        return score_calibrated_episode(case, env.snapshot(), decisions, exhausted)

    def test_native_text_after_goal_remains_semantically_unknown(self):
        score = self.score(fixture(), "shipped, ORD-101", with_goal=True)
        self.assertTrue(score["tool_goal_attainment"])
        self.assertFalse(score["exact_grounded_structured_completion"])
        self.assertTrue(score["natural_termination_semantics_unknown"])
        self.assertFalse(score["premature_stop_without_goal_evidence"])
        self.assertNotIn("success", score)
        self.assertNotIn("task_success", score)

    def test_correct_string_guess_without_reading_is_not_goal_evidence(self):
        score = self.score(fixture(), xml("finish_task", answer="shipped", identifier="ORD-101"))
        self.assertFalse(score["tool_goal_attainment"])
        self.assertFalse(score["exact_grounded_structured_completion"])
        self.assertTrue(score["unsupported_finish_wrapper"])

    def test_exact_grounded_finish_is_narrow_structured_completion(self):
        score = self.score(fixture(), xml("finish_task", answer="shipped", identifier="ORD-101"), with_goal=True)
        self.assertTrue(score["tool_goal_attainment"])
        self.assertTrue(score["exact_grounded_structured_completion"])
        self.assertTrue(score["terminal_form_valid"])
        self.assertFalse(score["unsupported_finish_wrapper"])

    def test_wrong_finish_identifier_not_repaired(self):
        score = self.score(fixture(), xml("finish_task", answer="shipped", identifier="completed"), with_goal=True)
        self.assertTrue(score["tool_goal_attainment"])
        self.assertFalse(score["exact_grounded_structured_completion"])
        self.assertTrue(score["unsupported_finish_wrapper"])

    def test_budget_exhaustion_does_not_erase_observed_goal(self):
        score = self.score(fixture(), xml("finish_task", answer="shipped", identifier="ORD-101"), with_goal=True, exhausted=True)
        self.assertTrue(score["tool_goal_attainment"])
        self.assertFalse(score["exact_grounded_structured_completion"])
        self.assertTrue(score["budget_exhausted"])

    def test_public_unavailability_support_not_prose_truth_grading(self):
        case = fixture("unrecoverable")
        for raw, exact in ((xml("stop_task", reason="RESOURCE_UNAVAILABLE"), True),
                           (xml("stop_task", reason="RESOURCE_UNAVAILABLE because a dragon ate it"), False),
                           ("The resource is unavailable.", False)):
            env = Environment(case)
            prior = parse_calibrated_output(xml("get_order", identifier="ORD-101"))
            env.execute(prior["action"])
            decision = parse_calibrated_output(raw)
            env.execute(decision["action"])
            score = score_calibrated_episode(case, env.snapshot(), [prior, decision], False)
            self.assertTrue(score["stopped_after_public_unavailable"])
            self.assertEqual(score["exact_stop_reason_code"], exact)
            self.assertEqual(score["stop_reason_semantics_unknown"], not exact)
            self.assertFalse(score["completable"])
            self.assertFalse(score["premature_stop_without_goal_evidence"])

    def test_stop_reason_without_observed_response_is_not_supported(self):
        score = self.score(fixture("unrecoverable"), xml("stop_task", reason="RESOURCE_UNAVAILABLE"))
        self.assertFalse(score["stopped_after_public_unavailable"])
        self.assertFalse(score["exact_stop_reason_code"])
        self.assertTrue(score["stop_reason_semantics_unknown"])

    def test_middle_blocked_decisions_cannot_create_goal_evidence(self):
        blocked = parse_calibrated_output(xml("get_order", identifier="ORD-101"))
        score = self.score(fixture(), "Cannot continue", middle=[blocked])
        self.assertFalse(score["tool_goal_attainment"])
        self.assertTrue(score["premature_stop_without_goal_evidence"])

    def test_snapshot_evidence_requires_matching_actual_ok(self):
        case = fixture()
        env = Environment(case)
        decision = parse_calibrated_output(xml("finish_task", answer="shipped", identifier="ORD-101"))
        env.execute(decision["action"])
        snapshot = env.snapshot()
        snapshot["evidence"] = {"identifier": "ORD-101", "value": "shipped"}
        self.assertFalse(score_calibrated_episode(case, snapshot, [decision], False)["tool_goal_attainment"])

    def test_no_terminal_and_mismatched_terminal_are_explicit(self):
        case = fixture()
        env = Environment(case)
        score = score_calibrated_episode(case, env.snapshot(), [], True)
        self.assertEqual(score["terminal_form"], "none")
        self.assertFalse(score["terminal_form_valid"])
        decision = parse_calibrated_output("done")
        with self.assertRaises(ValueError):
            score_calibrated_episode(case, env.snapshot(), [decision], False)


if __name__ == "__main__":
    unittest.main()
