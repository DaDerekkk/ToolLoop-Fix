"""Nine pre-registered configurations; no jitter, smoothing, or fitted frontier."""
from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont
here=Path(__file__).resolve().parent
data=json.loads((here.parents[2]/'experiments/EXP-20261120-105R1/g3_g4_comparison.json').read_text())['g4_e2e']['arms']
W,H=2120,800; bg='#FAF6ED'; ink='#2B2235'; muted='#655969';violet='#624394';amber='#A34D0D'
im=Image.new('RGB',(W,H),bg);d=ImageDraw.Draw(im)
def text(x,y,t,size,col=ink,anchor='lm'):
 f=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Unicode.ttf',size)
 d.text((x,y),t,font=f,fill=col,anchor=anchor)
def X(v):return 135+v*11.45
def Y(v):return 640-v*13.0
text(35,46,'出现无效循环的任务占比',38)
for y in [0,10,20,30,40]:
 d.line((X(0),Y(y),X(100),Y(y)),fill='#CFC6CC',width=2);text(99,Y(y),str(y)+'%',27,muted,'rm')
for x in [0,25,50,75,100]:
 d.line((X(x),Y(0),X(x),Y(40)),fill='#DCD4D7',width=2);text(X(x),687,str(x)+'%',28,muted,'mm')
text(725,755,'精确完成率',38,ink,'mm')
names=['原模型','强提示','外部守卫','纠错监督','单 token 偏好','专家偏好','动作偏好','移除重试样本','移除正常样本']
text(1365,50,'配置',33);text(1865,50,'完成',32,ink,'mm');text(2025,50,'循环',32,ink,'mm')
for i,(a,name) in enumerate(zip(['B0','B1','B2','B3','B4','B5','P','P_no_retry','P_no_normal'],names)):
 m=data[a]['overall']; rate=lambda k:m[k]['value']*100
 x=X(rate('task_completion_exact'));y=Y(rate('invalid_loop_rate_v2'));c=violet if a=='B3' else amber if a=='P' else '#615866';r=21
 d.ellipse((x-r,y-r,x+r,y+r),fill=c,outline=bg,width=2);text(x,y+1,str(i+1),23,'white','mm')
 yy=119+64*i;d.ellipse((1363,yy-18,1399,yy+18),fill=c);text(1381,yy,str(i+1),20,'white','mm')
 text(1420,yy,name,33,c);text(1865,yy,f"{m['task_completion_exact']['numerator']}/30",29,c,'mm');text(2025,yy,f"{m['invalid_loop_rate_v2']['numerator']}/36",29,c,'mm')
im.save(here/'assets/tradeoff.png')
