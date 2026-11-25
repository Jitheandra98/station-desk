/* ══════════════════════════════════════════════════════════════════
   StationChart — a purpose-built canvas tape.
   No libraries: candles, stations, pools, sweeps, appointments and the
   live trade, drawn exactly the way the desk reads them.
   ══════════════════════════════════════════════════════════════════ */
class StationChart {
  constructor(canvas){
    this.c = canvas;
    this.ctx = canvas.getContext('2d');
    this.bars = []; this.zones = []; this.pools = []; this.signals = []; this.trades = [];
    this.layers = { zones:true, pools:true, signals:true, trades:true };
    this.view = { offset:0, count:150 };   // offset = bars back from the right edge
    this.mouse = null;
    this.pad = { l:14, r:78, t:18, b:30 };
    this._bind();
    this._resize();
    window.addEventListener('resize', () => { this._resize(); this.draw(); });
  }

  setData({bars, zones, pools, signals, trades}){
    if (bars) this.bars = bars;
    if (zones) this.zones = zones;
    if (pools) this.pools = pools;
    if (signals) this.signals = signals;
    if (trades) this.trades = trades;
    this.draw();
  }
  toggle(layer){ this.layers[layer] = !this.layers[layer]; this.draw(); return this.layers[layer]; }

  /* ── geometry ─────────────────────────────────────────────────── */
  _resize(){
    const r = this.c.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    this.c.width = r.width * dpr; this.c.height = r.height * dpr;
    this.ctx.setTransform(dpr,0,0,dpr,0,0);
    this.w = r.width; this.h = r.height;
  }
  _slice(){
    const n = this.bars.length;
    const end = Math.max(10, n - this.view.offset);
    const start = Math.max(0, end - this.view.count);
    return this.bars.slice(start, end).map((b,i) => ({...b, _i:start+i}));
  }
  _scales(vis){
    let hi = -Infinity, lo = Infinity;
    vis.forEach(b => { hi = Math.max(hi,b.high); lo = Math.min(lo,b.low); });
    // let the live stations breathe inside the frame
    if (this.layers.zones) this.zones.forEach(z => {
      if (z.top < hi*1.02 && z.bot > lo*0.98){ hi = Math.max(hi,z.top); lo = Math.min(lo,z.bot); }
    });
    const span = (hi-lo) || 1, padY = span*0.09;
    hi += padY; lo -= padY;
    const plotW = this.w - this.pad.l - this.pad.r;
    const plotH = this.h - this.pad.t - this.pad.b;
    return {
      hi, lo,
      x: i => this.pad.l + ((i - vis[0]._i) / Math.max(vis.length,1)) * plotW,
      y: p => this.pad.t + (hi - p) / (hi - lo) * plotH,
      bw: Math.max(1.4, (plotW / Math.max(vis.length,1)) * 0.62),
      plotW, plotH
    };
  }

  /* ── paint ────────────────────────────────────────────────────── */
  draw(){
    const ctx = this.ctx;
    ctx.clearRect(0,0,this.w,this.h);
    if (!this.bars.length){ this._placeholder(); return; }
    const vis = this._slice(); if (!vis.length) return;
    const s = this._scales(vis);
    this._grid(s, vis);
    if (this.layers.zones)   this._zones(s, vis);
    if (this.layers.pools)   this._pools(s, vis);
    if (this.layers.trades)  this._trades(s, vis);
    this._candles(s, vis);
    if (this.layers.signals) this._signals(s, vis);
    this._lastPrice(s, vis);
    if (this.mouse) this._crosshair(s, vis);
  }

  _placeholder(){
    const ctx = this.ctx;
    ctx.fillStyle = 'rgba(255,255,255,.22)';
    ctx.font = '13px -apple-system,system-ui,sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('loading the tape…', this.w/2, this.h/2);
  }

  _grid(s, vis){
    const ctx = this.ctx;
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,.045)'; ctx.lineWidth = 1;
    ctx.fillStyle = 'rgba(242,244,248,.34)';
    ctx.font = '10.5px "SF Mono",ui-monospace,monospace'; ctx.textAlign = 'left';
    const steps = 6;
    for (let k=0;k<=steps;k++){
      const p = s.lo + (s.hi-s.lo)*k/steps, y = Math.round(s.y(p))+0.5;
      ctx.beginPath(); ctx.moveTo(this.pad.l,y); ctx.lineTo(this.w-this.pad.r,y); ctx.stroke();
      ctx.fillText(p.toFixed(1), this.w-this.pad.r+9, y+3.5);
    }
    // session shading: the day's rhythm, drawn faintly behind price
    let prev = null;
    vis.forEach(b => {
      const d = new Date(b.ts*1000), h = d.getUTCHours();
      const tint = h<7 ? 'rgba(90,200,250,.030)' : h<12 ? 'rgba(255,90,106,.026)'
                 : h<17 ? 'rgba(167,139,250,.030)' : 'rgba(255,255,255,.012)';
      const x = s.x(b._i);
      ctx.fillStyle = tint;
      ctx.fillRect(x - s.bw*0.6, this.pad.t, s.bw*1.65, s.plotH);
      if (h !== prev && (h===0||h===7||h===12||h===17)){
        ctx.strokeStyle='rgba(255,255,255,.07)';
        ctx.beginPath(); ctx.moveTo(x,this.pad.t); ctx.lineTo(x,this.h-this.pad.b); ctx.stroke();
        ctx.fillStyle='rgba(242,244,248,.3)'; ctx.font='9.5px -apple-system,system-ui,sans-serif';
        ctx.fillText(h===0?'ASIA':h===7?'LONDON':h===12?'NY':'LATE', x+4, this.pad.t+11);
        prev = h;
      }
    });
    ctx.restore();
  }

  _zones(s, vis){
    const ctx = this.ctx, x0 = this.pad.l, x1 = this.w - this.pad.r;
    this.zones.forEach(z => {
      const yTop = s.y(z.top), yBot = s.y(z.bot);
      if (yBot < this.pad.t-40 || yTop > this.h-this.pad.b+40) return;
      const demand = z.dir === 1;
      const alive = z.state <= 1, star = z.eligible;
      const base = demand ? '61,220,151' : '255,90,106';
      const a = star ? .13 : alive ? .07 : .035;
      const xs = Math.max(x0, s.x(z.born));
      ctx.fillStyle = `rgba(${base},${a})`;
      ctx.fillRect(xs, yTop, x1-xs, Math.max(1.5, yBot-yTop));
      ctx.strokeStyle = `rgba(${base},${star?.5:.2})`;
      ctx.lineWidth = star ? 1.35 : 1;
      ctx.setLineDash(z.state === 3 ? [3,3] : star ? [] : [5,4]);
      ctx.strokeRect(xs, yTop, x1-xs, Math.max(1.5, yBot-yTop));
      ctx.setLineDash([]);
      if (star && (yBot-yTop) > 11){
        ctx.fillStyle = `rgba(${base},.9)`;
        ctx.font = '9.5px -apple-system,system-ui,sans-serif'; ctx.textAlign='left';
        ctx.fillText(`★ ${z.kind} S${z.score}·T${z.touches}`, xs+7, yTop+11.5);
      }
    });
  }

  _pools(s){
    const ctx = this.ctx, x1 = this.w - this.pad.r;
    ctx.save(); ctx.setLineDash([2,4]); ctx.lineWidth = 1.3;
    this.pools.forEach(p => {
      const y = s.y(p.level);
      if (y < this.pad.t || y > this.h-this.pad.b) return;
      ctx.strokeStyle = p.is_high ? 'rgba(90,200,250,.5)' : 'rgba(255,179,64,.5)';
      ctx.beginPath(); ctx.moveTo(Math.max(this.pad.l, s.x(p.bar)), y); ctx.lineTo(x1, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = p.is_high ? 'rgba(90,200,250,.75)' : 'rgba(255,179,64,.75)';
      ctx.font = '9px "SF Mono",ui-monospace,monospace'; ctx.textAlign='right';
      ctx.fillText(p.is_high?'BSL':'SSL', x1-3, y-3.5);
      ctx.setLineDash([2,4]);
    });
    ctx.restore();
  }

  _trades(s, vis){
    const ctx = this.ctx, x1 = this.w - this.pad.r;
    this.trades.forEach(t => {
      const xe = s.x(t.entry_bar);
      if (xe < this.pad.l - 60) return;
      const ye = s.y(t.entry), ys = s.y(t.stop);
      ctx.fillStyle = 'rgba(255,90,106,.1)';
      ctx.fillRect(xe, Math.min(ye,ys), x1-xe, Math.abs(ys-ye));
      if (t.target){
        const yt = s.y(t.target);
        ctx.fillStyle = 'rgba(61,220,151,.09)';
        ctx.fillRect(xe, Math.min(ye,yt), x1-xe, Math.abs(yt-ye));
        ctx.strokeStyle='rgba(61,220,151,.55)'; ctx.setLineDash([4,4]); ctx.lineWidth=1;
        ctx.beginPath(); ctx.moveTo(xe,yt); ctx.lineTo(x1,yt); ctx.stroke(); ctx.setLineDash([]);
      }
      ctx.strokeStyle='rgba(255,255,255,.55)'; ctx.lineWidth=1.2;
      ctx.beginPath(); ctx.moveTo(xe,ye); ctx.lineTo(x1,ye); ctx.stroke();
      ctx.strokeStyle='rgba(255,90,106,.6)'; ctx.setLineDash([4,4]);
      ctx.beginPath(); ctx.moveTo(xe,ys); ctx.lineTo(x1,ys); ctx.stroke(); ctx.setLineDash([]);
    });
  }

  _candles(s, vis){
    const ctx = this.ctx;
    vis.forEach(b => {
      const x = s.x(b._i), up = b.close >= b.open;
      const body = up ? '#3ddc97' : '#ff5a6a';
      ctx.strokeStyle = up ? 'rgba(61,220,151,.85)' : 'rgba(255,90,106,.85)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(Math.round(x)+0.5, s.y(b.high));
      ctx.lineTo(Math.round(x)+0.5, s.y(b.low));
      ctx.stroke();
      const yO = s.y(b.open), yC = s.y(b.close);
      ctx.fillStyle = body;
      ctx.fillRect(x - s.bw/2, Math.min(yO,yC), s.bw, Math.max(1.2, Math.abs(yC-yO)));
    });
  }

  _signals(s, vis){
    const ctx = this.ctx;
    const lo = vis[0]._i, hi = vis[vis.length-1]._i;
    this.signals.forEach(sig => {
      if (sig.bar < lo || sig.bar > hi) return;
      const x = s.x(sig.bar), y = s.y(sig.price);
      if (sig.kind === 'ENTRY'){
        const up = sig.dir === 1;
        ctx.fillStyle = '#f0c96b';
        ctx.beginPath();
        const yy = up ? y + 16 : y - 16;
        ctx.moveTo(x, up ? yy-9 : yy+9);
        ctx.lineTo(x-5.5, up ? yy : yy);
        ctx.lineTo(x+5.5, up ? yy : yy);
        ctx.closePath(); ctx.fill();
        ctx.fillStyle='rgba(240,201,107,.95)';
        ctx.font='600 9.5px -apple-system,system-ui,sans-serif'; ctx.textAlign='center';
        ctx.fillText('★', x, up ? yy+10 : yy-3);
      } else if (sig.kind === 'SWEEP'){
        ctx.strokeStyle = sig.dir === 1 ? 'rgba(90,200,250,.9)' : 'rgba(255,179,64,.9)';
        ctx.lineWidth = 1.6;
        ctx.beginPath(); ctx.arc(x, y, 3.4, 0, Math.PI*2); ctx.stroke();
      } else if (sig.kind === 'EXIT'){
        ctx.fillStyle = (sig.r||0) > 0 ? 'rgba(61,220,151,.9)' : 'rgba(255,90,106,.9)';
        ctx.fillRect(x-2.5, y-2.5, 5, 5);
      }
    });
  }

  _lastPrice(s, vis){
    const ctx = this.ctx, last = vis[vis.length-1];
    const y = s.y(last.close), up = last.close >= last.open;
    ctx.save();
    ctx.strokeStyle = up ? 'rgba(61,220,151,.45)' : 'rgba(255,90,106,.45)';
    ctx.setLineDash([3,3]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(this.pad.l,y); ctx.lineTo(this.w-this.pad.r,y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = up ? '#3ddc97' : '#ff5a6a';
    const label = last.close.toFixed(1), w = 52;
    ctx.fillRect(this.w-this.pad.r+3, y-9, w, 18);
    ctx.fillStyle = '#07080c';
    ctx.font = '600 10.5px "SF Mono",ui-monospace,monospace'; ctx.textAlign='center';
    ctx.fillText(label, this.w-this.pad.r+3+w/2, y+3.5);
    ctx.restore();
  }

  _crosshair(s, vis){
    const ctx = this.ctx, {x:mx,y:my} = this.mouse;
    if (mx < this.pad.l || mx > this.w-this.pad.r) return;
    ctx.save();
    ctx.strokeStyle='rgba(255,255,255,.16)'; ctx.setLineDash([3,3]); ctx.lineWidth=1;
    ctx.beginPath(); ctx.moveTo(mx,this.pad.t); ctx.lineTo(mx,this.h-this.pad.b); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(this.pad.l,my); ctx.lineTo(this.w-this.pad.r,my); ctx.stroke();
    ctx.setLineDash([]);
    const price = s.hi - (my-this.pad.t)/s.plotH*(s.hi-s.lo);
    ctx.fillStyle='rgba(255,255,255,.9)'; ctx.fillRect(this.w-this.pad.r+3, my-9, 52, 18);
    ctx.fillStyle='#07080c'; ctx.font='600 10.5px "SF Mono",ui-monospace,monospace'; ctx.textAlign='center';
    ctx.fillText(price.toFixed(1), this.w-this.pad.r+29, my+3.5);
    // bar tooltip
    const idx = Math.round(vis[0]._i + (mx-this.pad.l)/s.plotW*vis.length);
    const b = this.bars[idx];
    if (b){
      const t = new Date(b.ts*1000).toISOString().slice(5,16).replace('T',' ');
      const txt = `${t}  O ${b.open.toFixed(1)}  H ${b.high.toFixed(1)}  L ${b.low.toFixed(1)}  C ${b.close.toFixed(1)}`;
      ctx.font='10.5px "SF Mono",ui-monospace,monospace'; ctx.textAlign='left';
      const w = ctx.measureText(txt).width + 16;
      ctx.fillStyle='rgba(10,12,18,.92)';
      ctx.strokeStyle='rgba(255,255,255,.1)';
      const bx = Math.min(mx+12, this.w-this.pad.r-w), by = this.pad.t+4;
      ctx.fillRect(bx,by,w,24); ctx.strokeRect(bx,by,w,24);
      ctx.fillStyle='rgba(242,244,248,.85)';
      ctx.fillText(txt, bx+8, by+15.5);
    }
    ctx.restore();
  }

  /* ── interaction ──────────────────────────────────────────────── */
  _bind(){
    let dragging = false, lastX = 0;
    this.c.addEventListener('mousedown', e => { dragging = true; lastX = e.clientX; });
    window.addEventListener('mouseup', () => { dragging = false; });
    this.c.addEventListener('mouseleave', () => { this.mouse = null; this.draw(); });
    this.c.addEventListener('mousemove', e => {
      const r = this.c.getBoundingClientRect();
      this.mouse = { x:e.clientX-r.left, y:e.clientY-r.top };
      if (dragging){
        const dx = e.clientX - lastX; lastX = e.clientX;
        const perBar = (this.w-this.pad.l-this.pad.r)/this.view.count;
        this.view.offset = Math.max(0, Math.min(this.bars.length - 30,
          this.view.offset + Math.round(dx/perBar)));
      }
      this.draw();
    });
    this.c.addEventListener('wheel', e => {
      e.preventDefault();
      const dir = e.deltaY > 0 ? 1.12 : 0.89;
      this.view.count = Math.max(35, Math.min(this.bars.length || 600,
        Math.round(this.view.count*dir)));
      this.draw();
    }, {passive:false});
  }
}

/* tiny sparkline for the equity curve */
function drawEquity(canvas, points){
  const ctx = canvas.getContext('2d');
  const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio||1;
  canvas.width = r.width*dpr; canvas.height = r.height*dpr;
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,r.width,r.height);
  if (!points || points.length < 2){
    ctx.fillStyle='rgba(255,255,255,.22)';
    ctx.font='11px -apple-system,system-ui,sans-serif'; ctx.textAlign='center';
    ctx.fillText('the curve draws itself as trades close', r.width/2, r.height/2);
    return;
  }
  const vals = points.map(p => p.equity_r);
  const hi = Math.max(...vals, 0.5), lo = Math.min(...vals, -0.5);
  const X = i => 6 + i/(points.length-1)*(r.width-12);
  const Y = v => 8 + (hi-v)/(hi-lo)*(r.height-16);
  ctx.strokeStyle='rgba(255,255,255,.09)'; ctx.setLineDash([3,3]);
  ctx.beginPath(); ctx.moveTo(6,Y(0)); ctx.lineTo(r.width-6,Y(0)); ctx.stroke(); ctx.setLineDash([]);
  const grad = ctx.createLinearGradient(0,0,0,r.height);
  const pos = vals[vals.length-1] >= 0;
  grad.addColorStop(0, pos?'rgba(61,220,151,.32)':'rgba(255,90,106,.3)');
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.beginPath(); ctx.moveTo(X(0), Y(vals[0]));
  vals.forEach((v,i) => ctx.lineTo(X(i), Y(v)));
  ctx.lineTo(X(vals.length-1), r.height); ctx.lineTo(X(0), r.height); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();
  ctx.beginPath(); ctx.moveTo(X(0), Y(vals[0]));
  vals.forEach((v,i) => ctx.lineTo(X(i), Y(v)));
  ctx.strokeStyle = pos?'#3ddc97':'#ff5a6a'; ctx.lineWidth = 1.8; ctx.stroke();
}
