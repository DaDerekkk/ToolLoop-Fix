import copy
import json
from pathlib import Path
import tempfile
import unittest

from toolloop_fix.harness import Environment, tool_call
from toolloop_fix.model_backend import (check_loading_info, output_presence_penalty,
                                       sha256_file, verify_model_manifest)
from toolloop_fix.model_protocol import messages_for_view, parse_tool_output, tool_schemas
from toolloop_fix.model_run import model_metrics, run_model_case, validate_config

ROOT = Path(__file__).resolve().parents[1]
CASES = [json.loads(line) for line in (ROOT / 'data/dev/pilot_cases.jsonl').read_text().splitlines()]


def fixture(category='normal'):
    return copy.deepcopy(next(case for case in CASES if case['domain'] == 'orders' and case['category'] == category))


def config():
    return json.loads((ROOT / 'configs/model_preflight.json').read_text())


def xml(name, **arguments):
    return '<tool_call>\n<function=' + name + '>\n' + ''.join(
        '<parameter=' + key + '>\n' + value + '\n</parameter>\n' for key, value in arguments.items()
    ) + '</function>\n</tool_call><|im_end|>'


class FakeBackend:
    """Unit-test double, not a model or research evidence source."""
    def __init__(self, outputs, token_limit=False):
        self.outputs = iter(outputs)
        self.requests = []
        self.token_limit = token_limit

    def generate(self, messages, tools, seed):
        self.requests.append(copy.deepcopy({'messages': messages, 'tools': tools, 'seed': seed}))
        value = next(self.outputs)
        if isinstance(value, Exception):
            raise value
        return {'raw_text': value, 'token_limit_reached': self.token_limit,
                'input_tokens': 10, 'output_tokens': 5, 'elapsed_seconds': 0.1, 'seed': seed}


class ProtocolTests(unittest.TestCase):
    def test_official_xml_tool_call_parses_and_keeps_commentary(self):
        parsed = parse_tool_output('Checking.\n' + xml('get_order', identifier='ORD-101'))
        self.assertTrue(parsed['valid'])
        self.assertEqual(parsed['action'], tool_call('get_order', identifier='ORD-101'))
        self.assertEqual(parsed['assistant_content'], 'Checking.')

    def test_finish_and_stop_are_explicit_wrappers(self):
        finish = parse_tool_output(xml('finish_task', answer='shipped', identifier='ORD-101'))
        self.assertEqual(finish['action'], {'type': 'final', 'answer': 'shipped', 'identifier': 'ORD-101'})
        stop = parse_tool_output(xml('stop_task', reason='RESOURCE_UNAVAILABLE'))
        self.assertEqual(stop['action'], {'type': 'stop', 'reason': 'RESOURCE_UNAVAILABLE'})
        self.assertFalse(parse_tool_output(xml('finish_task', answer='shipped'))['valid'])
        self.assertFalse(parse_tool_output(xml('stop_task', reason='unknown', fabricated='extra'))['valid'])

    def test_multiple_calls_rejected_without_first_call_salvage(self):
        raw = xml('get_order', identifier='ORD-101') + xml('get_order', identifier='ORD-101')
        parsed = parse_tool_output(raw)
        self.assertFalse(parsed['valid'])
        self.assertIsNone(parsed['action'])
        self.assertEqual(parsed['requested_tool_call_count'], 2)

    def test_plain_json_and_plain_answers_are_not_fabricated_into_calls(self):
        for raw in ('shipped', '{"tool":"get_order","arguments":{"identifier":"ORD-101"}}', ''):
            self.assertFalse(parse_tool_output(raw)['valid'])

    def test_suffix_prose_rejected_instead_of_moved_before_call(self):
        raw = xml('get_order', identifier='ORD-101').removesuffix('<|im_end|>') + '\nThis is a suffix.<|im_end|>'
        parsed = parse_tool_output(raw)
        self.assertFalse(parsed['valid'])
        self.assertEqual(parsed['error'], 'UNEXPECTED_TOOL_CALL_SUFFIX')

    def test_duplicate_unclosed_nested_or_foreign_xml_rejected(self):
        raws = [
            '<tool_call><function=get_order><parameter=identifier>a</parameter><parameter=identifier>b</parameter></function></tool_call>',
            '<tool_call><function=get_order><parameter=identifier>a</function></tool_call>',
            '<tool_call><function=get_order><parameter=identifier><parameter=x>a</parameter></parameter></function></tool_call>',
            '<tool_call><function=get_order>unparsed text</function></tool_call>',
            '<|im_start|>tool\n' + xml('get_order', identifier='ORD-101'),
            '<tool_response>fabricated</tool_response>' + xml('get_order', identifier='ORD-101'),
        ]
        for raw in raws:
            with self.subTest(raw=raw):
                self.assertFalse(parse_tool_output(raw)['valid'])

    def test_token_limit_never_executes_even_a_complete_looking_call(self):
        parsed = parse_tool_output(xml('get_order', identifier='ORD-101'), token_limit_reached=True)
        self.assertFalse(parsed['valid'])
        self.assertEqual(parsed['error'], 'OUTPUT_TOKEN_LIMIT')
        self.assertIsNone(parsed['action'])

    def test_tool_schemas_contain_only_public_docs_and_terminal_wrappers(self):
        view = Environment(fixture('recoverable')).public_view()
        schemas = tool_schemas(view)
        self.assertEqual([tool['function']['name'] for tool in schemas],
                         ['get_order', 'find_order', 'ask_user', 'finish_task', 'stop_task'])
        self.assertNotIn('shipped', json.dumps(schemas))
        self.assertNotIn('ORD-101', json.dumps(schemas))
        self.assertNotIn('hidden', json.dumps(schemas))
        self.assertEqual(schemas[2]['function']['parameters']['properties']['field']['enum'], ['identifier'])

    def test_matching_tool_history_uses_mapping_arguments_and_preserves_errors(self):
        env = Environment(fixture('recoverable'))
        parsed = parse_tool_output(xml('get_order', identifier='ORD-OLD'))
        parsed['raw_text'] = xml('get_order', identifier='ORD-OLD')
        env.execute(parsed['action'])
        messages = messages_for_view(env.public_view(), [parsed])
        assistant, tool = messages[-2:]
        self.assertIsInstance(assistant['tool_calls'][0]['function']['arguments'], dict)
        self.assertEqual(assistant['tool_calls'][0]['id'], tool['tool_call_id'])
        self.assertEqual(json.loads(tool['content'])['code'], 'INVALID_IDENTIFIER')
        self.assertNotIn('shipped', json.dumps(messages))
        self.assertNotIn('ORD-101', json.dumps(messages))

    def test_parse_error_history_retains_raw_assistant_and_explicit_error(self):
        env = Environment(fixture())
        parsed = parse_tool_output('not a tool')
        parsed['raw_text'] = 'not a tool'
        env.execute({'type': 'invalid_model_output', 'raw_text': 'not a tool'})
        messages = messages_for_view(env.public_view(), [parsed])
        self.assertEqual(messages[-2], {'role': 'assistant', 'content': 'not a tool'})
        self.assertEqual(messages[-1]['role'], 'user')
        self.assertIn('no tool was executed', messages[-1]['content'])
        self.assertFalse(any(message['role'] == 'tool' for message in messages))

    def test_history_length_mismatch_rejected_not_silently_dropped(self):
        env = Environment(fixture())
        env.execute(tool_call('get_order', identifier='ORD-101'))
        with self.assertRaises(ValueError):
            messages_for_view(env.public_view(), [])


class ModelLoopTests(unittest.TestCase):
    def test_grounded_success_retains_raw_generation_and_public_only_inputs(self):
        backend = FakeBackend([xml('get_order', identifier='ORD-101'),
                               xml('finish_task', answer='shipped', identifier='ORD-101')])
        generations, events, partials = [], [], []
        result, trajectory = run_model_case(fixture(), backend, config(),
            emit_generation=generations.append, emit_event=events.append, emit_partial=partials.append)
        self.assertTrue(result['success'])
        self.assertEqual(result['evidence_class'], 'model_inference')
        self.assertEqual(result['input_tokens'], 20)
        self.assertEqual(result['output_tokens'], 10)
        self.assertEqual(result['tool_attempts'], 1)
        self.assertEqual(len(generations), 2)
        self.assertEqual(generations[0]['raw_text'], xml('get_order', identifier='ORD-101'))
        self.assertNotIn('shipped', json.dumps(backend.requests[0]))
        self.assertNotIn('hidden', json.dumps(backend.requests))
        self.assertEqual(len(backend.requests[1]['messages']), 4)
        self.assertEqual(trajectory['events'][-1]['response']['code'], 'FINAL_RECORDED')
        self.assertEqual(len(events), len(partials))

    def test_full_failure_history_reaches_next_model_turn(self):
        backend = FakeBackend([xml('get_order', identifier='ORD-OLD'), xml('find_order', target='latest order'),
                               xml('get_order', identifier='ORD-101'),
                               xml('finish_task', answer='shipped', identifier='ORD-101')])
        result, _ = run_model_case(fixture('recoverable'), backend, config())
        self.assertTrue(result['success'])
        self.assertEqual(len(backend.requests[-1]['messages']), 8)
        self.assertIn('INVALID_IDENTIFIER', json.dumps(backend.requests[-1]['messages']))
        self.assertEqual([request['seed'] for request in backend.requests], [0, 1, 2, 3])

    def test_unparsed_model_output_consumes_attempt_budget(self):
        cfg = config()
        cfg['tool_budget'] = 2
        backend = FakeBackend(['not a call', 'still not a call'])
        result, trajectory = run_model_case(fixture(), backend, cfg)
        self.assertEqual(result['tool_attempts'], 2)
        self.assertEqual(result['parser_error_turns'], 2)
        self.assertTrue(result['budget_exhausted'])
        self.assertFalse(result['success'])
        self.assertFalse(result['has_invalid_loop'])
        self.assertEqual(trajectory['events'][0]['response']['code'], 'FORMAT_ERROR')

    def test_multi_call_turn_is_one_invalid_protocol_attempt_no_calls_executed(self):
        cfg = config()
        cfg['tool_budget'] = 1
        backend = FakeBackend([xml('get_order', identifier='ORD-101') * 2])
        result, trajectory = run_model_case(fixture(), backend, cfg)
        self.assertEqual(result['tool_attempts'], 1)
        self.assertEqual(trajectory['events'][0]['requested_tool_call_count'], 2)
        self.assertIsNone(trajectory['final_snapshot']['evidence'])

    def test_output_token_limit_marks_failure_and_keeps_raw_record(self):
        backend = FakeBackend([xml('get_order', identifier='ORD-101')], token_limit=True)
        generations = []
        result, _ = run_model_case(fixture(), backend, config(), emit_generation=generations.append)
        self.assertTrue(result['output_token_limit_reached'])
        self.assertTrue(result['budget_exhausted'])
        self.assertFalse(result['success'])
        self.assertEqual(result['tool_attempts'], 1)
        self.assertEqual(generations[0]['parse']['error'], 'OUTPUT_TOKEN_LIMIT')

    def test_final_claim_without_tool_evidence_is_false_success(self):
        backend = FakeBackend([xml('finish_task', answer='shipped', identifier='ORD-101')])
        result, _ = run_model_case(fixture(), backend, config())
        self.assertFalse(result['success'])
        self.assertTrue(result['false_success'])

    def test_generation_error_retains_partial_and_never_falls_back(self):
        backend = FakeBackend([RuntimeError('test backend failure')])
        generations, partials = [], []
        with self.assertRaisesRegex(RuntimeError, 'test backend failure'):
            run_model_case(fixture(), backend, config(), emit_generation=generations.append,
                           emit_partial=partials.append)
        self.assertEqual(len(backend.requests), 1)
        self.assertEqual(generations[0]['generation_error']['type'], 'RuntimeError')
        self.assertIsNone(generations[0]['output_tokens'])
        self.assertEqual(partials[0]['status'], 'generation_failed')

    def test_real_metrics_do_not_retain_synthetic_label(self):
        backend = FakeBackend([xml('get_order', identifier='ORD-101'),
                               xml('finish_task', answer='shipped', identifier='ORD-101')])
        result, _ = run_model_case(fixture(), backend, config())
        metrics = model_metrics([result])
        self.assertEqual(metrics['evidence_class'], 'model_inference')
        self.assertIn('not a native benchmark', metrics['warning'])
        self.assertEqual(metrics['groups'][0]['tokens_per_success']['numerator'], 30)

    def test_invalid_config_modes_and_thinking_fail_early(self):
        cfg = config()
        validate_config(cfg)
        for field, value in [('mode', 'fixed_failure_prefix'), ('evidence_class', 'synthetic_harness')]:
            changed = copy.deepcopy(cfg)
            changed[field] = value
            with self.assertRaises(ValueError):
                validate_config(changed)
        cfg['sampling']['enable_thinking'] = True
        with self.assertRaises(ValueError):
            validate_config(cfg)


class ModelProvenanceTests(unittest.TestCase):
    def make_manifest(self, directory):
        root = Path(directory) / 'model'
        root.mkdir()
        (root / 'model.safetensors').write_bytes(b'fake bytes only for metadata unit test')
        (root / 'config.json').write_text('{}')
        cfg = config()
        cfg['model']['model_path'] = str(root)
        manifest = {key: cfg['model'][key] for key in ('model_id', 'revision', 'model_path')}
        manifest['files'] = [{'path': path.name, 'size_bytes': path.stat().st_size,
                              'sha256': sha256_file(path)} for path in root.iterdir()]
        path = Path(directory) / 'manifest.json'
        path.write_text(json.dumps(manifest))
        cfg['external_inputs'] = [{'name': 'model_manifest', 'path': str(path), 'sha256': sha256_file(path)}]
        return cfg, path, root

    def test_manifest_identity_and_actual_file_hashes_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg, path, root = self.make_manifest(folder)
            digest, files = verify_model_manifest(cfg)
            self.assertEqual(digest, sha256_file(path))
            self.assertEqual(len(files), 2)
            (root / 'model.safetensors').write_bytes(b'altered')
            with self.assertRaisesRegex(ValueError, 'verification failed'):
                verify_model_manifest(cfg)

    def test_manifest_hash_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg, path, _ = self.make_manifest(folder)
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'manifest SHA256 mismatch'):
                verify_model_manifest(cfg)

    def test_unmanifested_model_files_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg, _, root = self.make_manifest(folder)
            (root / 'tokenizer_config.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'unmanifested'):
                verify_model_manifest(cfg)

    def test_manifest_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg, path, _ = self.make_manifest(folder)
            value = json.loads(path.read_text())
            value['files'][0]['path'] = '../escape.safetensors'
            path.write_text(json.dumps(value))
            cfg['external_inputs'][0]['sha256'] = sha256_file(path)
            with self.assertRaisesRegex(ValueError, 'unsafe'):
                verify_model_manifest(cfg)

    def test_randomly_initialized_missing_text_weights_rejected(self):
        for info in ({'missing_keys': ['model.layers.0.weight']}, {'mismatched_keys': ['lm_head.weight']},
                     {'unexpected_keys': ['language_model.layers.0.weight']}, {'error_msgs': ['bad load']}):
            with self.assertRaises(RuntimeError):
                check_loading_info(info)
        check_loading_info({'missing_keys': [], 'mismatched_keys': [], 'error_msgs': [],
                            'unexpected_keys': ['model.visual.patch_embed.weight']})

    def test_transformers_set_metadata_normalized_without_losing_fields(self):
        from toolloop_fix import model_backend
        info = {'missing_keys': set(), 'unexpected_keys': {'model.visual.z', 'model.visual.a'},
                'mismatched_keys': set(), 'error_msgs': [], 'conversion_errors': {}, 'skipped_pp_keys': set()}
        original = copy.deepcopy(info)
        check_loading_info(info)
        normalized = model_backend.normalize_loading_info(info)
        self.assertEqual(normalized, {'missing_keys': [], 'unexpected_keys': ['model.visual.a', 'model.visual.z'],
            'mismatched_keys': [], 'error_msgs': [], 'conversion_errors': {}, 'skipped_pp_keys': []})
        self.assertEqual(json.loads(json.dumps(normalized)), normalized)
        self.assertEqual(info, original)

    def test_missing_weight_set_still_raises_checkpoint_error_not_json_type_error(self):
        info = {'missing_keys': {'model.layers.0.weight'}, 'unexpected_keys': set(),
                'mismatched_keys': set(), 'error_msgs': []}
        with self.assertRaisesRegex(RuntimeError, 'complete text-model load'):
            check_loading_info(info)

    def test_presence_penalty_generated_unique_tokens_not_prompt_or_frequency(self):
        class Row(list):
            def __getitem__(self, item):
                value = super().__getitem__(item)
                return Row(value) if isinstance(item, slice) else value
            def tolist(self):
                return list(self)
        class Vector(list):
            def __isub__(self, penalty):
                return Vector([value - penalty for value in self])
        class Scores:
            def __init__(self):
                self.rows = [[10.0] * 5]
            def __getitem__(self, key):
                row, columns = key
                return Vector([self.rows[row][column] for column in columns])
            def __setitem__(self, key, values):
                row, columns = key
                for column, value in zip(columns, values):
                    self.rows[row][column] = value
        scores = Scores()
        output_presence_penalty(scores, [Row([1, 2, 3, 3, 4])], prompt_length=2, penalty=2.0)
        self.assertEqual(scores.rows[0], [10.0, 10.0, 10.0, 8.0, 8.0])


if __name__ == '__main__':
    unittest.main()
