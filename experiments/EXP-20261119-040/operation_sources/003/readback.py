"""Independent public-byte fallback: fetch ONLY this operation's sentinel and receipt, no generation."""
import hashlib,json,re,subprocess,tempfile
from pathlib import Path
base=Path(__file__).resolve().parent
rows=[json.loads(l) for l in (base/'lifecycle.jsonl').read_text().splitlines()]
assert 'owned_l4' in [r['step'] for r in rows]
name=rows[0]['session']
assert re.fullmatch(r'tlfix-[a-f0-9]{12}',name)
if 'finished' in [r['step'] for r in rows]:raise SystemExit('operation closed; no read')
log=base/'contents_readback.jsonl'
def record(**kw):
    with log.open('a') as f:f.write(json.dumps(kw)+'\n')
    print(json.dumps(kw),flush=True)
with tempfile.TemporaryDirectory() as d:
    for prefix in [f'/content/{name}/',f'{name}/']:
        tmp=Path(d)/'read.tmp'
        def get(rel):
            try:
                p=subprocess.run([str(Path.home()/'.local/bin/colab'),'download','-s',name,prefix+rel,str(tmp)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=16)
                return tmp.read_bytes() if p.returncode==0 and tmp.is_file() else None
            except subprocess.TimeoutExpired:return None
        sent=get('sentinel/toolloop_probe.json')
        if sent is None or len(sent)>512:
            record(path_class='with_content' if prefix.startswith('/') else 'relative',sentinel_matched=False);continue
        try: matched=json.loads(sent)==json.loads((base/'sentinel.json').read_bytes())
        except ValueError:matched=False
        record(path_class='with_content' if prefix.startswith('/') else 'relative',sentinel_matched=matched)
        if not matched:continue
        if tmp.exists():tmp.unlink()
        raw=get('toolloop_probe.json')
        if not raw or len(raw)>65536:record(receipt_bytes=len(raw) if raw is not None else None);break
        try: r=json.loads(raw)
        except ValueError:record(receipt_complete=False);break
        if r.get('scope')=='single_public_tool_call_proposal_only' and r.get('status') in ('failed','completed'):
            with (base/'contents_receipt.json').open('xb') as f:f.write(raw)
            record(receipt_complete=True,candidate_sha256=hashlib.sha256(raw).hexdigest())
        break
