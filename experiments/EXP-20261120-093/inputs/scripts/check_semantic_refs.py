#!/usr/bin/env python3
"""Check source references and non-training status, not semantic correctness."""
import argparse
import hashlib
import json
from pathlib import Path


def check(path):
    review=json.loads(Path(path).read_text());cache={};counts={}
    if review['old_automatic_metrics_changed'] is not False or review['training_eligible'] is not False:
        raise ValueError('review must not regrade old runs or be training eligible')
    items=review['items']+review['supplementary_exact_structured_controls']
    for item in items:
        source=Path(item['source_generations_path'])
        if source not in cache:
            cache[source]=(hashlib.sha256(source.read_bytes()).hexdigest(),[json.loads(l) for l in source.read_text().splitlines()])
        digest,rows=cache[source]
        if digest!=item['source_generations_sha256']:raise ValueError('source hash mismatch')
        row=rows[item['generation_line_1based']-1]
        if any(row[k]!=item[k] for k in ('case_id','turn','raw_text')):raise ValueError('source row mismatch')
        if item['review_status']!='agent_review_pending_human' or item['training_eligible'] is not False or item['human_reviewer'] is not None:
            raise ValueError('review status upgraded without human evidence')
        counts[item['agent_review_label']]=counts.get(item['agent_review_label'],0)+1
    return {'evidence_class':'analysis','source_rows_checked':len(items),'queue_items':len(review['items']),
            'supplementary_controls':len(review['supplementary_exact_structured_controls']),
            'agent_label_counts_not_performance_metrics':counts,'source_refs_valid':True,
            'human_review_performed':False,'training_labels_created':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();report=check(args.input)
    with (Path(args.output)/'semantic_reference_check.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
