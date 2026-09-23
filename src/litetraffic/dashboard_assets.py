"""Inline CSS and JavaScript for the dashboard; no external requests, no data interpolated into scripts."""

STYLE = """
:root{--bg:#f7f8fa;--fg:#17211b;--card:#fff;--line:#e5e7eb;--link:#0e6b3c;--muted:#56615b;
--pass:#0e6b3c;--fail:#b42318;--warn:#8a5a00;--info:#3730a3;color-scheme:light}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#111513;--fg:#e6ece8;--card:#1a201d;
--line:#2f3833;--link:#7ddcaa;--muted:#a3afa8;--pass:#7ddcaa;--fail:#ff9a8f;--warn:#f0c96b;--info:#b3bcff;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#111513;--fg:#e6ece8;--card:#1a201d;--line:#2f3833;--link:#7ddcaa;--muted:#a3afa8;
--pass:#7ddcaa;--fail:#ff9a8f;--warn:#f0c96b;--info:#b3bcff;color-scheme:dark}
*{box-sizing:border-box}
body{font:15px/1.45 system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:16px;color:var(--fg);background:var(--bg)}
nav{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between}
a{color:var(--link)}:focus-visible{outline:3px solid var(--link);outline-offset:2px}
h1{overflow-wrap:anywhere}.muted{color:var(--muted)}
.scroll{overflow-x:auto;max-width:100%}
table{width:100%;border-collapse:collapse;background:var(--card)}th,td{padding:8px;text-align:left;border-bottom:1px solid var(--line);overflow-wrap:break-word}
th,.badge{white-space:nowrap}
pre{background:var(--card);padding:12px;overflow-x:auto}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:16px 0;overflow-x:auto}
button,input,select{font:inherit;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:4px 8px}
button{cursor:pointer}button:disabled{opacity:.5;cursor:not-allowed}
.controls{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:end;margin:12px 0}.controls label{display:flex;flex-direction:column;font-size:.85rem}
.badge{display:inline-block;padding:1px 8px;border:1px solid currentColor;border-radius:999px;font-size:.8rem;font-weight:650;color:var(--info)}
.verdict{font-size:1.6rem}.v-pass{color:var(--pass)}.v-fail,.v-error{color:var(--fail)}.v-inconclusive,.v-unreadable{color:var(--warn)}
svg.chart{width:100%;height:auto;max-width:720px}svg.chart text{fill:var(--muted);font-size:14px}
/* The 640-wide viewBox shrinks with the screen; keep chart text >= 11px rendered down to 375px. */
@media (max-width:560px){svg.chart text{font-size:22px}}
svg.chart polyline{fill:none;stroke:var(--link);stroke-width:2}svg.chart line{stroke:var(--line)}
svg.chart circle{fill:currentColor;stroke:var(--card);stroke-width:2}
"""

# Runs in <head> so a stored theme applies before first paint.
THEME_INIT = (
    "(function(){try{var t=localStorage.getItem('lt-theme');"
    "if(t==='light'||t==='dark')document.documentElement.dataset.theme=t}catch(e){}})();"
)

THEME_TOGGLE = """
document.getElementById('theme').addEventListener('click',function(){
  var root=document.documentElement;
  var dark=root.dataset.theme?root.dataset.theme==='dark':matchMedia('(prefers-color-scheme: dark)').matches;
  root.dataset.theme=dark?'light':'dark';
  try{localStorage.setItem('lt-theme',root.dataset.theme)}catch(e){}
});
"""

# Mirrors dashboard_pages._index_row; every value goes through textContent, never innerHTML.
INDEX = """
var form=document.getElementById('select'),compare=document.getElementById('compare');
var auto=document.getElementById('autorefresh'),rows=document.getElementById('rows'),note=document.getElementById('refresh-status');
function sync(){compare.disabled=!(form.querySelectorAll('input[name=run]:checked').length === 2)}
form.addEventListener('change',sync);sync();
function plain(v){return v==null?'':typeof v==='string'?v:JSON.stringify(v)}
function cell(tr,v){var td=document.createElement('td');td.textContent=plain(v);tr.appendChild(td);return td}
function link(td,href,text){var a=document.createElement('a');a.href=href;a.textContent=text;td.replaceChildren(a)}
function row(e,checked){
  var tr=document.createElement('tr'),sel=cell(tr,'');
  if(e.kind==='run'){var box=document.createElement('input');box.type='checkbox';box.name='run';box.value=e.run_id;
    box.checked=checked.has(e.run_id);box.setAttribute('aria-label','Select '+e.run_id);sel.appendChild(box)}
  var id=cell(tr,e.run_id);if(e.kind!=='series')link(id,'/runs/'+encodeURIComponent(e.run_id),e.run_id);
  cell(tr,e.kind);
  var sc=cell(tr,e.scenario);if(typeof e.scenario==='string')link(sc,'/scenarios/'+encodeURIComponent(e.scenario),e.scenario);
  cell(tr,e.lifecycle);cell(tr,e.seed);cell(tr,e.finished_at);
  var badge=document.createElement('span');
  if(e.kind==='activity'){badge.className='badge';badge.textContent='background'}
  else{badge.className='badge v-'+plain(e.verdict);badge.textContent=plain(e.verdict).toUpperCase()}
  cell(tr,'').appendChild(badge);return tr}
function refresh(){
  fetch('/api/runs'+location.search).then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json()})
  .then(function(entries){
    var checked=new Set(Array.from(form.querySelectorAll('input[name=run]:checked'),function(i){return i.value}));
    rows.replaceChildren.apply(rows,entries.map(function(e){return row(e,checked)}));sync();note.textContent=''})
  .catch(function(err){note.textContent='Refresh failed: '+err.message});
  fetch('/api/scenarios').then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json()})
  .then(options).catch(function(err){note.textContent='Refresh failed: '+err.message})}
var pick=document.getElementById('scenario-filter');
function options(names){
  var chosen=pick.value;if(chosen&&names.indexOf(chosen)<0)names.push(chosen);
  var any=document.createElement('option');any.value='';any.textContent='Any';
  pick.replaceChildren.apply(pick,[any].concat(names.map(function(n){
    var o=document.createElement('option');o.value=n;o.textContent=n;return o})));pick.value=chosen}
var timer=null;
function schedule(){clearInterval(timer);timer=auto.checked?setInterval(refresh,5000):null}
auto.addEventListener('change',schedule);schedule();
"""
