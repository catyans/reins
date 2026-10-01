let credential="",timer,selection=location.hash.slice(1);
const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};
async function refresh(){
 try{
 const response=await fetch("/v1/status",{method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+credential},body:"{}"});
 if(!response.ok)throw Error("Unable to connect. Check the local credential and service.");
 const data=await response.json(),root=document.querySelector("#workflows");const opened=new Set([...root.querySelectorAll("details[open]")].map(d=>d.dataset.key));root.replaceChildren();
 document.querySelector("#message").textContent="Connected · "+data.workflows.length+" workflows · "+data.unmatched_bill_lines+" unmatched invoice revisions";
 const filter=document.querySelector("#filter"),selected=selection;
 filter.replaceChildren();const all=el("option","All workflows");all.value="";filter.append(all);
 for(const w of data.workflows){const option=el("option",w.workflow_id);option.value=w.workflow_id;filter.append(option)}
 filter.value=selected;
 for(const w of data.workflows.filter(w=>!filter.value||w.workflow_id===filter.value)){
 const card=el("article");card.append(el("h2",w.task_type+" · "+w.state),el("p",w.customer_id+" / "+w.workflow_id+" · "+w.mode));
 const metrics=el("div");metrics.className="metrics";
 for(const [label,value] of [["Usage-priced cost",w.known_cost],["Reserved / unresolved",w.reserved_cost],["Shared budget",w.budget],["Invoice reconciled",w.bill_reconciled_cost]]){
 const m=el("div");m.className="metric";m.append(el("strong","$"+Number(value).toFixed(Number(value)>0&&Number(value)<0.01?7:4)),el("span",label));metrics.append(m)}
 card.append(metrics);const bar=el("progress");bar.max=Number(w.budget)||1;bar.value=Number(w.known_cost)+Number(w.reserved_cost);bar.setAttribute("aria-label","Committed budget");card.append(bar);
 card.append(el("p",w.forecast.remaining_p90===null?"Forecast becomes available after 30 comparable completed workflows.":"Remaining cost: P50 $"+w.forecast.remaining_p50+" / P90 $"+w.forecast.remaining_p90+" · advisory"));
 const detail=el("details"),table=el("table"),head=el("tr");detail.dataset.key=w.workflow_id+"/operations";detail.open=opened.has(detail.dataset.key);detail.append(el("summary",w.tasks.length+" tasks · "+w.requests.length+" paid operations"));for(const h of ["Task","Model / tool","Status","Cost"])head.append(el("th",h));table.append(head);
 for(const r of w.requests){const tr=el("tr");for(const v of [r.task_id,r.model,r.state,r.actual_cost===null?"Reserved $"+r.reserved_cost:"$"+r.actual_cost])tr.append(el("td",v));table.append(tr)}
 const scroll=el("div");scroll.className="scroll";scroll.append(table);detail.append(scroll);card.append(detail);
 const ownership=el("details");ownership.dataset.key=w.workflow_id+"/ownership";ownership.open=opened.has(ownership.dataset.key);ownership.append(el("summary","Task ownership"));
 const tree=el("table");const th=el("tr");for(const h of ["Task","Parent","Task cap"])th.append(el("th",h));tree.append(th);
 for(const t of w.tasks){const tr=el("tr");for(const v of [t.task_id,t.parent_task_id||"Workflow root","$"+t.budget])tr.append(el("td",v));tree.append(tr)}
 const treeScroll=el("div");treeScroll.className="scroll";treeScroll.append(tree);ownership.append(treeScroll);card.append(ownership);root.append(card)}
 }catch(e){document.querySelector("#message").textContent=e.message}
}
document.querySelector("#connect").addEventListener("submit",e=>{e.preventDefault();credential=document.querySelector("#token").value;document.querySelector("#token").value="";clearInterval(timer);refresh();timer=setInterval(refresh,3000)});

document.querySelector("#filter").addEventListener("change",()=>{selection=document.querySelector("#filter").value;refresh()});
