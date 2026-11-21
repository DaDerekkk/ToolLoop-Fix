/* English research talk. Images of measurements are produced separately from
 * immutable archives. The training and sandbox plates are explanatory drawings. */
const fs=require('fs'),path=require('path'),Pptx=require('pptxgenjs');
const p=new Pptx();p.layout='LAYOUT_WIDE';p.lang='en-US';p.author='ToolLoop-Fix';
p.title='When should a tool-using model try again?';p.subject='A paired study of tool-call recovery in Qwen3.5-0.8B';
const root=path.resolve(__dirname,'../../..'),ex=path.join(root,'experiments');
const read=f=>fs.readFileSync(f,'utf8'), parse=f=>JSON.parse(read(f));
const results=parse(path.join(ex,'EXP-20261120-105R1/g3_g4_comparison.json')),arms=results.g4_e2e.arms;
const original=n=>path.join(__dirname,'../v3/assets',n),asset=n=>path.join(__dirname,'assets',n);
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
const invalid=JSON.parse(read(path.join(ex,'EXP-20261120-082/generations.jsonl')).split('\n')[0]);
const rejected=JSON.parse(read(path.join(ex,'EXP-20261120-082/trajectories.jsonl')).split('\n')[0]).events[0];
if(invalid.raw_text.split('\n')[1]!=='<=read_file>'||rejected.actual_tool_dispatch!==false||rejected.parser_error!=='MALFORMED_TOOL_XML')throw Error('Parser example changed');
function table(s,rows,{x=.73,y=2.07,w=11.85,widths,rowH=.44,font=15}={}){s.addTable(rows.map((r,i)=>r.map((v,j)=>({text:String(v),options:{color:i?C.ink:C.white,bold:!i||!j,fill:!i?C.burg:i%2?C.paper:C.panel,align:j?'center':'left'}}))),{x,y,w,colW:widths,rowH,fontFace:face,fontSize:font,margin:.075,border:{pt:.7,color:C.paper},autoPage:false})}
// Page 1: open on a decision, before naming training configurations.
let s=page(C.deep,'A request fails. If the order number is wrong, repeating it wastes another call. If the service timed out, that same retry can be exactly right. We asked whether a small model could tell the difference. It was not enough to count repeated calls: the model also had to finish the job. I will show you one order, the comparison we built around it, and the one task that only preference training completed exactly.');
head(s,'TOOLLOOP-FIX   /   RESEARCH TALK',true);
text(s,'When should a tool-using model try again?',.70,.98,12.02,.97,36,{fontFace:serif,color:C.paper});
text(s,'A controlled study of recovery in Qwen3.5-0.8B',.75,2.00,11.83,.58,21,{color:C.fog});
fig(s,asset('cover.png'),.73,2.93,8.75,3.50,'Conceptual illustration of a tool execution line: a record lookup, a timer and a repeated call.');
line(s,9.78,3.1,0,3.06,'9F7580',1.35);
text(s,'BAD IDENTIFIER',10.05,3.18,2.57,.42,15,{color:C.peach,bold:true});
text(s,'Find the right ID.',10.05,3.72,2.56,.72,18,{color:C.paper});
text(s,'TIMEOUT',10.05,4.91,2.57,.42,15,{color:C.peach,bold:true});
text(s,'Try the call again.',10.05,5.46,2.57,.74,18,{color:C.paper});
foot(s,'Simulated order, file and job tools  /  November 2026',true);
// Page 2: the English GIF replays a logged sequence and has a self-contained first frame.
s=page(C.paper,'Read the four calls from left to right. The first order identifier is not recognized, so the model looks it up. The lookup works, but fetching the order now times out. It does not look up the identifier again. It repeats the fetch, and the fourth call returns OK. Nothing in this figure is a hypothetical extra tool call. The animation highlights sequence, not elapsed time.');
head(s,'THE CASE');title(s,'One order needed both a lookup and a retry.');
text(s,'Four actual calls from a held-out order task',.73,2.02,11.32,.5,20,{color:C.muted});
fig(s,asset('trace.gif'),.72,2.65,11.88,2.83,'get_order returns INVALID_IDENTIFIER; find_order returns LOCATED; get_order returns TIMEOUT; repeating get_order returns OK.');
text(s,'The feedback changed. The right next action changed with it.',.75,5.92,11.75,.58,23,{bold:true,color:C.burg});
foot(s,'Recorded trajectory: EXP-20261120-081, order recovery case h3. Four tool dispatches, not four model retries.');
// Page 3: name the methods only after the motivating action is clear.
s=page(C.paper,'For each history, we checked the preferred next action by executing it. Both models saw exactly that history and that action. Corrective supervised fine-tuning, B3, learned to produce the action. The preference model, P, also saw a rejected alternative and was trained to favor the preferred one, with a supervised anchor. The 400 decisions included recovery, legitimate retries, normal completion and correct stopping. We fixed the base model and used 50 training steps per configuration.');
head(s,'THE COMPARISON');title(s,'Same histories. Same right action. Different training.');
fig(s,original('training.png'),.72,2.0,6.04,4.34,'Illustration of matched training examples: the supervised lane uses the chosen action; the preference lane also includes its rejected alternative.');
text(s,'B3: Supervised fine-tuning (SFT)',7.02,2.25,5.48,.72,20,{bold:true,color:C.burg});
text(s,'Imitate the verified next action.',7.04,3.0,5.28,.72,21);
line(s,7.04,3.99,5.36,0,C.rule);
text(s,'P: Direct preference optimization (DPO)',7.02,4.16,5.48,.82,20,{bold:true,color:C.copper});
text(s,'Prefer that action to a rejected one; keep a supervised loss as well.',7.04,4.99,5.3,1.23,20);
foot(s,'400 matched decision points  /  50 training steps per configuration  /  identical base model.');
// Page 4: make success and proper stopping separate outcomes.
s=page(C.pale,'What does a win mean here? The model gets the user request, tool descriptions and visible history, but not the hidden state used to score it. Thirty tasks are possible to complete. Six others are intentionally impossible: we score an appropriate stop separately. Ten of the completable tasks use a jobs tool that never appeared in training. No external loop guard assisted either trained model.');
head(s,'THE TEST');title(s,'Thirty tasks should finish. Six should stop.');
text(s,'30',.75,2.34,2.28,.99,55,{fontFace:serif,color:C.burg});
text(s,'completable tasks',2.59,2.41,3.61,.74,22,{bold:true});
text(s,'10 orders, 10 files, 10 jobs. The jobs domain was excluded from training.',.81,3.42,5.35,1.11,19);
line(s,.8,4.87,5.17,0,C.rule);
text(s,'6',.8,5.16,1.09,.73,36,{fontFace:serif,color:C.copper});
text(s,'unrecoverable tasks, scored for correct stopping',1.77,5.13,4.32,.94,18);
fig(s,original('evaluation.png'),6.54,2.01,6.02,4.4,'The model receives visible requests, documentation and tool feedback. Only the scorer reads hidden task state.');
foot(s,'36 unseen template groups. A separate 18-prefix recovery test does not contribute to end-to-end completion.');
// Page 5: paired cases expose the exception that an aggregate alone conceals.
s=page(C.paper,'A difference of eight sounds as though one method simply subsumed the other. The paired outcomes tell a different story. Twenty tasks were solved by both. Nine were solved only by corrective supervision, and one only by preference training. On the one jobs task that only DPO completed exactly, SFT reached the tool goal but did not produce a valid final response. There were no tasks missed by both in this particular comparison. That yields 29 versus 21 exact completions on the same thirty tasks. The task, not each model outcome, is the unit of comparison.');
head(s,'PAIRED OUTCOMES');title(s,'Nine tasks went one way. One went the other.');
text(s,'Same 30 tasks, same training seed (101). Exact completion is scored per task.',.74,1.96,11.88,.57,19,{color:C.muted});
fig(s,asset('pairs.png'),.7,2.64,11.95,3.64,'Paired task outcomes: both methods completed 20 tasks; only B3 completed nine; only P completed one; neither completed zero.');
text(s,'Net difference: 8 more completions with supervision.',.76,6.13,11.71,.48,22,{color:C.burg,bold:true});
foot(s,'The one DPO-only success was a jobs task: SFT reached the tool goal but returned no valid final answer.');
// Page 6: show all seeds instead of allowing the headline run to stand for stability.
s=page(C.paper,'A different training seed does not create a new test set. It does tell us whether the training result repeats. Supervision completed 29 tasks each time. Preference training completed 21, 21 and 13. Focus on the held-out jobs subset: nine out of ten for supervision in all three runs, versus five, five and zero for preference training. This is a warning about stability in this setting, not an estimate across all possible tools.');
head(s,'REPEATED TRAINING');title(s,'On unseen job tools, one preference run solved none.');
fig(s,asset('seeds.png'),.69,2.02,11.95,4.39,'Training seeds 101, 202, 303: B3 completes 29, 29, 29 of 30 tasks and 9, 9, 9 of 10 jobs. P completes 21, 21, 13 of 30 tasks and 5, 5, 0 of 10 jobs.');
foot(s,'The 10 jobs are a subset of the 30 completable tasks. The three seeds repeat training, not test-set sampling.');
// Page 7: negative control, not cosmetic metrics.
s=page(C.burg,'If we only optimized the headline loop rate, we could select a model that gives up. Here we removed the normal-task examples from preference training. The resulting model had no flagged loops, but it completed no tasks and abandoned 22 out of 30 early. In the full preference configuration, abandonment was one out of 30. This ablation changes both composition and data size, so it is not an isolated test of one causal factor.');
head(s,'A FAILURE OF THE METRIC',true);title(s,'Zero loops. Zero completed tasks.',{color:C.paper,size:37});
text(s,'Preference training with normal-task examples removed',.77,2.05,11.78,.52,21,{color:C.fog});
text(s,fr(m('P_no_normal','invalid_loop_rate_v2')),.79,2.95,5.0,1.09,59,{fontFace:serif,color:C.peach});
text(s,'tasks with an invalid loop',.83,4.31,5.23,.69,22,{color:C.paper});
line(s,6.53,3.08,0,1.84,'B0808C',1.2);
text(s,fr(m('P_no_normal',metric)),7.13,2.95,5.0,1.09,59,{fontFace:serif,color:C.peach});
text(s,'completable tasks finished',7.16,4.31,5.23,.69,22,{color:C.paper});
text(s,'Early abandonment rose from 1/30 to 22/30.',.82,5.88,11.59,.62,24,{bold:true,color:C.paper});
foot(s,'Removing examples changes the size as well as the makeup of the training set.',true);
// Page 8: a model trace supports the failure mode, not a hypothesis about gradients.
s=page(C.paper,'This is one actual generation from the single-token preference baseline, B4. On the second line, the opening angle bracket is immediately followed by an equals sign rather than a valid function tag. The parser marked it as malformed; no file read was dispatched. Across logged turns, format errors were rarer with B3, but this observation does not establish why. We did not intervene on the gradient or grammar in isolation.');
head(s,'INSIDE A FAILED CALL');title(s,'A broken tag stopped the call before dispatch.');
rect(s,.74,2.2,6.15,3.96,C.ink);text(s,'B4  /  RAW OUTPUT, FIRST TWO LINES',1.0,2.55,5.59,.36,14,{color:C.fog,bold:true});text(s,'<tool_call>',1.0,3.25,5.65,.59,25,{fontFace:'Courier New',color:C.paper});text(s,invalid.raw_text.split('\n')[1],1.0,3.96,5.67,.64,26,{fontFace:'Courier New',color:C.peach,bold:true});text(s,'MALFORMED_TOOL_XML',1.0,5.15,5.56,.48,17,{fontFace:'Courier New',color:C.fog});
text(s,'Share of generated turns rejected by the parser',7.21,2.24,5.12,1.05,21,{bold:true,color:C.burg});
for(const [i,a] of ['B3','P','B4'].entries()){let vv=diag(a),lo=pc(Math.min(...vv)),hi=pc(Math.max(...vv)),y=3.54+i*.77;text(s,a,7.26,y,1.13,.45,18,{bold:true});text(s,lo===hi?lo+'%':lo+'% to '+hi+'%',8.5,y,3.88,.45,20,{color:a==='P'?C.copper:C.burg})}
foot(s,'Observed range across 3 training seeds for B3 and P; 1 seed for B4. Percentages count generated turns, not tasks.');
// Page 9: make a decision and state what evidence would change it.
s=page(C.deep,'If I had to choose a training recipe for this particular model tomorrow, I would start with verified corrections. The advantage survived all three training seeds on this held-out suite. The CPU audit replayed 846 episodes and 4,029 saved generations with zero scoring-field discrepancies. That verifies the archived scoring procedure; it does not add new model samples. I would want to repeat the comparison with real tools, human review and a different training budget before generalizing.');
head(s,'WHAT I WOULD USE',true);title(s,'For this model, start with verified corrections.',{color:C.paper,size:34});
text(s,'29/30',.75,2.33,5.1,1.42,74,{fontFace:serif,color:C.peach});
text(s,'exact completions with B3,\nin each of three training runs.',.79,4.08,5.29,1.12,23,{color:C.paper});
line(s,6.46,2.63,0,3.12,'886773');
text(s,'What we checked',6.93,2.42,5.24,.52,20,{bold:true,color:C.peach});
text(s,'CPU replay of 846 recorded episodes reproduced the archived scoring fields.',6.94,3.0,5.26,1.03,19,{color:C.paper});
text(s,'What remains open',6.93,4.54,5.24,.52,20,{bold:true,color:C.peach});
text(s,'Whether the ranking holds with real tools and human review.',6.94,5.11,5.23,.94,19,{color:C.paper});
foot(s,'One 0.8B base model, one training budget, simulated tools. This is not a general ranking of SFT and DPO.',true);
// Backup A: underlying count matrix (P and B3 take seed 101).
s=page(C.paper,'The first three configurations are untrained baselines: the original model, a stronger prompt and an external guard. B4 and B5 test different preference recipes; the last two rows remove types of examples from P. Each cell gives its numerator and denominator. Lower is better for invalid loops and false success, higher is better for the other columns.');
head(s,'BACKUP A  /  FULL RESULTS');title(s,'All nine configurations on the held-out tasks.');
const keys=['task_completion_exact','necessary_retry_success_exact','invalid_loop_rate_v2','false_success','correct_termination_exact'];
table(s,[['Configuration','Exact finish','Required retry','Invalid loop','False success','Correct stop'],...['B0','B1','B2','B3','B4','B5','P','P_no_retry','P_no_normal'].map((a,i)=>[['B0  Original model','B1  Strong prompt','B2  External guard','B3  Corrective SFT','B4  Single-token preference','B5  Expert preference','P    Action preference','P    No retry examples','P    No normal examples'][i],...keys.map(k=>fr(m(a,k)))])],{y:2.1,widths:[3.51,1.62,1.85,1.55,1.71,1.61],rowH:.43,font:14});
foot(s,'Main B3 and P configurations: training seed 101. Invalid loop and false success cover all 36 tasks; exact finish covers 30.');
// Backup B: confidence intervals and distinct units of inference.
s=page(C.paper,'The archived contrast is P minus B3. In the display we reverse the sign, reordering the interval endpoints, so positive means supervision performs better. For the three-seed estimate, results are first averaged over training seeds and then paired by template. The interval is a template-clustered t interval over the eligible groups, not a three-seed standard deviation. The audit is deterministic replay of saved outputs, not independent inference.');
head(s,'BACKUP B  /  SCORING AND UNCERTAINTY');title(s,'How large is the completion advantage?');
text(s,'Difference in exact completion, in percentage points',.74,1.96,11.79,.52,20,{color:C.muted});
const c1=diff('P_vs_B3',true),c3=diff('P_vs_B3_3train_seed_cluster_avg',true);
table(s,[['B3 minus P','Estimate','95% interval'],['Matched seed 101','+'+c1[0],c1[1]+' to '+c1[2]],['Mean across 3 training seeds','+'+c3[0],c3[1]+' to '+c3[2]]],{y:2.75,widths:[5.58,2.73,3.54],rowH:.68,font:18});
text(s,'Unit of comparison: 30 matched, completable template groups.',.77,5.27,11.74,.62,21,{bold:true,color:C.burg});text(s,'The three-seed interval is not a measure of seed-to-seed spread.',.77,6.03,11.74,.45,18,{color:C.muted});
foot(s,'Archived analysis: EXP-20261120-105R1. Template-clustered, paired t intervals; signs reversed from the original P-minus-B3 contrast.');
p.writeFile({fileName:path.join(__dirname,'ToolLoop-Fix-English.pptx')});
