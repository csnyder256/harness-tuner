"""Portable report controls. All data and code stay inside the HTML file."""
from __future__ import annotations

import html
import json
from typing import Any

STYLE = """
.explorer {margin:2rem 0;border:1px solid var(--line,#2b3547);border-radius:12px;padding:1.25rem}
.tools {display:flex;gap:.6rem;flex-wrap:wrap;align-items:center;margin:1rem 0}
button,input,select {font:inherit;padding:.45rem .65rem;border-radius:5px;border:1px solid var(--line,#aaa)}
button {cursor:pointer} button:focus-visible,input:focus-visible,select:focus-visible {outline:3px solid #7ea5ff}
.explorer th button {border:0;background:transparent;color:inherit;text-align:left}
.explorer details {margin-top:1rem}.explorer .foot {font-size:.85rem;color:var(--muted,#aaa)}
@media print {.tools,th button {display:none} .explorer {break-inside:avoid}}
"""

SCRIPT = r"""
(() => {
const data=JSON.parse(document.getElementById("report-data").textContent);
const tbody=document.getElementById("explorer-rows");
let sort="metric",direction=1;
const num=x=>x==null?"unavailable":typeof x==="number"?Number(x.toPrecision(7)).toLocaleString():String(x);
function render(){
 const query=document.getElementById("metric-search").value.toLowerCase(),mode=document.getElementById("coverage-filter").value;
 const rows=data.rows.filter(r=>r.metric.toLowerCase().includes(query)&&(mode==="all" || (mode==="measured"?r.value!=null||r.mean_baseline!=null:r.value==null&&r.mean_baseline==null)));
 rows.sort((a,b)=>{const x=a[sort],y=b[sort];if(x==null&&y==null)return a.metric.localeCompare(b.metric);if(x==null)return 1;if(y==null)return -1;return direction*(typeof x==="number"&&typeof y==="number"?x-y:String(x).localeCompare(String(y)));});
 tbody.replaceChildren();
 for(const row of rows){
  const tr=document.createElement("tr");
  for(const field of data.columns){const td=document.createElement("td");td.textContent=num(row[field]);tr.append(td);}
  tbody.append(tr);
 }
 document.getElementById("metric-count").textContent=rows.length+" of "+data.rows.length+" metrics shown";
}
document.getElementById("metric-search").addEventListener("input",render);
document.getElementById("coverage-filter").addEventListener("change",render);
document.querySelectorAll("[data-report-sort]").forEach(button=>button.addEventListener("click",()=>{const next=button.dataset.reportSort;direction=next===sort?-direction:1;sort=next;render();}));
function download(name,body,type){const url=URL.createObjectURL(new Blob([body],{type})),a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
document.getElementById("export-json").addEventListener("click",()=>download("harness-report.json",JSON.stringify(data,null,2)+"\n","application/json"));
document.getElementById("export-csv").addEventListener("click",()=>{
 const quote=x=>{let s=x==null?"":String(x);if(/^[=+\-@\t\r]/.test(s)&&typeof x!=="number")s="'"+s;return '"'+s.replace(/"/g,'""')+'"';};
 download("harness-report.csv",[data.columns,...data.rows.map(r=>data.columns.map(k=>r[k]))].map(row=>row.map(quote).join(",")).join("\r\n")+"\r\n","text/csv");
});
document.getElementById("print-report").addEventListener("click",()=>window.print());
render();

const select=document.getElementById("task-metric"),plot=document.getElementById("task-chart");
for(const row of data.rows){const o=document.createElement("option");o.value=row.metric;o.textContent=row.metric+(row.unit?" ("+row.unit+")":"");select.append(o);}
if([...select.options].some(o=>o.value==="cost_usd"))select.value="cost_usd";
function drawTasks(){
 const metric=select.value,unit=data.rows.find(r=>r.metric===metric)?.unit||"",isPair=Array.isArray(data.pairs);
 const tasks=isPair?data.pairs.filter(p=>p.metric===metric):(data.tasks||[]).map(t=>{const m=t.metrics[metric];return {task_id:t.task_id,baseline:m?.status==="measured"&&(m.unit===unit||(metric==="task_success"&&m.unit==="bool"&&unit==="ratio"))?m.value:null};});
 plot.replaceChildren();const values=tasks.flatMap(t=>[t.baseline,t.variant]).map(v=>typeof v==="boolean"?Number(v):v).filter(v=>typeof v==="number"&&Number.isFinite(v));
 if(!values.length){plot.textContent="No comparable measurements for this metric.";return;}
 const lo=Math.min(0,...values),hi=Math.max(0,...values),span=hi-lo||1,ns="http://www.w3.org/2000/svg";
 const svg=document.createElementNS(ns,"svg");svg.setAttribute("viewBox","0 0 980 "+(tasks.length*47+75));svg.setAttribute("role","img");svg.setAttribute("aria-label",metric+" by task; "+(isPair?"blue before, green after":"blue measured value"));svg.style.width="100%";svg.style.minWidth="620px";
 const x=v=>230+(v-lo)/span*570;
 function el(tag,attrs,text){const n=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text!=null)n.textContent=text;svg.append(n);return n;}
 for(let i=0;i<5;i++){const v=lo+span*i/4;el("line",{x1:x(v),x2:x(v),y1:27,y2:tasks.length*47+40,stroke:"#39445b"});el("text",{x:x(v),y:20,fill:"#aebbd0","font-size":12,"text-anchor":"middle"},num(v));}
 tasks.forEach((row,i)=>{
  const y=i*47+48;el("text",{x:5,y:y+5,fill:"#d7e0ef","font-size":12},row.task_id.length>26?row.task_id.slice(0,24)+"…":row.task_id);
  for(const [key,color,offset]of [["baseline","#7baaff",-4],["variant","#74d8b7",10]]){
   if(!isPair&&key==="variant")continue;const v=typeof row[key]==="boolean"?Number(row[key]):row[key];
   if(v==null){el("text",{x:810,y:y+offset+4,fill:"#aebbd0","font-size":12},key+": unavailable");continue;}
   const bar=el("rect",{x:Math.min(x(0),x(v)),y:y+offset-6,width:Math.max(1,Math.abs(x(v)-x(0))),height:10,fill:color,rx:2});
   const title=document.createElementNS(ns,"title");title.textContent=row.task_id+" · "+key+": "+v+" "+unit;bar.append(title);
   el("text",{x:810,y:y+offset+4,fill:color,"font-size":12},key+": "+num(v)+" "+unit);
  }
 });
 plot.append(svg);
}
select.addEventListener("change",drawTasks);drawTasks();

})();
"""


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True).replace("<", "\\u003c")


def explorer(data: dict[str, Any]) -> str:
    """Static fallback table remains readable when scripting is disabled."""
    e = html.escape
    header = "".join(f'<th><button type="button" data-report-sort="{e(key)}">{e(key.replace("_", " "))}</button></th>' for key in data["columns"])
    rows = "".join("<tr>" + "".join(f'<td>{e(str(row.get(key))) if row.get(key) is not None else "unavailable"}</td>' for key in data["columns"]) + "</tr>" for row in data["rows"])
    return (
        f'<style>{STYLE}</style><section class="explorer" aria-label="Interactive metric explorer">'
        '<h2>Explore the measurements</h2><div class="tools">'
        '<label>Search metrics <input id="metric-search" type="search" placeholder="cost, rework, success"></label>'
        '<label>Coverage <select id="coverage-filter"><option value="all">All metrics</option>'
        '<option value="measured">Measured</option><option value="unavailable">Unavailable</option></select></label>'
        '<button type="button" id="export-json">Export JSON</button><button type="button" id="export-csv">Export CSV</button>'
        '<button type="button" id="print-report">Print report</button></div>'
        '<p id="metric-count" role="status" aria-live="polite"></p>'
        f'<div class="wrap"><table><thead><tr>{header}</tr></thead><tbody id="explorer-rows">{rows}</tbody></table></div>'
        '<h3>Compare task measurements</h3><label>Chart metric <select id="task-metric"></select></label><p class="foot">Blue: before or run value · green: after. Each row is a task, not a time series. Unknown values remain unavailable.</p><div id="task-chart" class="wrap"></div>'
        '<p class="foot">Click column headings to sort. Unknown values stay unavailable. Exports contain measurements and provenance; raw prompts, traces and configuration are excluded.</p>'
        f'<script type="application/json" id="report-data">{safe_json(data)}</script><script>{SCRIPT}</script></section>'
    )


def run_explorer(run: dict, agg: dict, tasks: list[dict] | None = None) -> str:
    rows = []
    for metric, entry in agg.items():
        coverage = entry.get("coverage") or {}
        rows.append({
            "metric": metric, "value": entry.get("value") if entry.get("status") == "measured" else None,
            "unit": entry.get("unit", ""), "coverage": f'{coverage.get("measured", "?")}/{coverage.get("total", "?")}',
            "combiner": coverage.get("combiner", ""), "reason": entry.get("reason", ""),
        })
    # Task IDs and scalar metrics only. Never include traces, task text or config.
    selected_tasks = [{"task_id": t["task_id"], "metrics": t["metrics"]} for t in (tasks or [])]
    data = {"schema": "harness-tuner.report", "version": 1, "run_id": run["run_id"],
            "htp_version": run["htp_version"], "rows": rows, "tasks": selected_tasks,
            "columns": ["metric", "value", "unit", "coverage", "combiner", "reason"]}
    return explorer(data)
