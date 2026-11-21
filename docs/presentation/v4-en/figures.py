"""Build the three evidence figures directly from frozen local records."""
from pathlib import Path
import json
from PIL import Image,ImageDraw,ImageFont
D=Path(__file__).resolve().parent
E=D.parents[2]/'experiments'; A=D/'assets'
F=Path('/System/Library/Fonts/Supplemental')
def f(n,b=False,mono=False):
 return ImageFont.truetype(str(F/('Courier New.ttf' if mono else 'Arial Bold.ttf' if b else 'Arial.ttf')),n)
paper='#FAF6EE';ink='#2C2529';wine='#682C45';copper='#A14C20';steel='#536A67';line='#D7CCC4'
def tx(d,xy,s,n,fill=ink,b=False,anchor='lm',mono=False):d.text(xy,s,font=f(n,b,mono),fill=fill,anchor=anchor)
def rows(n,filename):return [json.loads(l) for l in (E/f'EXP-20261120-{n}'/filename).read_text().splitlines()]
def cases(n):return {v['case_id']:v['exact_grounded_structured_completion'] for v in rows(n,'case_results.jsonl') if v['completable']}
p,b=cases('084'),cases('081');assert set(p)==set(b) and len(b)==30
j=json.loads((E/'EXP-20261120-105R1/g3_g4_comparison.json').read_text())
def pairmark(d,x,y,k,sc=1):
 r=16*sc
 if k==(True,True):d.ellipse((x-r,y-r,x+r,y+r),fill=steel)
 elif k==(True,False):d.rectangle((x-r,y-r,x+r,y+r),fill=wine)
 elif k==(False,True):d.polygon([(x,y-r*1.2),(x+r*1.2,y),(x,y+r*1.2),(x-r*1.2,y)],fill=copper)
 else:d.ellipse((x-r,y-r,x+r,y+r),outline='#80757B',width=3)
im=Image.new('RGB',(1760,620),paper);d=ImageDraw.Draw(im)
for h,dom in enumerate(['files','jobs','orders']):
 off=35+h*574
 tx(d,(off,76),['Files','Jobs: held out','Orders'][h],46,b=True)
 d.line((off,129,off+525,129),fill=line,width=3)
 keys=sorted(k for k in b if '-'+dom+'-' in k);assert len(keys)==10
 for i,k in enumerate(keys):pairmark(d,off+25+i*52,210,(b[k],p[k]),1.23)
tx(d,(35,311),'Each mark is one task evaluated by both methods.',32,fill='#64595E')
d.line((35,376,1715,376),fill=line,width=2)
for idx,(k,name) in enumerate([((1,1),'Both completed'),((1,0),'SFT only'),((0,1),'DPO only'),((0,0),'Neither')]):
 x=38+idx*438; count=sum((b[task],p[task])==k for task in b)
 pairmark(d,x+20,466,k,.84);tx(d,(x+53,467),str(count),66,b=True);tx(d,(x+4,547),name,34)
im.save(A/'pairs.png')
im=Image.new('RGB',(1760,620),paper);d=ImageDraw.Draw(im)
for i,(domain,label) in enumerate([('overall','All eligible tasks'),('cross_domain_heldout_jobs','Held-out jobs')]):
 off=42+i*870;tx(d,(off,50),label,42,b=True)
 x=lambda v:off+122+v*625
 for v in [0,.5,1]:
  d.line((x(v),130,x(v),460),fill=line,width=2);tx(d,(x(v),499),f'{v:.0%}',26,anchor='mm')
 for yi,seed in enumerate([101,202,303]):
  y=190+yi*119; tx(d,(off,y),str(seed),28)
  pts=[j['multi_train_seed']['e2e'][arm]['by_train_seed'][str(seed)][domain]['task_completion_exact'] for arm in ['P','B3']]
  d.line((x(pts[0]['value']),y,x(pts[1]['value']),y),fill='#A5999A',width=5)
  for h,pt in enumerate(pts):
   x0=x(pt['value']); r=11
   if h:d.rectangle((x0-r,y-r,x0+r,y+r),fill=wine)
   else:d.ellipse((x0-r,y-r,x0+r,y+r),fill=copper)
   tx(d,(x0,y-36),f"{pt['numerator']}/{pt['denominator']}",29,fill=wine if h else copper,b=True,anchor='mm')
 tx(d,(off,106),'Seed',22,fill='#685B60')
for h,(color,label) in enumerate([(copper,'Action DPO'),(wine,'Corrective SFT')]):
 x0=562+h*328;y=570
 if h:d.rectangle((x0-9,y-9,x0+9,y+9),fill=color)
 else:d.ellipse((x0-10,y-10,x0+10,y+10),fill=color)
 tx(d,(x0+25,y),label,29)
im.save(A/'seeds.png')
hist=next(r['actual_tool_history'] for r in rows('081','trajectories.jsonl') if r['case_id']=='g4h9-orders-recoverable-h3-01')
assert [a['response']['code'] for a in hist]==['INVALID_IDENTIFIER','LOCATED','TIMEOUT','OK']
frames=[]
for hi in (None,0,1,2,3,None):
 im=Image.new('RGB',(1800,440),'#31242D');d=ImageDraw.Draw(im)
 for i,(a,caption) in enumerate(zip(hist,['Wrong identifier','Identifier found','Timed out','Order retrieved'])):
  x0=45+440*i;c='#EFBC81' if i in (0,2) else '#D7C7DF'
  tx(d,(x0,57),f'CALL  {i+1:02d}',26,c,b=True)
  tx(d,(x0,153),a['action']['tool'],44,'white',mono=True)
  d.line((x0,222,x0+383,222),fill=c if i==hi else '#73616D',width=9 if hi==i else 2)
  tx(d,(x0,285),caption,39,'white',b=True);tx(d,(x0,365),a['response']['code'],31,fill=c,mono=True)
 frames.append(im)
frames[0].save(A/'trace.gif',save_all=True,append_images=frames[1:],duration=[1200,900,900,900,900,1300],loop=0,disposal=2)
