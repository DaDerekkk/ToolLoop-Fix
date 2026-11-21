"""Read-only checks against immutable, locally recorded authentic prefixes."""
import copy
import json
from pathlib import Path
import unittest
from scripts.build_fixed_prefixes import build_entry
from toolloop_fix.harness import Environment, state_hash

ROOT=Path(__file__).resolve().parents[1]


class PrefixBuilderTests(unittest.TestCase):
    def setUp(self):
        self.selections=json.loads((ROOT/'configs/iteration4_prefix_build.json').read_text())['selections']

    def test_six_prefixes_replay_and_have_correct_error_or_control(self):
        codes=['TIMEOUT','TIMEOUT','PRECONDITION','INVALID_IDENTIFIER','UNNECESSARY_CLARIFICATION','OK']
        for selection,code in zip(self.selections,codes):
            row=build_entry(selection)
            self.assertEqual(row['prefix_events'][-1]['response']['code'],code)
            self.assertIsNone(row['prefix_snapshot']['terminal'])
            self.assertEqual(len(row['prefix_generations']),selection['prefix_turns'])
            self.assertEqual(row['provenance']['source_case_sha256'],state_hash(row['original_case']))
            self.assertEqual(row['prefix_schema_version'],'authentic_tool_prefix_v1')

    def test_source_manifest_pin_rejected(self):
        item=copy.deepcopy(self.selections[0]);item['source_manifest_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'source manifest changed'):build_entry(item)

    def test_no_synthetic_terminal_or_invalid_prefix_salvage(self):
        for number,length in ((0,2),(1,2),(5,2)):
            item=copy.deepcopy(self.selections[number]);item['prefix_turns']=length
            with self.subTest(number=number),self.assertRaises(ValueError):build_entry(item)

    def test_missing_or_invalid_prefix_length_rejected(self):
        for length in (0,-1,True,999):
            item=copy.deepcopy(self.selections[0]);item['prefix_turns']=length
            with self.subTest(length=length),self.assertRaises(ValueError):build_entry(item)

    def test_source_not_mutated_and_rows_independent(self):
        item=copy.deepcopy(self.selections[0]);first=build_entry(item);second=build_entry(item)
        first['prefix_snapshot']['state']['value']='MUTATED'
        self.assertNotEqual(first['prefix_snapshot'],second['prefix_snapshot'])
        self.assertEqual(item,self.selections[0])
        self.assertEqual(build_entry(item),second)

    def test_correct_completion_control_not_a_new_recovery(self):
        row=build_entry(self.selections[-1]);env=Environment(row['original_case'])
        self.assertIsNone(env.snapshot()['evidence'])
        self.assertEqual(row['role'],'normal_completion_control')
        self.assertIsNotNone(row['prefix_snapshot']['evidence'])


if __name__=='__main__':unittest.main()
