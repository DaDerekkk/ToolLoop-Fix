"""After teardown only: pin one detached L4 operation and replay its public receipt offline."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(cond, label):
    if not cond:
        raise ValueError(label)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    cfg = json.loads(args.config.read_bytes())
    b = args.output/'operation_sources'
    b.mkdir(exist_ok=False)
    for item in cfg['sources']:
        name = item['name']
        require(name == Path(name).name, 'invalid archive name')
        raw = Path(item['path']).read_bytes()
        require(sha(raw) == item['sha256'], 'source changed')
        with (b/name).open('xb') as f:
            f.write(raw)
    records = [json.loads(l) for l in (b/'lifecycle.jsonl').read_bytes().splitlines()]
    event = {r['step']:r for r in records}
    require(records[0]['step']=='reservation' and records[-1]['step']=='finished' and
            [r['elapsed_s'] for r in records] == sorted(r['elapsed_s'] for r in records), 'incomplete timeline')
    pre = json.loads((b/'preflight_config.json').read_bytes())
    require(sha((b/'lifecycle.py').read_bytes()) == pre['controller_sha256'] and pre['training'] is False and
            event['reservation']['source_sha256'] == pre['source_sha256'], 'controller or sources not bound')
    for name,want in pre['source_sha256'].items():
        require(sha((b/name).read_bytes()) == want, 'worker payload SHA')
    use = [event[k+'_usage_observation'] for k in ('baseline','allocated','final')]
    release = event['cleanup_observed']
    require(event['new']['exit'] == 0 and 'owned_l4' in event and event['stop_ack']['confirmed'] is True
            and event['stop_owned_only']['exit']==0 and release['owned_absent'] is True and
            event['finished']['owned_released'] is True, 'allocation/release mismatch')
    stages = [r['stage'] for r in records if r['step']=='worker_stage']
    armed = 'arm_single_worker' in event and event['arm_single_worker']['exit'] == 0
    sentinel = 'two_way_sentinel' in event and event['two_way_sentinel']['identical'] is True
    if sentinel:
        fixture = dict(schema_version=1,scope='single_public_tool_call_proposal_only',training_performed=False,
                       status='failed',stage='sentinel_no_model',tool_executed=False)
        raw = (b/'sentinel.json').read_bytes()
        require(raw==(json.dumps(fixture,sort_keys=True)+'\n').encode()
                and event['two_way_sentinel']['bytes']==len(raw), 'sentinel mismatch')
    if armed:
        require(sentinel and (b/'armed.txt').read_text().strip()==event['reservation']['receipt_nonce'], 'arm mismatch')
    rec = b/'toolloop_probe.json'
    observed=event.get('local_receipt')
    require(rec.exists()==bool(observed), 'missing or orphaned receipt')
    verified = False
    proposal = None
    if observed:
        raw = rec.read_bytes()
        r = json.loads(raw)
        require(armed and sha(raw)==observed['sha256'] and observed['status']==r['status']
                and observed['stage']==r['stage'] and observed['output_tokens']==r.get('output_tokens')
                and observed['error_type']==r.get('error_type'), 'receipt not bound to collector')
        checker = Path(__file__).with_name('verify_l4_output.py')
        require(sha(checker.read_bytes())==cfg['frozen_checker_sha256'], 'incorrect offline verifier')
        out = args.output/'l4_output_verification.json'
        proc=subprocess.run([sys.executable,str(checker),'--expected-sha256',observed['sha256'],
                             '--receipt',str(rec),'--model-dir',cfg['local_model_dir'],'--output',str(out)])
        audit=json.loads(out.read_bytes())
        require(audit['receipt_sha256']==observed['sha256'] and (proc.returncode==0) is audit['verified']
                and proc.returncode in (0,1), 'verifier mismatch')
        verified,proposal=audit['verified'],audit.get('expected_call')
    report = dict(evidence_class='analysis', operation=cfg['operation'], local_generations=0,
                  model_evidence_source='remote model_inference' if verified else None,
                  remote_output_verified=verified, remote_expected_proposal=proposal,
                  source_count=len(cfg['sources']), received_remote_status=observed['status'] if observed else None,
                  received_remote_stage=observed['stage'] if observed else None, remote_stages=stages,
                  received_receipt_sha256=observed['sha256'] if observed else None,
                  two_transport_sentinel_verified=sentinel, worker_activation_confirmed=armed,
                  original_assignments_unchanged=release['original_set_unchanged'], owned_released=True,
                  account_assignments=[r['assignments'] for r in use], account_rates_cu_hr=[r['rate_cu_hr'] for r in use],
                  gross_client_elapsed_s=records[-1]['elapsed_s'], exact_session_bill=None, tool_dispatched=False,
                  training_performed=False, collector_error=event.get('work_failed',{}).get('code'))
    with (args.output/'l4_detached_audit.json').open('x') as f:
        json.dump(report,f,indent=2);f.write('\n')
    print(json.dumps(report))


if __name__=='__main__':
    main()
