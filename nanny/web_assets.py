"""The embedded operator web console (HTML + CSS + vanilla JS).

Pure asset string — no logic — peeled out of nanny.core to keep it legible.
"""

WEB_CONSOLE_HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><title>nanny console</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--bg:#0e1116;--panel:#161b22;--line:#2a313c;--dim:#7d8590;--cyan:#39c5cf;--red:#f85149;--green:#3fb950;--amber:#d29922}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:#e6edf3;font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
  header{display:flex;align-items:center;gap:14px;padding:10px 18px;border-bottom:1px solid var(--line);background:var(--panel);flex-wrap:wrap}
  .brand{color:var(--cyan);font-weight:700;letter-spacing:.5px}
  .spacer{flex:1} .pill{color:var(--dim)}
  nav.tabs{display:flex;gap:4px}
  .tab{background:transparent;color:var(--dim);border:0;border-bottom:2px solid transparent;padding:6px 12px;cursor:pointer;font:inherit}
  .tab:hover{color:#e6edf3} .tab.active{color:var(--cyan);border-bottom-color:var(--cyan)}
  main{padding:18px;max-width:1100px;margin:0 auto}
  h2{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:20px 0 8px}
  table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}
  th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line)}
  th{color:var(--dim);font-weight:600;font-size:12px} tr:last-child td{border-bottom:0}
  .sev{font-weight:700;color:var(--amber)} .sev.crit{color:var(--red)}
  .dim{color:var(--dim)} .ok{color:var(--green)} .down{color:var(--red);font-weight:700} .up{color:var(--green)}
  .actions{white-space:nowrap;text-align:right}
  button.act{background:#21262d;color:#e6edf3;border:1px solid var(--line);border-radius:6px;padding:5px 10px;cursor:pointer;font:inherit;margin-left:6px}
  button.act:hover{border-color:var(--cyan)}
  .cards{display:flex;gap:12px;flex-wrap:wrap}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 18px;min-width:120px}
  .card .n{font-size:26px;font-weight:700} .card .l{color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:1px}
  .op{display:inline-block;margin:0 12px 6px 0;color:var(--green)}
  .empty{padding:14px;color:var(--dim);background:var(--panel);border:1px solid var(--line);border-radius:8px}
  .badge{display:inline-block;min-width:42px;text-align:center;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:700;letter-spacing:.5px}
  .badge.ok{background:rgba(63,185,80,.15);color:var(--green)} .badge.warn{background:rgba(210,153,34,.18);color:var(--amber)}
  .badge.crit{background:rgba(248,81,73,.18);color:var(--red)} .badge.unk{background:rgba(125,133,144,.2);color:var(--dim)}
  .chip{display:inline-block;margin-left:6px;padding:0 6px;border-radius:8px;font-size:10px;font-weight:700;letter-spacing:.5px;border:1px solid var(--line);color:var(--dim)}
  .chip.dt{color:var(--cyan);border-color:var(--cyan)} .chip.ack{color:var(--green);border-color:var(--green)} .chip.soft{color:var(--amber);border-color:var(--amber)}
  .hostgrp{margin:10px 0;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--panel)}
  .hosthdr{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:9px 12px;background:#1b2230;border-bottom:1px solid var(--line);cursor:default}
  .hosthdr .hn{font-weight:700} .hosthdr .roll{margin-left:auto;color:var(--dim);font-size:12px}
  .hostgrp table{border:0;border-radius:0} .hostgrp .srow{cursor:pointer} .hostgrp .srow:hover{background:#1b2230}
  .out{color:#c9d1d9;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:520px}
  .det td{background:#10151c;color:var(--dim);font-size:12px;white-space:pre-wrap}
  .det b{color:#c9d1d9;font-weight:600} .det a{color:var(--cyan)}
  .note{color:var(--dim);font-size:12px;font-style:italic}
  .card .n.ok{color:var(--green)} .card .n.warn{color:var(--amber)} .card .n.crit{color:var(--red)} .card .n.unk{color:var(--dim)}
  .card .mod{color:var(--cyan);font-size:11px;margin-top:3px;text-transform:none;letter-spacing:0}
  .logwin{background:#0b0e13;border:1px solid var(--line);border-radius:8px;padding:10px;height:62vh;overflow:auto;font-size:12.5px;line-height:1.65}
  .logln{white-space:pre-wrap;border-bottom:1px solid rgba(42,49,60,.4);padding:2px 2px}
  .logln .lt{color:var(--dim);margin-right:8px} .logln .lk{display:inline-block;min-width:92px;font-weight:700;margin-right:8px}
  .lk.crit{color:var(--red)} .lk.warn{color:var(--amber)} .lk.ok{color:var(--green)} .lk.info{color:var(--cyan)} .lk.ack2{color:#7aa2ff} .lk.test{color:#c08cff} .lk.dim2{color:var(--dim)}
  .chart{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;min-width:240px}
  .chart .ct{color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px}
  .donutrow{display:flex;align-items:center;gap:16px}
  .donut{width:118px;height:118px;border-radius:50%;display:grid;place-items:center;flex:none}
  .donut-hole{width:82px;height:82px;border-radius:50%;background:var(--panel);display:grid;place-items:center;text-align:center}
  .donut-hole .dn{font-size:21px;font-weight:700;line-height:1} .donut-hole .ds{font-size:9px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;margin-top:3px}
  .legend{display:flex;flex-direction:column;gap:4px} .lg{font-size:12px;color:var(--dim)} .lg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:middle}
  .hbars{display:flex;flex-direction:column;gap:6px}
  .hb{display:grid;grid-template-columns:150px 1fr 52px;align-items:center;gap:8px}
  .hb .hl{color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}
  .hb .ht{background:#0b0e13;border-radius:5px;height:14px;overflow:hidden} .hb .hf{display:block;height:100%;background:var(--cyan)}
  .hf.warn{background:var(--amber)} .hf.crit{background:var(--red)} .hf.green{background:var(--green)} .hb .hv{text-align:right;font-size:12px}
  .cols{display:flex;align-items:flex-end;gap:3px;height:130px;padding-top:8px}
  .colwrap{flex:1;display:flex;flex-direction:column;align-items:center;height:100%;justify-content:flex-end;min-width:8px}
  .col{width:100%;max-width:26px;display:flex;flex-direction:column-reverse;border-radius:3px 3px 0 0;overflow:hidden;min-height:2px;background:#0b0e13}
  .cseg.crit{background:var(--red)} .cseg.ok{background:var(--green)} .cx{font-size:9px;color:var(--dim);margin-top:3px;white-space:nowrap}
  .spark{display:block} .gaugewrap{margin-top:6px}
  .gauge{display:grid;grid-template-columns:128px 1fr 74px;align-items:center;gap:8px;margin:3px 0}
  .gauge .gl{color:var(--dim);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .gauge .gt{position:relative;background:#0b0e13;height:12px;border-radius:5px;overflow:visible}
  .gauge .gf{display:block;height:100%;border-radius:5px;background:var(--green)} .gf.warn{background:var(--amber)} .gf.crit{background:var(--red)}
  .gauge .gt .tk{position:absolute;top:-2px;width:2px;height:16px} .tk.warn{background:var(--amber)} .tk.crit{background:var(--red)} .gauge .gv{font-size:11px;text-align:right;color:#c9d1d9}
  #status{max-width:1100px;margin:8px auto 0;padding:0 18px;color:var(--dim);min-height:1.2em}
  #gate{max-width:360px;margin:13vh auto;padding:30px 28px;background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:0 10px 34px rgba(0,0,0,.4)}
  #gate .brand{font-size:26px}
  #gate .sub{color:var(--dim);font-size:13px;margin:4px 0 20px}
  #gate label{display:block;font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;margin:14px 0 5px}
  #gate input{width:100%;padding:10px 11px;background:var(--bg);border:1px solid var(--line);border-radius:8px;color:inherit;font:inherit}
  #gate input:focus{outline:none;border-color:var(--cyan)}
  #gate button{width:100%;margin-top:20px;padding:11px;background:var(--cyan);color:#04121f;border:0;border-radius:8px;cursor:pointer;font:inherit;font-weight:700;letter-spacing:.3px}
  #gate button:hover{filter:brightness(1.08)}
  #gateErr{color:var(--red);min-height:1.1em;margin-top:12px;font-size:13px}
  #gateHint{color:var(--dim);min-height:1em;margin-top:10px;font-size:12px}
  .card.clk{cursor:pointer;transition:border-color .12s,transform .12s} .card.clk:hover{border-color:var(--cyan);transform:translateY(-1px)}
  #logout{background:#21262d;color:var(--dim)} #logout:hover{border-color:var(--red);color:#e6edf3}
  .spin{display:inline-block;width:12px;height:12px;border:2px solid var(--line);border-top-color:var(--cyan);border-radius:50%;vertical-align:middle;opacity:.3;transition:opacity .2s}
  .spin.spinning{opacity:1;animation:spin .7s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  .trcard{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:8px 0;padding:10px 12px;cursor:pointer}
  .trcard.open{border-color:var(--cyan)} .trcard:hover{border-color:#3a4452}
  .trhead{display:flex;align-items:center;gap:8px} .trsum{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .trmeta{font-size:12px;margin-top:5px}
  .trdetail{margin-top:10px;padding-top:10px;border-top:1px solid var(--line);cursor:default}
  .tractions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .tractions .act{text-decoration:none;display:inline-block}
  .trsec{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:1px;margin:12px 0 5px}
  .tranalysis{white-space:pre-wrap;background:#10151c;border:1px solid var(--line);border-radius:6px;padding:9px 11px;font-size:12.5px;line-height:1.6}
  footer{color:var(--dim);text-align:center;padding:18px;font-size:12px}
</style></head>
<body>
<div id="gate">
  <div class="brand">nanny</div>
  <div class="sub">on-call console &mdash; sign in</div>
  <label for="name">Username</label>
  <input id="name" placeholder="username" autocomplete="username" autofocus>
  <div id="passwrap" style="display:none">
    <label for="pass">Password</label>
    <input id="pass" type="password" placeholder="password" autocomplete="current-password">
  </div>
  <button id="join">Sign in</button>
  <div id="gateErr"></div>
  <div id="gateHint"></div>
</div>
<div id="app" style="display:none">
  <header>
    <span class="brand">nanny</span>
    <span id="spin" class="spin" title="background activity"></span>
    <nav class="tabs" id="tabs">
      <button class="tab active" data-view="dashboard">dashboard</button>
      <button class="tab" data-view="triage">triage</button>
      <button class="tab" data-view="incidents">incidents</button>
      <button class="tab" data-view="fleet">fleet</button>
      <button class="tab" data-view="operators">operators</button>
      <button class="tab" data-view="reports">reports</button>
      <button class="tab" data-view="log">log</button>
      <button class="tab" data-view="controls">controls</button>
    </nav>
    <span class="spacer"></span>
    <span class="pill"><b id="online">0</b> online</span>
    <span class="pill">operator: <b id="who"></b></span>
    <button id="logout" class="act" style="padding:3px 10px" title="sign out">Sign out</button>
  </header>
  <main id="main"></main>
  <div id="status"></div>
  <footer id="foot">keep behind your VPN. shared state, idempotent claim/ack.</footer>
</div>
<script>
let OP=null, current='dashboard', S={incidents:[],operators:[]};
let FLEET=[], fleetUI={q:'',status:'all',page:0,size:25,open:{}};
let LOG=[], logSeen=0;
let _lastHTML={}, _ticking=false;
let AUTH=false, _wired=false, _timer=null, JIRA_BASE='';
let _lastActivity=Date.now();
const IDLE_LIMIT=15*60*1000;          // auto-logout after 15 min idle (enforced when LDAP auth is on)
let triageUI={open:null}, triageDetail=null;
let _busy=0, _serverBusy=0;            // _busy: in-flight operator actions · _serverBusy: server-side jobs/triage
// The header spinner animates only while nanny is actually doing work (an operator
// action in flight, or the server reports running jobs / mid-triage incidents);
// it sits still otherwise.
function paintSpinner(){const el=document.getElementById('spin'); if(el)el.classList.toggle('spinning',_busy>0||_serverBusy>0);}
function setBusy(d){_busy=Math.max(0,_busy+d); paintSpinner();}
async function busy(promise){ setBusy(1); try{ return await promise; } finally{ setBusy(-1); } }
const esc=s=>(s==null?'':String(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Only let http(s) URLs become href targets — blocks javascript:/data:/vbscript:
// injection via Icinga-sourced notes_url (which an operator might click).
const safeUrl=u=>/^https?:\/\//i.test(String(u||''))?esc(u):'#';
const setStatus=t=>document.getElementById('status').textContent=t;
// Session token rides in the Authorization header on every call — never the URL.
function authHeaders(json){const h=OP?{'Authorization':'Bearer '+OP}:{}; if(json)h['Content-Type']='application/json'; return h;}
const getJSON=async p=>(await fetch(p,{headers:authHeaders()})).json();

async function login(name,password){
  const ge=document.getElementById('gateErr'); if(ge)ge.textContent='';
  let res;
  try{ res=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,password:password||''})}); }
  catch(e){ if(ge)ge.textContent='cannot reach nanny'; return; }
  if(res.status!==200){ let j={}; try{j=await res.json();}catch(e){} if(ge)ge.textContent=j.error||('login failed ('+res.status+')'); return; }
  const j=await res.json();
  OP=j.token; const disp=j.name||name;
  localStorage.setItem('nanny_name',name);
  const p=document.getElementById('pass'); if(p)p.value='';
  document.getElementById('gate').style.display='none';
  document.getElementById('app').style.display='block';
  document.getElementById('who').textContent=disp;
  if(AUTH){const f=document.getElementById('foot'); if(f)f.textContent='LDAP-authenticated session. shared state, idempotent claim/ack.';}
  if(!_wired){                                  // delegated handlers — attach exactly once
    document.getElementById('tabs').addEventListener('click',e=>{const t=e.target.closest('.tab'); if(t)setView(t.dataset.view);});
    document.getElementById('main').addEventListener('click',e=>{
      const b=e.target.closest('button[data-act]'); if(b){act(b.dataset.id,b.dataset.act);return;}
      const nv=e.target.closest('[data-nav]'); if(nv){navTo(nv.dataset.nav,nv.dataset.status,nv.dataset.q);return;}
      if(e.target.closest('a')) return;             // let runbook/ticket links work without toggling
      const tg=e.target.closest('[data-triage]'); if(tg)triageToggle(tg.dataset.triage);
    });
    _wired=true;
  }
  tick(); if(!_timer)_timer=setInterval(tick,2500);
}
// Drop back to the gate (explicit logout, server forgot us, or auth lapsed/idle).
// Stops the poll loop and drops the session token so nothing keeps running.
function showGate(msg){
  OP=null;
  if(_timer){ clearInterval(_timer); _timer=null; }
  document.getElementById('app').style.display='none';
  document.getElementById('gate').style.display='';
  const ge=document.getElementById('gateErr'); if(ge)ge.textContent=msg||'';
  const p=document.getElementById('pass'); if(p)p.value='';
  (AUTH?document.getElementById('pass'):document.getElementById('name')).focus();
}
// Jump to a view, optionally pre-setting the fleet status filter + search query.
function navTo(view,status,q){
  if(status!==undefined){ fleetUI.status=status||'all'; fleetUI.page=0; fleetUI.q=q||''; }
  setView(view);
}
function triageToggle(id){ triageUI.open=(triageUI.open===id?null:id); triageDetail=null; render(); }
// Write into #main only when the markup actually changed — avoids the per-tick
// flicker and scroll-jump of blindly replacing innerHTML every 2.5s. Scroll
// position is preserved across a real change too.
function setMain(html){
  const m=document.getElementById('main');
  if(_lastHTML[current]===html) return;
  _lastHTML[current]=html;
  const y=window.scrollY;
  m.innerHTML=html;
  window.scrollTo(0,y);
}
function setView(v){
  current=v;
  _lastHTML={};                       // force a fresh write for the newly-selected view
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.view===v));
  render();
}
async function tick(){
  if(!OP||_ticking) return;           // skip if a previous (slow) tick is still running
  if(AUTH && Date.now()-_lastActivity>IDLE_LIMIT){ showGate('signed out — inactive'); return; }
  _ticking=true;
  try{
    try{ const r=await fetch('/api/state',{headers:authHeaders()});
         if(r.status===401){ showGate('session expired — sign in again'); return; }
         S=await r.json();
         _serverBusy=S.busy||0; paintSpinner();
         document.getElementById('online').textContent=(S.operators||[]).length; }
    catch(e){ setStatus('reconnecting…'); }
    await render();
    setStatus('● live · updated '+new Date().toLocaleTimeString());
  } finally { _ticking=false; }
}
async function render(){
  const m=document.getElementById('main');
  try{
    if(current==='incidents') setMain(incidentsHTML(S.incidents||[]));
    else if(current==='triage') await renderTriage();
    else if(current==='operators') setMain(operatorsHTML(S.operators||[]));
    else if(current==='fleet') await renderFleet();
    else if(current==='reports'){ const [r,se]=await Promise.all([getJSON('/api/reports'),getJSON('/api/series?window=86400&bucket=3600')]); setMain(reportsHTML(r,(se&&se.series)||[])); }
    else if(current==='log') await renderLog();
    else if(current==='controls') await renderControls();
    else { const [f,r]=await Promise.all([getJSON('/api/fleet'),getJSON('/api/reports')]); setMain(dashboardHTML(S,f.fleet||[],r)); }
  }catch(e){ m.innerHTML='<div class="empty">failed to load view</div>'; _lastHTML[current]=null; }
}
function card(n,l,cls,mod,view,status){
  const nav=view?(' card clk" data-nav="'+esc(view)+'"'+(status!=null?' data-status="'+esc(status)+'"':'')+' title="open '+esc(view)+(status?' · '+esc(status):'')+'"'):' card"';
  return '<div class="'+nav+'><div class="n '+(cls||'')+'">'+n+'</div><div class="l">'+l+'</div>'+(mod?'<div class="mod">'+mod+'</div>':'')+'</div>';
}
// ── zero-dependency charts (CSS conic-gradient donuts, flex bars, inline-SVG sparkline) ──
function donutBg(segs){const tot=segs.reduce((a,s)=>a+s.v,0)||1;let acc=0,st=[];for(const s of segs){if(s.v>0){const f=acc/tot*100,t=(acc+s.v)/tot*100;st.push(s.color+' '+f.toFixed(2)+'% '+t.toFixed(2)+'%');acc+=s.v;}}if(!st.length)st.push('var(--line) 0 100%');return 'conic-gradient('+st.join(',')+')';}
function donutCard(title,segs,cn,cl){return '<div class="chart"><div class="ct">'+title+'</div><div class="donutrow"><div class="donut" style="background:'+donutBg(segs)+'"><div class="donut-hole"><div class="dn">'+cn+'</div><div class="ds">'+esc(cl||'')+'</div></div></div><div class="legend">'+segs.map(s=>'<span class="lg"><i style="background:'+s.color+'"></i>'+esc(s.label)+' '+s.v+'</span>').join('')+'</div></div></div>';}
function hbarsCard(title,items,unit){if(!items.length)return '<div class="chart"><div class="ct">'+title+'</div><div class="dim">no data</div></div>';const max=Math.max(1,...items.map(i=>i.v));return '<div class="chart" style="flex:1;min-width:320px"><div class="ct">'+title+'</div><div class="hbars">'+items.map(i=>'<div class="hb"><span class="hl">'+esc(i.label)+'</span><span class="ht"><span class="hf '+(i.cls||'')+'" style="width:'+(i.v/max*100).toFixed(1)+'%"></span></span><span class="hv">'+i.v+(unit||'')+'</span></div>').join('')+'</div></div>';}
function stackbarsCard(title,buckets){const max=Math.max(1,...buckets.map(b=>b.paged+b.self_resolved));const everyN=Math.max(1,Math.ceil(buckets.length/12));return '<div class="chart" style="flex:1;min-width:340px"><div class="ct">'+title+'</div><div class="cols">'+buckets.map((b,i)=>{const tot=b.paged+b.self_resolved,h=tot/max*100,ph=tot?b.paged/tot*100:0;return '<div class="colwrap" title="'+esc(b.label)+' — '+tot+' ('+b.paged+' paged, '+b.self_resolved+' self-resolved)"><div class="col" style="height:'+h.toFixed(1)+'%">'+(b.paged?'<div class="cseg crit" style="height:'+ph.toFixed(1)+'%"></div>':'')+(b.self_resolved?'<div class="cseg ok" style="height:'+(100-ph).toFixed(1)+'%"></div>':'')+'</div><div class="cx">'+(i%everyN===0?esc(b.short):'')+'</div></div>';}).join('')+'</div><div class="legend" style="flex-direction:row;gap:14px;margin-top:6px"><span class="lg"><i style="background:var(--red)"></i>paged</span><span class="lg"><i style="background:var(--green)"></i>self-resolved</span></div></div>';}
function spark(vals,w,h){w=w||300;h=h||44;if(!vals.some(v=>v>0))return '<div class="dim">no data</div>';const max=Math.max(...vals),rng=max||1;const pts=vals.map((v,i)=>(vals.length<2?w/2:i/(vals.length-1)*w).toFixed(1)+','+(h-(v/rng)*h).toFixed(1)).join(' ');return '<svg class="spark" width="100%" height="'+h+'" viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none"><polyline points="'+pts+'" fill="none" stroke="var(--cyan)" stroke-width="2"/></svg>';}
function sparkCard(title,vals,sub){return '<div class="chart" style="flex:1;min-width:260px"><div class="ct">'+title+'</div>'+spark(vals)+'<div class="dim" style="font-size:11px;margin-top:4px">'+esc(sub||'')+'</div></div>';}
function parsePerf(s){if(!s)return [];return s.trim().split(/\s+/).map(tok=>{const eq=tok.indexOf('=');if(eq<1)return null;const label=tok.slice(0,eq),parts=tok.slice(eq+1).split(';'),m=(parts[0]||'').match(/^(-?[0-9.]+)(.*)$/);if(!m)return null;const num=x=>{const f=parseFloat(x);return isNaN(f)?null:f;};return {label:label,val:parseFloat(m[1]),uom:(m[2]||'').trim(),warn:num(parts[1]),crit:num(parts[2]),min:num(parts[3]),max:num(parts[4])};}).filter(Boolean);}
function gaugesBlock(perf){const ps=parsePerf(perf);if(!ps.length)return '';return '<div class="gaugewrap">'+ps.map(p=>{const base=p.min!=null?p.min:0;let scale=p.max!=null?p.max:(p.crit!=null?p.crit*1.25:(p.warn!=null?p.warn*1.5:Math.max(Math.abs(p.val),1)*1.25));if(scale<=base)scale=base+1;const pct=v=>v==null?null:Math.max(0,Math.min(100,(v-base)/(scale-base)*100));const cls=(p.crit!=null&&p.val>=p.crit)?'crit':(p.warn!=null&&p.val>=p.warn)?'warn':'green';let tk='';if(p.warn!=null)tk+='<i class="tk warn" style="left:'+pct(p.warn).toFixed(1)+'%"></i>';if(p.crit!=null)tk+='<i class="tk crit" style="left:'+pct(p.crit).toFixed(1)+'%"></i>';return '<div class="gauge"><span class="gl">'+esc(p.label)+'</span><span class="gt"><span class="gf '+cls+'" style="width:'+pct(p.val).toFixed(1)+'%"></span>'+tk+'</span><span class="gv">'+esc(''+p.val+p.uom)+'</span></div>';}).join('')+'</div>';}
function dashboardHTML(s,fleet,rep){
  const checks=fleet.filter(x=>x.service!=='host');
  const cnt=v=>checks.filter(x=>x.state===v).length, dt=v=>checks.filter(x=>x.state===v&&x.downtime).length;
  const ok=cnt(0), w=cnt(1), c=cnt(2), u=cnt(3);
  // "down" the operator must act on = not OK and NOT in downtime (handled) — that's what colors the board.
  const activeDown=checks.filter(x=>!x.up&&!x.downtime).length;
  const inc=s.incidents||[], firing=inc.length, unclaimed=inc.filter(i=>!i.owner).length;
  const day=(rep.windows&&rep.windows['24h'])||{total:0,paged:0,self_resolved:0,mttr:0};
  const md=n=>n?n+' in downtime':'';
  return '<h2>fleet health</h2><div class="cards">'+
    card(ok,'OK','ok',null,'fleet','ok')+
    card(c,'critical','crit',md(dt(2)),'fleet','crit')+
    card(w,'warning','warn',md(dt(1)),'fleet','warn')+
    card(u,'unknown','unk',md(dt(3)),'fleet','unknown')+
    card(activeDown,'down (active)',activeDown?'down':'ok','excl. downtime','fleet','problems')+
    '</div><h2>incidents &amp; activity</h2><div class="cards">'+
    card(firing,'firing now',firing?'down':'up',null,'incidents')+
    card(unclaimed,'unclaimed',unclaimed?'down':'',null,'incidents')+
    card((s.operators||[]).length,'online',null,null,'operators')+
    card(day.total,'24h incidents',null,null,'reports')+
    card(day.paged,'24h paged',null,null,'reports')+
    card(day.mttr+'s','24h MTTR',null,null,'reports')+
    '</div>'+
    '<h2>at a glance</h2><div class="cards">'+
      donutCard('fleet health',[
        {label:'OK',v:ok,color:'var(--green)'},{label:'WARNING',v:w,color:'var(--amber)'},
        {label:'CRITICAL',v:c,color:'var(--red)'},{label:'UNKNOWN',v:u,color:'var(--dim)'}],
        activeDown, activeDown?'active down':'all clear')+
      donutCard('24h · paged vs self-resolved',[
        {label:'self-resolved',v:day.self_resolved,color:'var(--green)'},
        {label:'paged',v:day.paged,color:'var(--red)'}], day.total,'incidents')+
    '</div>'+
    '<h2>recent incidents (24h)</h2>'+recentHTML(rep.recent||[]);
}
function incidentsHTML(list){
  if(!list.length)return '<div class="empty">nothing firing</div>';
  return '<h2>firing incidents</h2><table><thead><tr><th>sev</th><th>summary</th><th>service</th><th>owner</th><th>ack</th><th>age</th><th>ticket</th><th></th></tr></thead><tbody>'+
    list.map(i=>'<tr><td class="sev '+(i.severity==='critical'?'crit':'')+'">'+esc(i.severity)+'</td>'+
      '<td>'+esc(i.summary)+'</td><td>'+esc(i.service)+'</td>'+
      '<td>'+(i.owner?esc(i.owner):'<span class=dim>&mdash;</span>')+'</td>'+
      '<td>'+(i.acked?'<span class=ok>&#10003;</span>':'<span class=dim>&mdash;</span>')+'</td>'+
      '<td>'+i.age+'s</td><td>'+esc(i.ticket||'')+'</td>'+
      '<td class="actions"><button class=act data-act="claim" data-id="'+esc(i.id)+'">claim</button>'+
      '<button class=act data-act="ack" data-id="'+esc(i.id)+'">ack</button>'+
      '<button class=act data-act="release" data-id="'+esc(i.id)+'">release</button></td></tr>').join('')+
    '</tbody></table>';
}
// Triage view: everything an operator needs to work an incident, with explicit
// action buttons. Lists firing incidents (paged first); expanding one fetches a
// detail bundle (live Icinga state + runbook + nanny's triage analysis & audit).
const _PHASE={paged:['crit','PAGED'],holding:['warn','HOLDING'],claimed:['ok','CLAIMED'],triaging:['unk','TRIAGING'],firing:['unk','FIRING']};
function phaseBadge(p){const x=_PHASE[p]||['unk',(''+p).toUpperCase()];return '<span class="badge '+x[0]+'">'+esc(x[1])+'</span>';}
function sevChip(sev){const c=sev==='critical'?'crit':(sev==='warning'?'warn':'unk');return '<span class="badge '+c+'">'+esc((sev||'?').slice(0,4).toUpperCase())+'</span>';}
async function renderTriage(){
  const list=S.incidents||[];
  if(triageUI.open && !list.some(i=>i.id===triageUI.open)){ triageUI.open=null; triageDetail=null; }
  if(triageUI.open){ try{ triageDetail=await getJSON('/api/incidents/'+encodeURIComponent(triageUI.open)); }catch(e){ triageDetail=null; } }
  setMain(triageHTML(list));
}
function triageHTML(list){
  if(!list.length)return '<h2>triage</h2><div class="empty">nothing firing &mdash; all clear</div>';
  const rank={paged:0,holding:1,triaging:2,claimed:3,firing:4};
  const sorted=list.slice().sort((a,b)=>((rank[a.phase]==null?9:rank[a.phase])-(rank[b.phase]==null?9:rank[b.phase]))||b.age-a.age);
  return '<h2>triage queue &mdash; '+list.length+'</h2>'+sorted.map(triageCardHTML).join('');
}
function triageCardHTML(i){
  const open=triageUI.open===i.id;
  let head='<div class="trcard'+(open?' open':'')+'" data-triage="'+esc(i.id)+'">'+
    '<div class="trhead">'+sevChip(i.severity)+phaseBadge(i.phase)+
      '<span class="trsum">'+esc(i.summary)+'</span>'+
      '<span class="dim" style="margin-left:auto">'+ageFmt(i.age)+'</span>'+
      '<span class="dim" style="width:16px;text-align:center">'+(open?'&#9662;':'&#9656;')+'</span></div>'+
    '<div class="trmeta dim">'+esc(i.service||'host')+' @ '+esc(i.instance||'?')+
      (i.owner?' · owner <b>'+esc(i.owner)+'</b>':' · <span class="down">unclaimed</span>')+
      (i.acked?' · <span class="ok">ACK</span>':'')+(i.ticket?' · '+esc(i.ticket):'')+'</div>';
  return head+(open?triageDetailHTML(i):'')+'</div>';
}
function triageDetailHTML(i){
  const d=triageDetail;
  if(!d||d.id!==i.id)return '<div class="trdetail dim">loading…</div>';
  if(d.error)return '<div class="trdetail dim">'+esc(d.error)+'</div>';
  let s='<div class="trdetail">';
  s+='<div class="tractions">'+
    '<button class="act" data-act="claim" data-id="'+esc(i.id)+'">claim</button>'+
    '<button class="act" data-act="ack" data-id="'+esc(i.id)+'">ack</button>'+
    '<button class="act" data-act="release" data-id="'+esc(i.id)+'">release</button>'+
    (d.runbook?'<a class="act" href="'+safeUrl(d.runbook)+'" target="_blank" rel="noopener noreferrer">runbook &#8599;</a>':'')+
    (d.ticket&&JIRA_BASE?'<a class="act" href="'+safeUrl(JIRA_BASE+'/browse/'+d.ticket)+'" target="_blank" rel="noopener noreferrer">'+esc(d.ticket)+' &#8599;</a>':(d.ticket?'<span class="act">'+esc(d.ticket)+'</span>':''))+
    '<button class="act" data-nav="fleet" data-status="all" data-q="'+esc(d.instance||'')+'">view host &#8599;</button>'+
    '</div>'+
    '<div class="dim" style="margin:8px 0">phase <b>'+esc(d.phase)+'</b>'+(d.hold?' · page hold '+d.hold+'s':'')+' · age '+ageFmt(d.age)+(d.paged?' · <span class="down">PAGED</span>':'')+'</div>';
  const st=d.state||{};
  if(st.host||(st.services&&st.services.length)){
    s+='<div class="trsec">live state (from Icinga, now)</div>';
    if(st.host)s+='<div style="margin:3px 0">'+hostBadge(st.host)+' <b>'+esc(st.host.host_display||d.instance)+'</b> <span class="dim out" style="max-width:none">'+esc(st.host.output||'')+'</span></div>';
    const probs=(st.services||[]).filter(e=>e.state!==0);
    s+='<div class="dim" style="margin:3px 0">'+(st.services||[]).length+' service checks · '+probs.length+' not OK'+(probs.length>1?' — possible host-wide problem':'')+'</div>';
    s+=probs.slice(0,12).map(e=>'<div style="margin:2px 0">'+sevBadge(e.state)+' '+esc(e.service)+' <span class="dim">'+esc(e.output||'')+'</span></div>').join('');
  }
  if(d.notes)s+='<div class="trsec">runbook notes</div><div class="note">'+esc(d.notes)+'</div>';
  if(d.analysis)s+='<div class="trsec">nanny triage analysis</div><div class="tranalysis">'+esc(d.analysis)+'</div>';
  if(d.audit&&d.audit.length)s+='<div class="trsec">audit — what nanny did</div>'+
    d.audit.map(a=>'<div class="logln"><span class="lk info">'+esc(a.tool)+'</span> <span class="dim">'+esc(a.args)+'</span> &rarr; '+esc(a.result)+'</div>').join('');
  return s+'</div>';
}
// Fleet view: search + status filter + pagination. The filter bar is built once
// and kept stable across the 2.5s auto-refresh (so typing/focus survive); only the
// results table + pager repaint. UI state lives in fleetUI and persists across tabs.
const FBAR='display:flex;gap:8px;align-items:center;margin:8px 0;flex-wrap:wrap';
const FIN='padding:6px;background:var(--bg);border:1px solid var(--line);border-radius:6px;color:inherit;font:inherit';
const STOPTS=[['all','all'],['problems','problems'],['ok','ok'],['crit','critical'],['warn','warning'],['unknown','unknown'],['downtime','in downtime'],['ack','acknowledged']];
const SZOPTS=[10,25,50,100];
function sevBadge(s){const m={0:['ok','OK'],1:['warn','WARN'],2:['crit','CRIT'],3:['unk','UNK']},x=m[s]||['unk','?'];return '<span class="badge '+x[0]+'">'+x[1]+'</span>';}
function hostBadge(e){if(!e)return '<span class="badge unk">?</span>';if(e.state===0)return '<span class="badge ok">UP</span>';if(e.state===2)return '<span class="badge unk">UNRCH</span>';return '<span class="badge crit">DOWN</span>';}
function ageFmt(s){if(s==null)return '';if(s<60)return s+'s';if(s<3600)return Math.floor(s/60)+'m';if(s<86400)return Math.floor(s/3600)+'h';return Math.floor(s/86400)+'d';}
function chips(e){let c='';if(e.downtime)c+='<span class="chip dt">DOWNTIME</span>';if(e.acked)c+='<span class="chip ack">ACK</span>';if(e.state!==0&&e.state_type==='soft')c+='<span class="chip soft">SOFT '+esc(e.attempt)+'</span>';return c;}
function dtLine(d){return '<div style="padding:6px 12px;border-bottom:1px solid var(--line)"><span class="chip dt">DOWNTIME</span> <span class="dim">by '+esc(d.author||'?')+(d.ends_in!=null&&d.ends_in>0?' · ends in '+ageFmt(d.ends_in):'')+(d.comment?' · '+esc(d.comment):'')+'</span></div>';}
async function renderFleet(){
  try{ FLEET=(await getJSON('/api/fleet')).fleet||[]; }catch(e){ /* keep last FLEET */ }
  if(!document.getElementById('fleetBox')){
    document.getElementById('main').innerHTML=
      '<div id="fleetBox">'+
        '<div style="'+FBAR+'">'+
          '<input id="fleetQ" placeholder="filter host, service or output…" style="'+FIN+';flex:1;min-width:220px" value="'+esc(fleetUI.q)+'">'+
          '<select id="fleetStatus" style="'+FIN+'">'+
            STOPTS.map(o=>'<option value="'+o[0]+'"'+(fleetUI.status===o[0]?' selected':'')+'>'+o[1]+'</option>').join('')+
          '</select>'+
          '<select id="fleetSize" style="'+FIN+'">'+
            SZOPTS.map(n=>'<option value="'+n+'"'+(fleetUI.size===n?' selected':'')+'>'+n+' hosts</option>').join('')+
          '</select>'+
          '<span id="fleetHead" class="dim"></span>'+
        '</div>'+
        '<div id="fleetResults"></div>'+
      '</div>';
    const box=document.getElementById('fleetBox');
    box.addEventListener('input',e=>{ if(e.target.id==='fleetQ'){ fleetUI.q=e.target.value; fleetUI.page=0; paintFleet(); }});
    box.addEventListener('change',e=>{
      if(e.target.id==='fleetStatus'){ fleetUI.status=e.target.value; fleetUI.page=0; paintFleet(); }
      else if(e.target.id==='fleetSize'){ fleetUI.size=+e.target.value; fleetUI.page=0; paintFleet(); }
    });
    box.addEventListener('click',e=>{
      const pg=e.target.closest('[data-page]'); if(pg){ fleetUI.page+=(+pg.dataset.page); paintFleet(); return; }
      const row=e.target.closest('tr.srow'); if(row){ const k=row.dataset.key; fleetUI.open[k]=!fleetUI.open[k]; paintFleet(); }
    });
  }
  paintFleet();
}
function paintFleet(){
  const res=document.getElementById('fleetResults'), head=document.getElementById('fleetHead');
  if(!res)return;
  const q=fleetUI.q.trim().toLowerCase(), st=fleetUI.status;
  const groups={};
  for(const e of FLEET){ const g=groups[e.instance]||(groups[e.instance]={host:null,svcs:[]}); if(e.service==='host')g.host=e; else g.svcs.push(e); }
  const sPass=e=>st==='all'||(st==='problems'&&e.state!==0)||(st==='ok'&&e.state===0)||(st==='crit'&&e.state===2)||(st==='warn'&&e.state===1)||(st==='unknown'&&e.state===3)||(st==='downtime'&&e.downtime)||(st==='ack'&&e.acked);
  const qHost=(addr,g)=> !!q && (((''+addr).toLowerCase().includes(q))||(g.host&&(''+(g.host.host_display||'')).toLowerCase().includes(q))||(g.host&&(''+(g.host.notes||'')).toLowerCase().includes(q)));
  const sText=(e,addr,g)=> !q||qHost(addr,g)||(''+e.service).toLowerCase().includes(q)||(''+(e.output||'')).toLowerCase().includes(q)||(''+(e.notes||'')).toLowerCase().includes(q);
  let shown=[], totChecks=0, totProblems=0, totHosts=Object.keys(groups).length;
  for(const addr of Object.keys(groups)){
    const g=groups[addr];
    totChecks+=g.svcs.length; totProblems+=g.svcs.filter(e=>e.state!==0).length;
    const svcs=g.svcs.filter(e=>sPass(e)&&sText(e,addr,g));
    const hostShown=g.host&&sPass(g.host)&&(!q||qHost(addr,g));
    if(svcs.length||hostShown) shown.push({addr,g,svcs});
  }
  const rank=it=>{const g=it.g,hs=g.host?g.host.state:0;if(hs===1||hs===2)return 0;if(g.svcs.some(e=>e.state===2))return 1;if(g.svcs.some(e=>e.state===3))return 2;if(g.svcs.some(e=>e.state===1))return 3;return 9;};
  shown.sort((a,b)=>rank(a)-rank(b)||(''+a.addr).localeCompare(''+b.addr));
  if(head)head.textContent=totHosts+' hosts · '+totChecks+' checks · '+totProblems+' not OK'+((q||st!=='all')?(' · '+shown.length+' hosts shown'):'');
  if(!FLEET.length){ res.innerHTML='<div class="empty">no fleet data &mdash; has discover run, and is Icinga reachable?</div>'; return; }
  if(!shown.length){ res.innerHTML='<div class="empty">no hosts match the filter</div>'; return; }
  const size=fleetUI.size, pages=Math.max(1,Math.ceil(shown.length/size));
  fleetUI.page=Math.min(Math.max(0,fleetUI.page),pages-1);
  const start=fleetUI.page*size, pageHosts=shown.slice(start,start+size);
  const pager='<div style="display:flex;gap:10px;align-items:center;margin-top:10px">'+
    '<button class="act" data-page="-1"'+(fleetUI.page<=0?' disabled':'')+'>&lsaquo; prev</button>'+
    '<span class="dim">hosts '+(start+1)+'–'+Math.min(start+size,shown.length)+' of '+shown.length+' · page '+(fleetUI.page+1)+'/'+pages+'</span>'+
    '<button class="act" data-page="1"'+(fleetUI.page>=pages-1?' disabled':'')+'>next &rsaquo;</button>'+
    '</div>';
  res.innerHTML=pageHosts.map(hostGroupHTML).join('')+pager;
}
function hostGroupHTML(item){
  const addr=item.addr, g=item.g, h=g.host, svcs=item.svcs;
  const c=g.svcs.filter(e=>e.state===2).length, w=g.svcs.filter(e=>e.state===1).length, u=g.svcs.filter(e=>e.state===3).length, ok=g.svcs.filter(e=>e.state===0).length;
  let parts=[]; if(c)parts.push(c+' CRIT'); if(w)parts.push(w+' WARN'); if(u)parts.push(u+' UNK'); parts.push(ok+' OK');
  let hdr='<div class="hosthdr">'+hostBadge(h)+'<span class="hn">'+esc(addr)+'</span>'+
    (h&&h.host_display&&h.host_display!==addr?'<span class="dim">'+esc(h.host_display)+'</span>':'')+
    (h?chips(h):'')+'<span class="roll">'+parts.join(' · ')+'</span></div>';
  if(h&&h.notes)hdr+='<div style="padding:6px 12px;border-bottom:1px solid var(--line)"><span class="note">'+esc(h.notes)+(h.notes_url?' <a href="'+safeUrl(h.notes_url)+'" target="_blank" rel="noopener noreferrer">&#8599;</a>':'')+'</span></div>';
  if(h&&h.downtime_info)hdr+=dtLine(h.downtime_info);
  if(!svcs.length)return '<div class="hostgrp">'+hdr+'<div style="padding:9px 12px" class="dim">'+(h&&h.state!==0?'host in a problem state':'host OK')+' &mdash; no matching services</div></div>';
  const sorted=svcs.slice().sort((a,b)=>(a.up-b.up)||(''+a.service).localeCompare(''+b.service));
  return '<div class="hostgrp">'+hdr+'<table><tbody>'+sorted.map(e=>svcRowHTML(addr,e)).join('')+'</tbody></table></div>';
}
function svcRowHTML(addr,e){
  const key=addr+'|'+e.service, open=!!fleetUI.open[key];
  const main='<tr class="srow" data-key="'+esc(key)+'">'+
    '<td style="width:64px">'+sevBadge(e.state)+'</td>'+
    '<td style="width:210px">'+esc(e.service)+chips(e)+'</td>'+
    '<td class="out">'+esc(e.output||'')+'</td>'+
    '<td class="dim" style="width:58px;text-align:right">'+ageFmt(e.age)+'</td>'+
    '<td class="dim" style="width:22px;text-align:center">'+(open?'&#9662;':'&#9656;')+'</td></tr>';
  if(!open)return main;
  let s='<b>output:</b> '+esc(e.output||'(none)')+'\n';
  if(e.perf)s+='<b>perf:</b> '+esc(e.perf)+'\n';
  s+='<b>state:</b> '+esc(e.state_type)+' ('+esc(e.attempt)+')   <b>command:</b> '+esc(e.command||'?')+'   <b>last check:</b> '+(e.last_check!=null?ageFmt(e.last_check)+' ago':'?')+'\n';
  s+='<b>acked:</b> '+(e.acked?'yes':'no')+'   <b>downtime:</b> '+(e.downtime?'yes':'no');
  if(e.downtime_info){const d=e.downtime_info;s+=' &mdash; by '+esc(d.author||'?')+(d.ends_in!=null&&d.ends_in>0?' for '+ageFmt(d.ends_in):'')+(d.comment?': '+esc(d.comment):'');}
  if(e.notes)s+='\n<b>notes:</b> '+esc(e.notes)+(e.notes_url?' <a href="'+safeUrl(e.notes_url)+'" target="_blank" rel="noopener noreferrer">&#8599;</a>':'');
  return main+'<tr class="det"><td colspan="5">'+s+gaugesBlock(e.perf)+'</td></tr>';
}
// Activity log: incremental poll (since the last id), capped, auto-scrolls when
// the operator is already at the bottom and preserves position otherwise.
function logCls(k){return ({PAGE:'crit',WARN:'warn',RECOVERED:'ok',TICKET:'info',PD:'info',ACK:'ack2',TEST:'test'})[k]||'dim2';}
function logLine(r){return '<div class="logln"><span class="lt">'+esc((r.ts||'').slice(11,19))+'</span><span class="lk '+logCls(r.kind)+'">'+esc(r.kind||'')+'</span>'+esc(r.text||'')+'</div>';}
async function renderLog(){
  try{ const inc=(await getJSON('/api/log?since='+logSeen)).log||[]; if(inc.length){ LOG=LOG.concat(inc).slice(-600); logSeen=inc[inc.length-1].id; } }catch(e){}
  if(!document.getElementById('logBox')) document.getElementById('main').innerHTML='<h2>activity log <span class="dim" style="text-transform:none;letter-spacing:0">&mdash; everything nanny does</span></h2><div id="logBox" class="logwin"></div>';
  const box=document.getElementById('logBox'); if(!box)return;
  const prevTop=box.scrollTop, atBottom=box.scrollTop+box.clientHeight>=box.scrollHeight-30;
  box.innerHTML = LOG.length? LOG.map(logLine).join('') : '<div class="dim">no activity yet</div>';
  box.scrollTop = atBottom? box.scrollHeight : prevTop;
}
function operatorsHTML(list){
  return '<h2>online operators &mdash; '+list.length+'</h2>'+(list.length?
    '<div>'+list.map(o=>'<span class="op">&#9679; '+esc(o.name)+' <span class=dim>'+o.idle+'s</span></span>').join('')+'</div>':
    '<div class="empty">none online</div>');
}
function recentHTML(list){
  if(!list.length)return '<div class="empty">none</div>';
  return '<table><thead><tr><th>ended</th><th>signature</th><th>instance</th><th>dur</th><th>outcome</th><th>ticket</th></tr></thead><tbody>'+
    list.map(r=>'<tr><td>'+esc((r.ended||'').slice(11,19))+'</td><td>'+esc(r.sig)+'</td><td>'+esc(r.instance||'')+'</td>'+
      '<td>'+r.duration+'s</td><td>'+(r.paged?'<span class=down>PAGED</span>':'<span class=ok>self-resolved</span>')+'</td>'+
      '<td>'+esc(r.ticket||'')+(r.closed?' &#10003;':'')+'</td></tr>').join('')+'</tbody></table>';
}
function reportsHTML(r,series){
  series=series||[];
  const w=r.windows||{}; const order=['8h','24h','7d'];
  let t='<h2>shift summary</h2><table><thead><tr><th>window</th><th>incidents</th><th>paged</th><th>self-resolved</th><th>MTTR</th></tr></thead><tbody>'+
    order.map(k=>{const s=w[k]||{total:0,paged:0,self_resolved:0,mttr:0};return '<tr><td>'+k+'</td><td>'+s.total+'</td><td>'+s.paged+'</td><td>'+s.self_resolved+'</td><td>'+s.mttr+'s</td></tr>';}).join('')+'</tbody></table>';
  t+='<h2>incidents over time</h2><div class="cards">'+
     (series.some(b=>b.paged+b.self_resolved>0)?
        stackbarsCard('last 24h (hourly)',series)+sparkCard('MTTR trend',series.map(b=>b.mttr),'avg seconds to resolve, per hour'):
        '<div class="empty">no incident history yet — resolve a few (or run a test page) to populate this</div>')+
     '</div>';
  const bs=(w['24h']&&w['24h'].by_service)||{};
  const items=Object.keys(bs).sort((a,b)=>bs[b]-bs[a]).slice(0,10).map(k=>({label:k,v:bs[k],cls:'crit'}));
  t+='<h2>noisiest services (24h)</h2>'+(items.length?'<div class="cards">'+hbarsCard('incidents by service',items)+'</div>':'<div class="empty">none</div>');
  t+='<h2>recent (24h)</h2>'+recentHTML(r.recent||[]);
  return t;
}
let FLEET_SOURCE=null, DRY_RUN=false;
async function renderControls(){
  const m=document.getElementById('main');
  if(FLEET_SOURCE===null){ try{ const cfg=await getJSON('/api/config'); FLEET_SOURCE=cfg.fleet_source; DRY_RUN=!!cfg.dry_run; }catch(e){ FLEET_SOURCE='cidr'; } }
  if(!document.getElementById('ctl')){
    const icinga=FLEET_SOURCE==='icinga';
    let INTG=[]; try{ INTG=(await getJSON('/api/integrations')).integrations||[]; }catch(e){}
    m.innerHTML='<h2>controls</h2>'+
      '<div id="ctl" class="card" style="max-width:680px">'+
        '<div class="l">'+(icinga?'refresh fleet from Icinga':'run discovery on demand')+'</div>'+
        '<div style="display:flex;gap:8px;margin-top:8px">'+
          (icinga?'':'<input id="cidr" placeholder="10.0.1.0/24, 10.0.2.0/24" style="flex:1;padding:8px;background:var(--bg);border:1px solid var(--line);border-radius:6px;color:inherit;font:inherit">')+
          '<button class="act" id="runDisc">'+(icinga?'refresh from Icinga':'run discovery')+'</button>'+
        '</div>'+
        '<div id="ctlmsg" class="dim" style="margin-top:8px">'+(icinga?
          'pulls hosts/services and live state from the Icinga API; the Fleet tab updates immediately.':
          'scans your network, writes Prometheus targets; the Fleet tab fills in after Prometheus reloads.')+'</div>'+
      '</div>'+
      '<h2>test mode '+(DRY_RUN?'<span class="chip ack">DRY-RUN ON</span>':'<span class="chip">off</span>')+'</h2>'+
      '<div class="card" style="max-width:680px">'+
        (DRY_RUN?'':'<div class="note">set NANNY_DRY_RUN=true on the server to enable — buttons are disabled.</div>')+
        '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">'+
          '<input id="tHost" placeholder="host" value="test-host-01" style="'+FIN+'">'+
          '<input id="tSvc" placeholder="service" value="synthetic-check" style="'+FIN+'">'+
          '<select id="tState" style="'+FIN+'">'+['CRITICAL','WARNING','UNKNOWN'].map(s=>'<option>'+s+'</option>').join('')+'</select>'+
          '<input id="tHold" type="number" min="0" value="8" title="hold seconds" style="'+FIN+';width:84px">'+
          '<button class="act" id="simPage"'+(DRY_RUN?'':' disabled')+'>simulate page</button>'+
          '<button class="act" id="simResolve"'+(DRY_RUN?'':' disabled')+'>resolve</button>'+
        '</div>'+
        '<div id="testmsg" class="dim" style="margin-top:8px">injects a synthetic Icinga page through the real incident pipeline (Jira/PagerDuty/Slack suppressed); watch it play out in the <b>log</b> tab.</div>'+
      '</div>'+
      '<h2>integration tests</h2>'+
      '<div class="card" style="max-width:680px">'+
        '<div class="l">connectivity checks &mdash; read-only, safe to run anytime (no tickets, no pages)</div>'+
        '<div id="intgRows" style="display:flex;flex-direction:column;gap:6px;margin-top:10px">'+
          INTG.map(it=>'<div style="display:flex;align-items:center;gap:10px">'+
            '<span style="width:104px"><b>'+esc(it.label)+'</b></span>'+
            '<button class="act" data-test="'+esc(it.name)+'"'+(it.configured?'':' title="not configured — will report SKIP"')+'>test</button>'+
            '<span id="intg-'+esc(it.name)+'" class="dim" style="flex:1">'+(it.configured?'configured':'not configured')+'</span>'+
          '</div>').join('')+
        '</div>'+
        '<button class="act" id="testAll" style="margin-top:10px">test all</button>'+
      '</div>'+
      '<h2>jobs</h2><div id="jobs"></div>';
    document.getElementById('runDisc').addEventListener('click',runDiscovery);
    const ci=document.getElementById('cidr');
    if(ci)ci.addEventListener('keydown',e=>{if(e.key==='Enter')runDiscovery();});
    if(DRY_RUN){
      document.getElementById('simPage').addEventListener('click',()=>simTest('page'));
      document.getElementById('simResolve').addEventListener('click',()=>simTest('resolve'));
    }
    const ib=document.getElementById('intgRows');
    if(ib)ib.addEventListener('click',e=>{const b=e.target.closest('button[data-test]'); if(b)testIntegration(b.dataset.test);});
    const ta=document.getElementById('testAll');
    if(ta)ta.addEventListener('click',()=>INTG.forEach(it=>testIntegration(it.name)));
  }
  document.getElementById('jobs').innerHTML=jobsHTML(await getJSON('/api/jobs'));
}
async function runDiscovery(){
  const icinga=FLEET_SOURCE==='icinga', ci=document.getElementById('cidr');
  const v=ci?ci.value.trim():'', msg=document.getElementById('ctlmsg');
  if(!icinga && !v){msg.textContent='enter at least one CIDR';return;}
  document.getElementById('runDisc').disabled=true; msg.textContent=icinga?'refreshing from Icinga…':'starting discovery…';
  try{
    const r=await busy(fetch('/api/actions/discover',{method:'POST',headers:authHeaders(true),body:JSON.stringify({cidrs:v})}));
    const jj=await r.json();
    msg.textContent = r.status===200 ? ((icinga?'refresh':'discovery')+' started (job '+jj.job_id+') — watch the jobs list below') : ('error: '+(jj.error||('HTTP '+r.status)));
  }catch(e){ msg.textContent='request failed'; }
  document.getElementById('runDisc').disabled=false;
  renderControls();
}
function val(id){const el=document.getElementById(id);return el?el.value:'';}
async function simTest(kind){
  const body={host:val('tHost'),service:val('tSvc'),state:val('tState'),hold:+val('tHold')};
  const msg=document.getElementById('testmsg');
  try{
    const r=await busy(fetch('/api/test/'+(kind==='resolve'?'resolve':'page'),{method:'POST',headers:authHeaders(true),body:JSON.stringify(body)}));
    const j=await r.json();
    msg.textContent = r.status===200
      ? (kind==='resolve' ? 'recovery injected — see the log tab' : 'page injected ('+(j.fingerprint||'')+') — watch the log tab for triage → hold → page')
      : ('error: '+(j.error||('HTTP '+r.status)));
  }catch(e){ msg.textContent='request failed'; }
}
// Run one integration connectivity probe and paint PASS/FAIL/SKIP into its row.
async function testIntegration(name){
  const cell=document.getElementById('intg-'+name);
  const btn=document.querySelector('button[data-test="'+name+'"]');
  if(cell){ cell.className='dim'; cell.textContent='testing…'; }
  if(btn)btn.disabled=true;
  try{
    const r=await busy(fetch('/api/test/integration',{method:'POST',headers:authHeaders(true),body:JSON.stringify({name})}));
    const j=await r.json();
    if(cell){
      if(r.status!==200){ cell.className='down'; cell.textContent=j.error||('HTTP '+r.status); }
      else{ const cls=j.status==='PASS'?'ok':(j.status==='FAIL'?'down':'dim');
        cell.className=cls; cell.innerHTML='<b>'+esc(j.status)+'</b> &mdash; '+esc(j.detail||''); }
    }
  }catch(e){ if(cell){ cell.className='down'; cell.textContent='request failed'; } }
  if(btn)btn.disabled=false;
}
function jobsHTML(data){
  const list=(data&&data.jobs)||[];
  if(!list.length)return '<div class="empty">no jobs yet</div>';
  return '<table><thead><tr><th>kind</th><th>status</th><th>started</th><th>finished</th><th>result</th></tr></thead><tbody>'+
    list.map(j=>'<tr><td>'+esc(j.kind)+'</td>'+
      '<td class="'+(j.status==='failed'?'down':(j.status==='running'?'':'ok'))+'">'+esc(j.status)+'</td>'+
      '<td>'+esc(j.started)+'</td><td>'+esc(j.finished||'\u2014')+'</td>'+
      '<td>'+esc(j.error||j.summary||'')+'</td></tr>').join('')+'</tbody></table>';
}
async function act(id,action){
  const body={}; if(action==='ack')body.idem=crypto.randomUUID();
  const r=await busy(fetch('/api/incidents/'+encodeURIComponent(id)+'/'+action,{method:'POST',headers:authHeaders(true),body:JSON.stringify(body)}));
  const jj=await r.json();
  setStatus(r.status===200?(action+' ok'+(jj.owner?(' (owner '+jj.owner+')'):'')):((jj.error||('HTTP '+r.status))+(jj.owner?(' \u2014 owned by '+jj.owner):'')));
  tick();
}
function doJoin(){login(document.getElementById('name').value.trim()||'operator',document.getElementById('pass').value);}
document.getElementById('join').addEventListener('click',doJoin);
document.getElementById('pass').addEventListener('keydown',e=>{if(e.key==='Enter')doJoin();});
document.getElementById('name').addEventListener('keydown',e=>{if(e.key!=='Enter')return; if(AUTH){document.getElementById('pass').focus();} else {doJoin();}});
async function logout(){ try{ await fetch('/api/logout',{method:'POST',headers:authHeaders()}); }catch(e){} showGate('signed out'); }
document.getElementById('logout').addEventListener('click',logout);
['click','keydown','mousemove','scroll'].forEach(ev=>document.addEventListener(ev,()=>{_lastActivity=Date.now();},{passive:true}));
async function boot(){
  let mode='open';
  try{ const cfg=await getJSON('/api/config'); AUTH=!!cfg.auth_required; mode=cfg.auth_mode||'open'; JIRA_BASE=cfg.jira_base||''; }catch(e){}
  // Tell the operator exactly what to enter — that's what made "join" confusing.
  document.getElementById('gateHint').textContent={
    ldap:'Sign in with your LDAP / directory credentials.',
    password:'Enter your name and the shared console password.',
    open:'Name-only access — no password needed.'}[mode]||'';
  if(AUTH) document.getElementById('passwrap').style.display='';
  const saved=localStorage.getItem('nanny_name');
  if(saved){ document.getElementById('name').value=saved;
    if(AUTH){ document.getElementById('pass').focus(); } else { login(saved); } }
}
boot();
</script></body></html>'''
