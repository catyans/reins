(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const names = {'parser':'Deterministic extraction','fixed-lite':'Flash Lite · full refresh','fixed-flash':'Flash · full refresh','cached-cascade':'Parser + model + cache','incremental':'Reins incremental'};
  const phases = {'initial':'First collection','update-1':'Release update 1','update-2':'Release update 2','unchanged':'Unchanged recheck'};
  const fields = {'purpose':'Purpose','install_command':'Install command','license':'License','release_note':'Release note'};
  let lastStatus = null;
  let lastReport = null;
  let lastRecordsKey = '';
  const money = value => '$' + Number(value || 0).toFixed(value && value < .01 ? 5 : 3);
  const node = (tag, text, cls) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; };
  function sourceLink(url) { try { const u = new URL(url); return u.protocol === 'https:' && ['raw.githubusercontent.com','github.com'].includes(u.hostname) ? u.href : null; } catch { return null; } }
  function renderRecords() {
    if (!lastStatus) return;
    const policy = $('record-filter').value;
    let rows = lastStatus.records.filter(r => r.policy === policy);
    if (!rows.length && lastReport) rows = lastReport.records.filter(r => r.policy === policy).slice(-12).reverse();
    const unique = new Map(); rows.forEach(r => { if (!unique.has(r.project)) unique.set(r.project,r); });
    rows = [...unique.values()].slice(0,6);
    const key = JSON.stringify([policy,rows]); if (key === lastRecordsKey) return; lastRecordsKey = key;
    $('records').replaceChildren();
    if (!rows.length) { $('records').append(node('p','No completed records for this strategy yet.','muted')); return; }
    rows.forEach(row => {
      const card = node('article', undefined, 'record'); card.append(node('h3',row.project));
      const evaluated=lastReport?.records.find(r=>r.project===row.project && r.policy===row.policy && r.phase===row.phase);
      const quality=evaluated?.accepted ? 'Accepted in this pilot' : row.source_backed===false ? 'Source review needed' : 'Saved output';
      card.append(node('div',`${row.version || ''} · ${phases[row.phase]} · ${row.reused} fields reused · ${quality}`,'record-meta'));
      Object.entries(fields).forEach(([field,label]) => {
        const item = row.fields[field]; const section = node('div',undefined,'field' + ((row.changed_fields || []).includes(field) ? ' changed' : ''));
        section.append(node('label',label)); section.append(node('p',item?.value ?? 'Not available in this source snapshot'));
        const url = sourceLink(row.sources[item?.source]?.url);
        if (url) { const link = node('a','View pinned source ↗'); link.href=url; link.target='_blank'; link.rel='noopener noreferrer'; section.append(link); }
        card.append(section);
      }); $('records').append(card);
    });
  }
  function renderStatus(data) {
    lastStatus=data;
    const stale = Date.now()/1000 - data.updated_at > 15;
    const active = data.state === 'running' && !stale;
    $('connection').textContent = active ? 'Receiving live updates' : data.state === 'completed' ? 'Run completed' : stale ? 'Last saved state' : 'Runner idle';
    $('dot').classList.toggle('live',active);
    $('completed').textContent=data.progress.completed.toLocaleString(); $('total').textContent=`of ${data.progress.total.toLocaleString()} recorded tasks`;
    $('reused').textContent=data.metrics.reused_fields.toLocaleString(); $('calls').textContent=data.metrics.calls.toLocaleString(); $('cost').textContent=money(data.metrics.estimated_api_cost_usd);
    $('updated').textContent=`Last snapshot: ${new Date(data.updated_at*1000).toLocaleString()}${active ? ' · Cost updates as requests finish.' : data.metrics.unsettled_calls ? ' · Some request costs remain unresolved.' : ''}`;
    $('activity').replaceChildren();
    if (!active) $('activity').append(node('p',data.state === 'completed' ? 'This finite run has finished. Saved records and measurements remain available below.' : 'No active heartbeat. The page is showing saved state, not a simulated running task.','muted'));
    else data.activity.forEach(a => { const row=node('div',undefined,'activity-row'); row.append(node('strong',a.project || 'Project task'),node('span',a.stage),node('small',`${names[a.policy] || a.model || ''} · ${phases[a.phase] || ''}`)); $('activity').append(row); });
    renderRecords();
  }
  function renderReport(data) {
    lastReport=data; $('pdf-link').hidden=false;
    const scores=data.splits.test; const savings=data.qualified_savings;
    if (savings !== null && savings > 0) $('result-title').textContent=`${(savings*100).toFixed(1)}% lower API cost per accepted task.`;
    $('result-intro').textContent=`${data.project_count} real projects. Three release snapshots each. The chart shows held-out test tasks, including unchanged rechecks. ${data.baseline ? 'Baseline selected on validation: ' + names[data.baseline] + '.' : 'Compare each strategy’s quality and cost below.'}`;
    const bounded=Object.values(scores).some(s=>!s.cost_complete || !s.evaluation_complete);
    $('charts').replaceChildren(node('h3',bounded ? 'API cost to run the held-out workload' : 'Estimated API cost per accepted task'));
    if(bounded) $('charts').append(node('p','Solid bars show recorded usage. The hatched portion reserves the maximum estimated cost of requests whose responses were not received.','footnote'));
    const max=Math.max(...Object.values(scores).map(s=>bounded ? (s.cost_upper_bound ?? s.estimated_cost) : (s.cost_per_accepted || 0)),.000001);
    Object.entries(scores).forEach(([policy,s]) => {
      const row=node('div',undefined,'chart-row'); row.append(node('span',names[policy],'chart-label'));
      const amount=bounded ? s.estimated_cost : (s.cost_per_accepted || 0);
      const track=node('div',undefined,'track'); const bar=node('div',undefined,'bar'+(policy==='incremental'?' highlight':'')); bar.style.width=`${100*amount/max}%`; track.append(bar);
      if(bounded && s.cost_upper_bound>amount) { const reserve=node('div',undefined,'bar reserved'); reserve.style.width=`${100*(s.cost_upper_bound-amount)/max}%`; track.append(reserve); } row.append(track);
      const label=bounded ? money(amount)+(s.cost_upper_bound>amount ? ' – '+money(s.cost_upper_bound) : '') : s.cost_per_accepted === null ? 'No accepted tasks' : money(s.cost_per_accepted);
      const value=node('div',label,'chart-value'); value.append(node('small',`${s.accepted} accepted · ${s.assessed ?? s.tasks}/${s.tasks} assessed`),node('small',`${s.calls} model calls`)); row.append(value); $('charts').append(row);
    });
    $('phases').replaceChildren();
    Object.entries(data.phases).forEach(([phase,values]) => { const s=values.incremental; const card=node('article',undefined,'phase'); card.append(node('h3',phases[phase]),node('strong',`${s.calls} model calls`),node('p',`${s.accepted} accepted · ${s.assessed ?? s.tasks}/${s.tasks} assessed`),node('p',`${money(s.estimated_cost)} recorded usage`)); $('phases').append(card); });
    $('research-cost').textContent='All experiment API usage: '+Object.entries(data.research_cost).map(([k,v])=>`${k}: ${money(v.estimated_cost)} (${v.calls} calls)`).join(' · ')+'. Evaluation overhead is separate from workload execution.';
    renderRecords();
  }
  async function poll() {
    try { const r=await fetch('project-status.json',{cache:'no-store'}); if(r.ok) renderStatus(await r.json()); else if(lastStatus) renderStatus(lastStatus); }
    catch { if(lastStatus) renderStatus(lastStatus); else $('connection').textContent='No local run connected'; }
  }
  async function loadReport() { try { const r=await fetch('project-results.json',{cache:'no-store'}); if(r.ok) renderReport(await r.json()); } catch {} }
  $('record-filter').addEventListener('change',renderRecords);
  poll(); loadReport(); setInterval(poll,2000); setInterval(loadReport,30000);
})();
