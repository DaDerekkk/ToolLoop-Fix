#!/usr/bin/env python3
"""Pre-generation equality and official tokenizer checks on fixed-prefix cells."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from toolloop_fix.harness import state_hash
from toolloop_fix.model_backend import verify_model_manifest
from toolloop_fix.prefix_run import prepare_prefix, validate_config


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();root=Path(__file__).resolve().parents[1];cfg=json.loads(Path(args.config).read_text())
    configs=[json.loads((root/name).read_text()) for name in cfg['inference_configs']]
    cells={(c['wording_profile'],c['comparison_arm']) for c in configs}
    if cells!={(w,a) for w in ('legacy','explicit') for a in ('B0','B1','B2')}:raise ValueError('six unique cells required')
    def shared(c):return {k:v for k,v in c.items() if k not in ('comparison_arm','wording_profile','policy_name')}
    if any(shared(c)!=shared(configs[0]) for c in configs):raise ValueError('uncontrolled configuration change')
    verify_model_manifest(configs[0])
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(configs[0]['model']['model_path'],local_files_only=True,trust_remote_code=False)
    all_rows={row['case_id']:row for row in (json.loads(l) for l in (root/configs[0]['cases_path']).read_text().splitlines())}
    checked=[];by_case={}
    for c in configs:
        validate_config(c)
        for cid in c['case_ids']:
            row=all_rows[cid];prepared=prepare_prefix(row,c);context=prepared['context']
            messages,tools=prepared['messages'],prepared['tools']
            previous=by_case.setdefault(cid,[])
            for old_cfg,old in previous:
                if context['prefix_snapshot_sha256']!=old['context']['prefix_snapshot_sha256'] or context['prefix_public_history_sha256']!=old['context']['prefix_public_history_sha256']:
                    raise ValueError('starting snapshot/history differs')
                if tools!=old['tools'] or messages[2:]!=old['messages'][2:]:raise ValueError('tool/history differs between cells')
                for key in ('remaining_tool_budget','remaining_turn_budget','prefix_turns','goal_at_prefix'):
                    if context[key]!=old['context'][key]:raise ValueError('budget/goal starting condition differs')
                if c['wording_profile']==old_cfg['wording_profile'] and messages[1]!=old['messages'][1]:raise ValueError('within-profile user task differs')
                if c['comparison_arm'] in ('B0','B2') and old_cfg['comparison_arm'] in ('B0','B2') and messages[0]!=old['messages'][0]:raise ValueError('B0/B2 system prompt differs')
            previous.append((c,prepared))
            prompt=tokenizer.apply_chat_template(messages,tools=tools,tokenize=False,add_generation_prompt=True,enable_thinking=False)
            ids=tokenizer(prompt,add_special_tokens=False)['input_ids']
            if len(ids)+c['sampling']['max_new_tokens']>c['max_context_tokens']:raise ValueError('context overflow')
            public=json.dumps({'messages':messages,'tools':tools},ensure_ascii=False)
            for private in ('"hidden"','"category"','"template_group"','"prefix_role"'):
                if private in prompt:raise ValueError('evaluation metadata in prompt')
            for key in ('value','valid_identifier'):
                value=row['original_case']['hidden'][key]
                if value in prompt and value not in public:raise ValueError('unseen target in rendered prompt')
            checked.append({'evidence_class':'analysis','case_id':cid,'wording_profile':c['wording_profile'],
                'comparison_arm':c['comparison_arm'],'first_continuation_seed':c['case_seeds'][cid]+context['prefix_turns'],
                'rendered_prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'prompt_tokens':len(ids),
                'system_prompt_sha256':hashlib.sha256(messages[0]['content'].encode()).hexdigest(),
                'messages_sha256':state_hash(messages),'tools_sha256':state_hash(tools),
                'prefix_context':context,'new_model_generation_performed':False})
    output=Path(args.output)
    with (output/'preflight_contexts.jsonl').open('x') as f:
        for row in checked:f.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n')
    report={'evidence_class':'analysis','cells':len(cells),'prefix_count':len(by_case),'contexts_verified':len(checked),
        'shared_snapshot_history_budget_verified':True,'only_initial_user_changes_for_wording':True,
        'B0_B2_system_prompt_identical':True,'official_tokenizer_render_passed':True,
        'max_prompt_tokens':max(row['prompt_tokens'] for row in checked),'model_loaded':False,'training_performed':False,
        'warning':'Source prefix transplant and prospective task-contract clarification; not natural error incidence or semantic success validation.'}
    with (output/'context_gate.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
