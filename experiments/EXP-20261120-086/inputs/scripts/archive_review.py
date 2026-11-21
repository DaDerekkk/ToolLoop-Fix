#!/usr/bin/env python3
"""Pin and archive agent-written review artifacts without upgrading their labels."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import math
import struct


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();cfg=json.loads(Path(args.config).read_text());output=Path(args.output)
    names=[ref['name'] for ref in cfg['review_files']]
    if len(names)!=len(set(names)):
        raise ValueError('review archive names must be unique before copying')
    pinned=[]
    for ref in cfg['review_files']:
        source=Path(ref['path']);name=ref['name']
        if Path(name).name!=name or not name:raise ValueError('review name must be a basename')
        if sha(source)!=ref['sha256']:raise ValueError('review source hash changed')
        target=output/'review_sources'/name;target.parent.mkdir(exist_ok=True)
        if target.exists():raise FileExistsError('review target exists')
        shutil.copyfile(source,target)
        if sha(target)!=ref['sha256']:raise ValueError('review copy hash mismatch')
        pinned.append({**ref,'archived_path':str(target.relative_to(output))})
    report={'evidence_class':'analysis','review_files':pinned,
        'review_status':'agent_authored_not_human_gold','model_inference_performed':False,'training_performed':False,
        'note':'Archive/hash check does not independently validate semantic labels, hardware capabilities, or trainability.'}
    if cfg.get('semantic_review_name'):
        from check_semantic_refs import check
        report['semantic_reference_check']=check(output/'review_sources'/cfg['semantic_review_name'])
    if cfg.get('future_plan_name'):
        plan=json.loads((output/'review_sources'/cfg['future_plan_name']).read_text())
        if not plan['plan_only'] or plan['enabled'] is not False or plan['authorization']['training_authorized'] is not False:
            raise ValueError('future training must remain disabled')
        weights=Path(cfg['local_weights_path'])
        if sha(weights)!=plan['base_model']['weights_sha256']:
            raise ValueError('local base weights hash differs from future plan')
        with weights.open('rb') as f:
            header_size=struct.unpack('<Q',f.read(8))[0]
            header=json.loads(f.read(header_size))
        groups={}
        for key,value in header.items():
            if key=='__metadata__':continue
            group='language_model' if key.startswith('model.language_model.') else 'visual' if key.startswith('model.visual.') else 'mtp'
            total=groups.setdefault(group,{'tensors':0,'stored_scalars':0,'stored_bytes':0})
            total['tensors']+=1;total['stored_scalars']+=math.prod(value['shape'])
            total['stored_bytes']+=value['data_offsets'][1]-value['data_offsets'][0]
        for group,values in groups.items():
            if any(plan['read_only_header_inventory'][group][key]!=value for key,value in values.items()):
                raise ValueError('planned header counts differ from observed weights')
        report['future_plan_check']={'enabled':False,'header_inventory':groups,
            'header_bytes':header_size,'hardware_target_verified':False,
            'model_loaded':False,'training_performed':False,'note':'File-header arithmetic only, not live memory or gradient feasibility.'}
    with (output/'review_artifacts.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'review_artifacts':len(pinned),'all_hashes_verified':True}))


if __name__=='__main__':main()
