"""Only a deterministic tokenizer double; these tests create no model evidence."""
import copy
import hashlib
import unittest

from scripts import verify_l4_output as v

CALL = '<tool_call><function=lookup_demo><parameter=identifier>DEMO-001</parameter></function></tool_call>'


class ToyTokenizer:
    pad_token_id = v.EOS[0]

    def __len__(self):
        return 248077

    def apply_chat_template(self, messages, **kwargs):
        assert kwargs['enable_thinking'] is False
        return 'fixture'

    def __call__(self, prompt, **kwargs):
        return {'input_ids':[10]}

    def decode(self, ids, **kwargs):
        return ''.join({1:CALL, 2:'text', v.EOS[0]:'<|endoftext|>'}[i] for i in ids)


def fixture():
    return dict(schema_version=1, scope='single_public_tool_call_proposal_only', status='completed', stage='completed',
                training_performed=False, tool_executed=False, model_id=v.probe.MODEL_ID, revision=v.probe.REVISION,
                verified_files=dict(v.probe.FILES), model_class='Qwen3_5ForCausalLM', parameter_device='cuda:0',
                parameter_dtype='torch.bfloat16', gpu=dict(name='NVIDIA L4',compute_capability=[8,9],memory_bytes=123),
                runtime=dict(python='3',cuda_version='12',packages=dict.fromkeys(
                    ['torch','transformers','huggingface-hub','tokenizers','safetensors'],'fixture')),
                messages=v.probe.MESSAGES, tools=v.probe.TOOLS, seed=0,
                generation_config=dict(do_sample=False,num_beams=1,use_cache=True,max_new_tokens=96,
                                       eos_token_id=v.EOS,pad_token_id=v.EOS[0]),
                prompt_text='fixture',prompt_sha256=hashlib.sha256(b'fixture').hexdigest(),prompt_token_ids=[10],
                input_tokens=1,generated_token_ids=[1,v.EOS[0]],output_tokens=2,token_limit_reached=False,
                raw_text=CALL+'<|endoftext|>',generation_seconds=0.1)


class ReceiptAuditTests(unittest.TestCase):
    def test_matching_proposal_no_business_action(self):
        result=v.verify(fixture(),ToyTokenizer())
        self.assertTrue(result['expected_call'])
        self.assertFalse(result['tool_executed'])

    def test_valid_tokens_wrong_proposal_is_not_transport_failure(self):
        x=fixture()
        x.update(generated_token_ids=[2,v.EOS[0]],raw_text='text<|endoftext|>')
        result=v.verify(x,ToyTokenizer())
        self.assertTrue(result['verified'])
        self.assertFalse(result['expected_call'])

    def test_reject_inconsistent_receipts(self):
        for field,value in [('revision','wrong'),('verified_files',{}),('parameter_dtype','float'),
                            ('prompt_token_ids',[11]),('prompt_sha256','0'*64),('raw_text','different'),
                            ('generated_token_ids',[v.EOS[0],v.EOS[0]]),('generated_token_ids',[1,2]),
                            ('output_tokens',3),('tool_executed',True),('seed',False),
                            ('gpu',dict(name='NVIDIA T4',compute_capability=[8,9],memory_bytes=123))]:
            x=fixture();x[field]=value
            with self.subTest(field=field), self.assertRaises(v.AuditError):
                v.verify(x,ToyTokenizer())

    def test_truncation_explicitly_not_a_call(self):
        x=fixture()
        x.update(generated_token_ids=[2]*96,raw_text='text'*96,output_tokens=96,token_limit_reached=True)
        r=v.verify(x,ToyTokenizer())
        self.assertEqual(r['stop_reason'],'token_limit')
        self.assertFalse(r['expected_call'])

    def test_sampling_rejected(self):
        x=copy.deepcopy(fixture())
        x['generation_config']['do_sample']=True
        with self.assertRaises(v.AuditError):
            v.verify(x,ToyTokenizer())
