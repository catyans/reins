let credential = "", timer, selection = location.hash.slice(1), refreshing = false;
const $ = s => document.querySelector(s);
const el = (tag, text) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; return n; };
const money = n => n === null || n === undefined ? "Not available" : "$" + Number(n).toLocaleString("en-US", {maximumFractionDigits: 7});
async function api(path, body) {
  const r = await fetch("/v1/" + path, {method: "POST", headers: {"Content-Type": "application/json", Authorization: "Bearer " + credential}, body: JSON.stringify(body)});
  if (!r.ok) throw Error(r.status === 403 ? "Operator credential required for this action." : "Request rejected. Check the inputs and current task state.");
  return r.json();
}
function table(headers, rows) {
  const scroll = el("div"), t = el("table"), h = el("tr"); scroll.className = "scroll";
  headers.forEach(v => h.append(el("th", v))); t.append(h);
  rows.forEach(row => { const r = el("tr"); row.forEach(v => r.append(el("td", v))); t.append(r); });
  scroll.append(t); return scroll;
}
function details(key, title, content, opened) {
  const d = el("details"); d.dataset.key = key; d.open = opened.has(key); d.append(el("summary", title), content); return d;
}
function meter(value, max, label) {
  const p = el("progress"); p.max = Math.max(Number(max), .000000001); p.value = Math.max(0, Number(value)); p.setAttribute("aria-label", label); return p;
}
function actionButton(label, action, w) {
  const b = el("button", label); b.type = "button";
  b.onclick = async () => {
    const reason = prompt("Reason for " + label.toLowerCase() + ":");
    if (!reason) return;
    b.disabled = true;
    try { await api("transition", {workflow_id: w.workflow_id, action, reason}); await refresh(); }
    catch (e) { $("#message").textContent = e.message; b.disabled = false; }
  }; return b;
}
const reasonLabels = {
  repeated_operation: "Repeated work stopped", consecutive_failures: "Repeated failures stopped",
  shared_budget: "Work budget reached", pool_budget: "Team budget reached",
  critical_reserve_protected: "Critical-task budget protected", deadline: "Task deadline reached",
  tool_limit: "Tool-call limit reached", iteration_limit: "Operation limit reached",
  wrapup_only: "Saving partial results", bound_exceeded: "Reported charge exceeded its declared bound"
};
async function refresh() {
  if (refreshing || !credential) return;
  refreshing = true;
  try {
    const data = await api("status", {}), root = $("#workflows");
    const opened = new Set([...root.querySelectorAll("details[open]")].map(d => d.dataset.key));
    root.replaceChildren();
    $("#message").textContent = "Connected · " + data.workflows.length + " workflows · " + data.unmatched_bill_lines + " unmatched invoice lines";
    const filter = $("#filter"); filter.replaceChildren();
    const all = el("option", "All workflows"); all.value = ""; filter.append(all);
    data.workflows.forEach(w => { const o = el("option", w.workflow_id); o.value = w.workflow_id; filter.append(o); });
    filter.value = selection;
    const pools = $("#pools"); pools.replaceChildren();
    for (const p of data.pools || []) {
      const card = el("article"); card.append(el("h2", p.team_id + " · shared budget"),
        el("p", money(p.committed) + " committed of " + money(p.budget)),
        meter(p.committed, p.budget, "Shared pool committed spend"),
        el("p", money(p.critical_reserve) + " protected for critical tasks")); pools.append(card);
    }
    const summary = $("#rollups"); summary.replaceChildren();
    if (data.rollups?.length) summary.append(table(["Customer / team", "Recorded cost", "Reserved", "Accepted results", "Cost / accepted result"],
      data.rollups.map(g => [g.customer_id + " / " + g.team_id, money(g.recorded_cost), money(g.reserved_cost), g.accepted, money(g.cost_per_accepted_result)])));
    for (const w of data.workflows.filter(w => !filter.value || w.workflow_id === filter.value)) {
      const card = el("article"), heading = el("div"); heading.className = "card-heading";
      heading.append(el("h2", w.task_type), el("span", w.state)); card.append(heading,
        el("p", w.customer_id + " / " + w.team_id + " · " + w.workflow_id));
      const metrics = el("div"); metrics.className = "metrics";
      for (const [label, value] of [["Usage-priced cost", w.known_cost], ["Reserved / unresolved", w.reserved_cost], ["Available for work", w.budget_state.work_remaining], ["Wrap-up reserve", w.budget_state.wrapup_reserve]]) {
        const m = el("div"); m.className = "metric"; m.append(el("strong", money(value)), el("span", label)); metrics.append(m);
      }
      card.append(metrics, meter(Number(w.known_cost) + Number(w.reserved_cost), w.budget, "Committed workflow budget"));
      const controls = el("div"); controls.className = "actions";
      if (w.state === "running") controls.append(actionButton("Pause", "pause", w), actionButton("Start wrap-up", "wrapup", w));
      if (w.state === "paused") controls.append(actionButton("Resume", "resume", w), actionButton("Start wrap-up", "wrapup", w));
      card.append(controls);
      const attention = w.events.find(e => ["denied", "bound_exceeded", "uncertain"].includes(e.kind));
      if (attention && w.state !== "completed") {
        const a = el("div"); a.className = "attention";
        a.append(el("strong", reasonLabels[attention.payload.reason || attention.kind] || "Review unresolved execution"),
          el("p", w.pending_requests ? "Unresolved charges remain reserved. Reconcile provider usage before repeating paid work." : "Review the task history, adjust a future workflow policy, or save partial results.")); card.append(a);
      }
      if (w.forecast.remaining_p90 !== null) card.append(el("p", "Estimated remaining cost: median " + money(w.forecast.remaining_p50) + " · P90 " + money(w.forecast.remaining_p90)));
      card.append(details(w.workflow_id + "/operations", "Execution history · " + w.requests.length + " operations", table(["Task", "Model / tool", "State", "Cost"],
        w.requests.map(r => [r.task_id, r.model, r.state, r.actual_cost === null ? "Reserved " + money(r.reserved_cost) : money(r.actual_cost)])), opened));
      card.append(details(w.workflow_id + "/ownership", "Task ownership · " + w.tasks.length + " tasks", table(["Task", "Parent", "Task cap"],
        w.tasks.map(t => [t.task_id, t.parent_task_id || "Workflow root", money(t.budget)])), opened));
      const timeline = el("ol"); timeline.className = "timeline";
      [...w.events].reverse().forEach(e => timeline.append(el("li", new Date(e.created * 1000).toLocaleTimeString() + " · " + e.task_id + " · " + (reasonLabels[e.payload.reason] || e.kind.replaceAll("_", " ")))));
      card.append(details(w.workflow_id + "/timeline", "Execution timeline", timeline, opened));
      if (w.context_samples.length) {
        const contexts = el("div"); contexts.append(el("p", "Local component estimates. Provider totals are recorded separately."));
        const revisions = new Map();
        for (const sample of w.context_samples) if (!revisions.has(sample.revision)) revisions.set(sample.revision, sample);
        for (const [revision, sample] of revisions) {
          contexts.append(el("h3", revision + " · provider input " + (sample.provider_input_tokens ?? "unavailable")));
          const total = Object.values(sample.components).reduce((a,b) => a+b, 0);
          for (const [name, value] of Object.entries(sample.components)) {
            const row = el("div"); row.className = "component";
            row.append(el("span", name.replaceAll("_", " ")), meter(value, total, name), el("span", value)); contexts.append(row);
          }
        }
        card.append(details(w.workflow_id + "/context", "Context composition and revisions", contexts, opened));
      }
      const econ = w.economics, e = el("div");
      e.append(table(["Recorded total cost", "Recorded revenue", "Margin"], [[money(econ.recorded_total_cost), money(econ.revenue), money(econ.margin)]]));
      if (!econ.coverage_complete) e.append(el("p", "Add external costs and confirm cost coverage to calculate a margin."));
      card.append(details(w.workflow_id + "/economics", "Task economics", e, opened));
      root.append(card);
    }
  } catch (e) { $("#message").textContent = e.message; }
  finally { refreshing = false; }
}
$("#connect").addEventListener("submit", e => { e.preventDefault(); credential = $("#token").value; $("#token").value = ""; clearInterval(timer); refresh(); timer = setInterval(refresh, 3000); });
$("#filter").addEventListener("change", () => { selection = $("#filter").value; refresh(); });
$("#approval").addEventListener("submit", async e => {
  e.preventDefault(); const f = new FormData(e.target), output = $("#approval-result");
  try {
    const result = await api("approvals", {approval_id: crypto.randomUUID(), task_id: f.get("task"), tool_name: f.get("tool"), arguments: JSON.parse(f.get("arguments")), ttl_seconds: 300});
    output.textContent = "Approved for 5 minutes, one use: " + result.approval_id;
  } catch (err) { output.textContent = err.message; }
});
$("#regression-file").addEventListener("change", async e => {
  const root = $("#regression-result"); root.replaceChildren();
  try {
    const report = JSON.parse(await e.target.files[0].text());
    if (!Array.isArray(report.cases)) throw Error("Select a Reins regression JSON report.");
    root.append(el("p", report.passing + " / " + report.total + " cases passed"), table(["Case", "Result", "Details"], report.cases.map(c => [c.case_id, c.passed ? "Passed" : "Needs attention", c.failures.join("; ")])));
  } catch (err) { root.append(el("p", err.message)); }
});
