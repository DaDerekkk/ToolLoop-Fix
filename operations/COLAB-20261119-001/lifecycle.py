"""Single-use client-side lifecycle. No other session may be stopped."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
import time
import uuid

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
CLI = str(Path.home()/'.local/bin/colab')
PAYLOAD = ROOT/'experiments/EXP-20261119-034/inputs/scripts/colab_inference_probe.py'
PIN = '99172529772f33e31d955c9f5dacfee448aa6f15326b3c030c6dbf3c8832401b'
NAME = 'tlfix-' + uuid.uuid4().hex[:10]
START = time.monotonic()
EVENTS = BASE/'lifecycle.jsonl'

def note(step, **fields):
    row = dict(step=step, utc=dt.datetime.now(dt.timezone.utc).isoformat(), elapsed_s=round(time.monotonic()-START,2), **fields)
    with EVENTS.open('a') as out: out.write(json.dumps(row)+'\n')
    print(json.dumps(row),flush=True)

def cli(step, *args, limit=35, stdin=None, cleanup=False):
    allowance = limit if cleanup else min(limit, 509-(time.monotonic()-START))
    if allowance < 1: raise RuntimeError('work_deadline')
    try:
        p = subprocess.run([CLI,*args],input=stdin or '',text=True,capture_output=True,timeout=allowance)
    except subprocess.TimeoutExpired:
        note(step, error='client_wall_timeout')
        raise RuntimeError('client_wall_timeout') from None
    out = p.stdout+'\n'+p.stderr
    # Never retain raw CLI diagnostics: signed URLs, kernel ids, account data may occur.
    note(step, exit=p.returncode, reason=next((s for s in ('Allocation refused','too many active sessions','Backend rejected accelerator','Session READY','Session terminated','Installation Complete','not found','Download failed','timed out') if s.lower() in out.lower()),None))
    return p.returncode, out

def assignments(step,cleanup=False):
    rc, out = cli(step,'sessions',cleanup=cleanup)
    if rc: raise RuntimeError('session_listing_failed')
    records = re.findall(r'^\[([^\]]+)\]\s+(\S+)\s+\| Hardware: ([^|]+?)\s+\|',out,re.M)
    if not records and 'No active sessions found on server.' not in out: raise RuntimeError('unrecognized_session_list')
    # Persist irreversible endpoint digest only; no remote identifiers/tokens.
    return {hashlib.sha256(ep.encode()).hexdigest():(name,hw.strip()) for name,ep,hw in records}

def usage(step,cleanup=False):
    rc, out=cli(step,'usage',cleanup=cleanup)
    if rc: raise RuntimeError('usage_failed')
    fields={k:float(re.search(p,out).group(1)) for k,p in [('balance_cu',r'Current balance:\s*([\d.]+)'),('rate_cu_hr',r'Usage rate:\s*([\d.]+)'),('assignments',r'Active assignments:\s*([\d]+)')]}
    note(step+'_observation',**fields)
    return fields

def main():
    EVENTS.touch(exist_ok=False)
    if hashlib.sha256(PAYLOAD.read_bytes()).hexdigest()!=PIN: raise RuntimeError('payload_pin')
    note('reservation',session=NAME,allowlist_sha256=PIN,training=False,work_deadline_seconds=509)
    before = assignments('baseline_sessions'); account=usage('baseline_usage')
    if any(n==NAME for n,_ in before.values()) or len(before)!=account['assignments']: raise RuntimeError('baseline_ambiguous')
    own=None; attempted=False; refused=False; completed=False; release=False
    try:
        attempted=True
        rc,out=cli('new','new','-s',NAME,'--gpu','L4',limit=55)
        refused=rc!=0 and ('Allocation refused' in out or 'Backend rejected accelerator' in out)
        if rc!=0 or 'Session READY' not in out:raise RuntimeError('allocation_failed')
        after=assignments('allocated_sessions')
        matches=[ep for ep,(n,hw) in after.items() if n==NAME and ep not in before and hw.upper()=='L4']
        if len(matches)!=1:raise RuntimeError('no_unique_new_L4')
        own=matches[0];note('new_endpoint_verified',endpoint_sha256=own)
        u=usage('allocated_usage')
        if set(after)-{own} != set(before) or u['assignments']!=len(after) or u['rate_cu_hr']-account['rate_cu_hr']>15:raise RuntimeError('unexpected_assignment_or_cost')
        rc,out=cli('status','status','-s',NAME)
        if rc or NAME not in out or 'Hardware: L4' not in out:raise RuntimeError('status_mismatch')
        # Only package metadata during precheck; no workspace input.
        rc,out=cli('version_check','exec','-s',NAME,'--timeout','45',limit=65,stdin="import importlib.metadata as _md\nprint('TRANSFORMERS_VERSION='+_md.version('transformers'))\n")
        if rc or 'TRANSFORMERS_VERSION=5.23.0' not in out:
            rc,out=cli('install_transformers','install','-s',NAME,'transformers==5.23.0',limit=95)
            if rc or 'Installation Complete' not in out:raise RuntimeError('install_unverified')
        rc,_=cli('single_inference','exec','-s',NAME,'-f',str(PAYLOAD),'--timeout','240',limit=280)
        file=BASE/'toolloop_probe.json'
        if file.exists():raise RuntimeError('prior_receipt')
        dl,_=cli('download_receipt','download','-s',NAME,'/content/toolloop_probe.json',str(file),limit=30)
        if dl or not file.is_file():raise RuntimeError('no_remote_receipt')
        result=json.loads(file.read_text());note('remote_receipt',status=result.get('status'),stage=result.get('stage'),sha256=hashlib.sha256(file.read_bytes()).hexdigest())
        completed=not rc and result.get('status')=='completed'
    except Exception as exc:
        note('work_failed',error=str(exc) if type(exc) is RuntimeError else type(exc).__name__)
    finally:
        try:
            if attempted and not refused and not own:
                current=assignments('discover_after_ambiguous_new',True)
                own=next((ep for ep,(n,hw) in current.items() if n==NAME and ep not in before),None)
            if own:
                rc,out=cli('stop_owned_only','stop','-s',NAME,limit=30,cleanup=True)
                note('unassign_ack',confirmed=rc==0 and 'Session terminated.' in out)
            for i in range(2):
                current=assignments('final_sessions',True); u=usage('final_usage',True)
                release=(own not in current and set(current)==set(before) and u['assignments']==len(before))
                note('cleanup_observed',new_assignment_absent=release,initial_assignments_unchanged=set(current)==set(before),owned_endpoint_known=bool(own))
                if release:break
                if i==0:time.sleep(2)
        except Exception as exc:
            note('cleanup_unconfirmed',error=type(exc).__name__)
    note('finished',fresh_inference_receipt=completed,baseline_assignments_restored=release,allocation_refused=refused)
    return 0 if completed and release else 1

if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
