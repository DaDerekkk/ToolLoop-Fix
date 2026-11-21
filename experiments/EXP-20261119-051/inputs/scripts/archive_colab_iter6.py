"""Archive checksum-pinned public evidence. Run only locally, after both sessions close."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def require(ok, label):
    if not ok:
        raise ValueError(label)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def archive(config, out):
    dst = out / 'operation_sources'
    dst.mkdir(exist_ok=False)
    for ref in config['sources']:
        src = Path(ref['path'])
        rel = Path(ref['archived_as'])
        require(not rel.is_absolute() and '..' not in rel.parts, 'archive path')
        data = src.read_bytes()
        require(digest(data) == ref['sha256'], 'source SHA256 mismatch')
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as f:
            f.write(data)
    results = []
    for op, pre in [('002','038'), ('003','039')]:
        base = dst / op
        events = [json.loads(l) for l in (base / 'lifecycle.jsonl').read_text().splitlines()]
        by = {e['step']:e for e in events}
        require(events[0]['step'] == 'reservation' and events[-1]['step'] == 'finished', 'incomplete lifecycle')
        require([e['elapsed_s'] for e in events] == sorted(e['elapsed_s'] for e in events), 'time order')
        plan = json.loads((dst / ('preflight_' + pre + '_config.json')).read_bytes())
        require(digest((base/'lifecycle.py').read_bytes()) == plan['controller_sha256'], 'controller not frozen')
        for key, name in [('probe_sha256','colab_inference_probe.py'), ('codec_sha256','colab_receipt_transport.py')]:
            require(digest((dst/name).read_bytes()) == plan[key] == events[0][key], 'public source not frozen')
        require(events[0]['training'] is False and plan['training'] is False and plan['gpu'] == 'L4', 'planned scope')
        require(by['stop_ack']['confirmed'] is True and by['stop_owned_only']['exit'] == 0 and
                by['cleanup_observed']['owned_absent'] is True and by['finished']['owned_released'] is True, 'release')
        usage = [by[k+'_usage_observation'] for k in ('baseline','allocated','final')]
        row = dict(operation='COLAB-20261119-'+op, provider_gpu='L4', owned_released=True,
                   initial_set_unchanged=by['cleanup_observed']['original_set_unchanged'],
                   account_assignments=[u['assignments'] for u in usage],
                   account_rates_cu_hr=[u['rate_cu_hr'] for u in usage], exact_session_bill=None,
                   gross_client_seconds=events[-1]['elapsed_s'],
                   sentinel_received=False, generation_cell_submitted='single_generation_cell' in by,
                   receipt_received=False, verified_generations=0, proposal_matches=None,
                   training_performed=False)
        require('owned_l4' in by and usage[1]['assignments'] == usage[0]['assignments']+1, 'allocation')
        sentinel = base/'sentinel.json'
        if sentinel.exists():
            raw = sentinel.read_bytes()
            fixture = dict(schema_version=1,scope='single_public_tool_call_proposal_only',status='failed',
                           stage='sentinel_no_model',training_performed=False,tool_executed=False)
            require(raw == (json.dumps(fixture,sort_keys=True)+'\n').encode() and
                    by['sentinel_identical']['nbytes'] == len(raw), 'sentinel')
            row['sentinel_received'] = True
        else:
            require('sentinel_identical' not in by, 'missing archived sentinel')
        if 'work_failed' in by:
            row['failure_code'] = by['work_failed']['code']
        if 'single_generation_cell' in by:
            runcell = by['single_generation_cell']
            row.update(generation_cell_error=runcell.get('error'), generation_cell_stdout_bytes=runcell.get('stdout_bytes'),
                       same_file_read_stdout_bytes=by.get('reread_same_file_no_generate',{}).get('stdout_bytes'))
        fallback = base/'contents_readback.jsonl'
        if fallback.exists():
            readback = [json.loads(l) for l in fallback.read_text().splitlines()]
            row.update(contents_sentinel_matched=any(r.get('sentinel_matched') is True for r in readback),
                       contents_receipt_received=any(r.get('receipt_complete') is True for r in readback))
        rec = base/'toolloop_probe.json' 
        require(('local_receipt' in by) == rec.exists(), 'missing or unexpected archived receipt')
        if rec.exists():
            raw = rec.read_bytes()
            receipt = json.loads(raw)
            observed = by['local_receipt']
            require(row['sentinel_received'] and row['generation_cell_submitted']
                    and observed['sha256'] == digest(raw) and observed['status'] == receipt['status']
                    and observed['stage'] == receipt['stage'], 'uncorrelated receipt')
            row.update(receipt_received=True, receipt_sha256=digest(raw), remote_reported_status=receipt['status'],
                       remote_reported_stage=receipt['stage'], remote_reported_error=receipt.get('error_type'))
            if receipt['status'] == 'completed':
                verification = out / (op+'_output_verification.json')
                p = subprocess.run([sys.executable, str(Path(__file__).with_name('verify_l4_output.py')),
                                    '--receipt',str(rec),'--model-dir',config['local_model_dir'],
                                    '--expected-sha256',digest(raw),'--output',str(verification)])
                audit = json.loads(verification.read_bytes())
                require(p.returncode in (0,1) and (p.returncode == 0) == audit['verified']
                        and audit['receipt_sha256'] == digest(raw), 'incomplete verifier output')
                row.update(verified_generations=int(audit['verified']),proposal_matches=audit.get('expected_call'),
                           output_verification=verification.name)
        require(not by['finished']['received_completed_generation'] or row['receipt_received'], 'missing completed receipt')
        row['actual_remote_generations'] = (1 if row['verified_generations'] else
                                           None if row['generation_cell_submitted'] else 0)
        results.append(row)
    report = dict(evidence_class='analysis', local_model_generations=0,
                  source_class_if_verified='remote model_inference', operations=results,
                  remote_verified_generations=sum(r['verified_generations'] for r in results),
                  tool_was_executed=False, training=False, source_count=len(config['sources']),
                  limitations='Client/VM evidence and tokenizer replay, not signed attestation, task completion, recovery rate or training.')
    with (out/'colab_iter6_audit.json').open('x') as f:
        json.dump(report,f,indent=2,ensure_ascii=False);f.write('\n')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    archive(json.loads(a.config.read_bytes()), a.output)
