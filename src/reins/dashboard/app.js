'use strict';
const $ = id => document.getElementById(id);
const labels = {running:'运行中',completed:'已完成',failed:'失败',unknown:'状态未知',cancelled:'已取消'};
const kinds = {run_start:'任务开始',run_end:'任务结束',step_start:'步骤开始',step_end:'步骤结束',span_start:'模型调用',span_end:'调用结束',span_update:'预算决策',retry:'应用重试',policy_validation:'策略验收'};
let page=1,total=0,selected=null,view='runs',busy=false,lastUpdated=null,cohorts=[],cohortSignature='';
const money = value => '$'+Number(value||0).toFixed(6);
const seconds = value => value < 60 ? value.toFixed(1)+' 秒' : Math.floor(value/60)+' 分 '+Math.floor(value%60)+' 秒';
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function badge(state){return el('span',labels[state]||state,'badge '+state);}
function card(label,value,note){const n=el('div',undefined,'card');n.append(el('div',label,'card-label'),el('div',value,'card-value'),el('div',note,'card-note'));return n;}
async function api(path){const response=await fetch(path,{signal:AbortSignal.timeout(8000)});if(!response.ok)throw Error('HTTP '+response.status);return response.json();}
function resultLabel(r){return r.success===null?'未标记':r.success?'合格':'不合格';}
async function loadRuns(){
 const query=new URLSearchParams({page,agent:$('agent-filter').value.trim(),task_type:$('task-filter').value.trim(),state:$('state-filter').value});
 const data=await api('/api/runs?'+query);total=data.total;
 if(page>1 && (page-1)*50>=total){page=1;return loadRuns();}
 $('summary').replaceChildren(card('运行中',data.items.filter(r=>r.state==='running').length,'当前页 · 包含子任务'),card('需要关注',data.items.filter(r=>r.inactive||['failed','unknown','cancelled'].includes(r.state)).length,'当前页 · 异常或长时间无活动'),card('已完成',data.items.filter(r=>r.state==='completed').length,'执行完成不等于业务合格'),card('筛选结果',total,'每页 50 条 · 运行中优先'));
 $('count').textContent=total+' 个任务';$('page').textContent=page+' / '+Math.max(1,Math.ceil(total/50));$('prev').disabled=page===1;$('next').disabled=page*50>=total;
 $('runs').replaceChildren();
 for(const r of data.items){const row=el('tr',undefined,'run-row');row.tabIndex=0;row.setAttribute('aria-label','查看 '+r.agent);row.onclick=()=>selectRun(r.run_id);row.onkeydown=e=>{if(e.key==='Enter')selectRun(r.run_id);};
 const name=el('td');name.append(el('strong',r.agent),el('small',(r.parent_run_id?'↳ 子任务 · ':'')+r.task_type));if(r.simulated)name.append(el('span','模拟数据','attention'));
 const state=el('td');state.append(badge(r.state));if(r.inactive)state.append(el('br'),el('span','长时间无活动','attention'));
 const active=el('td',r.active_steps.map(s=>s.name).join(' / ')||'—');active.append(el('small','最近活动 '+new Date(r.last_activity).toLocaleTimeString()));
 const cost=el('td',money(r.known_cost));cost.append(el('small','预留 '+money(r.reserved_cost)+' · 待结算 '+r.pending_requests));if(r.unknown_reservations)cost.append(el('small','含未知金额预留'));
 row.append(name,state,active,el('td',seconds(r.duration_seconds)),cost,el('td',resultLabel(r)));$('runs').append(row);}
 if(!data.items.length){const row=el('tr'),cell=el('td','暂无匹配任务。运行带 @trace 的 Agent 后，任务会显示在这里。','empty');cell.colSpan=6;row.append(cell);$('runs').append(row);}
 if(selected)await loadDetail();
}
async function selectRun(id){selected=id;try{await loadDetail();$('detail').scrollIntoView({behavior:'smooth',block:'start'});}catch(e){offline();}}
async function loadDetail(){
 const id=selected,r=await api('/api/runs/'+encodeURIComponent(id));if(id!==selected)return;
 const opened=new Set([...$('detail').querySelectorAll('details[open]')].map(n=>n.dataset.seq));const box=$('detail');box.hidden=false;box.replaceChildren();
 const header=el('div',undefined,'detail-head'),title=el('div');title.append(el('h2',r.agent+' · 任务详情'),el('p',r.run_id,'detail-meta'));
 const close=el('button','收起');close.onclick=()=>{selected=null;box.hidden=true;};header.append(title,close);box.append(header,badge(r.state),el('p','已确认 '+money(r.known_cost)+' · 预留 '+money(r.reserved_cost)+' · 业务结果：'+resultLabel(r),'detail-meta'));
 if(r.state==='unknown')box.append(el('p','运行中断或状态未知：未检测到新鲜心跳。下方保留最后观测到的步骤。','attention'));
 for(const child of r.children){const button=el('button','↳ '+child.agent+' · '+labels[child.state],'child');button.onclick=()=>selectRun(child.run_id);box.append(button);}
 box.append(el('h2','执行时间线'));
 for(const e of r.events){const row=el('div',undefined,'event');row.append(el('time',new Date(e.timestamp).toLocaleTimeString()),el('div',undefined,'event-dot'));const detail=el('details');detail.dataset.seq=e.seq;detail.open=opened.has(String(e.seq));const p=e.payload;
 detail.append(el('summary',(kinds[e.event_type]||e.event_type)+' · '+(p.name||p.error||'')+(p.status?' · '+p.status:'')));
 const lines=[['运行',e.run_id],['步骤',e.span_id],['上级步骤',p.parent_span_id],['模型',p.model],['原始模型',p.model_requested],['预算决策',p.decision],['输入 / 输出 tokens',p.tokens_in===undefined?null:p.tokens_in+' / '+p.tokens_out],['重试次数',p.count],['耗时',p.elapsed_seconds===undefined?null:seconds(p.elapsed_seconds)],['已确认费用',p.confirmed_cost===undefined?null:p.confirmed_cost===null?'待结算':money(p.confirmed_cost)],['待结算预留',p.cost_pending?money(p.reserved_cost):null],['策略',p.policy],['执行阶段',p.stage],['验收合格',p.accepted],['错误摘要',p.error]];
 for(const [key,value]of lines){if(value!==undefined&&value!==null&&value!=='')detail.append(el('p',key+'：'+value));}row.append(detail);box.append(row);}
 if(!r.events.length)box.append(el('p','历史任务没有记录步骤事件。','muted'));
}
function svg(tag,attrs,text){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;return n;}
function renderCompare(){
 const chosen=$('cohort').value;const rows=cohorts.filter(r=>JSON.stringify([r.task_type,r.dataset_id,r.mode,r.experiment_id,r.split])===chosen);
 $('compare-summary').replaceChildren(card('策略数量',rows.length,'同一数据集与运行模式'),card('运行次数',rows.reduce((a,r)=>a+r.runs,0),'包含重复案例'),card('待结算调用',rows.reduce((a,r)=>a+r.pending_requests,0),'结清前不输出完整成本结论'),card('已评估任务',rows.reduce((a,r)=>a+r.evaluated,0),'业务结果由应用标记'));
 $('comparison').replaceChildren();for(const r of rows){const tr=el('tr');[r.policy_version,r.unique_cases+' / '+r.runs,r.success_rate===null?'—':(r.success_rate*100).toFixed(1)+'%',r.cost_per_success===null?'不可计算':money(r.cost_per_success),money(r.known_total_cost),r.pending_requests].forEach(v=>tr.append(el('td',v)));$('comparison').append(tr);}
 const valid=rows.filter(r=>r.cost_per_success!==null&&r.success_rate!==null),colors=['#087f79','#467ac6','#bd7450','#926eb6','#c29831'];
 const chart=svg('svg',{viewBox:'0 0 900 330',role:'img','aria-label':'策略成本和合格率散点图'});const max=Math.max(...valid.map(r=>r.cost_per_success),0.000001)*1.15;
 for(let i=0;i<=4;i++){const y=270-i*55;chart.append(svg('line',{x1:85,y1:y,x2:840,y2:y,stroke:'#e2eaea'}),svg('text',{x:67,y:y+4,'text-anchor':'end',fill:'#687e89','font-size':12},i*25+'%'));const x=85+i*755/4;chart.append(svg('text',{x,y:295,'text-anchor':'middle',fill:'#687e89','font-size':11},money(max*i/4)));}
 chart.append(svg('text',{x:85,y:27,fill:'#687e89','font-size':12},'任务合格率'),svg('text',{x:840,y:320,'text-anchor':'end',fill:'#687e89','font-size':12},'每个合格任务成本（USD）'));
 $('legend').replaceChildren();valid.forEach((r,i)=>{const x=85+r.cost_per_success/max*755,y=270-r.success_rate*220;const point=svg('circle',{cx:x,cy:y,r:10+(valid.length-i)*2,fill:'none',stroke:colors[i%colors.length],'stroke-width':3});point.append(svg('title',{},r.policy_version+' · '+money(r.cost_per_success)+' · '+(r.success_rate*100).toFixed(1)+'%'));chart.append(point);const label=el('span',r.policy_version,'legend-item');const marker=svg('svg',{width:14,height:14});marker.append(svg('circle',{cx:7,cy:7,r:5,fill:colors[i%colors.length]}));label.prepend(marker);$('legend').append(label);});
 $('plot').replaceChildren(valid.length?chart:el('div','当前没有可绘制的完整成本与质量数据。','empty'));
 $('plot-note').textContent=(rows.some(r=>r.task_type.startsWith('simulated_'))?'模拟数据，仅用于演示。 ':'')+'重合圆环表示结果相同；不可计算的策略保留在表格中。';
}
async function loadCompare(){cohorts=await api('/api/compare');const value=$('cohort').value;const groups=new Map(cohorts.map(r=>[JSON.stringify([r.task_type,r.dataset_id,r.mode,r.experiment_id,r.split]),r]));const signature=JSON.stringify([...groups.keys()]);if(signature!==cohortSignature){cohortSignature=signature;$('cohort').replaceChildren();for(const[key,r]of groups){const o=el('option',r.task_type+' / '+r.dataset_id+' / '+r.mode+' / '+r.split+' / '+(r.experiment_id||'历史').slice(0,8));o.value=key;$('cohort').append(o);}if(groups.has(value))$('cohort').value=value;else {const ready=cohorts.find(r=>r.cost_per_success!==null);if(ready)$('cohort').value=JSON.stringify([ready.task_type,ready.dataset_id,ready.mode,ready.experiment_id,ready.split]);}}renderCompare();}
function offline(){$('connection').className='connection off';$('connection').textContent='连接已断开 · 最后更新 '+(lastUpdated||'尚未成功');}
async function refresh(){if(busy)return;busy=true;try{await(view==='runs'?loadRuns():view==='optimize'?loadOptimization():loadCompare());lastUpdated=new Date().toLocaleTimeString();$('connection').className='connection';$('connection').textContent='● 已连接 · '+lastUpdated;}catch(e){offline();}finally{busy=false;}}
async function loadOptimization(){
 const experiments=await api('/api/experiments'), select=$('experiment'), previous=select.value;
 const signature=JSON.stringify(experiments.map(e=>e.experiment_id));
 if(select.dataset.signature!==signature){select.dataset.signature=signature;select.replaceChildren();for(const e of experiments){const o=el('option',e.split+' · '+e.task_type+' · '+e.experiment_id.slice(0,8));o.value=e.experiment_id;select.append(o);}if(experiments.some(e=>e.experiment_id===previous))select.value=previous;}
 if(!select.value){$('optimization-summary').replaceChildren();$('optimization-rows').replaceChildren();$('optimization-note').textContent='还没有策略实验。运行 examples/outcome_optimizer.py --database ... --dashboard 创建一个模拟案例。';return;}
 const r=await api('/api/optimization?experiment='+encodeURIComponent(select.value));
 const reasons={insufficient_cases:'案例数不足',missing_outcomes:'缺少验收',cost_unavailable:'费用未完整结算',quality_below_floor:'质量低于门槛',latency_above_limit:'延迟超过上限'};
 $('optimization-summary').replaceChildren(card('推荐策略',r.recommendation||'不推荐新策略',r.split==='test'?'独立测试只报告结果':'验证集上的经验选择'),card('质量门槛',(r.quality_floor*100).toFixed(1)+'%','同时满足绝对门槛和基准允许降幅'),card('最低案例数',r.constraints.min_cases,'每个候选策略都运行相同案例'),card('相对基准成本变化',r.observed_savings_fraction===null?'—':(r.observed_savings_fraction*100).toFixed(1)+'%','正值表示观察到节省 · 非未来保证'));
 $('optimization-note').textContent=(r.task_type.startsWith('simulated_')?'模拟数据，不代表真实模型效果。 ':'')+(r.integrity_errors.length?'实验数据不完整：'+r.integrity_errors.join(', '):r.split==='test'?'测试集不重新选优；检查已选策略是否仍然达标。':'先排除质量、延迟或费用不合格的方案，再按合格结果成本排序。')+' 只计算已记录费用；上线前使用独立测试。';
 $('optimization-rows').replaceChildren();for(const c of r.candidates){const tr=el('tr');[c.policy_version,c.unique_cases,c.success_rate===null?'—':(c.success_rate*100).toFixed(1)+'%',c.cost_per_success===null?'不可计算':money(c.cost_per_success),c.latency_p95_ms===null?'—':c.latency_p95_ms.toFixed(1)+' ms',c.eligible?(c.policy_version===r.recommendation?'推荐':'达标'):c.reasons.map(x=>reasons[x]||x).join(' / ')||'实验不完整'].forEach(v=>tr.append(el('td',v)));$('optimization-rows').append(tr);}
}
function switchView(next){window.scrollTo({top:0});view=next;for(const key of ['runs','compare','optimize']){$(key+'-view').hidden=next!==key;$(key+'-tab').classList.toggle('active',next===key);}const titles={runs:['Agent 运行监控','从当前步骤到每次调用，看清任务如何完成。'],compare:['成本与质量','把每个合格结果的成本与质量放在一起比较。'],optimize:['质量约束下的策略优化','同一批任务、同一套验收标准，选择交付成本更低的合格方案。']};$('title').textContent=titles[next][0];$('subtitle').textContent=titles[next][1];refresh();}
$('optimize-tab').onclick=()=>switchView('optimize');$('experiment').onchange=refresh;
$('runs-tab').onclick=()=>switchView('runs');$('compare-tab').onclick=()=>switchView('compare');$('apply').onclick=()=>{page=1;refresh();};$('prev').onclick=()=>{page--;refresh();};$('next').onclick=()=>{page++;refresh();};$('cohort').onchange=renderCompare;refresh();setInterval(refresh,2000);
