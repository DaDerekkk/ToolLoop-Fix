/* English research talk. Images of measurements are produced separately from
 * immutable archives. The training and sandbox plates are explanatory drawings. */
const fs=require('fs'),path=require('path'),Pptx=require('pptxgenjs');
const p=new Pptx();p.layout='LAYOUT_WIDE';p.lang='en-US';p.author='ToolLoop-Fix';
p.title='Teaching a small model when to retry a tool';p.subject='A paired study of tool-call recovery in Qwen3.5-0.8B';
const root=path.resolve(__dirname,'../../..'),ex=path.join(root,'experiments');
const read=f=>fs.readFileSync(f,'utf8'), parse=f=>JSON.parse(read(f));
const results=parse(path.join(ex,'EXP-20261120-105R1/g3_g4_comparison.json')),arms=results.g4_e2e.arms;
const asset=n=>path.join(__dirname,'assets',n);
const C={paper:'F9F4EA',ink:'28272B',burg:'67273F',deep:'471E30',copper:'A24E29',peach:'F2D1AC',rule:'CFC4BA',muted:'60585B',fog:'DACBCB',white:'FFFFFF',pale:'F6E9DA',panel:'EFE4DB'};
const face='Arial',serif='Georgia';p.theme={headFontFace:serif,bodyFontFace:face,lang:'en-US'};
let number=0;
function page(color,notes){const s=p.addSlide();number++;s.background={color};s.addNotes(notes);return s}
function text(s,v,x,y,w,h,size=20,options={}){s.addText(v,{x,y,w,h,margin:0,fontFace:face,fontSize:size,lang:'en-US',color:C.ink,valign:'mid',align:'left',...options})}
function line(s,x,y,w,h,c=C.rule,width=1.05){s.addShape(p.ShapeType.line,{x,y,w,h,line:{color:c,width}})}
function rect(s,x,y,w,h,c){s.addShape(p.ShapeType.rect,{x,y,w,h,line:{type:'none'},fill:{color:c}})}
function head(s,label,dark=false){const color=dark?C.peach:C.copper;text(s,label,.72,.37,11,.35,13,{bold:true,color,charSpacing:1.1});text(s,('0'+number).slice(-2),12.07,.37,.51,.35,13,{align:'right',color})}
function title(s,t,opt={}){text(s,t,opt.x??.70,opt.y??.95,opt.w??11.98,opt.h??.98,opt.size??33,{fontFace:serif,color:opt.color??C.burg,bold:false})}
function foot(s,t,dark=false){let c=dark?C.fog:C.muted;line(s,.72,6.79,11.9,0,dark?'825D68':C.rule);text(s,t,.72,6.90,11.9,.36,12,{color:c})}
function fig(s,f,x,y,w,h,description){const b=fs.readFileSync(f);let a=f.endsWith('.gif')?[b.readUInt16LE(6),b.readUInt16LE(8)]:[b.readUInt32BE(16),b.readUInt32BE(20)];const k=Math.min(w/a[0],h/a[1]);s.addImage({path:f,x:x+(w-a[0]*k)/2,y:y+(h-a[1]*k)/2,w:a[0]*k,h:a[1]*k,altText:description})}
const m=(arm,key)=>arms[arm].overall[key],fr=o=>o.numerator+'/'+o.denominator,pc=x=>(100*x).toFixed(1),ratio=(arm)=>fr(m(arm,'task_completion_exact'));
const metric='task_completion_exact',cmp=q=>results.paired_clustered_contrasts.g4_e2e[q].overall[metric];
const diff=(q,flip=false)=>{const a=cmp(q);return flip?[pc(-a.mean_diff),pc(-a.ci95[1]),pc(-a.ci95[0])]:[pc(a.mean_diff),pc(a.ci95[0]),pc(a.ci95[1])]};
const diag=a=>(a==='B4'?[arms.B4]:Object.values(results.multi_train_seed.e2e[a].by_train_seed)).map(k=>k.overall.diagnostics).map(k=>k.parser_error_turns/k.total_turns);
const cases=a=>Object.fromEntries(read(path.join(ex,arms[a].run_ids[0],'case_results.jsonl')).trim().split('\n').map(JSON.parse).filter(k=>k.completable).map(k=>[k.case_id,k.exact_grounded_structured_completion]));
const b3=cases('B3'),pref=cases('P');if(Object.keys(b3).length!==30||Object.keys(pref).length!==30)throw Error('Paired set size changed');
const counts=[0,0,0,0];Object.keys(b3).forEach(id=>{if(!(id in pref))throw Error('Unmatched case');counts[b3[id]?(pref[id]?0:1):(pref[id]?2:3)]++});
if(counts.join('/')!=='20/9/1/0')throw Error('Updated data: review narrative before export');
const trace=read(path.join(ex,'EXP-20261120-081/trajectories.jsonl')).trim().split('\n').map(JSON.parse).find(t=>t.case_id==='g4h9-orders-recoverable-h3-01').actual_tool_history;
if(trace.map(e=>e.response.code).join('/')!=='INVALID_IDENTIFIER/LOCATED/TIMEOUT/OK')throw Error('Trace changed');
const onlyDpo=Object.keys(b3).find(id=>!b3[id]&&pref[id]);
const record=(a,stem)=>read(path.join(ex,arms[a].run_ids[0],stem+'.jsonl')).trim().split('\n').map(JSON.parse).find(r=>r.case_id===onlyDpo);
const exceptions=['B3','P'].map(a=>record(a,'case_results'));
if(onlyDpo!=='g4h9-jobs-recoverable-h1-01'||!exceptions[0].tool_goal_attainment||!exceptions[0].budget_exhausted||exceptions[0].terminal_form!=='none'||exceptions[0].turns!==12||!exceptions[1].tool_goal_attainment||exceptions[1].turns!==4||exceptions[1].terminal_form!=='structured_finish')throw Error('Recheck job exception and narrative');
function table(s,rows,{x=.73,y=2.07,w=11.85,widths,rowH=.44,font=15}={}){s.addTable(rows.map((r,i)=>r.map((v,j)=>({text:String(v),options:{color:i?C.ink:C.white,bold:!i||!j,fill:!i?C.burg:i%2?C.paper:C.panel,align:j?'center':'left'}}))),{x,y,w,colW:widths,rowH,fontFace:face,fontSize:font,margin:.075,border:{pt:.7,color:C.paper},autoPage:false})}
let s=page(C.deep,'Imagine asking an assistant to look up an order. It does not know the current answer. It sends the order identifier to a tool, which is just a program that can look it up. If the identifier is wrong, the assistant needs a different identifier. If the service times out, it may need to send exactly the same request again. We tested two ways to teach that judgment to a small model.');
head(s,'TOOLLOOP-FIX   /   RESEARCH TALK',true);
text(s,'Teaching a small model when to retry a tool',.70,.92,12.03,1.02,35,{fontFace:serif,color:C.paper});
text(s,'A customer asks for an order update. A bad ID needs fixing; a timeout may just need another try.',.75,2.03,11.9,.63,19,{color:C.fog});
fig(s,asset('cover.png'),.72,2.91,8.7,3.48,'Concept illustration of an order lookup and a timed retry.');
line(s,9.71,3.08,0,3.09,'9F7580',1.35);
text(s,'Wrong order ID',10.0,3.25,2.56,.41,17,{color:C.peach,bold:true});text(s,'Look up the ID.',10.0,3.74,2.55,.56,17,{color:C.paper});
text(s,'Service timeout',10.0,4.82,2.58,.41,17,{color:C.peach,bold:true});text(s,'Try the call again.',10.0,5.34,2.6,.6,17,{color:C.paper});
foot(s,'The assistant calls a program to look up the order. We compare two ways to teach Qwen3.5-0.8B how to recover.',true);

s=page(C.paper,'Here is one actual order lookup. The model first supplied an invalid identifier. Searching for the right identifier worked, but its next order lookup timed out. By then, searching again would have repeated work that was already done. Sending the same lookup again was appropriate: the fourth call returned OK. A loop detector that bans every repeated call would punish the action that retrieves this order.');
head(s,'ONE OBSERVED RECOVERY');title(s,'The wrong ID needed a lookup. The timeout needed a retry.',{size:30});
text(s,'Each position below is one tool call, followed by the tool’s response.',.74,1.93,11.8,.59,19,{color:C.muted});
fig(s,asset('trace.gif'),.73,2.62,11.88,2.83,'First get_order returns INVALID_IDENTIFIER; find_order locates the ID; get_order times out; repeating get_order succeeds.');
text(s,'An invalid loop repeats a failed action without progress; a permitted retry can recover.',.75,5.78,11.77,.78,21,{bold:true,color:C.burg});
foot(s,'Source: EXP-20261120-081, order-recovery case h3. The animation shows call order, not elapsed time.');

s=page(C.paper,'This question builds on three lines of work. ReAct lets a model act, read the result and revise its plan. AntiDoom targets the onset of repetitive text with single-token preference pairs. That is not quite the same problem: repeating a tool call can be right. Fission-GRPO studies tool recovery directly, branching failed trajectories and resampling with diagnostic feedback. We ask a narrower, offline question. If the correct action is already known, does adding a rejected action help more than simply teaching the correct one? We did not run these published systems as baselines.');
head(s,'BACKGROUND  /  PRIOR WORK');title(s,'A repeated tool call is not always a mistake.');
const refs=[
 ['ReAct','Yao et al., ICLR 2023','The model acts, observes a result and revises its plan.','https://arxiv.org/abs/2210.03629'],
 ['AntiDoom','Liquid AI, 2026','Training targets one token where generated text starts to repeat.','https://www.liquid.ai/blog/antidoom'],
 ['Fission-GRPO','Zhang et al., ACL 2026','After a tool fails, the model gets a diagnosis and tries new ways to recover.','https://aclanthology.org/2026.acl-long.1880/']
];
refs.forEach(([t,origin,claim,url],i)=>{let x=.75+4.12*i;line(s,x,2.23,3.66,0,i===1?C.copper:C.burg,1.6);text(s,t,x,2.48,3.73,.62,24,{fontFace:serif,color:C.burg});text(s,origin,x,3.16,3.68,.39,13,{color:C.copper,hyperlink:{url}});text(s,claim,x,3.82,3.64,1.34,17,{color:C.ink})});
rect(s,.74,5.58,11.82,.89,C.panel);text(s,'Do examples of what not to do improve on correct examples alone? We compare both recipes on the same 400 tool decisions.',.94,5.65,11.42,.74,19,{bold:true,color:C.burg});
foot(s,'Linked author lines open the original papers or technical report. These are related approaches, not direct experimental baselines.');

s=page(C.paper,'We made both training recipes see the same failure history. The action selected as correct was verified by executing it. Corrective supervised fine-tuning simply imitates that action. Action-level direct preference optimization uses the same action, but also learns to rank it above a rejected alternative, while keeping a supervised term. The examples include not just repairs and retries, but ordinary completion and cases where stopping is right. The training budget and the base model were held fixed.');
head(s,'EXPERIMENT  /  TRAINING');title(s,'Both models see the same right answer.');
fig(s,asset('training.png'),.72,2.03,5.81,4.23,'Training illustration: the supervised model learns a correct action; the preference model compares it with a rejected alternative.');
text(s,'After a temporary timeout, retrying is right; giving up now is wrong.',6.76,2.04,5.5,.78,19,{bold:true,color:C.ink});
text(s,'Corrective SFT (B3)',6.77,3.13,5.56,.48,20,{bold:true,color:C.burg});text(s,'Supervised fine-tuning: imitate the verified retry.',6.77,3.66,5.44,.7,17);
text(s,'Action DPO (P)',6.77,4.63,5.56,.49,20,{bold:true,color:C.copper});text(s,'Direct preference optimization: prefer retry over stop, with a supervised loss as well.',6.77,5.18,5.57,1,17);
foot(s,'400 shared decision points: repair, legal retry, normal completion or correct stop. Same 0.8B model and 50 training steps per recipe.');

s=page(C.pale,'The evaluator needs a definition of success before we can compare methods. Thirty tasks were possible to finish: ten orders, ten files and ten background processing jobs. The jobs tools were never used in training. A finish only counts when the right tool result has been obtained and the model returns a valid final answer supported by it. Six other tasks are intentionally impossible; stopping is the appropriate outcome there. The model cannot read the private state used by the evaluator.');
head(s,'EXPERIMENT  /  SCORING');title(s,'Getting the right value is only half the job.');
text(s,'30 tasks should finish',.75,2.19,5.52,.69,26,{fontFace:serif,color:C.burg});text(s,'10 orders, 10 files, 10 background jobs. The jobs tools were held out of training.',.77,3.00,5.55,.94,18);
line(s,.78,4.16,5.28,0,C.rule);text(s,'Exact completion requires both:',.76,4.46,5.52,.57,19,{bold:true});text(s,'1. Retrieve the requested value.\n2. Return it in a valid final answer.',.78,5.15,5.59,1.07,19);
fig(s,asset('evaluation.png'),6.57,2.01,5.98,4.45,'Only the scoring component reads private task state. The model receives the request, tool documentation and visible feedback.');
foot(s,'6 additional tasks should stop and are scored separately. The models see public feedback, never the scorer’s hidden state.');

s=page(C.paper,'Here are the primary results, before averaging over training seeds. At seed 101, supervision finished twenty-nine out of thirty tasks; preference training finished twenty-one. Match the outcomes by task: twenty were finished by both, nine only by supervision, one only by preference training, and none by neither. The net difference is eight. The same tasks were given to both models, so the paired task, not each model output in isolation, is what we compare.');
head(s,'RESULT  /  MATCHED TASKS');title(s,'Supervision finished 29 of 30 tasks; preference training finished 21.',{size:27});
text(s,'One mark per task. Color and shape show which training method produced an exact finish.',.74,1.95,11.89,.55,18,{color:C.muted});
fig(s,asset('pairs.png'),.7,2.62,11.94,3.65,'Out of 30 matched tasks: 20 finished by both, 9 by corrective SFT only, 1 by action DPO only, and 0 by neither.');
const margin=diff('P_vs_B3',true);
text(s,'A net gain of 8 tasks at training seed 101.',.75,6.12,11.75,.5,23,{bold:true,color:C.burg});
foot(s,'The untrained model finished '+ratio('B0')+'. SFT minus DPO: '+margin[0]+' percentage points (paired 95% interval, '+margin[1]+' to '+margin[2]+').');

s=page(C.paper,'The one exception is worth opening. In a background job task, the two models both corrected an invalid identifier and retrieved the job result. The supervised model kept going instead of finishing correctly. It exhausted the twelve-turn budget without a valid final answer. The preference model returned a valid structured answer on turn four. Because the evaluator required the final answer, only preference training earned an exact completion. This also shows why our advantage is a net result, not per-task dominance.');
head(s,'RESULT  /  THE EXCEPTION');title(s,'Both found the job result. Only DPO delivered it.');
text(s,'After an invalid job ID, both models found the correct ID and fetched the result.',.74,2.03,11.84,.63,19,{color:C.muted});
line(s,.79,3.04,5.57,0,C.burg,2);line(s,6.97,3.04,5.57,0,C.copper,2);
text(s,'Corrective SFT',.8,3.28,5.42,.65,24,{fontFace:serif,color:C.burg});text(s,'Reached the tool goal, but used all 12 turns without a valid final answer.',.81,4.12,5.35,1.1,20);text(s,'Not an exact finish.',.81,5.55,5.35,.51,18,{bold:true,color:C.burg});
text(s,'Action DPO',6.98,3.28,5.42,.65,24,{fontFace:serif,color:C.copper});text(s,'Reached the same goal and returned a valid final answer on turn 4.',6.99,4.12,5.35,1.1,20);text(s,'Exact finish.',6.99,5.55,5.35,.51,18,{bold:true,color:C.copper});
foot(s,'Seed 101, case g4h9-jobs-recoverable-h1-01. Scoring records: EXP-20261120-081 (SFT) and EXP-20261120-084 (DPO).');

s=page(C.paper,'A seed changes the random choices during training. We trained fresh versions three times, then gave them the same test tasks. SFT finished 29 each time. DPO finished 21, 21 and 13. The contrast is particularly clear on the ten background jobs: supervision gets nine every time; the third preference run gets none. These are three runs on one shared test set, not three new test sets.');
head(s,'RESULT  /  THREE TRAINING RUNS');title(s,'On 10 unseen jobs, SFT finished 9 each time. DPO finished 5, 5 and 0.',{size:27});
text(s,'Same test, three fresh training runs. Each line pairs the two methods at the same seed.',.74,1.96,11.87,.56,18,{color:C.muted});
fig(s,asset('seeds.png'),.7,2.51,11.94,3.98,'At seeds 101, 202, 303, SFT finished 29/30, 29/30, 29/30 and 9/10 jobs each time. DPO finished 21/30, 21/30, 13/30 and 5/10, 5/10, 0/10 jobs.');
foot(s,'Left: all 30 completable tasks. Right: the 10 background jobs within those 30, not an additional test set.');

s=page(C.burg,'Suppose we ignored completions and just chose whatever looped least. To see what can go wrong, look at the preference model after we remove normal-task examples from training. It records no invalid loops, but also completes none of the thirty eligible tasks. It gives up early twenty-two times. With normal examples present, it gives up only once. We removed examples, so the data size changed along with the composition; it is not a controlled attribution to only one factor.');
head(s,'RESULT  /  WHY COUNT FINISHED TASKS?',true);title(s,'A model can stop looping by giving up.',{color:C.paper,size:36});
text(s,'Preference training, with normal-task examples removed:',.77,2.07,11.78,.6,20,{color:C.fog});
text(s,fr(m('P_no_normal','invalid_loop_rate_v2')),.78,3.04,5.39,1.14,61,{fontFace:serif,color:C.peach});text(s,'tasks with an invalid loop',.8,4.31,5.35,.65,22,{color:C.paper});
line(s,6.49,3.04,0,2.04,'AE7F8E',1.2);
text(s,fr(m('P_no_normal',metric)),7.02,3.04,5.39,1.14,61,{fontFace:serif,color:C.peach});text(s,'completable tasks finished',7.05,4.31,5.35,.65,22,{color:C.paper});
text(s,'Early abandonment: '+fr(m('P_no_normal','premature_abandonment'))+', versus '+fr(m('P','premature_abandonment'))+' with the full training set.',.78,5.88,11.64,.59,22,{bold:true,color:C.paper});
foot(s,'All 36 tasks count for looping. Only the 30 feasible tasks count for completion and early abandonment.',true);

s=page(C.deep,'Our answer has two parts. To act after a failure, use the feedback: change an invalid identifier, but allow a retry after a transient timeout. To train this 0.8B model to make those decisions, start by imitating verified corrections. Adding a rejected alternative did not improve exact task completion here. Supervision finished 29 out of 30 across all three runs, and the advantage carried to the unseen jobs domain. Retain normal examples and score completed tasks, not just fewer loops. What I would test next is whether the choice survives real services, human review and a different training budget. The archived scoring was replayed on CPU, but that is not an independent evaluation in a real service.');
head(s,'ANSWER  /  TRAINING CHOICE',true);title(s,'Use supervised corrections for this model.',{color:C.paper,size:32});
text(s,'29/30',.74,2.38,5.45,1.48,73,{fontFace:serif,color:C.peach});text(s,'exact finishes with SFT in\neach of three training runs.',.79,4.05,5.46,1.09,22,{color:C.paper});
line(s,6.5,2.51,0,3.51,'94737E',1.15);
text(s,'What to teach',6.89,2.33,5.35,.49,19,{bold:true,color:C.peach});text(s,'Repair a bad request; retry a temporary failure. Keep normal finishes and correct stops in training.',6.91,2.94,5.32,1.3,18,{color:C.paper});
text(s,'What to measure',6.89,4.65,5.36,.49,19,{bold:true,color:C.peach});text(s,'Count grounded final answers alongside loops. Test again on real services with human review.',6.91,5.24,5.32,1.12,18,{color:C.paper});
foot(s,'One 0.8B base model, a 50-step training budget, simulated tools. This is not a general ranking of SFT and DPO.',true);
// Backup A: underlying count matrix (P and B3 take seed 101).
s=page(C.paper,'The first three configurations are untrained baselines: the original model, a stronger prompt and an external guard. B4 and B5 test different preference recipes; the last two rows remove types of examples from P. Each cell gives its numerator and denominator. Lower is better for invalid loops and false success, higher is better for the other columns.');
head(s,'BACKUP A  /  FULL RESULTS');title(s,'All nine configurations on the held-out tasks.');
const keys=['task_completion_exact','necessary_retry_success_exact','invalid_loop_rate_v2','false_success','correct_termination_exact'];
table(s,[['Configuration','Exact finish','Required retry','Invalid loop','False success','Correct stop'],...['B0','B1','B2','B3','B4','B5','P','P_no_retry','P_no_normal'].map((a,i)=>[['B0  Original model','B1  Strong prompt','B2  External guard','B3  Corrective SFT','B4  Single-token preference','B5  Expert preference','P    Action preference','P    No retry examples','P    No normal examples'][i],...keys.map(k=>fr(m(a,k)))])],{y:2.1,widths:[3.51,1.62,1.85,1.55,1.71,1.61],rowH:.43,font:14});
foot(s,'B3 and P: seed 101. Lower is better for invalid loops and false success; higher is better for the other columns.');
// Backup B: confidence intervals and distinct units of inference.
s=page(C.paper,'The archived contrast is P minus B3. In the display we reverse the sign, reordering the interval endpoints, so positive means supervision performs better. For the three-seed estimate, results are first averaged over training seeds and then paired by template. The interval is a template-clustered t interval over the eligible groups, not a three-seed standard deviation. The audit is deterministic replay of saved outputs, not independent inference.');
head(s,'BACKUP B  /  SCORING AND UNCERTAINTY');title(s,'How large is the completion advantage?');
text(s,'Difference in exact completion, in percentage points',.74,1.96,11.79,.52,20,{color:C.muted});
const c1=diff('P_vs_B3',true),c3=diff('P_vs_B3_3train_seed_cluster_avg',true);
table(s,[['B3 minus P','Estimate','95% interval'],['Matched seed 101','+'+c1[0],c1[1]+' to '+c1[2]],['Mean across 3 training seeds','+'+c3[0],c3[1]+' to '+c3[2]]],{y:2.75,widths:[5.58,2.73,3.54],rowH:.68,font:18});
text(s,'Unit of comparison: 30 matched, completable template groups.',.77,5.27,11.74,.62,21,{bold:true,color:C.burg});text(s,'The three-seed interval is not a measure of seed-to-seed spread.',.77,6.03,11.74,.45,18,{color:C.muted});
foot(s,'Archived analysis: EXP-20261120-105R1. Template-clustered, paired t intervals; signs reversed from the original P-minus-B3 contrast.');
p.writeFile({fileName:path.join(__dirname,'ToolLoop-Fix.pptx')});
