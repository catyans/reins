'use strict';
const $ = id => document.getElementById(id);
const state = {budget: .08, mode: 'enforce', action: 'degrade', running: false, cost: 0, diagnoses: []};
const money = n => '$' + n.toFixed(3);
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
function node(tag, text, cls) {const n = document.createElement(tag);n.textContent = text;if(cls)n.className = cls;return n;}
function openTab(name) {
 for(const tab of ['runs','budget','diagnostics','optimize']) {$('tab-'+tab).hidden = tab !== name;document.querySelector('[data-tab="'+tab+'"]').classList.toggle('selected', tab === name);}
 $('breadcrumb').textContent = {runs:'Run explorer',budget:'Budget controls',diagnostics:'Diagnostics',optimize:'Policy optimizer'}[name];
}
document.querySelectorAll('[data-tab],[data-open]').forEach(b=>b.addEventListener('click',()=>openTab(b.dataset.tab||b.dataset.open)));
function log(message){const row=node('div','','log-entry');row.append(node('time',new Date().toLocaleTimeString('en-US',{hour12:false})),node('p',message));$('event-log').append(row);$('event-log').scrollTop=$('event-log').scrollHeight;}
function diagnose(title,description){state.diagnoses.push({title,description});$('diag-count').textContent=state.diagnoses.length;$('diagnosis-list').replaceChildren();for(const d of state.diagnoses){const box=node('article','','diagnosis-item');box.append(node('h4',d.title),node('p',d.description));$('diagnosis-list').append(box);}}
function step(index,status){const row=document.querySelector('[data-step="'+index+'"]');row.className='trace-row '+status;row.querySelector('.step-state').textContent={active:'Running',done:'Completed',failed:'Stopped',waiting:'Queued'}[status];}
$('budget-range').oninput=()=>{$('task-budget').value=Number($('budget-range').value).toFixed(3);};
$('task-budget').oninput=()=>{$('budget-range').value=Math.min(.2,Number($('task-budget').value));};
$('budget-form').onsubmit=e=>{e.preventDefault();if(!$('budget-form').reportValidity())return;state.budget=Number($('task-budget').value);state.mode=$('budget-mode').value;state.action=$('budget-action').value;$('budget-value').textContent=money(state.budget);$('budget-feedback').textContent=state.running?'Saved for the next simulated run.':'Saved. Open the run explorer to try it.';};
$('run-demo').onclick=async()=>{
 if(state.running)return;
 state.running=true;state.cost=0;state.diagnoses=[];$('diag-count').textContent='0';$('diagnosis-list').replaceChildren(node('p','This task is running.'));const config={...state}, scenario=$('scenario').value;
 $('run-demo').disabled=true;$('scenario').disabled=true;$('event-log').replaceChildren();$('cost-value').textContent=money(0);$('step-count').textContent='0';$('result-caption').textContent='Awaiting validation';$('run-badge').textContent='Running';$('run-badge').className='badge';$('model-caption').textContent='LLM · demo-strong';
 for(let i=0;i<4;i++)step(i,'waiting');
 const started=Date.now();let complete=true, accepted=true;
 try{
  log('Task started · '+config.mode+' · Budget '+money(config.budget));
  for(let i=0;i<4;i++){
   step(i,'active');await wait(500);
   if(i===0&&scenario==='timeout'){log('Source timed out. Starting one simulated retry.');diagnose('Source timeout','The simulated retry recovered. Check source availability and retry limits in a real integration.');await wait(500);}
   if(i===1){let charge=scenario==='pressure'?.12:.04;let model='demo-strong';
    if(config.mode==='enforce'&&charge>config.budget){if(config.action==='degrade'&&config.budget>=.008){charge=.008;model='demo-small';accepted=scenario!=='pressure';log('Budget insufficient. Switching to approved demo-small.');diagnose('Budget-triggered model switch','An approved substitute may not meet the same quality bar. Compare acceptance in the policy optimizer.');}else{log('The cost bound exceeds the available budget. No request was sent.');diagnose('Request blocked by budget','No model cost was incurred for this call. Adjust the budget or redesign the task.');step(i,'failed');complete=false;break;}}
    else if(charge>config.budget){log('Observe mode: record the overage without blocking.');diagnose('Simulated budget exceeded','Observe mode does not enforce a hard limit. Configure Enforce for supported budget controls.');}
    state.cost+=charge;$('cost-value').textContent=money(state.cost);$('model-caption').textContent='LLM · '+model;log(model+' completed. Simulated cost: '+money(charge));
   }
   if(i===2){log(accepted?'Fields passed source validation.':'Headquarters confused with distributor location. Validation failed.');if(!accepted){step(i,'failed');complete=false;diagnose('Business validation failed','This result is not delivered. A validated cascade can escalate to a stronger model, counting both attempts and validation costs.');break;}}
   step(i,'done');$('step-count').textContent=String(i+1);
  }
  $('run-badge').textContent=complete?'Completed':'Stopped';$('result-caption').textContent=complete?'Result accepted':'No accepted result delivered';log(complete?'Structured result saved (simulation only).':'Task stopped. Incurred costs remain in the ledger.');
  if(!state.diagnoses.length)diagnose('Simulation completed normally','Source validation passed. Try a complex task or change the budget to explore other decisions.');
 }finally{state.running=false;$('run-demo').disabled=false;$('scenario').disabled=false;$('elapsed').textContent=((Date.now()-started)/1000).toFixed(1)+'s';}
};
$('copy-code').onclick=async()=>{try{await navigator.clipboard.writeText($('sdk-code').textContent);$('toast').textContent='Code copied';}catch{$('toast').textContent='Clipboard unavailable. Select the code to copy it.';}$('toast').hidden=false;setTimeout(()=>{$('toast').hidden=true;},2200);};
let report;
function renderOptimization(){
 const floor=Number($('quality-floor').value)/100;$('quality-display').textContent=(floor*100).toFixed(0)+'%';if(!report)return;
 const rows=report.validation.candidates, eligible=rows.filter(r=>r.success_rate>=Math.max(floor,report.validation.quality_floor)&&r.cost_per_success!==null), best=[...eligible].sort((a,b)=>a.cost_per_success-b.cost_per_success)[0];
 $('optimizer-result').textContent=best?'Sandbox selection: '+best.policy_version+' · Per accepted task '+money(best.cost_per_success):'No policy meets this quality threshold';
 $('optimizer-rows').replaceChildren();for(const r of rows){const tr=document.createElement('tr');[r.policy_version,(r.success_rate*100).toFixed(0)+'%',money(r.cost_per_success),r===best?'Lowest eligible cost':eligible.includes(r)?'Eligible':'Below quality bar'].forEach(t=>tr.append(node('td',t)));$('optimizer-rows').append(tr);}
}
$('quality-floor').oninput=renderOptimization;
fetch('demo-report.json').then(r=>{if(!r.ok)throw Error('report');return r.json();}).then(r=>{report=r;renderOptimization();}).catch(()=>{$('optimizer-result').textContent='Report unavailable. Generate website/demo-report.json with examples/outcome_optimizer.py and refresh.';});
// A public deployment must not send visitors to a service on their own computer.
if(!['127.0.0.1','localhost'].includes(location.hostname))document.querySelectorAll('a[href^="http://127.0.0.1"]').forEach(a=>a.hidden=true);

openTab("optimize");
