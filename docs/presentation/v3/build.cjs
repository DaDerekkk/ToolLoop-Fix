/* Rebuild from the project root. The two methods plates are illustrations; all
   result displays read the archived G4 comparison or its case-level outputs. */
const fs=require('fs'),path=require('path'),Pptx=require('pptxgenjs'),p=new Pptx();
p.layout='LAYOUT_WIDE';p.author='ToolLoop-Fix';p.title='同一张订单，两种失败';p.subject='Qwen3.5-0.8B 工具调用恢复实验';p.lang='zh-CN';
const root=path.resolve(__dirname,'../../..'),ex=path.join(root,'experiments'),asset=n=>path.join(__dirname,'assets',n),read=n=>fs.readFileSync(n,'utf8'),j=JSON.parse(read(path.join(ex,'EXP-20261120-105R1/g3_g4_comparison.json'))),d=j.g4_e2e.arms;
const C={ink:'2B2235',violet:'624394',sat:'704EA8',lav:'E5DBF0',pale:'F2ECF7',amber:'B55716',gold:'E3A755',sun:'F5D9AE',paper:'FAF6ED',white:'FFFFFF',muted:'615969',line:'D8D0CA',dark:'251E30',soft:'D8CCDD',fog:'CBC0D7'};
const F='Arial Unicode MS';p.theme={headFontFace:F,bodyFontFace:F,lang:'zh-CN'};let count=0;
function txt(s,t,x,y,w,h,size,opt={}){s.addText(t,{x,y,w,h,margin:0,fontFace:F,fontSize:size,color:C.ink,lang:'zh-CN',valign:'mid',align:'left',...opt})}
function rect(s,x,y,w,h,color,shape='rect'){s.addShape(p.ShapeType[shape],{x,y,w,h,line:{type:'none'},fill:{color}})}
function line(s,x,y,w,h,color=C.line,width=1.2){s.addShape(p.ShapeType.line,{x,y,w,h,line:{color,width}})}
function page(color,notes){const s=p.addSlide();count++;s.background={color};s.addNotes(notes);return s}
function folio(s,label,dark=false){let c=dark?C.fog:C.muted;txt(s,label,.69,.33,10,.32,13,{color:c});txt(s,String(count).padStart(2,'0'),12.06,.33,.57,.32,13,{color:c,align:'right'});}
function title(s,str,y=.97,size=31){txt(s,str,.68,y,12,.82,size,{bold:true})}
function foot(s,str,dark=false){const c=dark?C.fog:C.muted;line(s,.7,6.77,11.93,0,dark?'504359':C.line,1);txt(s,str,.7,6.9,11.95,.35,12,{color:c})}
function img(s,name,x,y,w,h,alt){const file=asset(name),b=fs.readFileSync(file);let iw,ih;if(name.endsWith('.png')){iw=b.readUInt32BE(16);ih=b.readUInt32BE(20)}else{iw=b.readUInt16LE(6);ih=b.readUInt16LE(8)}let r=Math.min(w/iw,h/ih);s.addImage({path:file,x:x+(w-iw*r)/2,y:y+(h-ih*r)/2,w:iw*r,h:ih*r,altText:alt})}
const at=(a,k)=>d[a].overall[k],fr=m=>m.numerator+'/'+m.denominator,pct=v=>(v*100).toFixed(1)+'%';
const cmp=(id)=>j.paired_clustered_contrasts.g4_e2e[id].overall.task_completion_exact;
const seed=(a,n,domain)=>j.multi_train_seed.e2e[a].by_train_seed[n][domain].task_completion_exact;
function table(s,rows,y,heights,cols,font=16){s.addTable(rows.map((r,i)=>r.map((v,k)=>({text:String(v),options:{bold:!i||!k,color:i?C.ink:C.white,fill:i?(i%2?C.paper:'F0EBE2'):C.ink,align:k?'center':'left'}}))),{x:.72,y,w:11.86,colW:cols,rowH:heights,fontFace:F,fontSize:font,margin:.075,autoPage:false,border:{pt:.8,color:C.paper}})}
// 01. Subject introduction, no acronyms or results before the question.
let s=page(C.dark,'一张订单查不到。假如订单号无效，原样再查不会有帮助；但如果服务只是超时，停下来也会做错。ToolLoop-Fix 比较两种教小模型做这个判断的方法。');
rect(s,0,0,.14,7.5,C.gold);txt(s,'TOOLLOOP-FIX',.73,.4,6,.3,15,{color:C.gold,bold:true});
txt(s,'同一张订单，两种失败',.71,.9,8.22,.91,37,{color:C.paper,bold:true});
txt(s,'标识无效，要换个查法。\n服务超时，再查一次才对。',8.85,.78,3.92,1.05,18,{color:'E5DCE9'});
img(s,'recovery.png',.7,1.99,11.93,4.46,'概念插图：订单查询遇到标识无效和暂时超时两种不同反馈。');
line(s,.77,6.68,11.8,0,'514857');txt(s,'Qwen3.5-0.8B 的工具调用恢复实验',.76,6.86,10.8,.37,15,{color:C.fog});
// 02. Play the evidence, not a mock terminal.
s=page(C.paper,'上面这次，模型先找回有效标识，再用一次重试克服超时。下面换到另一张订单比较两种模型：原模型在不需要澄清时连续询问五次，而纠错监督模型三次调用就取回订单。这是两条不同的测试用例。');
folio(s,'现场记录  /  订单查询');title(s,'查到有效标识后，超时就原样重试');
txt(s,'纠错监督模型的四次实际调用',.71,1.99,9,.48,18,{color:C.muted});
img(s,'trace.gif',.71,2.52,11.91,2.78,'首次查单得到无效标识，找到标识后查询超时，再次查询返回 OK。');
line(s,.72,5.53,11.88,0,C.ink,1.4);txt(s,'另一张订单',.74,5.75,2.23,.58,20,{bold:true,color:C.violet});
txt(s,'原模型连续五次追问；纠错监督模型用三次工具调用取回订单。',3.02,5.72,9.39,.7,20);
foot(s,'上图为订单任务 h3；下方对照为 h1。动画只高亮调用顺序，不代表响应耗时。');
// 03. Methods plate right, text left.
s=page(C.pale,'我们只在模型必须选下一步的现场制作样本。给两种方法完全相同的历史和同一个经过执行验证的正确动作。B3 用它做监督目标；P 还学习正确动作相对被拒动作的偏好，并保留监督项。');
folio(s,'训练  /  同一批决策点');title(s,'400 个现场，两种教法');
txt(s,'以一次查单超时为例',.73,1.98,5.43,.68,24,{bold:true});
txt(s,'B3   纠错监督',.75,3.12,5,.48,19,{bold:true,color:C.violet});
txt(s,'直接模仿经验证的重试动作。',.75,3.68,5.05,.72,21);
txt(s,'P     动作偏好',.75,4.82,5,.48,19,{bold:true,color:C.amber});
txt(s,'把“重试”排在“停止”之前，\n同时保留监督训练。',.75,5.38,5.25,1.05,20);
img(s,'training.png',6.02,1.86,6.58,4.65,'共享的失败现场分成两条训练路径：SFT 学习正确动作；DPO 比较偏好对。');
foot(s,'400 个决策点包含纠错、合法重试、正确停止和正常完成；同一基座，同样训练 50 步。');
// 04. Switch the viewing direction; one technical drawing, two test questions.
s=page(C.paper,'训练只用订单和文件任务。测试保留了全新的模板，后台作业甚至是整个留出的工具域。模型只收到公开信息；评测器知道任务状态，能区别真完成和口头宣称完成。');
folio(s,'评测  /  从头验证任务');title(s,'模型看公开信息，隐藏状态只给评分器');
img(s,'evaluation.png',.69,1.92,6.4,4.53,'模型经公开动作和反馈连接沙盒；隐藏状态仅供评分，不向模型提供。');
txt(s,'30 个应完成',7.57,2.23,4.84,.78,29,{bold:true,color:C.violet});txt(s,'订单、文件、后台作业各 10 个。\n后台作业在训练中从未出现。',7.58,3.06,5.02,1.02,19);
line(s,7.58,4.37,4.75,0,C.line);txt(s,'6 个应停止',7.57,4.74,4.8,.6,24,{bold:true});txt(s,'它们本来就无法完成，单独计分。',7.58,5.5,4.91,.8,19);
foot(s,'模型可见用户请求、工具说明和交互历史。另测 18 个固定失败前缀，不计入端到端完成率。');
// 05. Data plate comes only after model names and denominator are introduced.
s=page(C.paper,'这张图的每列是同一项任务，各行是一种方法。固定训练种子 101，原模型做到三项，动作偏好做到二十一项，纠错监督做到二十九项。八项是完成数之差，不意味着所有个例都单向改善。');
folio(s,'结果  /  逐例核对');title(s,'同做 30 个任务，纠错监督多完成 8 个');
txt(s,'比的是 B3 与 P，不是与原模型比。此处固定训练种子 101。',.73,1.88,11.78,.55,19,{color:C.muted});
img(s,'fingerprint.png',.71,2.56,11.93,3.42,'30 个同列对齐的任务。原模型完成 3 个，动作偏好完成 21 个，纠错监督完成 29 个。');
let c1=cmp('P_vs_B3');const signflip=m=>[(-100*m.mean_diff).toFixed(1),(-100*m.ci95[1]).toFixed(1),(-100*m.ci95[0]).toFixed(1)];let [v,l,h]=signflip(c1);
txt(s,'完成率差 '+v+' 个百分点；95% 区间为 '+l+' 至 '+h+' 个百分点。',.74,6.05,11.8,.44,19,{bold:true,color:C.violet});
foot(s,'主指标为有依据的精确完成；区间按相同任务模板配对计算。三种子结果见下页。');
// 06. Less chart chrome; seed numbers in the left margin label independent training trials.
s=page(C.paper,'换训练种子，B3 的完成数三次相同。P 的第三次少了八项。在从未训练过的后台作业上，第三个 P 没有完成一个。两个面板用的是同一批任务，右图是左图的子集，不是另外抽样了十项。');
folio(s,'结果  /  三次独立训练，同一套测试');title(s,'换个训练种子，P 在后台作业上从 5 个降到 0 个',.94,29);
for(let q=0;q<2;q++){let L=.72+q*6.2;txt(s,q?'未见域：后台作业':'全部可完成任务',L,1.97,5.81,.52,23,{bold:true});txt(s,q?'10 个任务':'30 个任务',L,2.52,4.9,.37,15,{color:C.muted});let X=n=>L+1.1+n*4.28;
[0,.5,1].forEach(f=>{line(s,X(f),3.21,0,2.45,C.line,.8);txt(s,100*f+'%',X(f)-.38,5.8,.76,.35,13,{align:'center',color:C.muted})});
[101,202,303].forEach((n,i)=>{let y=3.54+i*.84;txt(s,''+n,L,y-.24,.86,.48,17);let a=['P','B3'].map(k=>seed(k,n,q?'cross_domain_heldout_jobs':'overall'));line(s,X(a[0].value),y,X(a[1].value)-X(a[0].value),0,'A298A8',2.3);a.forEach((m,k)=>{let color=k?C.violet:C.amber;rect(s,X(m.value)-.087,y-.087,.174,.174,color,k?'rect':'ellipse');txt(s,fr(m),X(m.value)-.44,y-.46,.88,.28,14,{align:'center',bold:true,color})})})}
let c3=signflip(cmp('P_vs_B3_3train_seed_cluster_avg'));
txt(s,'三种子平均：B3 领先 '+c3[0]+' 个百分点（95% 区间 '+c3[1]+' 至 '+c3[2]+'）。',.75,6.25,11.77,.36,18,{color:C.violet,bold:true});
foot(s,'橙色圆点为 P，紫色方点为 B3；每行同一个训练种子。区间按模板聚类，不是种子标准差。');
// 07. Put the failure next to its counter-metric rather than treating zero as success.
s=page(C.sun,'现在删去正常任务训练样本。循环指标看起来很漂亮，但三十个可完成任务无一完成，其中二十二个被提前放弃。删掉重试样本则没有一个必要重试任务成功。由于删除也改变了样本量，这不是对单一因子的完整隔离。');
folio(s,'反例  /  移除一类训练样本');title(s,'不再循环，也可能是什么都没做成');
txt(s,'P 移除正常任务样本后',.75,2.01,9,.5,20,{color:C.muted});
txt(s,fr(at('P_no_normal','invalid_loop_rate_v2')),.73,2.7,5.72,1.37,72,{bold:true});txt(s,'测试任务中，出现循环的任务数',.78,4.05,5.72,.56,22);
line(s,6.26,2.83,0,1.83,'B89468',1.3);
txt(s,fr(at('P_no_normal','task_completion_exact')),6.76,2.7,5.72,1.37,72,{bold:true,color:C.amber});txt(s,'应完成的任务中，实际完成数',6.81,4.05,5.62,.56,22);
line(s,.78,5.11,11.7,0,'BD9A77',1.3);txt(s,'22/30 提前放弃。完整 P 的同一指标只有 1/30。',.78,5.51,11.73,.69,24,{bold:true});
foot(s,'训练样本的组成和数量都变了。这里比较的是两组实际配置，不作单一原因归因。');
// 08. One genuine invalid XML, with the corresponding archived rejection verified.
const rr=JSON.parse(read(path.join(ex,'EXP-20261120-082','generations.jsonl')).split('\n')[0]);
const rejection=JSON.parse(read(path.join(ex,'EXP-20261120-082','trajectories.jsonl')).split('\n')[0]).events[0];
if(rr.raw_text.split('\n')[1]!=='<=read_file>'||rejection.parser_error!=='MALFORMED_TOOL_XML'||rejection.actual_tool_dispatch!==false)throw Error('unexpected archived XML example');
s=page(C.dark,'这是单 token 偏好模型 B4 的一条真实生成。错误发生在调用格式，环境根本没有执行 read_file。B3 的解析错误较低，P 对种子更敏感。这是观察到的伴随关系；不能说负偏好梯度就是原因。');
folio(s,'故障记录  /  工具没有收到调用',true);txt(s,'有时模型选了工具，却没能把调用发出去',.7,.95,12.12,.83,30,{color:C.paper,bold:true});
line(s,6.46,2.07,0,4.07,'65596E',1);txt(s,'B4 实际输出，截取前两行',.75,2.18,5.45,.44,17,{color:C.fog});
txt(s,'<tool_call>',.77,2.9,5.42,.63,30,{color:C.paper});txt(s,rr.raw_text.split('\n')[1],.77,3.6,5.42,.73,32,{bold:true,color:C.gold});txt(s,'解析器拒绝这段 XML。\n工具没有执行。',.77,4.75,5.3,1,21,{color:C.paper});
txt(s,'无法解析的生成轮次占比',6.84,2.2,5.58,.48,20,{color:C.paper,bold:true});
for(const [i,a] of ['B3','P','B4'].entries()){const ds=(a==='B4'?[d.B4]:Object.values(j.multi_train_seed.e2e[a].by_train_seed)).map(z=>z.overall.diagnostics).map(z=>z.parser_error_turns/z.total_turns);const lo=Math.min(...ds),hi=Math.max(...ds),y=3.15+i*.91;txt(s,a,6.89,y,1,.54,21,{color:C.fog});txt(s,lo===hi?pct(lo):pct(lo)+' 至 '+pct(hi),8.05,y,4.27,.54,23,{bold:true,color:a==='B3'?'D9C7FF':a==='P'?C.gold:C.paper})}
foot(s,'文件任务 h1，B4 种子 101 第 1 轮。B3、P 的范围为 3 个训练种子的最小值至最大值。',true);
// 09. An audit of scoring, not additional inference. Concrete inputs and recomputation.
s=page(C.paper,'推理日志已经存在。CPU 审计读取这些输出，再让评测环境按相同规则走一遍，核对每一个评分字段。我们复核的是计分过程，不是又做了一次训练或对模型进行了独立抽样。');
folio(s,'复核  /  已保存的模型输出');title(s,'如果只拿到日志，分数还能对上吗？');
const steps=[['读取','32 个推理实验','846 个任务回合'],['重放','4,029 轮生成','CPU 按原规则重新计分'],['比对','0 处字段差异','重放结果与归档记录一致']];
steps.forEach(([k,a,b],i)=>{const x=.76+4.11*i;line(s,x,2.39,3.62,0,i===2?C.violet:C.ink,2);txt(s,k,x,2.65,3.67,.65,23,{color:C.muted});txt(s,a,x,3.54,3.67,.78,27,{bold:true,color:i===2?C.violet:C.ink});txt(s,b,x,4.48,3.72,.83,18)});
txt(s,'这说明评分可复核，并不说明模型在真实业务里也能完成同样多的任务。',.76,5.95,11.73,.59,18,{color:C.muted});
foot(s,'档案：EXP-20261120-REVIEW-009R2。重放使用已生成的输出，没有重新训练或生成。');
// 10. Quiet close, no repeated abstract bullet list.
s=page(C.dark,'如果我要给这个 0.8B 助手选方案，我会先选从正确下一步学习的 B3，而不是直接从偏好优化开始。同一套封闭测试里三个训练种子各完成二十九个任务，但并非三个独立测试集。下一步，我最想知道的是换到真实工具、由人来判断时，排序能否保住。');
folio(s,'结语',true);txt(s,'如果明天要给这个\n小模型选一套训练法',.73,1.34,7.12,1.97,36,{bold:true,color:C.paper});txt(s,'先用纠错监督。',.75,3.48,7.24,1.02,43,{bold:true,color:C.gold});line(s,8.39,1.58,0,3.97,'64566E');
txt(s,'29/30',8.85,2.25,3.71,1.09,47,{bold:true,color:'DDC9F4'});txt(s,'三次训练，\n同一套封闭任务。',8.87,3.56,3.66,1.18,21,{color:C.paper});
txt(s,'这个选择还欠一次检验：换真实工具，让人工盲审恢复动作。',.76,5.88,11.79,.64,21,{color:C.paper});
foot(s,'结论限于当前的 Qwen3.5-0.8B、50 步训练预算和模拟工具环境。',true);
// 11–12. Keep details for questions, rather than making the talk read like an index.
s=page(C.paper,'各列的分母不同。必要重试成功与正确停止各有六项；循环与虚假成功按全部三十六项计。这里列出九组结果，以便查找正文没有展开的基线和消融。');
folio(s,'备查 A  /  全部对照');title(s,'九种配置，同一批封闭任务');
table(s,[['方法','精确完成','必要重试成功','无效循环','虚假成功','正确停止'],...['B0','B1','B2','B3','B4','B5','P','P_no_retry','P_no_normal'].map((a,i)=>[['B0  原模型','B1  强提示','B2  外部守卫','B3  纠错监督','B4  单 token 偏好','B5  专家偏好','P    动作偏好','P    移除重试','P    移除正常'][i],...['task_completion_exact','necessary_retry_success_exact','invalid_loop_rate_v2','false_success','correct_termination_exact'].map(k=>fr(at(a,k)))])],2.03,.43,[2.96,1.79,2,1.72,1.7,1.69],15);
foot(s,'B3 与 P 的主配置取种子 101。数据：EXP-20261120-105R1。');
s=page(C.paper,'左边是检测器校准，不是模型成功率；右边是算力需求，也不表示行为变好。检测器使用盲审代理银标，未来仍需要人工盲审。');
folio(s,'备查 B  /  判定口径与资源');title(s,'什么算循环？训练用了多少资源？');
for(const [i,[head,a,b,c]] of [['循环检测器','162/164','标注为循环的样本中，新版找回 162 个。','85 个合法重试样本均未误报；参照标签为代理银标。'],['单卡 L4 训练','3.57 至 5.54 GiB','10 个 LoRA 适配器实测峰值显存范围。','共 3 个训练批次，每个适配器约 271 万个可训练参数。']].entries()){
const x=.74+i*6.22;line(s,x,2.25,5.55,0,C.violet,2);txt(s,head,x,2.58,5.6,.52,22,{bold:true});txt(s,a,x,3.37,5.55,.79,32,{bold:true,color:C.violet});txt(s,b,x,4.36,5.49,.81,19);txt(s,c,x,5.38,5.4,.97,17,{color:C.muted})}
foot(s,'检测器冻结：ITER-008；训练与审计记录：ITER-009。');
s=page(C.paper,'横轴向右表示完成率更高，纵轴向下表示无效循环率更低。一个系统可能循环更少却没有完成任务。请结合右下区域和每点的原始分母读图；这幅图本身不测量真实服务中的恢复能力。');
folio(s,'备查 C  /  完成与空转');title(s,'哪种方法做成的任务多，也更少空转？');
img(s,'tradeoff.png',.7,1.95,11.9,4.47,'九组配置的完成率和无效循环率散点图；横轴精确完成率，纵轴发生无效循环的任务比例。');
foot(s,'九种配置，G4 模拟工具测试。精确完成分母为 30，无效循环分母为 36；B3、P 主配置取种子 101。');
p.writeFile({fileName:path.join(__dirname,'ToolLoop-Fix-v3.pptx')});
