"""Render one archived trajectory, without fabricating intermediate tool calls."""
from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont
here=Path(__file__).resolve().parent
rows=map(json.loads,(here.parents[2]/'experiments/EXP-20261120-081/trajectories.jsonl').read_text().splitlines())
row=next(r for r in rows if r['case_id']=='g4h9-orders-recoverable-h3-01')
history=row['actual_tool_history']; assert [a['response']['code'] for a in history]==['INVALID_IDENTIFIER','LOCATED','TIMEOUT','OK']
font='/System/Library/Fonts/Supplemental/Arial Unicode.ttf'
def f(k):return ImageFont.truetype(font,k)
frames=[]; W,H=1820,420
for step in [None,0,1,2,3,None]:
 im=Image.new('RGB',(W,H),'#271F30'); d=ImageDraw.Draw(im)
 for i,(a,lab) in enumerate(zip(history,['订单标识无效','找到有效标识','查询暂时超时','重试取得结果'])):
  x=72+i*440; y=130
  col='#CEC0FF' if i in (1,3) else '#F2BA70'
  if i<3:
   d.line((x+25,y,x+440,y),fill='#9E8CA7',width=4);d.polygon([(x+385,y-10),(x+405,y),(x+385,y+10)],fill='#9E8CA7')
  if i==step:d.ellipse((x-43,y-43,x+43,y+43),outline=col,width=6)
  d.ellipse((x-25,y-25,x+25,y+25),fill=col); d.text((x-13,y-25),str(i+1),font=f(38),fill='#271F30')
  d.text((x-23,35),a['action']['tool'],font=f(38),fill='white')
  d.text((x-23,200),lab,font=f(42),fill='white');d.text((x-23,282),a['response']['code'],font=f(31),fill=col)
 frames.append(im)
frames[0].save(here/'assets/trace.gif',save_all=True,append_images=frames[1:],duration=[1200,850,850,950,1000,1000],loop=0,disposal=2)
