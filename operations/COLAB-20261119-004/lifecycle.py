"""Own L4 only. Short kernel bootstrap, no long exec; a public nonce gates single detached execution."""
import base64, hashlib, io, json, re, signal, subprocess, tempfile, time, types, uuid, zlib
from pathlib import Path

B=Path(__file__).resolve().parent
R=B.parents[1]
def sha(b):return hashlib.sha256(b).hexdigest()
def source(p,h):
    b=p.read_bytes()
    if sha(b)!=h:raise ValueError('pin mismatch')
    return b.decode()
def module(s,n,p):
    m=types.ModuleType(n);m.__file__=str(p);exec(compile(s,str(p),'exec'),m.__dict__);return m
p=R/'experiments/EXP-20261119-035/operation_sources/lifecycle.py'
c=module(source(p,'34d37ff12069f1136ed1a6c01ba5c23ed4a15e5d41463654402366a0ba0f9cde'),'helpers',p)
C=R/'experiments/EXP-20261119-041'
cfg=json.loads((C/'config.json').read_text()); nonce=uuid.uuid4().hex; ticket=uuid.uuid4().hex
c.BASE=B;c.EVENTS=B/'lifecycle.jsonl';c.NAME='tlfix-'+nonce[:12]
name=c.NAME;w='/content/'+name;note=c.note
sources={k:source(path,cfg['source_sha256'][k]) for k,path in {
 'probe.py':C/'inputs/scripts/colab_inference_probe.py','tx.py':C/'inputs/scripts/colab_receipt_transport.py','worker.py':B/'worker.py'}.items()}
if sha(Path(__file__).read_bytes())!=cfg['controller_sha256']:raise ValueError('controller not pinned')
tx=module(sources['tx.py'],'codec',C/'inputs/scripts/colab_receipt_transport.py')
sent=(json.dumps(dict(schema_version=1,scope='single_public_tool_call_proposal_only',status='failed',stage='sentinel_no_model',training_performed=False,tool_executed=False),sort_keys=True)+'\n').encode()
boot=f"""from pathlib import Path
import os,sys,subprocess
W=Path({w!r});W.mkdir(exist_ok=True)
for k,v in {dict(sources,**{'nonce.txt':nonce})!r}.items():
    f=W/k
    if f.exists(): assert f.read_text()==v
    else: f.write_text(v)
sys.path.insert(0,str(W))
import tx
S=W/'sentinel';S.mkdir(exist_ok=True); f=S/'toolloop_probe.json';f.write_bytes({sent!r});frame=tx.emit(f,{ticket!r})+'\\n';(W/'sentinel_frame.txt').write_text(frame)
if not (W/'started.lock').exists():
    subprocess.Popen([sys.executable,str(W/'worker.py')],cwd=str(W),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True,start_new_session=True)
print(frame,end='',flush=True)
"""
pack="import base64,zlib;exec(compile(zlib.decompress(base64.b64decode("+repr(base64.b64encode(zlib.compress(boot.encode())).decode())+")), '<public-bootstrap>', 'exec'))"
def cli(args,secs,inp=b''):
    remain=505-(time.monotonic()-c.START)
    if remain<1:raise RuntimeError('wall_deadline')
    try:
        proc=subprocess.run([c.CLI,*args],input=inp,capture_output=True,timeout=min(secs,remain))
        return proc.returncode,proc.stdout
    except subprocess.TimeoutExpired as e:return None,e.stdout or b''
def cell(label,code,secs):
    rc,out=cli(['exec','-s',name,'--timeout',str(secs)],secs+5,code.encode())
    note(label,exit=rc,stdout_bytes=len(out),cell_sha256=sha(code.encode()))
    return out
def get(fn):
    assert fn in ('sentinel_frame.txt','result_frame.txt','stage.txt','diagnostic.txt')
    with tempfile.TemporaryDirectory() as td:
        x=Path(td)/'public'
        rc,_=cli(['download','-s',name,w+'/'+fn,str(x)],10)
        return x.read_bytes() if rc==0 and x.exists() and x.stat().st_size<=110000 else b''
def decode(buf,n,path):
    try:tx.receive(io.BytesIO(buf),n,path);return True
    except (ValueError,OSError):return False
def run():
    c.EVENTS.touch(exist_ok=False)
    note('reservation',session=name,receipt_nonce=nonce,training=False,source_sha256=cfg['source_sha256'],time_goal=600)
    before=c.assignments('baseline_sessions'); u0=c.usage('baseline_usage')
    if len(before)!=u0['assignments'] or any(n==name for n,h in before.values()):raise RuntimeError('ambiguous_baseline')
    own=None; released=ok=False
    try:
        rc,_=c.cli('new','new','-s',name,'--gpu','L4',limit=60)
        cur=c.assignments('allocated_sessions')
        own=next((k for k,(n,h) in cur.items() if n==name and k not in before),None)
        if rc or not own or cur[own][1].upper()!='L4' or set(cur)-{own}!=set(before):raise RuntimeError('new_l4_not_isolated')
        note('owned_l4',endpoint_sha256=own);u=c.usage('allocated_usage')
        if u['assignments']!=len(cur) or u['rate_cu_hr']-u0['rate_cu_hr']>15:raise RuntimeError('rate_or_count')
        rc,o=c.cli('status','status','-s',name)
        if rc or 'Hardware: L4' not in o:raise RuntimeError('status_mismatch')
        p="print('TLP_READY',flush=True)"
        if b'TLP_READY' not in cell('warm',p,75).splitlines() and b'TLP_READY' not in cell('warm_again',p,45).splitlines():raise RuntimeError('kernel_unreachable')
        buf=cell('bootstrap_no_model_until_armed',pack,40)
        s=B/'sentinel.json'
        if not decode(buf,ticket,s):
            if not decode(get('sentinel_frame.txt'),ticket,s):
                # Safe before arm only. Worker lock forbids duplicate generation.
                cell('idempotent_bootstrap_no_model',pack,40)
                if not decode(get('sentinel_frame.txt'),ticket,s):raise RuntimeError('sentinel_lost')
        if s.read_bytes()!=sent:raise RuntimeError('sentinel_different')
        # Validate the independent transport and worker presence before spending on the model.
        with tempfile.TemporaryDirectory() as td:
            other=Path(td)/'sent'
            if not decode(get('sentinel_frame.txt'),ticket,other) or other.read_bytes()!=sent:raise RuntimeError('contents_not_verified')
        note('two_way_sentinel',identical=True,bytes=len(sent))
        ready=b''
        for _ in range(3):
            ready=get('stage.txt').strip()
            if ready==b'waiting_for_arm':break
            time.sleep(1)
        if ready!=b'waiting_for_arm':raise RuntimeError('detached_worker_not_ready')
        arm=B/'armed.txt'
        with arm.open('x') as f:f.write(nonce+'\n')
        rc,_=cli(['upload','-s',name,str(arm),w+'/armed.txt'],18)
        note('arm_single_worker',exit=rc)
        if rc!=0:raise RuntimeError('activation_not_acknowledged')
        last=None;result=B/'toolloop_probe.json'
        stages={'waiting_for_arm','package_check','install_transformers','runtime_check','anonymous_public_download','verify_public_snapshot','local_model_load','render_public_prompt','generate','receipt_published_completed','receipt_published_failed','armed','publish_receipt','starting'}
        while time.monotonic()-c.START<495:
            stage=get('stage.txt').decode(errors='replace').strip()
            if stage!=last:
                note('worker_stage',stage=stage if stage in stages else 'not_observed_or_unrecognized');last=stage
            if stage.startswith('receipt_published') or not stage or time.monotonic()-c.START>475:
                frame=get('result_frame.txt')
                if frame and decode(frame,nonce,result):break
            time.sleep(3)
        if not result.exists() and not decode(get('result_frame.txt'),nonce,result):raise RuntimeError('verified_frame_missing')
        r=json.loads(result.read_bytes());ok=r.get('status')=='completed' and bool(r.get('generated_token_ids'))
        if not ok:
            diag=get('diagnostic.txt').strip()
            if diag and len(diag)<410 and re.fullmatch(rb'[a-zA-Z0-9_.,:= -]+',diag):note('sanitized_diagnostic',code=diag.decode())
        note('local_receipt',status=r.get('status'),stage=r.get('stage'),error_type=r.get('error_type'),output_tokens=r.get('output_tokens'),sha256=sha(result.read_bytes()))
    except Exception as exc:note('work_failed',code=str(exc) if type(exc) is RuntimeError else type(exc).__name__)
    finally:
        for _ in range(3):
            try:
                cur=c.assignments('release_sessions_check',True)
                if not own:own=next((k for k,(n,h) in cur.items() if n==name and k not in before),None)
                if own in cur and cur[own][0]==name:
                    rc,msg=c.cli('stop_owned_only','stop','-s',name,limit=30,cleanup=True)
                    note('stop_ack',confirmed=not rc and 'Session terminated' in msg)
                fin=c.assignments('final_sessions',True); f=c.usage('final_usage',True)
                released=own not in fin and all(n!=name for n,h in fin.values()) and len(fin)==f['assignments']
                note('cleanup_observed',owned_absent=released,original_set_unchanged=set(before)==set(fin),active_count=len(fin))
                if released:break
            except Exception as exc:note('cleanup_check_error',error=type(exc).__name__)
            time.sleep(1)
    note('finished',received_completed_generation=ok,owned_released=released)
    return 0 if ok and released else 1
if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(run())
