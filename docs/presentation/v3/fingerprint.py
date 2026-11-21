"""Aligned task fingerprints. Bright bars = exact completion; hollow cells = not completed."""
import json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
p=Path(__file__).resolve().parent
e=p.parents[2]/'experiments'
def load(n):
 return sorted((r for r in map(json.loads,(e/f'EXP-20261120-{n}/case_results.jsonl').read_text().splitlines()) if r['completable']),key=lambda r:r['case_id'])
runs=[load(x) for x in ['078','084','081']]
ids=lambda run:[r['case_id'] for r in run]
assert len(runs[0])==30 and ids(runs[0])==ids(runs[1])==ids(runs[2])
W,H=2100,540;im=Image.new('RGB',(W,H),'#FAF6EE');d=ImageDraw.Draw(im)
def t(x,y,text,size,col='#30273A'):
 d.text((x,y),text,font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Unicode.ttf',size),fill=col)
start=294; step=49; gap=38
for k,name in enumerate(['文件','后台作业（未见域）','订单']):
 x=start+k*(step*10+gap);t(x,18,name,36);d.line((x,79,x+step*10-8,79),fill='#AFA2B1',width=3)
for r,(run,label,col) in enumerate(zip(runs,['B0  原模型','P    动作偏好','B3  纠错监督'],['#60596A','#A35A16','#583983'])):
 y=124+r*108;t(14,y-7,label,38);hits=sum(x['exact_grounded_structured_completion'] for x in run);t(1900,y-14,f'{hits}/30',50,col)
 for i,case in enumerate(run):
  x=start+i*step+(i//10)*gap
  d.rounded_rectangle((x,y,x+36,y+51),radius=3,fill=col if case['exact_grounded_structured_completion'] else '#FAF6EE',outline=col if case['exact_grounded_structured_completion'] else '#ACA6AD',width=2)
t(15,459,'每列是同一任务。实心格为精确完成，空心格为未完成。',34,'#5F5365')
im.save(p/'assets/fingerprint.png')
