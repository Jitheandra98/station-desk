/* ══════════════════════════════════════════════════════════════════
   STATION DESK — application shell
   ══════════════════════════════════════════════════════════════════ */
const API = '';
const TFS  = ['5m','15m','30m','1h','4h'];
const SYMS = [['XAUUSD','gold'],['BTCUSD','btc'],['MGC','mgc'],['ETHUSD','eth']];

/* Every knob the engine obeys, grouped the way a trader thinks about them.
   `note` is what the setting actually costs you if you get it wrong.        */
const CFG_SCHEMA = [
 ['The ★ gate', [
   ['require_sweep','bool','Require a sweep before the turn','the finding the whole doctrine rests on'],
   ['sweep_window','int','Sweep window (bars)','how recent the liquidity grab must be'],
   ['require_engulf','bool','Turn bar must engulf',''],
   ['require_route_clear','bool','Route must be clear','no unfinished business behind the station'],
   ['require_destination','bool','Refuse trades with no destination','stand aside beats guessing'],
 ]],
 ['The stop', [
   ['stop_mode','select:sweep|zone|atr|structure','Stop placement','where you say the idea is wrong'],
   ['tight_pad_atr','float','Pad beyond the sweep wick (ATR)',''],
   ['stop_pad_atr','float','Pad beyond the zone edge (ATR)',''],
   ['stop_atr_mult','float','Fixed ATR stop distance',''],
   ['stop_struct_lookback','int','Structural stop lookback (bars)',''],
 ]],
 ['The target', [
   ['target_mode','select:far|nearest|rr|atr|trail','Target selection','destination-led, fixed, or trailed'],
   ['tp_rr','float','Reward:risk for rr mode',''],
   ['tp_atr','float','Distance for atr mode (ATR)',''],
   ['min_rr','float','Minimum reward:risk to board','0 turns the floor off'],
   ['dest_max_atr','float','Max reach to a destination (ATR)',''],
   ['breakeven_at_r','float','Move stop to entry at +R','0 = off'],
   ['partial_at_r','float','Bank half at +R','0 = off'],
 ]],
 ['Stations', [
   ['score_min','int','Minimum station score (1–5)','size+2 · OB+1 · first touch+1 · trend+1'],
   ['max_touches','int','Touches before a station dies',''],
   ['anchor_atr','float','Turn must land within (ATR) of the station',''],
   ['zone_min_atr','float','Smallest zone worth drawing (ATR)',''],
   ['zone_max_atr','float','Largest zone worth drawing (ATR)',''],
   ['size_law_atr','float','"Big zone" threshold (ATR)',''],
   ['pivot_len','int','Structure pivot length',''],
 ]],
 ['Contracts', [
   ['position_enabled','bool','POSITION — turn at a station',''],
   ['position_live','bool','  └ moves capital',''],
   ['pool_ride_enabled','bool','POOL-RIDE — ferry to resting liquidity',''],
   ['pool_ride_live','bool','  └ moves capital',''],
   ['reaction_enabled','bool','REACTION — the reflex trade',''],
   ['reaction_live','bool','  └ moves capital','LIVE by your order'],
   ['breaker_enabled','bool','BREAKER — door two',''],
   ['breaker_live','bool','  └ moves capital',''],
 ]],
 ['Clocks & costs', [
   ['pos_clock','int','Position clock (bars)','how long a thesis gets'],
   ['rx_clock','int','Reaction clock (bars)',''],
   ['rx_target_atr','float','Reaction target (ATR)',''],
   ['cost_pts','float','Cost per trade (points)','spread + commission, charged every time'],
   ['min_risk_atr','float','Minimum risk distance (ATR)','stops R inflation on tiny stops'],
 ]],
 ['Risk governors', [
   ['risk_pct','float','Risk per trade (%)',''],
   ['day_budget_r','float','Daily loss budget (R)',''],
   ['week_budget_r','float','Weekly loss halt (R)',''],
   ['dd_mode_r','float','Drawdown mode trigger (R)',''],
   ['susp_ci_floor','float','Kill-switch win-rate floor',''],
   ['susp_net_r','float','Kill-switch rolling-30 net R',''],
 ]],
 ['Sessions', [
   ['asia_end','int','Asia ends (UTC hour)',''],
   ['london_end','int','London ends (UTC hour)',''],
   ['ny_end','int','NY ends (UTC hour)',''],
 ]],
];
const state = { symbol:'XAUUSD', timeframe:'30m', snap:null, account:null, side:'BUY', auto:false, cfg:null };
const $  = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const fmt  = (n,d=2) => n==null||Number.isNaN(n) ? '—' : Number(n).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const money = n => n==null ? '—' : (n<0?'-':'')+'$'+Math.abs(n).toLocaleString('en-US',{maximumFractionDigits:0});
const clock = ts => new Date(ts*1000).toISOString().slice(11,16);

async function api(path, opts){
  const res = await fetch(API+path, {headers:{'Content-Type':'application/json'}, ...opts});
  if (!res.ok) throw new Error((await res.json().catch(()=>({detail:res.statusText}))).detail || 'request failed');
  return res.json();
}

/* ── chart ─────────────────────────────────────────────────────── */
const chart = new StationChart($('#chart'));

/* ── boot ──────────────────────────────────────────────────────── */
function buildSymbols(){
  $('#symSwitch').innerHTML = SYMS.map(([s,l]) =>
    `<button data-sym="${s}" class="${s===state.symbol?'on':''}">${l}</button>`).join('');
  $$('#symSwitch button').forEach(b => b.onclick = () => {
    state.symbol = b.dataset.sym;
    $$('#symSwitch button').forEach(x => x.classList.toggle('on', x===b));
    $('.label').innerHTML = `${state.symbol} · <span id="tfLabel">${state.timeframe}</span>`;
    refresh(true);
  });
}

function buildSwitcher(){
  $('#tfSwitch').innerHTML = TFS.map(tf =>
    `<button data-tf="${tf}" class="${tf===state.timeframe?'on':''}">${tf}</button>`).join('');
  $$('#tfSwitch button').forEach(b => b.onclick = () => {
    state.timeframe = b.dataset.tf;
    $$('#tfSwitch button').forEach(x => x.classList.toggle('on', x===b));
    $('#tfLabel').textContent = state.timeframe;
    refresh(true);
  });
}

async function refresh(rebuild=false){
  try{
    const [snap, candles, quote, acct, pos] = await Promise.all([
      api(`/api/engine/snapshot?symbol=${state.symbol}&timeframe=${state.timeframe}${rebuild?'&rebuild=true':''}`),
      api(`/api/market/candles?symbol=${state.symbol}&timeframe=${state.timeframe}&limit=600`),
      api(`/api/market/quote?symbol=${state.symbol}`),
      api('/api/broker/account'),
      api('/api/broker/positions'),
    ]);
    state.snap = snap; state.account = acct;
    renderQuote(quote, snap);
    renderRead(snap.read, snap);
    chart.setData({ bars:candles.bars, zones:snap.zones, pools:snap.pools,
                    signals:snap.signals, trades:snap.open_trades });
    renderRoute(snap);
    renderAccount(acct, pos);
    renderCourt(snap.court);
    renderTape(snap.signals);
    $('#sourceText').textContent = `${candles.source} · ${state.timeframe}`;
    $('#hashFoot').textContent = `calibration ${snap.config_hash}`;
  }catch(e){
    $('#sourceText').textContent = 'reconnecting…';
  }
}

/* ── renderers ─────────────────────────────────────────────────── */
function renderQuote(q, snap){
  $('#price').textContent = fmt(q.price, 2);
  const d = $('#delta'), up = q.change >= 0;
  d.textContent = `${up?'+':''}${fmt(q.change,2)} (${up?'+':''}${fmt(q.change_pct,2)}%)`;
  d.className = 'delta ' + (up?'up':'down');
  $('#dayHigh').textContent = fmt(q.day_high,2);
  $('#dayLow').textContent  = fmt(q.day_low,2);
  $('#atr').textContent     = snap.ready ? fmt(snap.atr,2) : '—';
  document.title = `${fmt(q.price,2)} · STATION`;
}

function renderRead(read, snap){
  if (!read) return;
  const chip = $('#readState');
  chip.textContent = read.state;
  chip.className = 'state-chip' + (read.state==='IN-TRADE'?' trade':read.state==='HALT'?' halt':'');
  $('#sessionChip').textContent = `${read.session} · ${read.behaviour}`;
  $('#readHeadline').textContent = read.headline;
  $('#readDetail').textContent = read.detail;
  $('#routeUp').textContent   = read.next_up   ? `${fmt(read.next_up.price,2)} ${read.next_up.kind}`   : 'clear';
  $('#routeDown').textContent = read.next_down ? `${fmt(read.next_down.price,2)} ${read.next_down.kind}` : 'clear';
}

function renderRoute(snap){
  const r = snap.route, px = snap.price, atr = snap.atr || 1;
  const rows = [
    ...r.up.slice(0,4).map(d => ({...d, dir:1})).reverse(),
    ...r.down.slice(0,4).map(d => ({...d, dir:-1})),
  ];
  $('#routeList').innerHTML = rows.length ? rows.map(d => {
    const dist = Math.abs(d.price - px);
    return `<div class="route-item">
      <span class="dir">${d.dir===1?'▲':'▼'}</span>
      <span class="kind">${d.kind}</span>
      <span class="px">${fmt(d.price,2)}</span>
      <span class="dist">${fmt(dist,1)} · ${fmt(dist/atr,1)}A</span>
    </div>`;
  }).join('') : `<div class="empty">no destination within reach — that is a reason to stand aside</div>`;
  $('#poolsAbove').textContent = `▲ ${r.pools_above} pools resting above`;
  $('#poolsBelow').textContent = `${r.pools_below} below ▼`;
}

function renderAccount(a, pos){
  const netUp = a.net_liq >= a.start_equity;
  $('#accountGrid').innerHTML = `
    <div class="stat"><span>net liq</span><b class="${netUp?'up':'down'}">${money(a.net_liq)}</b></div>
    <div class="stat"><span>return</span><b class="${netUp?'up':'down'}">${a.return_pct>0?'+':''}${fmt(a.return_pct,2)}%</b></div>
    <div class="stat"><span>open</span><b>${a.open_positions}</b></div>
    <div class="stat"><span>win rate</span><b>${a.win_rate==null?'—':fmt(a.win_rate,0)+'%'}</b></div>`;
  const list = pos.positions || [];
  $('#positions').innerHTML = list.length ? list.map(p => `
    <div class="pos">
      <span class="side ${p.side}">${p.side}</span>
      <span>${fmt(p.qty,2)} @ ${fmt(p.entry,2)}</span>
      <span class="pnl ${p.unrealised>=0?'up':'down'}" style="color:${p.unrealised>=0?'var(--up)':'var(--down)'}">
        ${p.unrealised>=0?'+':''}${money(p.unrealised)}${p.r_multiple!=null?` · ${p.r_multiple>0?'+':''}${fmt(p.r_multiple,2)}R`:''}
      </span>
      <button data-close="${p.id}">close</button>
    </div>`).join('') : `<div class="empty">flat — waiting is a position</div>`;
  $$('#positions button[data-close]').forEach(b => b.onclick = async () => {
    await api(`/api/broker/close/${b.dataset.close}`, {method:'POST'});
    refresh();
  });
}

function renderCourt(c){
  if (!c) return;
  $('#courtSummary').innerHTML = `
    <div><span>trades</span><b>${c.n}</b></div>
    <div><span>win rate</span><b>${c.win_rate==null?'—':fmt(c.win_rate,1)+'%'}</b></div>
    <div><span>avg R</span><b style="color:${(c.avg_r||0)>=0?'var(--up)':'var(--down)'}">${c.avg_r==null?'—':(c.avg_r>0?'+':'')+fmt(c.avg_r,3)}</b></div>
    <div><span>net R</span><b style="color:${(c.net_r||0)>=0?'var(--up)':'var(--down)'}">${(c.net_r>0?'+':'')+fmt(c.net_r,1)}</b></div>
    <div><span>max DD</span><b>${fmt(c.max_dd_r,1)}R</b></div>`;
  drawEquity($('#equity'), c.equity_curve);
  const rows = Object.entries(c.buckets||{}).filter(([,b]) => b.n > 0 || b.live);
  $('#courtTable').innerHTML = `
    <thead><tr><th>pattern</th><th>n</th><th>win</th><th>avg R</th><th>net R</th><th>PF</th><th>verdict</th></tr></thead>
    <tbody>${rows.map(([k,b]) => `
      <tr>
        <td class="name">${k}<span class="tag ${b.live?'live':'paper'}">${b.live?'live':'paper'}</span></td>
        <td>${b.n}</td>
        <td>${b.win_rate==null?'—':fmt(b.win_rate,0)+'%'}</td>
        <td style="color:${(b.avg_r||0)>=0?'var(--up)':'var(--down)'}">${b.avg_r==null?'—':(b.avg_r>0?'+':'')+fmt(b.avg_r,3)}</td>
        <td style="color:${(b.net_r||0)>=0?'var(--up)':'var(--down)'}">${(b.net_r>0?'+':'')+fmt(b.net_r,1)}</td>
        <td>${b.profit_factor==null?'—':fmt(b.profit_factor,2)}</td>
        <td class="verdict ${/STRUCTURE/.test(b.verdict)?'structure':/BETA/.test(b.verdict)?'beta':''}">${b.verdict}</td>
      </tr>`).join('')}</tbody>`;
  const mini = obj => Object.entries(obj||{}).length ? Object.entries(obj).map(([k,b]) => `
      <div class="mini-row"><b>${k}</b><span class="n">n ${b.n} · ${b.win_rate==null?'—':fmt(b.win_rate,0)+'%'}</span>
      <span class="r" style="color:${(b.avg_r||0)>=0?'var(--up)':'var(--down)'}">${(b.avg_r>0?'+':'')+fmt(b.avg_r,3)}R</span></div>`).join('')
    : `<div class="empty">collecting</div>`;
  $('#sessTable').innerHTML = mini(c.sessions);
  $('#tgtTable').innerHTML  = mini(c.targets);
  $('#caveat').textContent =
    'Single instrument, one history, small samples. These numbers are a measurement of this engine on this tape — not a promise about the next one. Every trade here fills at the next bar’s open, checks its stop before its target, records timeouts, and pays costs.';
}

function renderTape(signals){
  const rows = (signals||[]).slice(-60).reverse();
  $('#tape').innerHTML = rows.length ? rows.map(s => `
    <div class="tape-row ${s.kind}">
      <span class="t">${clock(s.ts)}</span>
      <span class="k">${s.kind}</span>
      <span>${s.title}</span>
      <span class="d">${s.detail||''}</span>
    </div>`).join('') : `<div class="empty">the tape is quiet</div>`;
}

/* ── ticket ────────────────────────────────────────────────────── */
function recalc(){
  const px = state.snap?.price;
  const stop = parseFloat($('#inStop').value);
  const risk = parseFloat($('#inRisk').value) || 0.5;
  const eq = state.account?.equity;
  if (!px || !stop || !eq){ $('#ticketCalc').textContent = 'size —'; return; }
  const pts = Math.abs(px - stop);
  const pv = state.account.point_value || 10;
  const qty = pts > 0 ? Math.min(50, (eq*risk/100)/(pts*pv)) : 0;
  const tgt = parseFloat($('#inTarget').value);
  const rr = tgt && pts ? Math.abs(tgt-px)/pts : null;
  $('#ticketCalc').innerHTML =
    `size <b>${qty.toFixed(2)}</b> · risking <b>${money(eq*risk/100)}</b> over <b>${pts.toFixed(2)}</b> pts` +
    (rr ? `<br>reward:risk <b style="color:${rr>=2?'var(--up)':'var(--amber)'}">${rr.toFixed(2)}R</b>${rr<2?' — below the 2R floor':''}` : '');
}

async function placeOrder(){
  const msg = $('#ticketMsg');
  try{
    const body = {
      symbol: state.symbol, side: state.side,
      stop_loss: parseFloat($('#inStop').value) || null,
      take_profit: parseFloat($('#inTarget').value) || null,
      risk_pct: parseFloat($('#inRisk').value) || 0.5,
      note: 'manual ticket',
    };
    if (!body.stop_loss){ msg.textContent = 'a stop is required — size comes from risk'; msg.className='ticket-msg err'; return; }
    const o = await api('/api/broker/order', {method:'POST', body:JSON.stringify(body)});
    if (o.status === 'REJECTED'){ msg.textContent = o.reject_reason; msg.className = 'ticket-msg err'; }
    else { msg.textContent = `filled ${o.qty} @ ${fmt(o.filled_price,2)}`; msg.className = 'ticket-msg ok'; }
    refresh();
  }catch(e){ msg.textContent = e.message; msg.className = 'ticket-msg err'; }
}

async function useEngine(){
  const msg = $('#ticketMsg');
  const s = await api(`/api/broker/suggest?symbol=${state.symbol}&timeframe=${state.timeframe}`);
  if (s.available === false){ msg.textContent = s.reason || 'no appointment right now'; msg.className='ticket-msg'; return; }
  state.side = s.side;
  $$('#sideSeg button').forEach(b => b.classList.toggle('on', b.dataset.side === s.side));
  $('#inStop').value = s.stop; $('#inTarget').value = s.target ?? '';
  recalc();
  msg.textContent = `engine: ${s.side} · risk ${money(s.risk_cash)}`; msg.className='ticket-msg ok';
}

/* ── wiring ────────────────────────────────────────────────────── */
function wire(){
  $$('#sideSeg button').forEach(b => b.onclick = () => {
    state.side = b.dataset.side;
    $$('#sideSeg button').forEach(x => x.classList.toggle('on', x===b));
  });
  ['#inStop','#inTarget','#inRisk'].forEach(s => $(s).addEventListener('input', recalc));
  $('#btnPlace').onclick = placeOrder;
  $('#btnSuggest').onclick = useEngine;
  $('#btnReset').onclick = async () => {
    await api('/api/broker/reset?equity=100000', {method:'POST'});
    refresh();
  };
  $('#autoBtn').onclick = async () => {
    state.auto = !state.auto;
    await api('/api/broker/auto', {method:'POST', body:JSON.stringify({enabled:state.auto})});
    $('#autoState').textContent = state.auto ? 'ARMED' : 'OFF';
    $('#autoBtn').classList.toggle('armed', state.auto);
  };
  $$('.chip[data-layer]').forEach(c => {
    c.classList.add('on');
    c.onclick = () => c.classList.toggle('on', chart.toggle(c.dataset.layer));
  });
  // settings drawer
  $('#cfgBtn').onclick = openDrawer;
  const shut = () => { $('#drawer').hidden = true; $('#scrim').hidden = true; };
  $('#drawerClose').onclick = shut;
  $('#scrim').onclick = shut;
  $('#cfgApply').onclick = applyConfig;
  $('#cfgReset').onclick = async () => {
    await api('/api/engine/config', {method:'POST', body:JSON.stringify({values:{}})});
    shut(); refresh(true);
  };
  // the lab
  $('#labBtn').onclick = () => {
    const lab = $('#lab');
    lab.hidden = !lab.hidden;
    if (!lab.hidden) lab.scrollIntoView({behavior:'smooth', block:'start'});
  };
}

/* ══ settings drawer ══════════════════════════════════════════════ */
async function openDrawer(){
  state.cfg = await api('/api/engine/config');
  const presets = (await api('/api/engine/presets')).presets;
  $('#presetRow').innerHTML = presets.map(p =>
    `<button class="preset" data-preset='${JSON.stringify(p.values)}'>
       <b>${p.name}</b><span>${p.note}</span></button>`).join('');
  $$('.preset').forEach(b => b.onclick = () => {
    const vals = JSON.parse(b.dataset.preset);
    Object.entries(vals).forEach(([k,v]) => {
      const el = document.querySelector(`[data-cfg="${k}"]`);
      if (!el) return;
      if (el.type === 'checkbox') el.checked = !!v; else el.value = v;
    });
  });
  $('#cfgForm').innerHTML = CFG_SCHEMA.map(([group, rows]) => `
    <div class="cfg-group"><h4>${group}</h4>
      ${rows.map(([key,type,label,note]) => {
        const v = state.cfg[key];
        let field;
        if (type === 'bool')
          field = `<span class="switch"><input type="checkbox" data-cfg="${key}" ${v?'checked':''}><i></i></span>`;
        else if (type.startsWith('select:'))
          field = `<select data-cfg="${key}">${type.slice(7).split('|').map(o =>
            `<option ${o===v?'selected':''}>${o}</option>`).join('')}</select>`;
        else
          field = `<input type="number" step="${type==='int'?1:0.05}" data-cfg="${key}" value="${v}">`;
        return `<div class="cfg-row"><label>${label}${note?`<span class="note">${note}</span>`:''}</label>${field}</div>`;
      }).join('')}
    </div>`).join('');
  $('#cfgHash').textContent = `hash ${state.cfg.hash}`;
  $('#drawer').hidden = false; $('#scrim').hidden = false;
}

async function applyConfig(){
  const values = {};
  $$('[data-cfg]').forEach(el => {
    const k = el.dataset.cfg;
    values[k] = el.type === 'checkbox' ? el.checked
              : el.tagName === 'SELECT' ? el.value
              : parseFloat(el.value);
  });
  const cfg = await api('/api/engine/config', {method:'POST', body:JSON.stringify({values})});
  $('#cfgHash').textContent = `hash ${cfg.hash}`;
  await refresh(true);
}

/* ══ the lab ══════════════════════════════════════════════════════ */
const LAB_PARAMS = ['stop_mode','target_mode','min_rr','tp_rr','tight_pad_atr','sweep_window',
                    'score_min','max_touches','pos_clock','require_sweep','anchor_atr','cost_pts'];
const LAB_DEFAULTS = {
  stop_mode:'sweep, zone, atr, structure', target_mode:'far, nearest, rr, atr, trail',
  min_rr:'0, 1, 1.5, 2, 2.5, 3, 4', tp_rr:'1.5, 2, 3, 4, 5',
  tight_pad_atr:'0.1, 0.25, 0.5, 0.75, 1', sweep_window:'4, 8, 12, 20, 30',
  score_min:'1, 2, 3, 4, 5', max_touches:'1, 2, 3', pos_clock:'16, 32, 48, 64, 96',
  require_sweep:'true, false', anchor_atr:'0.5, 1, 1.6, 2.5, 4', cost_pts:'0, 0.5, 1, 2',
};
function buildLab(){
  $('#labParam').innerHTML = LAB_PARAMS.map(p => `<option>${p}</option>`).join('');
  $('#labValues').value = LAB_DEFAULTS['stop_mode'];
  $('#labParam').onchange = e => { $('#labValues').value = LAB_DEFAULTS[e.target.value] || ''; };
  $('#labRun').onclick = runSweep;
}
async function runSweep(){
  const param = $('#labParam').value;
  const raw = $('#labValues').value.split(',').map(s => s.trim()).filter(Boolean);
  const values = raw.map(v => v === 'true' ? true : v === 'false' ? false
                       : isNaN(parseFloat(v)) ? v : parseFloat(v));
  $('#labTable').innerHTML = `<tbody><tr><td>running ${values.length} full backtests…</td></tr></tbody>`;
  const d = await api('/api/engine/sweep', {method:'POST', body:JSON.stringify(
    {symbol:state.symbol, timeframe:state.timeframe, limit:1200, param, values})});
  $('#labTable').innerHTML = `
    <thead><tr><th>${param}</th><th>n</th><th>win</th><th>avg R</th><th>net R</th><th>PF</th><th>max DD</th></tr></thead>
    <tbody>${d.rows.map(r => `
      <tr class="${d.best && r.value===d.best.value ? 'best':''}">
        <td class="name">${r.value}</td><td>${r.n}</td>
        <td>${r.win_rate==null?'—':fmt(r.win_rate,0)+'%'}</td>
        <td style="color:${(r.avg_r||0)>=0?'var(--up)':'var(--down)'}">${r.avg_r==null?'—':(r.avg_r>0?'+':'')+fmt(r.avg_r,3)}</td>
        <td style="color:${(r.net_r||0)>=0?'var(--up)':'var(--down)'}">${(r.net_r>0?'+':'')+fmt(r.net_r,1)}</td>
        <td>${r.profit_factor==null?'—':fmt(r.profit_factor,2)}</td>
        <td>${fmt(r.max_dd_r,1)}R</td>
      </tr>`).join('')}</tbody>`;
  $('#labCaveat').textContent = `${d.bars} bars of ${d.symbol} ${d.timeframe}. ${d.caveat}`;
}

function connect(){
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.quote) renderQuote(m.quote, state.snap || {ready:false});
    if (m.read) renderRead(m.read, state.snap);
    if (m.account) { state.account = m.account; }
    if (m.signals?.length) refresh();
  };
  ws.onclose = () => setTimeout(connect, 4000);
}

buildSymbols(); buildSwitcher(); buildLab(); wire(); refresh(); connect();
setInterval(() => refresh(), 30000);
