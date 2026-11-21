"""One named L4. Frozen public payload; validate transport before generation, read same file on loss."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import types
import uuid

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]
FROZEN=ROOT/'experiments/EXP-20261119-038/inputs/scripts'
def trusted(path, pin):
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=pin: raise ValueError('source_hash_mismatch')
    return raw.decode()
def module(source, name, path):
    m=types.ModuleType(name); m.__file__=str(path)
    exec(compile(source,str(path),'exec'),m.__dict__)
    return m
corepath=ROOT/'experiments/EXP-20261119-035/operation_sources/lifecycle.py'
core=module(trusted(corepath,'34d37ff12069f1136ed1a6c01ba5c23ed4a15e5d41463654402366a0ba0f9cde'),'session_helpers',corepath)
nonce=uuid.uuid4().hex
core.BASE=BASE;core.EVENTS=BASE/'lifecycle.jsonl';core.NAME='tlfix-'+nonce[:12]
CLI=core.CLI
note=core.note
PROBE_PIN='99172529772f33e31d955c9f5dacfee448aa6f15326b3c030c6dbf3c8832401b'
CODEC_PIN='4deb6bef46d5b011239b6a6e26ff55b66070443abb6a080bb9cac5c0283c8b64'
probe=trusted(FROZEN/'colab_inference_probe.py',PROBE_PIN)
codec=trusted(FROZEN/'colab_receipt_transport.py',CODEC_PIN)
tx=module(codec,'codec',FROZEN/'colab_receipt_transport.py')
work='/content/'+core.NAME
sent_nonce=uuid.uuid4().hex
public_fixture={"schema_version":1,"scope":"single_public_tool_call_proposal_only","status":"failed","stage":"sentinel_no_model","training_performed":False,"tool_executed":False}
SENT_BYTES=(json.dumps(public_fixture,sort_keys=True)+'\n').encode()
# Do not print unfiltered SDK/CLI diagnostics (may include account or signed URLs).
DRIVER="""from pathlib import Path
import re
from probe import main, run_probe
def observed(r):
    try: run_probe(r)
    except Exception as e:
        chain=[]; cur=e
        for _ in range(4):
            if cur is None: break
            typ=type(cur).__name__
            if re.fullmatch('[A-Za-z0-9_]{1,48}',typ): chain.append(typ)
            missing=getattr(cur,'name',None)
            if isinstance(missing,str) and re.fullmatch('[A-Za-z0-9_.]{1,64}',missing): chain.append('module.'+missing)
            code=getattr(getattr(cur,'response',None),'status_code',None)
            if type(code) is int: chain.append('http'+str(code))
            for k in ['unexpected EOS','CUDA required','incomplete checkpoint','unexpected nonvision','public snapshot file hash mismatch','torchvision::nms','cannot import name','no matching distribution','No space left']:
                if k.lower() in str(cur).lower(): chain.append('class.'+re.sub('[^a-zA-Z0-9]','_',k))
            cur=cur.__cause__ or cur.__context__
        print('TLP_SAFE_DIAG='+','.join(chain),flush=True)
        raise
main(Path(__file__).resolve().parent/'toolloop_probe.json', observed)
"""
def cell(step,code,limit):
    remain=509-(time.monotonic()-core.START)
    if remain<2: raise RuntimeError('work_deadline')
    try:
        p=subprocess.run([CLI,'exec','-s',core.NAME,'--timeout',str(min(limit,400))],input=code.encode(),capture_output=True,timeout=min(limit+10,remain))
        out=p.stdout
        note(step,exit=p.returncode,stdout_bytes=len(out),cell_sha256=hashlib.sha256(code.encode()).hexdigest())
        for item in re.findall(rb'^TLP_SAFE_DIAG=([a-zA-Z0-9_.,]{1,380})\r?$',out,re.M):
            note('sanitized_remote_diagnostic',codes=item.decode())
        return out
    except subprocess.TimeoutExpired as e:
        note(step,error='client_wall_timeout');return e.stdout or b''
def extract(out,n,path):
    try: tx.receive(io.BytesIO(out),n,path);return True
    except (ValueError,OSError):return False

INIT=f"""import sys, os, json, subprocess
from pathlib import Path
W=Path({work!r}); W.mkdir(exist_ok=False); (W/'sentinel').mkdir()
(W/'tx.py').write_text({codec!r}); (W/'probe.py').write_text({probe!r}); (W/'driver.py').write_text({DRIVER!r})
sys.path.insert(0,str(W))
import tx
sf=W/'sentinel/toolloop_probe.json';sf.write_bytes({SENT_BYTES!r})
print(tx.emit(sf,{sent_nonce!r}),flush=True)
"""
READ=f"""from pathlib import Path
import sys
W=Path({work!r});sys.path.insert(0,str(W))
import tx
print(tx.emit(W/'toolloop_probe.json',{nonce!r}),flush=True)
"""
RUN=f"""import os,sys,subprocess,re
from pathlib import Path
W=Path({work!r})
env=dict(os.environ, HF_HUB_DISABLE_XET='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1',HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_PROGRESS_BARS='1',TQDM_DISABLE='1', TOKENIZERS_PARALLELISM='false',PYTHONDONTWRITEBYTECODE='1')
p=subprocess.run([sys.executable,str(W/'driver.py')],env=env,cwd=str(W),capture_output=True,text=True,timeout=365)
for line in p.stdout.splitlines():
    if re.fullmatch(r'TLP_SAFE_DIAG=[a-zA-Z0-9_.,]{{1,380}}',line): print(line,flush=True)
print('TLP_CHILD_EXIT='+str(p.returncode),flush=True)
"""+READ
def go():
    core.EVENTS.touch(exist_ok=False)
    note('reservation',session=core.NAME,receipt_nonce=nonce,probe_sha256=PROBE_PIN,codec_sha256=CODEC_PIN,driver_sha256=hashlib.sha256(DRIVER.encode()).hexdigest(),training=False)
    before=core.assignments('baseline_sessions'); u0=core.usage('baseline_usage')
    if len(before)!=u0['assignments'] or any(n==core.NAME for n,_ in before.values()):raise RuntimeError('ambiguous_baseline')
    assigned=False; own=None; released=done=False
    try:
        assigned=True; rc,_=core.cli('new','new','-s',core.NAME,'--gpu','L4',limit=60)
        after=core.assignments('post_new_sessions')
        own=next((ep for ep,(n,hw) in after.items() if n==core.NAME and ep not in before),None)
        if rc or not own or after[own][1].upper()!='L4' or set(after)-{own}!=set(before):raise RuntimeError('new_l4_not_isolated')
        note('owned_l4',endpoint_sha256=own)
        u=core.usage('allocated_usage')
        if u['assignments']!=len(after) or u['rate_cu_hr']-u0['rate_cu_hr']>15:raise RuntimeError('unexpected_rate_or_count')
        out=cell('public_transport_sentinel',INIT,40)
        if not extract(out,sent_nonce,BASE/'sentinel.json') or (BASE/'sentinel.json').read_bytes()!=SENT_BYTES:raise RuntimeError('sentinel_not_identical')
        note('sentinel_identical',nbytes=len(SENT_BYTES))
        meta=cell('package_check',"import importlib.metadata as md\nprint('TLP_VERSION='+md.version('transformers'))\n",30)
        if b'TLP_VERSION=5.23.0' not in meta.splitlines():
            rc,out=core.cli('install','install','-s',core.NAME,'transformers==5.23.0',limit=95)
            if rc or 'Installation Complete' not in out:raise RuntimeError('install_failed')
        rcpt=BASE/'toolloop_probe.json'
        out=cell('single_generation_cell',RUN,365)
        child=re.search(rb'^TLP_CHILD_EXIT=(-?\d+)$',out,re.M)
        if child:note('child_exit',code=int(child.group(1)))
        if not extract(out,nonce,rcpt):
            out=cell('reread_same_file_no_generate',READ,30)
            if not extract(out,nonce,rcpt):raise RuntimeError('missing_valid_frame')
        r=json.loads(rcpt.read_bytes()); done=r.get('status')=='completed' and bool(r.get('generated_token_ids'))
        note('local_receipt',sha256=hashlib.sha256(rcpt.read_bytes()).hexdigest(),status=r.get('status'),stage=r.get('stage'),error_type=r.get('error_type'),output_tokens=r.get('output_tokens'))
    except Exception as exc:
        note('work_failed',code=str(exc) if type(exc) is RuntimeError else type(exc).__name__)
    finally:
        # No shutdown based solely on a local alias; match the new name against server-side set.
        for attempt in range(3):
            try:
                current=core.assignments('release_sessions_check',True)
                if not own and assigned:
                    own=next((ep for ep,(n,_) in current.items() if n==core.NAME and ep not in before),None)
                if own in current and current[own][0]==core.NAME:
                    rc,out=core.cli('stop_owned_only','stop','-s',core.NAME,limit=30,cleanup=True)
                    note('stop_ack',confirmed=not rc and 'Session terminated' in out)
                final=core.assignments('final_sessions',True); f=core.usage('final_usage',True)
                released=own not in final and not any(n==core.NAME for n,_ in final.values()) and len(final)==f['assignments']
                note('cleanup_observed',owned_absent=released,original_set_unchanged=set(final)==set(before),active_count=len(final))
                if released:break
            except Exception as exc:note('cleanup_check_error',error=type(exc).__name__)
            time.sleep(1)
    note('finished',received_completed_generation=done,owned_released=released)
    return 0 if done and released else 1
if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(go())
