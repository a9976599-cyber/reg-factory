/* ===== O · 邮箱注册台（outlook-auto-register 全功能原生移植） ===== */
async function oarApi(path, opts={}){
  const r = await fetch('/oar-api'+path, {
    headers: {'Content-Type':'application/json', ...(opts.headers||{})},
    ...opts,
  });
  const text = await r.text();
  let data = null;
  try{ data = text ? JSON.parse(text) : null; }catch(_){ }
  if(!r.ok){
    const detail = (data && (data.detail || data.error)) || text.slice(0,200) || r.status;
    throw new Error(typeof detail==='string' ? detail : JSON.stringify(detail));
  }
  return data;
}
function oarJpost(path, body){ return oarApi(path, {method:'POST', body:JSON.stringify(body||{})}); }

/* ---------- 小工具 ---------- */
const OAR = {
  tab:'overview', CFG:null, ACCOUNTS:[], SELECTED:new Set(), PAGE:1, PAGE_SIZE:50,
  FILTER:{view:'all', batch:'all', q:''},
  PROXY_ROWS:[], PROXY_SEL:new Set(), PROXY_STATS_TS:null, PROXY_CHART_HIDDEN:new Set(), PROXY_CHART_LAYOUT:null,
  jobs:[], es:null, jobAccts:{}, seenLogKeys:new Set(), DETAIL_EMAIL:'', NOTE_EMAIL:'',
};
const CHART_PALETTE = ['#4f8cff','#a855f7','#22c55e','#f59e0b','#ef4444','#06b6d4','#ec4899','#84cc16','#f97316','#6366f1'];
const OAR_STMAP = {'等待中':'wait','进行中':'run','成功':'ok','成功(干跑)':'ok','失败':'err'};
function O$(id){ return document.getElementById(id); }
function escHtml(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function trunc(s,n=16){ if(!s) return ''; return s.length>n ? s.slice(0,n)+'…' : s; }
function oarToast(m){
  let t=O$('oar-toast');
  if(!t){
    t=document.createElement('div'); t.id='oar-toast';
    t.style.cssText='position:fixed;top:64px;right:24px;z-index:9999;background:#1b2030;border:1px solid rgba(79,140,255,.4);color:#f4f6fb;padding:10px 18px;border-radius:12px;font-size:13px;box-shadow:0 8px 30px rgba(0,0,0,.5);opacity:0;transition:.25s;pointer-events:none';
    document.body.appendChild(t);
  }
  t.textContent=m; t.style.opacity='1'; t.style.transform='translateY(0)';
  clearTimeout(t._tid); t._tid=setTimeout(()=>{t.style.opacity='0';},2600);
}
function copyText(t){ navigator.clipboard.writeText(t).then(()=>oarToast('已复制')); }
function _parseIso(raw){ if(!raw) return null; const d=new Date(raw); return Number.isNaN(d.getTime())?null:d; }
function fmtFullTime(raw){
  const d=_parseIso(raw);
  if(!d) return raw?String(raw).replace('T',' ').replace(/\.\d+/,'').slice(0,19):'—';
  const p=n=>String(n).padStart(2,'0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function fmtShortTime(raw){
  const d=_parseIso(raw);
  if(!d) return raw?String(raw).replace('T',' ').slice(5,16):'—';
  const p=n=>String(n).padStart(2,'0');
  return `${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function fmtAge(sec){
  if(sec==null||sec==='') return '—';
  const n=Number(sec); if(!Number.isFinite(n)||n<0) return '—';
  if(n<60) return '刚刚';
  if(n<3600) return Math.floor(n/60)+' 分钟';
  if(n<86400) return Math.floor(n/3600)+' 小时';
  if(n<86400*30) return Math.floor(n/86400)+' 天';
  if(n<86400*365) return Math.floor(n/(86400*30))+' 个月';
  return Math.floor(n/(86400*365))+' 年';
}
function fmtSurvivalAge(a){ if(a.alive_seconds==null||a.alive_seconds==='') return '未测活'; return fmtAge(a.alive_seconds); }
function survivalTip(a){
  if(!a.survival_end_at) return '尚未测活：存活时长 = 注册 → 首次测活确认（活/死）';
  const ok=a.verify&&a.verify.ok;
  return `${fmtFullTime(a.created_at)} → ${fmtFullTime(a.survival_end_at)}（测活确认${ok?'存活':'失效'}）`;
}
function rescueTip(a){
  const n=Number(a.rescue_count)||0;
  if(!n) return '未执行过重登 (rescue_login)';
  const ok=a.last_rescue_ok===true?'成功':(a.last_rescue_ok===false?'失败':'未知');
  const reason=a.last_rescue_reason?` · ${a.last_rescue_reason}`:'';
  return `重登 ${n} 次 · 最近 ${fmtFullTime(a.last_rescue_at)} ${ok}${reason}`;
}
function fmtRescueCount(a){ const n=Number(a.rescue_count)||0; return n?`${n}次`:'—'; }
function isGraphReadable(a){ const v=a&&a.verify; return !!(v&&(v.ok||v.graph)); }
function accountBatchLabel(a){ return (a&&(a.batch_label||(a.batch_no?('B'+a.batch_no):'')))||''; }
function accountByEmail(email){ return OAR.ACCOUNTS.find(x=>x.email===email); }
function combo6(a){
  if(a.combo_recovery) return a.combo_recovery;
  if(a.recovery_email&&a.combo){
    const p=a.combo.split('----');
    if(p.length>=4) return p.slice(0,4).concat([a.recovery_email,a.recovery_password||'']).join('----');
  }
  return '';
}
const COPY_ICON='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>';
const ICO_INFO='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>';
const ICO_REFRESH='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>';
function cellWithCopy(email, field, display){
  if(!display) return '<span class="muted">—</span>';
  return `<div style="display:flex;align-items:center;gap:4px"><span class="mono" title="${escHtml(display)}">${escHtml(trunc(display,22))}</span>
    <button type="button" class="pw-eye" data-oact="copy-field" data-email="${escHtml(email)}" data-field="${field}" title="复制">${COPY_ICON}</button></div>`;
}
function cellPassword(email, password){
  if(!password) return '<span class="muted">—</span>';
  return `<div style="display:flex;align-items:center;gap:4px">
    <button type="button" class="pw-eye" data-opwd="${escHtml(email)}" title="显示/隐藏">👁</button>
    <button type="button" class="pw-eye" data-oact="copy-field" data-email="${escHtml(email)}" data-field="password" title="复制密码">${COPY_ICON}</button></div>`;
}
function copyIconBtn(val){
  if(!val) return '<span class="muted">—</span>';
  return `<div style="display:flex;align-items:center;gap:4px"><span class="mono">${escHtml(trunc(val,12))}</span>
    <button type="button" class="pw-eye" data-ocopyraw="${escHtml(val)}" title="复制">${COPY_ICON}</button></div>`;
}
function tokenStatusMain(v, hasToken){
  if(!hasToken) return '<span class="tag" style="color:#ff7b72;border-color:rgba(255,123,114,.4)">无 Token</span>';
  if(!v) return '<span class="tag">未测活</span>';
  if(v.ok||v.graph) return '<span class="tag" style="color:#7ee787;border-color:rgba(126,231,135,.4)">✓ Graph 可读信</span>';
  return '<span class="tag" style="color:#ff7b72;border-color:rgba(255,123,114,.4)">✗ 令牌失效</span>';
}

/* ---------- 分页切换 ---------- */
const OAR_TAB_LOADERS = {
  overview(){ loadOarJobs(); loadOarAccounts(); },
  register(){ loadOarRegisterConfig(); },
  pool(){ loadOarAccounts(); },
  proxies(){ loadOarProxyPool(); },
  'proxy-stats'(){ loadOarProxyStats(); },
  verify(){},
  ops(){},
};
function oarSwitchTab(tab){
  OAR.tab = tab;
  document.querySelectorAll('#oar-tabs button').forEach(b=>b.classList.toggle('active', b.dataset.tab===tab));
  document.querySelectorAll('.oar-pane').forEach(p=>{ p.style.display = p.id==='oar-pane-'+tab ? '' : 'none'; });
  (OAR_TAB_LOADERS[tab]||(()=>{}))();
}

/* ---------- 概览 ---------- */
function renderOarStatCards(boxId, items){
  const box=O$(boxId); if(!box) return;
  box.innerHTML = items.map(([l,v,c])=>
    `<div class="stat-card"><div class="stat-value" style="-webkit-text-fill-color:${c};background:none;color:${c}">${v}</div><div class="stat-label">${l}</div></div>`
  ).join('');
}
async function loadOarJobs(){
  try{
    const j=await oarApi('/jobs');
    OAR.jobs=j.jobs||[];
    const tb=O$('oar-jobsBody');
    if(tb){
      if(!OAR.jobs.length){ tb.innerHTML='<tr><td colspan="7" class="muted">暂无批次</td></tr>'; }
      else tb.innerHTML=OAR.jobs.map(x=>{
        const label=x.batch_label||(x.batch_no?('B'+x.batch_no):x.id);
        return `<tr>
          <td class="mono">${escHtml(label)}</td><td>${(x.created_at||'').replace('T',' ').slice(0,19)}</td>
          <td>${x.count}</td><td>${x.concurrency}</td><td>${x.dry_run?'干跑·':''}${escHtml(x.token_mode||'')}</td>
          <td><span class="tag">${escHtml(x.status)}</span></td>
          <td><span style="color:#7ee787">${x.ok_count||0}</span> / <span style="color:#ff7b72">${x.fail_count||0}</span></td></tr>`;
      }).join('');
    }
    setTxt('oar-acc-count', String(OAR.jobs.length));
  }catch(e){}
}
function setTxt(id,v){ const el=O$(id); if(el) el.textContent=v; }

/* ---------- 批次注册 ---------- */
async function loadOarRegisterConfig(){
  if(OAR.CFG){ fillOarRegForm(); return; }
  try{
    const c=await oarApi('/config'); OAR.CFG=c;
    O$('oar-pxMode').innerHTML=(c.px_modes||[]).map(m=>`<option>${escHtml(m)}</option>`).join('');
    const products=c.product_modes||[{id:'graph',label:'Graph 四段式'},{id:'graph_recovery',label:'Graph 六段式（推荐）'}];
    const def=c.default_token_mode||'graph';
    O$('oar-tokenMode').innerHTML=products.map(m=>{
      const id=m.id||m, label=m.label||id;
      return `<option value="${escHtml(id)}"${id===def?' selected':''}>${escHtml(label)}</option>`;
    }).join('');
    O$('oar-domain').innerHTML=(c.domains||[]).map(d=>`<option>${escHtml(d)}</option>`).join('');
    O$('oar-country').value=c.default_country||'US';
    O$('oar-proxy').value=localStorage.getItem('outlook_reg_proxy')||'';
    O$('oar-captchaKey').value=localStorage.getItem('outlook_reg_captcha_key')||'';
    if(O$('oar-useProxyPool')) O$('oar-useProxyPool').checked = localStorage.getItem('outlook_use_proxy_pool')!=='0';
    const fmtLabel={graph:'4 段式(Graph 读信)',dual:'6 段式(双令牌 SSO)',recovery:'6 段式(含恢复邮箱)'};
    O$('oar-exportFmt').innerHTML=(c.export_formats||['graph']).map(f=>`<option value="${f}">${fmtLabel[f]||f}</option>`).join('');
    O$('oar-exportFmt').value='graph';
    if(c.captcha_key_set && !O$('oar-captchaKey').value) O$('oar-cfgHint').textContent='后端已保存 Key，留空即可；注册默认走代理池，备用代理可留空';
    else O$('oar-cfgHint').textContent='';
  }catch(e){ O$('oar-cfgHint').textContent='配置加载失败: '+e.message; }
}
function fillOarRegForm(){ /* 配置只拉一次 */ }

async function oarStart(){
  const srcEl=O$('oar-proxySource');
  const src=srcEl?srcEl.value:(O$('oar-useProxyPool').checked?'pool':'manual');
  const proxy=(O$('oar-proxy')?O$('oar-proxy').value.trim():'');
  const captchaKey=O$('oar-captchaKey').value.trim();
  const dryRun=O$('oar-dryRun').checked;
  const solverEl=O$('oar-pxSolver');
  const solver=solverEl?solverEl.value:'auto';
  if(src==='manual'&&!proxy&&!dryRun){ oarToast('手动模式下请填写备用代理'); return; }
  if(src==='manual'&&!captchaKey&&!dryRun&&!OAR.CFG?.captcha_key_set&&solver!=='local'){ oarToast('请填写 captcha.run Key 或选择本地打码'); return; }
  localStorage.setItem('outlook_reg_proxy', proxy);
  localStorage.setItem('outlook_use_proxy_pool', src==='pool'?'1':'0');
  localStorage.setItem('outlook_reg_captcha_key', captchaKey);
  const body={
    count:+O$('oar-count').value||1, concurrency:+O$('oar-concurrency').value||1,
    prefix:O$('oar-prefix').value.trim()||null, domain:O$('oar-domain').value,
    country:O$('oar-country').value.trim()||'US',
    proxy:src==='manual'?proxy:(src==='pool'?proxy:''), use_proxy_pool:src==='pool',
    proxy_source:src==='network'?'clash':src,
    px_solver:solver, px_mode:O$('oar-pxMode').value,
    skip_login:O$('oar-skipLogin').checked, no_mail_token:false,
    captcha_key:captchaKey, token_mode:O$('oar-tokenMode').value, dry_run:dryRun,
  };
  const bl=O$('oar-batchLabel').value.trim(); if(bl) body.batch_label=bl;
  const jmin=parseFloat(O$('oar-jitterMin').value), jmax=parseFloat(O$('oar-jitterMax').value);
  if(!isNaN(jmin)) body.jitter_min=jmin;
  if(!isNaN(jmax)) body.jitter_max=jmax;
  O$('btn-oar-start').disabled=true; oarClearLog(); OAR.seenLogKeys.clear();
  for(const k in OAR.jobAccts) delete OAR.jobAccts[k];
  oarRenderProgress(); oarRenderSummary(null);
  O$('oar-logLive').style.display='';
  try{
    const j=await oarJpost('/register', body);
    O$('oar-jobBadge').innerHTML=`<span class="tag">${escHtml(j.batch_label||'')}</span> <span class="tag">${j.dry_run?'干跑':'真实'} · ${j.count}个 · 并发${j.concurrency}</span>`;
    oarToast('批次已启动'); oarConnectSSE(j.job_id);
    OAR.CURRENT_JOB=j.job_id;
    const sb=O$('btn-oar-stop'); if(sb){ sb.style.display=''; sb.disabled=false; sb.textContent='停止'; }
  }catch(e){
    oarLogLine({level:'ERROR', msg:'启动失败: '+e.message});
    O$('btn-oar-start').disabled=false; if(O$('btn-oar-stop')){O$('btn-oar-stop').style.display='none';O$('btn-oar-stop').disabled=false;} O$('oar-logLive').style.display='none';
  }
}
function oarConnectSSE(id){
  if(OAR.es) OAR.es.close();
  const es=new EventSource(`/oar-api/jobs/${id}/events`);
  OAR.es=es;
  es.onmessage=m=>{
    let ev=null; try{ ev=JSON.parse(m.data); }catch(_){ return; }
    if(ev.type==='snapshot'){
      (ev.snapshot.accounts||[]).forEach(a=>OAR.jobAccts[a.index]=a);
      (ev.snapshot.logs||[]).forEach(oarLogLine);
      oarRenderSummary(ev.snapshot.batch_summary); oarRenderProgress();
    }
    else if(ev.type==='log') oarLogLine(ev);
    else if(ev.type==='account'){ OAR.jobAccts[ev.account.index]=ev.account; oarRenderProgress(); }
    else if(ev.type==='summary') oarRenderSummary(ev.summary);
    else if(ev.type==='done'){ es.close(); OAR.es=null; O$('btn-oar-start').disabled=false; if(O$('btn-oar-stop')){O$('btn-oar-stop').style.display='none';O$('btn-oar-stop').disabled=false;} O$('oar-logLive').style.display='none'; oarToast('批次完成'); loadOarAccounts(); loadOarJobs(); }
  };
  es.onerror=()=>oarLogLine({level:'WARNING',msg:'SSE 连接中断（任务可能已结束）'});
}
function oarRenderProgress(){
  const rows=Object.values(OAR.jobAccts).sort((a,b)=>a.index-b.index);
  const tb=O$('oar-progressBody');
  if(!rows.length){ tb.innerHTML='<tr><td colspan="6" class="muted">开始注册后，各账号状态会显示在这里</td></tr>'; return; }
  let ok=0,err=0,run=0;
  tb.innerHTML=rows.map(a=>{
    if((a.status||'').startsWith('成功'))ok++;
    else if(a.status==='失败')err++;
    else if(a.status==='进行中')run++;
    return `<tr><td>${a.index}</td><td><span class="tag">${escHtml(a.status)}</span></td>
      <td class="mono">${escHtml(a.email||'—')}</td>
      <td>${a.password?copyIconBtn(a.password):'<span class="muted">—</span>'}</td>
      <td>${a.refresh_token?copyIconBtn(a.refresh_token):'<span class="muted">—</span>'}</td>
      <td style="color:#ff7b72;font-size:11px;">${escHtml(a.error||'')}</td></tr>`;
  }).join('');
  setTxt('oar-nTotal', String(rows.length)); setTxt('oar-nOk', String(ok)); setTxt('oar-nErr', String(err)); setTxt('oar-nRun', String(run));
}
function oarLogLine(ev){
  const key=(ev.ts||'')+'|'+(ev.level||'INFO')+'|'+(ev.msg||'');
  if(OAR.seenLogKeys.has(key)) return;
  OAR.seenLogKeys.add(key);
  const empty=O$('oar-logEmpty'); if(empty) empty.remove();
  const c=O$('oar-console');
  const d=document.createElement('div');
  d.style.cssText = ev.level==='ERROR' ? 'color:#ff7b72' : (ev.level==='WARNING' ? 'color:#f59e0b' : 'color:#a8d08d');
  d.innerHTML=`<span style="opacity:.55">${ev.ts||''}</span> ${(ev.msg||'').replace(/</g,'&lt;')}`;
  c.appendChild(d); c.scrollTop=c.scrollHeight;
  if(O$('oar-logCopyBtn')) O$('oar-logCopyBtn').style.display='';
}
function oarClearLog(){
  const c=O$('oar-console'); c.innerHTML='<p id="oar-logEmpty" style="padding:8px">配置完成后点击「开始批次注册」，代理预检、PX、注册各阶段会实时显示在这里</p>';
  if(O$('oar-logCopyBtn')) O$('oar-logCopyBtn').style.display='none';
}
function oarRenderSummary(s){
  const box=O$('oar-batchSummary'); if(!box) return;
  if(!s){ box.className='oar-msgbox'; box.innerHTML=''; return; }
  const st=s.avg_stage_timings||{};
  const stages=Object.entries(st).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<span class="tag">${escHtml(k)} ${v}s</span>`).join(' ')||'—';
  box.className='oar-msgbox show ok';
  box.innerHTML=`<b>本批小结</b>　成功 ${s.ok}/${s.total}（失败 ${s.failed}）　本批耗时 <b>${s.elapsed}s</b>　单号均耗 <b>${s.avg_per_account}s</b><br><span style="opacity:.6">阶段平均：</span>${stages}`;
}

/* ---------- 账号池 ---------- */
async function loadOarAccounts(){
  try{
    const j=await oarApi('/accounts');
    OAR.ACCOUNTS=j.accounts||[];
    oarRenderStats(j.stats);
    oarRenderBatchFilter();
    oarRenderPool();
  }catch(e){ const tb=O$('oar-poolBody'); if(tb) tb.innerHTML=`<tr><td colspan="12" class="muted">加载失败: ${escHtml(String(e.message||e))}</td></tr>`; }
}
function oarRenderStats(s){
  const items=[
    ['总数',s.total,'#f4f6fb'],['有Token',s.with_token,'#9ec1ff'],['可用',s.usable,'#7ee787'],
    ['失活',s.dead,'#ff7b72'],['未测',s.untested,'#6b7280'],['今日新增',s.today_new,'#4f8cff'],
  ];
  renderOarStatCards('oar-statCards', items);
  renderOarStatCards('oar-statcards-pool', items);
  setTxt('oar-acc-count', String(OAR.ACCOUNTS.length));
}
function oarRenderBatchFilter(){
  const sel=O$('oar-filterBatch'); if(!sel) return;
  const cur=OAR.FILTER.batch||'all';
  const counts=new Map();
  OAR.ACCOUNTS.forEach(a=>{ const l=accountBatchLabel(a); if(l) counts.set(l,(counts.get(l)||0)+1); });
  const extras=[...counts.entries()].sort((x,y)=>String(y[0]).localeCompare(String(x[0]),'zh'));
  const opts=[['all','全部批次'],['none','无批次'],...extras.map(([l,n])=>[l,`${l}（${n}）`])];
  sel.innerHTML=opts.map(([v,t])=>`<option value="${escHtml(v)}">${escHtml(t)}</option>`).join('');
  sel.value=opts.some(o=>o[0]===cur)?cur:'all';
  OAR.FILTER.batch=sel.value;
}
function oarFilteredAccounts(){
  return OAR.ACCOUNTS.filter(a=>{
    const v=a.verify, view=OAR.FILTER.view;
    if(view==='without'&&a.has_token) return false;
    if(view==='dual'&&!a.login_token) return false;
    if(view==='untested'&&v) return false;
    if(view==='usable'&&!isGraphReadable(a)) return false;
    if(view==='dead'&&!(v&&!v.ok&&!v.graph)) return false;
    const batch=accountBatchLabel(a);
    if(OAR.FILTER.batch==='none'&&batch) return false;
    if(OAR.FILTER.batch&&OAR.FILTER.batch!=='all'&&OAR.FILTER.batch!=='none'&&batch!==OAR.FILTER.batch) return false;
    if(OAR.FILTER.q){
      const q=OAR.FILTER.q.toLowerCase();
      const hay=(a.email+' '+(a.recovery_email||'')+' '+(a.note||'')+' '+(a.tags||[]).join(' ')+' '+batch).toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  }).sort((a,b)=>{
    const ga=isGraphReadable(a)?1:0, gb=isGraphReadable(b)?1:0;
    if(ga!==gb) return gb-ga;
    return String(b.created_at||'').localeCompare(String(a.created_at||''));
  });
}
function oarRenderPool(){
  const rows=oarFilteredAccounts();
  const pages=Math.max(1,Math.ceil(rows.length/OAR.PAGE_SIZE));
  if(OAR.PAGE>pages) OAR.PAGE=pages;
  const slice=rows.slice((OAR.PAGE-1)*OAR.PAGE_SIZE, OAR.PAGE*OAR.PAGE_SIZE);
  const tb=O$('oar-poolBody'); if(!tb) return;
  if(!slice.length) tb.innerHTML='<tr><td colspan="12" class="muted">无匹配账号</td></tr>';
  else tb.innerHTML=slice.map(a=>{
    const em=escHtml(a.email);
    const rec=a.recovery_email?cellWithCopy(a.email,'recovery_email',a.recovery_email):'<span class="muted">未绑定</span>';
    const tags=(a.tags||[]).map(t=>`<span class="tag">${escHtml(t)}</span>`).join(' ');
    const note=a.note?`<span style="color:#c8d2e8">${escHtml(a.note)}</span> `:'';
    const batch=accountBatchLabel(a);
    return `<tr>
      <td><input type="checkbox" class="oar-rowck" data-email="${em}" ${OAR.SELECTED.has(a.email)?'checked':''} style="accent-color:#4f8cff"/></td>
      <td>${cellWithCopy(a.email,'email',a.email)}</td>
      <td>${cellPassword(a.email,a.password)}</td>
      <td>${rec}</td>
      <td>${tokenStatusMain(a.verify,a.has_token)}</td>
      <td>${batch?`<span class="tag">${escHtml(batch)}</span>`:'<span class="muted">—</span>'}</td>
      <td title="${escHtml(fmtFullTime(a.created_at))}">${escHtml(fmtShortTime(a.created_at))}</td>
      <td title="${escHtml(survivalTip(a))}">${escHtml(fmtSurvivalAge(a))}</td>
      <td class="mono" title="${escHtml(rescueTip(a))}">${escHtml(fmtRescueCount(a))}</td>
      <td title="${escHtml(fmtFullTime(a.updated_at))}">${escHtml(fmtShortTime(a.updated_at))}</td>
      <td style="font-size:11.5px;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${note}${tags||'<span class="muted">—</span>'}</td>
      <td><div style="display:flex;gap:3px">
        <button type="button" class="pw-eye" data-oact="detail" data-email="${em}" title="详情">${ICO_INFO}</button>
        <button type="button" class="pw-eye" data-oact="verify" data-email="${em}" title="测活">${ICO_REFRESH}</button>
        <button type="button" class="pw-eye" data-oact="delete" data-email="${em}" title="删除" style="color:#ff7b72">✕</button>
      </div></td></tr>`;
  }).join('');
  document.querySelectorAll('.oar-rowck').forEach(c=>c.onchange=()=>{ c.checked?OAR.SELECTED.add(c.dataset.email):OAR.SELECTED.delete(c.dataset.email); oarUpdateSel(); });
  setTxt('oar-pageInfo', `第 ${OAR.PAGE}/${pages} 页 · 共 ${rows.length} 条`);
  oarUpdateSel();
}
function oarUpdateSel(){
  setTxt('oar-selCount', String(OAR.SELECTED.size));
  const vis=oarFilteredAccounts().slice((OAR.PAGE-1)*OAR.PAGE_SIZE,OAR.PAGE*OAR.PAGE_SIZE);
  const all=vis.length&&vis.every(a=>OAR.SELECTED.has(a.email));
  if(O$('oar-ckAll')) O$('oar-ckAll').checked=!!all;
}
function selectedEmails(){ return [...OAR.SELECTED]; }

/* 账号池行内点击（复制/显密/详情/测活/删除） */
document.addEventListener('click', e=>{
  const cp=e.target.closest('[data-ocopyraw]');
  if(cp){ copyText(cp.dataset.ocopyraw); return; }
  const eye=e.target.closest('[data-opwd]');
  if(eye){
    const cell=eye.parentElement.querySelector('.oar-pw-val');
    if(cell){ cell.remove(); } else {
      const a=accountByEmail(eye.dataset.opwd);
      const span=document.createElement('span');
      span.className='mono oar-pw-val'; span.textContent=a?a.password||'':''; span.title='点👁收起';
      eye.parentElement.insertBefore(span, eye);
    }
    return;
  }
  const btn=e.target.closest('[data-oact]');
  if(!btn) return;
  const email=btn.dataset.email, act=btn.dataset.oact;
  const a=accountByEmail(email);
  if(act==='copy-field'&&a){ copyText(a[btn.dataset.field]||''); return; }
  if(act==='detail'&&a){ oarShowDetail(email); return; }
  if(act==='verify'&&a){ oarVerifyRow(email); return; }
  if(act==='delete'&&email){ oarDelRow(email); return; }
});

/* 详情弹窗（通用 modal） */
function oarModal(html, footHtml){
  const bg=O$('oar-modal-bg'), box=O$('oar-modal-box');
  box.innerHTML = html + (footHtml!==undefined
    ? `<div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap">${footHtml}<span style="flex:1"></span><button class="btn-inline" data-omclose>关闭</button></div>` : '');
  bg.style.display='flex';
}
function oarCloseModal(){ O$('oar-modal-bg').style.display='none'; }
function tokenStatusDetail(v){
  if(!v) return '<span class="tag">尚未测活</span>';
  const pill=(label,ok,hint)=>{
    const col=ok===null?'#6b7280':(ok?'#7ee787':'#ff7b72');
    return `<span class="tag" style="color:${col};border-color:${col}55" title="${escHtml(hint||'')}">${label}${ok===null?' · 未测':ok?' · 通':' · 不通'}</span>`;
  };
  return `<div style="display:flex;gap:6px;flex-wrap:wrap">
    ${pill('Graph 读信', v.graph, 'Microsoft Graph API，导入工具用这个')}
    ${pill('Outlook REST', v.outlook_rest, '旧版 REST API，一般可忽略')}
    ${v.imap!=null?pill('IMAP 登录', v.imap, 'Thunderbird 协议，新号通常关'):''}
  </div>`;
}
function oarShowDetail(email){
  const a=accountByEmail(email); if(!a) return;
  OAR.DETAIL_EMAIL=email;
  const row=(k,v,copyable)=>`<div style="margin-bottom:8px"><div class="muted" style="font-size:11px;margin-bottom:2px">${k}</div>
    <div style="font-size:12.5px;word-break:break-all">${copyable?copyIconBtn(v):(escHtml(v||'—'))}</div></div>`;
  oarModal(
    `<h3 style="margin:0 0 10px">账号详情 <span class="mono" style="color:#9ec1ff;font-size:13px">${escHtml(a.email)}</span></h3>
     <div style="max-height:52vh;overflow:auto;padding-right:4px">
       ${row('密码', a.password, true)}
       ${row('client_id', a.client_id, true)}
       ${row('refresh_token', a.refresh_token, true)}
       ${row('四段 combo', a.combo, true)}
       ${a.combo_dual?row('六段 dual', a.combo_dual, true):''}
       ${row('恢复邮箱', a.recovery_email)}
       ${row('读信状态', '')}${tokenStatusDetail(a.verify)}
       ${row('批次', accountBatchLabel(a))}
       ${row('备注', [a.note,(a.tags||[]).join(', ')].filter(Boolean).join(' · '))}
     </div>`,
    `<button class="btn-inline" id="oar-d-note">备注</button>
     <button class="btn-inline" id="oar-d-c6">复制六段</button>
     <button class="btn-inline" id="oar-d-verify">测活</button>
     <button class="btn-inline" id="oar-d-del" style="color:#ff7b72">删除</button>`
  );
  O$('oar-d-note').onclick=()=>oarEditNote(email);
  O$('oar-d-c6').onclick=()=>{ const c=combo6(accountByEmail(email)||{}); c?copyText(c):oarToast('无六段 combo'); };
  O$('oar-d-verify').onclick=()=>oarVerifyRow(email).then(()=>oarShowDetail(email));
  O$('oar-d-del').onclick=()=>{ oarDelRow(email); oarCloseModal(); };
}
O$('oar-modal-bg').addEventListener('click', e=>{
  if(e.target.id==='oar-modal-bg'||e.target.closest('[data-omclose]')) oarCloseModal();
});
function oarEditNote(email){
  const a=accountByEmail(email)||{};
  OAR.NOTE_EMAIL=email;
  oarModal(
    `<h3 style="margin:0 0 12px">备注 / 标签 <span class="mono muted" style="font-size:12px">${escHtml(email)}</span></h3>
     <label class="oar-field" style="margin-bottom:10px"><span>备注</span><input type="text" id="oar-noteInput" value="${escHtml(a.note||'')}" placeholder="可选" /></label>
     <label class="oar-field"><span>标签（逗号分隔）</span><input type="text" id="oar-noteTags" value="${escHtml((a.tags||[]).join(','))}" placeholder="可选" /></label>`,
    `<button class="btn-run" id="oar-noteSave" type="button">保存</button>`
  );
  O$('oar-noteSave').onclick=async()=>{
    const note=O$('oar-noteInput').value.trim();
    const tags=O$('oar-noteTags').value.split(',').map(s=>s.trim()).filter(Boolean);
    try{ await oarJpost('/accounts/meta',{email:OAR.NOTE_EMAIL,note,tags}); oarCloseModal(); oarToast('备注已保存'); loadOarAccounts(); }
    catch(e){ oarToast('保存失败: '+e.message); }
  };
}
async function oarVerifyRow(email){
  const a=accountByEmail(email); if(!a) return {ok:false};
  const rt=a.refresh_token||'';
  if(!rt){ oarToast('无 refresh_token，无法测活'); return {ok:false}; }
  try{
    const j=await oarJpost('/verify-combo',{email:a.email,refresh_token:rt,combo:a.combo||'',test_imap:false});
    oarToast(j.transient?(j.summary||'网络异常，保留原状态'):(j.summary||(j.ok?'Graph 可用':'不可用')));
    await loadOarAccounts();
    return j;
  }catch(e){ oarToast('测活失败: '+e.message); }
}
async function oarDelRow(email){
  if(!confirm(`确定删除 ${email} ？此操作不可恢复。`)) return;
  try{ await oarJpost('/accounts/delete',{emails:[email]}); OAR.SELECTED.delete(email); loadOarAccounts(); oarToast('已删除'); }
  catch(e){ oarToast('删除失败：'+e.message); }
}

/* 批量操作 */
async function runBatchVerify(emails, btn){
  const picked=(emails&&emails.length)?OAR.ACCOUNTS.filter(a=>emails.includes(a.email)):OAR.ACCOUNTS.slice();
  const list=picked.filter(a=>a.refresh_token);
  const noToken=picked.length-list.length;
  let ok=0,fail=0,died=0,skipped=0,i=0;
  if(!list.length){ oarToast('没有可测活账号（缺 refresh_token）'); return; }
  const old=btn?btn.textContent:null;
  if(btn){ btn.disabled=true; btn.textContent=`测活中 0/${list.length}`; }
  oarToast(`开始测活 ${list.length} 个`);
  if(noToken) console.warn(`${noToken} 个没有 refresh_token`);
  const queue=list.slice();
  const workers=Array.from({length:Math.min(3,queue.length||1)}, async()=>{
    while(queue.length){
      const a=queue.shift();
      const n=++i;
      const wasOk=isGraphReadable(a);
      try{
        const j=await oarJpost('/verify-combo',{email:a.email,refresh_token:a.refresh_token,combo:a.combo||'',test_imap:false});
        if(j.transient){ skipped++; }
        else if(j.unable){ fail++; }
        else if(j.ok){ ok++; }
        else{ fail++; if(wasOk)died++; }
        if(btn) btn.textContent=`测活中 ${i}/${list.length}`;
        oarLogLine({level:j.ok?'INFO':(j.unable||j.transient?'WARNING':'ERROR'), ts:new Date().toTimeString().slice(0,8), msg:`[${n}/${list.length}] ${a.email} ${j.summary||(j.ok?'可用':'不可用')}`});
      }catch(err){ fail++; oarLogLine({level:'ERROR',ts:new Date().toTimeString().slice(0,8),msg:`[${n}] ${a.email} ${err.message}`}); }
    }
  });
  await Promise.all(workers);
  oarToast(`测活完成：可用 ${ok} / 失效 ${fail}${skipped?` / 网络跳过 ${skipped}`:''}`);
  await loadOarAccounts();
  if(btn){ btn.disabled=false; btn.textContent=old; }
}
async function runBatchRescue(emails, btn){
  const picked=(emails&&emails.length)?OAR.ACCOUNTS.filter(a=>emails.includes(a.email)):OAR.ACCOUNTS.slice();
  const list=picked.filter(a=>(a.password||'').trim());
  const noPwd=picked.length-list.length;
  if(!list.length){ oarToast('没有可重登账号（需有密码）'); return; }
  const proxy=O$('oar-proxy')?O$('oar-proxy').value.trim():'';
  const usePool=O$('oar-useProxyPool')?O$('oar-useProxyPool').checked:true;
  if(noPwd) oarToast(`${noPwd} 个缺密码已跳过`);
  const old=btn?btn.textContent:null;
  if(btn){ btn.disabled=true; btn.textContent='重登中…'; }
  oarToast(`开始重登 ${list.length} 个（并发 1）`);
  try{
    const j=await oarJpost('/rescue',{emails:list.map(a=>a.email),proxy,use_proxy_pool:usePool,concurrency:1});
    if(!j.implemented){ oarToast(j.message||'重登不可用'); return; }
    (j.results||[]).forEach(r=>{
      oarLogLine({level:r.ok?'INFO':'ERROR',ts:new Date().toTimeString().slice(0,8),
        msg:`${r.email} ${r.ok?'重登成功'+(r.rescue_count!=null?' · 累计 '+r.rescue_count+' 次':''):'重登失败：'+(r.reason||r.message||'未知')}`});
    });
    oarToast(`重登完成：${j.ok_count||0}/${j.total||0}`);
    await loadOarAccounts();
  }catch(e){ oarToast('重登失败: '+e.message); }
  finally{ if(btn){ btn.disabled=false; btn.textContent=old; } }
}
async function downloadExport(emails){
  const fmt=O$('oar-exportFmt').value;
  try{
    const r=await fetch('/oar-api/accounts/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({emails,format:fmt})});
    if(!r.ok) throw new Error(await r.text());
    const blob=await r.blob(); const url=URL.createObjectURL(blob);
    const a=document.createElement('a'); a.href=url; a.download='accounts_'+fmt+'.txt'; a.click(); URL.revokeObjectURL(url);
    oarToast('已导出 '+fmt+' 格式');
  }catch(e){ oarToast('导出失败: '+e.message); }
}
function oarImportModal(){
  oarModal(
    `<h3 style="margin:0 0 8px">导入账号（4 段 / 6 段自动识别）</h3>
     <p class="muted" style="font-size:11.5px;margin:0 0 10px">按 <code>----</code> 分段数自动识别：<br>· 4 段 Graph：<code>email----password----client_id----refresh_token</code><br>· 6 段恢复邮箱：<code>…----recovery_email----recovery_password</code><br>· 6 段双令牌 SSO：<code>…----login_client_id----login_refresh_token</code><br>自动按邮箱去重。</p>
     <textarea id="oar-importText" rows="9" style="width:100%;background:#0f1219;color:#e5eaf5;border:1px solid rgba(120,150,255,.22);border-radius:10px;padding:10px;font-size:12px" placeholder="4段: a@outlook.com----pwd----cid----M.C5xx...&#10;6段: b@outlook.com----pwd----cid----M.C5yy...----login_cid----M.C5zz..."></textarea>`,
    `<button class="btn-run" id="oar-importDo" type="button">导入</button>`
  );
  O$('oar-importDo').onclick=async()=>{
    const text=O$('oar-importText').value;
    if(!text.trim()){ oarToast('无内容'); return; }
    try{
      const j=await oarJpost('/accounts/import',{text});
      oarToast(`导入 ${j.imported}（其中 6 段 ${j.six_seg||0}），重复 ${j.duplicate}，无效 ${j.invalid}`);
      oarCloseModal(); loadOarAccounts();
    }catch(e){ oarToast('导入失败: '+e.message); }
  };
}
async function runKeepalive(emails){
  const btn=O$('btn-oar-keepaliveAll');
  if(btn){ btn.disabled=true; btn.textContent='保活中…'; }
  opsMsg('warn','保活中…（refresh→access→GET /me+列信→轮换回写，并发≤5）');
  oarToast('保活开始…');
  try{
    const j=await oarJpost('/keepalive',{emails});
    if(j.implemented===false){ opsMsg('err',j.message||'保活不可用'); return; }
    const rows=j.results||[];
    if(!rows.length && j.message){ opsMsg('warn', j.message); oarToast(j.message); return; }
    oarToast(`保活完成：${j.ok_count??rows.filter(r=>r.ok).length}/${rows.length}`);
    if(rows.length){
      O$('oar-kaWrap').style.display='';
      O$('oar-kaBody').innerHTML=rows.map(r=>{
        const ok=r.ok===true;
        return `<tr><td class="mono">${escHtml(r.email)}</td><td>${ok?'<span class="tag" style="color:#7ee787">存活</span>':'<span class="tag" style="color:#ff7b72">'+escHtml(r.reason||r.stage||'失活')+'</span>'}</td>
          <td class="mono">${escHtml(r.profile||'—')}</td><td>${r.mail_count??'—'}</td><td>${r.rotated?'✓ 已轮换':'—'}</td><td style="font-size:11px">${escHtml(r.detail||'')}</td></tr>`;
      }).join('');
    }
    opsMsg('ok', `保活完成：${j.ok_count??rows.filter(r=>r.ok).length}/${j.total??rows.length}`);
    await loadOarAccounts();
  }catch(e){ opsMsg('err','保活失败: '+e.message); oarToast('保活失败: '+e.message); }
  finally{ if(btn){ btn.disabled=false; btn.textContent='保活全部'; } }
}
function opsMsg(kind,text){
  const m=O$('oar-opsMsg');
  const col=kind==='ok'?'#7ee787':(kind==='err'?'#ff7b72':'#f59e0b');
  m.style.cssText=`margin-top:12px;padding:10px 14px;border-radius:10px;font-size:12.5px;background:rgba(79,140,255,.08);border-left:3px solid ${col};color:${col}`;
  m.textContent=text;
}

/* ---------- 代理池 ---------- */
function proxyStatusBadge(st){
  const s=st||'unknown';
  const col=s==='ok'?'#7ee787':(s==='dead'?'#ff7b72':'#6b7280');
  const lbl={ok:'可用',dead:'失效',unknown:'未检',checking:'检测中'}[s]||s;
  return `<span class="tag" style="color:${col};border-color:${col}55">${lbl}</span>`;
}
function oarRenderProxyStats(s){
  if(!s) return;
  renderOarStatCards('oar-proxyStatCards', [
    ['总数',s.total,'#f4f6fb'],['代理商',s.providers||0,'#9ec1ff'],['启用',s.enabled,'#4f8cff'],
    ['可用',s.ok,'#7ee787'],['失效',s.dead,'#ff7b72'],['未检',s.unknown,'#6b7280'],['绑定',s.bindings,'#9ec1ff'],
  ]);
  const banner=O$('oar-proxyEmptyBanner');
  if(banner) banner.style.display=(s.total||0)===0?'block':'none';
  setTxt('oar-px-count', String(s.enabled||0));
}
function oarRenderProxyTable(){
  const tb=O$('oar-proxyBody'); if(!tb) return;
  if(!OAR.PROXY_ROWS.length){ tb.innerHTML='<tr><td colspan="12" class="muted">暂无代理，点击「添加代理」</td></tr>'; return; }
  tb.innerHTML=OAR.PROXY_ROWS.map(p=>{
    const st=p.stats||{};
    const chk=OAR.PROXY_SEL.has(p.id)?'checked':'';
    const en=p.enabled!==false;
    return `<tr>
      <td><input type="checkbox" class="oar-pxck" data-id="${escHtml(p.id)}" ${chk} style="accent-color:#4f8cff"/></td>
      <td>${escHtml(p.label||'—')}</td><td>${escHtml(p.provider||'—')}</td><td>${escHtml(p.country||'—')}</td>
      <td class="mono" style="font-size:11px">${escHtml(p.template_masked||'—')}</td>
      <td>${p.has_sid?'<span class="tag">是</span>':'<span class="tag">否</span>'}</td>
      <td>${proxyStatusBadge(p.status)}</td>
      <td class="mono" style="font-size:11px">${escHtml(p.exit_ip||'—')}</td>
      <td class="mono" style="font-size:11px">${st.assigned||0}/${st.success||0}/${st.fail||0}</td>
      <td title="${escHtml(p.last_check_msg||'')}">${escHtml((p.last_check_at||'').replace('T',' ').slice(0,16)||'—')}</td>
      <td><input type="checkbox" class="oar-px-en" data-id="${escHtml(p.id)}" ${en?'checked':''} style="accent-color:#4f8cff"/></td>
      <td><button type="button" class="btn-inline oar-px-check-one" data-id="${escHtml(p.id)}">预检</button></td>
    </tr>`;
  }).join('');
  document.querySelectorAll('.oar-pxck').forEach(c=>c.onchange=()=>{ c.checked?OAR.PROXY_SEL.add(c.dataset.id):OAR.PROXY_SEL.delete(c.dataset.id); });
}
function oarRenderProxyBindings(rows){
  const tb=O$('oar-proxyBindBody'); if(!tb) return;
  if(!rows||!rows.length){ tb.innerHTML='<tr><td colspan="6" class="muted">暂无绑定</td></tr>'; return; }
  tb.innerHTML=rows.map(b=>`<tr>
    <td class="mono">${escHtml(b.email)}</td><td>${escHtml(b.proxy_label||b.proxy_id||'—')}</td>
    <td class="mono" style="font-size:11px">${escHtml(b.resolved_masked||'—')}</td><td>${escHtml(b.purpose||'—')}</td>
    <td>${escHtml((b.assigned_at||'').replace('T',' ').slice(0,19))}</td>
    <td><button type="button" class="btn-inline oar-px-unbind" data-email="${escHtml(b.email)}">解绑</button></td></tr>`).join('');
}
async function loadOarProxyPool(){
  try{
    const prov=(O$('oar-proxyFilterProvider')&&O$('oar-proxyFilterProvider').value)||'';
    const q=prov?('?provider='+encodeURIComponent(prov)):'';
    const j=await oarApi('/proxy-pool'+q);
    OAR.PROXY_ROWS=j.proxies||[];
    oarRenderProxyStats(j.stats);
    oarRenderProxyTable();
    oarRenderProxyBindings(j.bindings||[]);
    if(O$('oar-proxyPoolFile')) O$('oar-proxyPoolFile').textContent=(j.backend||'sqlite')+' · '+(j.file||'—');
    const provSel=O$('oar-proxyFilterProvider');
    if(provSel){
      const cur=provSel.value;
      const names=(j.providers||[]).map(p=>p.name).filter(Boolean);
      provSel.innerHTML='<option value="">全部代理商</option>'+names.map(n=>`<option value="${escHtml(n)}">${escHtml(n)}</option>`).join('');
      provSel.value=cur;
    }
    const s=j.settings||{};
    if(O$('oar-proxyStrategy')) O$('oar-proxyStrategy').value=s.strategy||'round_robin';
    if(O$('oar-proxyRequireHealthy')) O$('oar-proxyRequireHealthy').checked=!!s.require_healthy;
    if(O$('oar-proxySticky')) O$('oar-proxySticky').checked=s.sticky_per_account!==false;
  }catch(e){ oarToast('代理池加载失败: '+e.message); }
}
function oarAddProxyModal(){
  oarModal(
    `<h3 style="margin:0 0 8px">添加代理模板</h3>
     <p class="muted" style="font-size:11.5px;margin:0 0 10px">每行一条，格式 <code>host:port:user:pass</code> 或带 <code>{sid}</code> 的 sticky 模板。<code>#</code> 开头为注释。</p>
     <div class="oar-grid" style="grid-template-columns:1fr;gap:8px;margin-bottom:10px">
       <label class="oar-field"><span>代理商（分组名）</span><input type="text" id="oar-pxAddProvider" placeholder="rapidproxy" /></label>
       <label class="oar-field"><span>国家代码（留空从模板推断）</span><input type="text" id="oar-pxAddCountry" placeholder="US" maxlength="6" /></label>
       <label class="oar-field"><span>标签（单条时可选）</span><input type="text" id="oar-pxAddLabel" placeholder="US residential" /></label>
     </div>
     <textarea id="oar-pxAddText" rows="8" style="width:100%;background:#0f1219;color:#e5eaf5;border:1px solid rgba(120,150,255,.22);border-radius:10px;padding:10px;font-size:12px" placeholder="us.rapidproxy.io:5001:user-session-{sid}-stime-10:pass"></textarea>`,
    `<button class="btn-run" id="oar-pxAddDo" type="button">添加</button>`
  );
  O$('oar-pxAddDo').onclick=async()=>{
    const text=O$('oar-pxAddText').value.trim();
    if(!text){ oarToast('请输入代理'); return; }
    try{
      const j=await oarJpost('/proxy-pool',{text,label:O$('oar-pxAddLabel').value.trim()||null,provider:O$('oar-pxAddProvider').value.trim()||null,country:(O$('oar-pxAddCountry')&&O$('oar-pxAddCountry').value.trim())||null});
      oarToast('已添加 '+j.added+' 条'); oarCloseModal(); loadOarProxyPool();
    }catch(e){ oarToast('添加失败: '+e.message); }
  };
}
async function oarCheckProxies(ids, btn){
  if(!ids.length){ oarToast('未选中'); return; }
  const old=btn?btn.textContent:null;
  if(btn){ btn.disabled=true; btn.textContent='预检中…'; }
  oarToast(`预检中…（${ids.length} 个代理）`);
  try{
    const j=await oarJpost('/proxy-pool/check',{ids});
    oarToast(`预检完成：${j.healthy}/${j.checked} 可用`);
  }catch(e){ oarToast('预检失败: '+e.message); }
  finally{
    if(btn){ btn.disabled=false; btn.textContent=old; }
    loadOarProxyPool();
  }
}

/* ---------- 代理统计 ---------- */
async function loadOarProxyStats(){
  try{
    const prov=(O$('oar-statsFilterProvider')&&O$('oar-statsFilterProvider').value)||'';
    const country=(O$('oar-statsFilterCountry')&&O$('oar-statsFilterCountry').value)||'';
    const reg=(O$('oar-statsFilterRegCountry')&&O$('oar-statsFilterRegCountry').value)||'';
    const groupBy=(O$('oar-statsGroupBy')&&O$('oar-statsGroupBy').value)||'provider';
    const days=(O$('oar-statsDays')&&O$('oar-statsDays').value)||'30';
    const qs=new URLSearchParams();
    if(prov) qs.set('provider',prov);
    if(country) qs.set('country',country);
    if(reg) qs.set('reg_country',reg);
    qs.set('group_by',groupBy); qs.set('days',days);
    const q='?'+qs.toString();
    const [aj,tj]=await Promise.all([oarApi('/proxy-pool/analytics'+q), oarApi('/proxy-pool/analytics/timeseries'+q)]);
    const fo=(tj.timeseries&&tj.timeseries.filters)||{};
    oarFillSelect('oar-statsFilterProvider',fo.providers,prov,'全部代理商');
    oarFillSelect('oar-statsFilterCountry',fo.countries,country,'全部代理国');
    oarFillSelect('oar-statsFilterRegCountry',fo.reg_countries,reg,'全部注册国');
    oarRenderAnalyticsStats(aj.analytics,tj.timeseries);
    oarRenderAnalyticsTable(aj.analytics);
    OAR.PROXY_STATS_TS=tj.timeseries;
    if(OAR.PROXY_CHART_HIDDEN.size&&!(OAR.PROXY_STATS_TS.lines||[]).some(ln=>OAR.PROXY_CHART_HIDDEN.has(ln.id))) OAR.PROXY_CHART_HIDDEN.clear();
    oarRenderChartLegend(tj.timeseries);
    oarRenderRateChart(tj.timeseries);
  }catch(e){ oarToast('统计加载失败: '+e.message); }
}
function oarFillSelect(id,values,cur,emptyLabel){
  const sel=O$(id); if(!sel) return;
  const v=cur||sel.value||'';
  sel.innerHTML=`<option value="">${escHtml(emptyLabel)}</option>`+(values||[]).map(x=>`<option value="${escHtml(x)}">${escHtml(x)}</option>`).join('');
  sel.value=v;
}
function oarRenderAnalyticsStats(a,ts){
  const ov=(ts&&ts.overall)||{};
  renderOarStatCards('oar-pxAnalyticsStatCards', [
    ['事件数',(a&&a.event_count)||0,'#f4f6fb'],
    ['区间成功率',ov.rate!=null?ov.rate+'%':'—','#4f8cff'],
    ['成功',ov.success||0,'#7ee787'],['失败',ov.fail||0,'#ff7b72'],
  ]);
}
function oarRenderAnalyticsTable(a){
  const el=O$('oar-pxAnalyticsBody'); if(!el) return;
  const rows=(a&&a.by_provider_country_reg_country)||(a&&a.by_provider_country)||[];
  if(!rows.length){ el.innerHTML='<tr><td colspan="7" class="muted">暂无统计数据（完成注册/重登后自动记录）</td></tr>'; return; }
  el.innerHTML=rows.map(r=>`<tr>
    <td>${escHtml(r.provider||'—')}</td><td>${escHtml(r.country||'—')}</td><td>${escHtml(r.reg_country||'—')}</td>
    <td>${r.total||0}</td><td style="color:#7ee787">${r.success||0}</td><td style="color:#ff7b72">${r.fail||0}</td>
    <td><b>${r.rate!=null?r.rate+'%':'—'}</b></td></tr>`).join('');
}
function chartColorForLine(line,idx){
  const cc=(line.country||'').trim().toUpperCase();
  const key=cc||(line.id||line.label||String(idx));
  let h=0;
  for(let i=0;i<key.length;i++) h=(h*31+key.charCodeAt(i))>>>0;
  return CHART_PALETTE[h%CHART_PALETTE.length];
}
function oarRenderChartLegend(ts){
  const box=O$('oar-chartLegend'); if(!box) return;
  const lines=(ts&&ts.lines)||[];
  if(!lines.length){ box.innerHTML='<span class="muted" style="font-size:11.5px">柱形=每日总事件量 · 悬停查看详情</span>'; return; }
  box.innerHTML=lines.map((ln,i)=>{
    const col=chartColorForLine(ln,i);
    const off=OAR.PROXY_CHART_HIDDEN.has(ln.id)?' opacity:.35;':'' ;
    return `<button type="button" data-lgid="${escHtml(ln.id)}" style="background:none;border:1px solid rgba(120,150,255,.2);border-radius:999px;padding:3px 10px;cursor:pointer;display:flex;align-items:center;gap:5px;color:#c8d2e8;font-size:11.5px;${off}"><span style="width:8px;height:8px;border-radius:99px;background:${col}"></span>${escHtml(ln.label)}</button>`;
  }).join('')+`<span class="muted" style="font-size:11.5px;align-self:center">柱形=每日总事件量 · 点击图例显隐曲线</span>`;
  box.querySelectorAll('[data-lgid]').forEach(btn=>btn.onclick=()=>{
    const id=btn.dataset.lgid;
    if(OAR.PROXY_CHART_HIDDEN.has(id)) OAR.PROXY_CHART_HIDDEN.delete(id); else OAR.PROXY_CHART_HIDDEN.add(id);
    oarRenderChartLegend(OAR.PROXY_STATS_TS); oarRenderRateChart(OAR.PROXY_STATS_TS);
  });
}
function oarShowTooltip(idx, clientX, clientY){
  const tip=O$('oar-chartTooltip');
  const layout=OAR.PROXY_CHART_LAYOUT, ts=OAR.PROXY_STATS_TS;
  if(!tip||!layout||!ts||idx<0){ if(tip) tip.style.display='none'; return; }
  const day=layout.series[idx]; if(!day){ tip.style.display='none'; return; }
  const lines=(ts.lines||[]).filter(ln=>!OAR.PROXY_CHART_HIDDEN.has(ln.id));
  let html=`<div style="opacity:.65;margin-bottom:4px">${escHtml(day.date||'')}</div>`;
  lines.forEach(ln=>{
    const p=(ln.points||[])[idx]||{};
    if(!(p.total>0)) return;
    html+=`<div style="display:flex;justify-content:space-between;gap:12px;margin-top:2px"><span>${escHtml(ln.label)}</span><b>${p.rate!=null?p.rate+'%':'—'} · ${p.success||0}/${p.total||0}</b></div>`;
  });
  html+=`<div style="margin-top:6px;opacity:.65;font-size:11px">当日总事件 ${day.total||0}（成功 ${day.success||0} / 失败 ${day.fail||0}）</div>`;
  tip.innerHTML=html; tip.style.display='block';
  const stage=O$('oar-chartStage'), rect=stage.getBoundingClientRect();
  let left=clientX-rect.left+12, top=clientY-rect.top-8;
  const tw=tip.offsetWidth||220, th=tip.offsetHeight||120;
  if(left+tw>rect.width-8) left=Math.max(8,left-tw-24);
  if(top+th>rect.height-8) top=Math.max(8,top-th-12);
  tip.style.left=left+'px'; tip.style.top=top+'px';
}
function oarRenderRateChart(ts){
  const canvas=O$('oar-rateChart'), empty=O$('oar-chartEmpty');
  if(!canvas||!empty) return;
  const series=(ts&&ts.series)||[], lines=(ts&&ts.lines)||[];
  const hasData=series.some(p=>(p.total||0)>0);
  canvas.style.display=hasData?'block':'none';
  empty.style.display=hasData?'none':'flex';
  if(O$('oar-chartTooltip')) O$('oar-chartTooltip').style.display='none';
  if(!hasData){ OAR.PROXY_CHART_LAYOUT=null; return; }
  const dpr=window.devicePixelRatio||1;
  const W=Math.max(320, canvas.clientWidth||600), H=260;
  canvas.width=Math.floor(W*dpr); canvas.height=Math.floor(H*dpr);
  const ctx=canvas.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,W,H);
  const pad={l:44,r:18,t:16,b:30}, innerW=W-pad.l-pad.r, innerH=H-pad.t-pad.b, n=series.length;
  const maxVol=Math.max(1,...series.map(p=>p.total||0));
  OAR.PROXY_CHART_LAYOUT={pad,innerW,innerH,n,series,W,H};
  ctx.strokeStyle='rgba(120,150,255,.15)'; ctx.lineWidth=1;
  for(let i=0;i<=4;i++){
    const y=pad.t+innerH*i/4;
    ctx.beginPath(); ctx.moveTo(pad.l,y); ctx.lineTo(W-pad.r,y); ctx.stroke();
  }
  ctx.fillStyle='rgba(148,163,184,.22)';
  const fg3='#94a3b8';
  series.forEach((p,i)=>{
    const x=pad.l+(n<=1?innerW/2:(innerW*i/(n-1)));
    const h=(p.total||0)/maxVol*innerH*0.28;
    const bw=Math.max(4,Math.min(14,innerW/Math.max(n,1)*0.55));
    ctx.fillRect(x-bw/2,pad.t+innerH-h,bw,h);
  });
  ctx.fillStyle=fg3; ctx.font='11px system-ui,sans-serif'; ctx.textAlign='center';
  const step=Math.max(1,Math.ceil(n/7));
  series.forEach((p,i)=>{
    if(i%step!==0&&i!==n-1) return;
    const x=pad.l+(n<=1?innerW/2:(innerW*i/(n-1)));
    ctx.fillText((p.date||'').slice(5),x,H-10);
  });
  ctx.textAlign='right';
  for(let i=0;i<=4;i++){
    const y=pad.t+innerH*i/4+4;
    ctx.fillText(String(100-Math.round(i*25)),pad.l-8,y);
  }
  lines.forEach((ln,li)=>{
    if(OAR.PROXY_CHART_HIDDEN.has(ln.id)) return;
    const col=chartColorForLine(ln,li), pts=ln.points||[];
    ctx.beginPath();
    let started=false;
    pts.forEach((p,i)=>{
      if(!(p.total>0)||p.rate==null){ started=false; return; }
      const x=pad.l+(n<=1?innerW/2:(innerW*i/(n-1)));
      const y=pad.t+innerH*(1-p.rate/100);
      if(!started){ ctx.moveTo(x,y); started=true; } else ctx.lineTo(x,y);
    });
    ctx.strokeStyle=col; ctx.lineWidth=ln.id==='overall'?2.5:2; ctx.stroke();
    pts.forEach((p,i)=>{
      if(!(p.total>0)||p.rate==null) return;
      const x=pad.l+(n<=1?innerW/2:(innerW*i/(n-1)));
      const y=pad.t+innerH*(1-p.rate/100);
      ctx.fillStyle=col;
      ctx.beginPath(); ctx.arc(x,y,3,0,Math.PI*2); ctx.fill();
    });
  });
  if(canvas.dataset.hoverBound!=='1'){
    canvas.dataset.hoverBound='1';
    canvas.addEventListener('mousemove',e=>{
      const layout=OAR.PROXY_CHART_LAYOUT; if(!layout) return;
      const rect=canvas.getBoundingClientRect(), x=e.clientX-rect.left;
      const {pad,innerW,n}=layout;
      if(x<pad.l||x>rect.width-pad.r){ oarShowTooltip(-1); return; }
      const ratio=(x-pad.l)/innerW;
      const idx=Math.max(0,Math.min(n-1,Math.round(ratio*(n-1))));
      oarShowTooltip(idx,e.clientX,e.clientY);
    });
    canvas.addEventListener('mouseleave',()=>oarShowTooltip(-1));
  }
}

/* ---------- 测活单条 ---------- */
function oarSetStBadge(el,label,state,status){
  const col=state===true?'#7ee787':(state===false?'#ff7b72':'#6b7280');
  const suf=(status===undefined||status===null)?(state===true?'✓':(state===false?'✗':'—')):status;
  el.style.color=col; el.style.borderColor=col+'66';
  el.textContent=`${label}: ${suf}`;
}
async function oarVerifyCombo(){
  const body={combo:O$('oar-verCombo').value.trim()||null,email:O$('oar-verEmail').value.trim()||null,
    refresh_token:O$('oar-verToken').value.trim()||null,proxy:O$('oar-verProxy').value.trim()||null,test_imap:O$('oar-verImap').checked};
  const btn=O$('btn-oar-verifyCombo');
  btn.disabled=true; O$('oar-verResult').style.display='block';
  const sum=O$('oar-vSummary'); sum.textContent='校验中…'; sum.style.color='#f59e0b'; setTxt('oar-vScope','');
  oarSetStBadge(O$('oar-vGraph'),'Graph 读信',null); oarSetStBadge(O$('oar-vRest'),'Outlook REST',null); oarSetStBadge(O$('oar-vImap'),'IMAP',null);
  try{
    const j=await oarJpost('/verify-combo',body);
    oarSetStBadge(O$('oar-vGraph'),'Graph 读信',j.graph?j.graph.ok:null,j.graph?j.graph.status:null);
    oarSetStBadge(O$('oar-vRest'),'Outlook REST',j.outlook_rest?j.outlook_rest.ok:null,j.outlook_rest?j.outlook_rest.status:null);
    if(j.imap&&j.imap.tested) oarSetStBadge(O$('oar-vImap'),'IMAP',j.imap.ok,j.imap.ok?'OK':(j.imap.stage||'✗'));
    else oarSetStBadge(O$('oar-vImap'),'IMAP',null,'未测');
    sum.textContent=j.summary||j.message||(j.ok?'可用':'不可用'); sum.style.color=j.ok?'#7ee787':'#ff7b72';
    let scope=j.granted_scope?('授予 scope: '+j.granted_scope):'';
    if(j.refresh_error) scope+=(scope?' | ':'')+'refresh 错误: '+j.refresh_error;
    if(j.imap&&j.imap.tested&&j.imap.detail) scope+=(scope?' | ':'')+'imap: '+j.imap.detail;
    setTxt('oar-vScope',scope);
    loadOarAccounts();
  }catch(e){ sum.textContent='校验失败: '+e.message; sum.style.color='#ff7b72'; }
  finally{ btn.disabled=false; }
}

/* ---------- 事件绑定 ---------- */
// 工具栏按钮「点谁谁亮」：点击瞬间高亮该按钮，同组其他按钮取消高亮
function oarMarkActive(btn){
  if(!btn || !btn.parentElement) return;
  btn.parentElement.querySelectorAll('button.active').forEach(b=>{
    if(b!==btn) b.classList.remove('active');
  });
  btn.classList.add('active');
}
window.oarMarkActive = oarMarkActive;
(function bindOarEvents(){
  if(window.__oarBound) return;
  window.__oarBound=true;

  document.querySelectorAll('#oar-tabs button').forEach(b=>b.addEventListener('click',()=>oarSwitchTab(b.dataset.tab)));

  /* 注册页 */
  O$('btn-oar-start').addEventListener('click', oarStart);
  if(O$('btn-oar-stop')) O$('btn-oar-stop').addEventListener('click', async ev=>{
    if(!OAR.CURRENT_JOB) return;
    ev.currentTarget.disabled=true;
    try{ await oarJpost('/jobs/'+OAR.CURRENT_JOB+'/cancel',{}); oarToast('已请求停止当前批次'); }
    catch(e){ oarToast('停止失败: '+e.message); ev.currentTarget.disabled=false; }
  });
  if(O$('oar-proxySource')) O$('oar-proxySource').addEventListener('change', e=>{
    const v=e.target.value;
    const mw=O$('oar-proxyManualWrap'); if(mw) mw.style.display = v==='manual'?'':'none';
    const hint=O$('oar-proxyHint'); if(hint) hint.textContent = {
      network:'将使用「网络出口」页配置的出口策略。',
      pool:'从 O 代理池（SQLite）轮换取代理，池空会提示补充。',
      manual:'仅使用下方手动填写的备用代理。',
      direct:'直连出口，不使用任何代理（成功率较低）。'}[v]||'';
  });
  O$('oar-logCopyBtn').addEventListener('click', ()=>{
    const c=O$('oar-console');
    const text=[...c.querySelectorAll('div')].map(el=>el.innerText).join('\n').trim();
    text?copyText(text):oarToast('暂无日志');
  });

  /* 账号池工具栏 */
  O$('oar-poolSearch').addEventListener('input', e=>{ OAR.FILTER.q=e.target.value; OAR.PAGE=1; oarRenderPool(); });
  O$('oar-filterView').addEventListener('change', e=>{ OAR.FILTER.view=e.target.value; OAR.PAGE=1; oarRenderPool(); });
  O$('oar-filterBatch').addEventListener('change', e=>{ OAR.FILTER.batch=e.target.value; OAR.PAGE=1; oarRenderPool(); });
  O$('oar-prevPage').addEventListener('click', ()=>{ if(OAR.PAGE>1){OAR.PAGE--; oarRenderPool();} });
  O$('oar-nextPage').addEventListener('click', ()=>{ OAR.PAGE++; oarRenderPool(); });
  O$('oar-ckAll').addEventListener('change', e=>{
    oarFilteredAccounts().slice((OAR.PAGE-1)*OAR.PAGE_SIZE,OAR.PAGE*OAR.PAGE_SIZE)
      .forEach(a=>{ e.target.checked?OAR.SELECTED.add(a.email):OAR.SELECTED.delete(a.email); });
    oarRenderPool();
  });
  O$('btn-oar-import').addEventListener('click', ev=>{ oarMarkActive(ev.currentTarget); oarImportModal(); });
  O$('btn-oar-export').addEventListener('click', ev=>{ oarMarkActive(ev.currentTarget); downloadExport(OAR.SELECTED.size?selectedEmails():null); });
  O$('btn-oar-copySel').addEventListener('click', ev=>{
    oarMarkActive(ev.currentTarget);
    if(!OAR.SELECTED.size){ oarToast('未选中账号'); return; }
    const lines=OAR.ACCOUNTS.filter(a=>OAR.SELECTED.has(a.email)).map(a=>a.combo).filter(Boolean);
    lines.length?copyText(lines.join('\n')):oarToast('选中项无 combo');
  });
  O$('btn-oar-verifySel').addEventListener('click', ev=>{ oarMarkActive(ev.currentTarget); runBatchVerify(OAR.SELECTED.size?selectedEmails():null, ev.currentTarget); });
  O$('btn-oar-rescueSel').addEventListener('click', ev=>{ oarMarkActive(ev.currentTarget); runBatchRescue(OAR.SELECTED.size?selectedEmails():null, ev.currentTarget); });
  O$('btn-oar-delSel').addEventListener('click', async ev=>{
    oarMarkActive(ev.currentTarget);
    if(!OAR.SELECTED.size){ oarToast('未选中账号'); return; }
    if(!confirm(`确定删除选中的 ${OAR.SELECTED.size} 个账号？`)) return;
    try{ await oarJpost('/accounts/delete',{emails:selectedEmails()}); OAR.SELECTED.clear(); loadOarAccounts(); oarToast('已删除'); }
    catch(e){ oarToast('删除失败: '+e.message); }
  });

  /* 代理池工具栏 */
  O$('btn-oar-pxAdd').addEventListener('click', ev=>{ oarMarkActive(ev.currentTarget); oarAddProxyModal(); });
  O$('btn-oar-pxRefresh').addEventListener('click', ev=>{ oarMarkActive(ev.currentTarget); loadOarProxyPool(); });
  O$('btn-oar-pxBackfill').addEventListener('click', async ev=>{
    const btn=ev.currentTarget, old=btn.textContent;
    oarMarkActive(btn);
    btn.disabled=true; btn.textContent='回填中…';
    try{
      const j=await oarJpost('/proxy-pool/backfill-countries',{});
      oarToast(`国家回填：更新 ${j.updated} 条，跳过 ${j.skipped}，仍为空 ${j.still_empty}`);
      loadOarProxyPool();
    }catch(e){ oarToast('回填失败: '+e.message); }
    finally{ btn.disabled=false; btn.textContent=old; }
  });
  O$('btn-oar-pxCheckAll').addEventListener('click', ev=>{ oarMarkActive(ev.currentTarget); oarCheckProxies(OAR.PROXY_ROWS.map(p=>p.id), ev.currentTarget); });
  O$('btn-oar-pxCheckSel').addEventListener('click', ev=>{ oarMarkActive(ev.currentTarget); oarCheckProxies([...OAR.PROXY_SEL], ev.currentTarget); });
  O$('btn-oar-pxDelSel').addEventListener('click', async ev=>{
    oarMarkActive(ev.currentTarget);
    if(!OAR.PROXY_SEL.size){ oarToast('未选中'); return; }
    if(!confirm(`确定删除选中的 ${OAR.PROXY_SEL.size} 条代理？`)) return;
    try{ await oarJpost('/proxy-pool/delete',{ids:[...OAR.PROXY_SEL]}); OAR.PROXY_SEL.clear(); loadOarProxyPool(); oarToast('已删除'); }
    catch(e){ oarToast('删除失败: '+e.message); }
  });
  O$('btn-oar-pxSaveSettings').addEventListener('click', async ()=>{
    try{
      await oarJpost('/proxy-pool/settings',{
        strategy:O$('oar-proxyStrategy').value,
        require_healthy:O$('oar-proxyRequireHealthy').checked,
        sticky_per_account:O$('oar-proxySticky').checked,
      });
      oarToast('设置已保存'); loadOarProxyPool();
    }catch(e){ oarToast('保存失败'); }
  });
  O$('oar-proxyFilterProvider').addEventListener('change', loadOarProxyPool);
  document.addEventListener('click', e=>{
    const one=e.target.closest('.oar-px-check-one');
    if(one){ oarCheckProxies([one.dataset.id]); return; }
    const ub=e.target.closest('.oar-px-unbind');
    if(ub){ oarJpost('/proxy-pool/unbind',{emails:[ub.dataset.email]}).then(()=>loadOarProxyPool()).catch(er=>oarToast(er.message)); return; }
    const en=e.target.closest('.oar-px-en');
    if(en){ oarApi('/proxy-pool/'+en.dataset.id,{method:'PUT',body:JSON.stringify({enabled:en.checked})}).catch(()=>loadOarProxyPool()); }
  });
  O$('oar-pxCkAll').addEventListener('change', e=>{
    const vis=OAR.PROXY_ROWS;
    vis.forEach(p=>{ e.target.checked?OAR.PROXY_SEL.add(p.id):OAR.PROXY_SEL.delete(p.id); });
    oarRenderProxyTable();
  });

  /* 统计筛选 */
  ['oar-statsGroupBy','oar-statsFilterProvider','oar-statsFilterCountry','oar-statsFilterRegCountry','oar-statsDays']
    .forEach(id=>O$(id).addEventListener('change', ()=>loadOarStatsSafe()));
  O$('btn-oar-statsRefresh').addEventListener('click', loadOarStatsSafe);
  window.addEventListener('resize', ()=>{
    if(OAR.tab==='proxy-stats'&&OAR.PROXY_STATS_TS) oarRenderRateChart(OAR.PROXY_STATS_TS);
  });

  /* 测活 & 保活 */
  O$('btn-oar-verifyCombo').addEventListener('click', ev=>{ oarMarkActive(ev.currentTarget); oarVerifyCombo(ev); });
  O$('btn-oar-imapEnable').addEventListener('click', async ev=>{
    oarMarkActive(ev.currentTarget);
    try{ const j=await oarJpost('/imap-enable'); opsMsg('warn',j.message||'IMAP 未实现'); }
    catch(e){ opsMsg('err','失败: '+e.message); }
  });
  O$('btn-oar-keepaliveAll').addEventListener('click', ev=>{ oarMarkActive(ev.currentTarget); runKeepalive(null); });
  O$('btn-oar-replenish').addEventListener('click', async (ev)=>{
    const btn=ev.currentTarget;
    oarMarkActive(btn);
    btn.disabled=true; btn.textContent='回补中…';
    oarToast('开始回补收码池'); opsMsg('warn','回补中…（会 probe_token 校验 graph=200）');
    try{
      const j=await oarJpost('/replenish',{emails:OAR.SELECTED.size?selectedEmails():null});
      if(j.implemented===false){ opsMsg('err',j.message||'不可用'); oarToast(j.message||'不可用'); return; }
      const msg=`回补完成：新增 ${j.added}，重复 ${j.duplicate}，跳过 ${j.skipped}`;
      opsMsg('ok', msg+`｜池文件 ${j.pool}`); oarToast(msg);
    }catch(e){ opsMsg('err','回补失败: '+e.message); oarToast('回补失败: '+e.message); }
    finally{ btn.disabled=false; btn.textContent='回补收码池'; }
  });
  O$('btn-oar-dbBackup').addEventListener('click', async (ev)=>{
    const btn=ev.currentTarget;
    oarMarkActive(btn);
    btn.disabled=true; btn.textContent='备份中…';
    opsMsg('warn','备份中…');
    try{
      const j=await oarJpost('/database/backup',{});
      if(!j.ok) throw new Error(j.detail||'backup failed');
      opsMsg('ok','已备份: '+j.path); oarToast('数据库已备份');
    }catch(e){ opsMsg('err','备份失败: '+e.message); oarToast('备份失败: '+e.message); }
    finally{ btn.disabled=false; btn.textContent='备份数据库'; }
  });
})();
function loadOarStatsSafe(){ loadOarStatsSilent(); }
async function loadOarStatsSilent(){ try{ await loadOarProxyStats(); }catch(_){ } }

/* ---------- 页面初始化入口 ---------- */
let __oarInitDone=false;
async function initOarView(){
  oarSwitchTab(OAR.tab || 'overview');
  if(__oarInitDone) return;
  __oarInitDone=true;
  loadOarJobs();
}
