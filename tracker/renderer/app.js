'use strict';

const $ = (id) => document.getElementById(id);
const money = (x, dp = 2) =>
  x == null || Number.isNaN(x) ? '--' : `${x < 0 ? '-' : ''}$${Math.abs(x).toFixed(dp)}`;
const signed = (x, dp = 2) =>
  x == null || Number.isNaN(x) ? '--' : `${x >= 0 ? '+' : '-'}$${Math.abs(x).toFixed(dp)}`;
const cls = (x) => (x > 0 ? 'pos' : x < 0 ? 'neg' : 'muted');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let lastSnapshot = null;

function rows(tbody, emptyEl, data, render) {
  tbody.innerHTML = data.map(render).join('');
  emptyEl.classList.toggle('hidden', data.length > 0);
}

function paint(s) {
  lastSnapshot = s;

  $('runDot').className = 'dot' + (s.killed ? ' killed' : s.engineRunning ? ' on' : '');
  const badge = $('modeBadge');
  badge.textContent = s.mode;
  badge.className = 'mode' + (s.mode === 'live' ? ' live' : '');
  $('btnMode').textContent = s.mode === 'live' ? 'Go paper' : 'Go live';
  $('btnStart').disabled = !!s.engineRunning;
  $('btnStop').disabled = !s.engineRunning;
  $('btnKill').textContent = s.killed ? 'Clear kill' : 'Kill';

  const banner = $('banner');
  let msg = '', err = false;
  if (s.offline) { msg = 'Bot is not running. Press Start to launch it.'; }
  else if (s.killed) { msg = `Kill switch engaged - no orders will be placed. ${esc(s.killReason || '')}`; err = true; }
  else if (s.otherHolder) { msg = `Another engine is running (${esc(s.otherHolder.label)}, pid ${s.otherHolder.pid}). Stop it before starting here.`; err = true; }
  else if (s.lastError) { msg = `Last error: ${esc(s.lastError)}`; err = true; }
  else if (!s.engineRunning) { msg = 'Engine stopped. Press Start to run the loop.'; }
  banner.className = 'banner' + (err ? ' err' : '') + (msg ? '' : ' hidden');
  banner.innerHTML = msg;

  const pnl = s.realised ?? 0;
  $('equity').textContent = money(s.equity);
  $('equity').className = cls(pnl);
  $('equityDelta').textContent = `from ${money(s.bankroll)}`;
  $('realised').textContent = signed(pnl);
  $('realised').className = cls(pnl);
  $('todayPnl').textContent = `today ${signed(s.today ?? 0)}`;
  $('exposure').textContent = money(s.exposure ?? 0);
  $('exposure').className = '';
  $('openCount').textContent = `${s.summary?.openCount ?? 0} open`;

  const sum = s.summary || {};
  $('hitRate').textContent = sum.hitRate == null ? '--' : `${(sum.hitRate * 100).toFixed(0)}%`;
  $('hitRate').className = '';
  $('winLoss').textContent = sum.settledCount
    ? `${sum.wins}W / ${sum.losses}L, fees ${money(sum.fees)}` : 'no settled trades';

  // Forecast skill: the number that says whether the model knows anything.
  const fc = s.forecastScore || {};
  if (!fc.n) {
    $('skill').textContent = '--';
    $('skill').className = 'muted';
    $('skillDetail').textContent = 'no resolved forecasts yet';
  } else {
    const pct = fc.skill * 100;
    $('skill').textContent = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
    $('skill').className = cls(fc.skill);
    $('skillDetail').textContent =
      `n=${fc.n} · Brier model ${fc.model.toFixed(4)} vs market ${fc.market.toFixed(4)}` +
      (fc.skill > 0 ? ' · beating the market' : ' · NOT beating the market');
  }

  const mk = Object.entries(s.markets || {});
  rows($('marketsTable').tBodies[0], $('marketsEmpty'), mk, ([series, m]) => {
    const q = (s.quotes || {})[seriesSymbol(series)] || {};
    const mid = m.yesBid != null && m.yesAsk != null ? (m.yesBid + m.yesAsk) / 2 : null;
    return `<tr>
      <td class="mono">${esc(m.ticker)}</td>
      <td>${m.secondsLeft != null ? fmtLeft(m.secondsLeft) : '--'}</td>
      <td class="mono">${q.price != null ? q.price.toFixed(2) : '--'}</td>
      <td class="mono">${m.strike != null ? Number(m.strike).toFixed(2) : '--'}</td>
      <td class="mono">${m.modelP != null ? m.modelP.toFixed(3) : '--'}</td>
      <td class="mono">${mid != null ? mid.toFixed(3) : '--'}</td>
    </tr>`;
  });
  $('marketsEmpty').textContent = s.engineRunning ? 'Waiting for the first window...' : 'Engine stopped.';

  rows($('openTable').tBodies[0], $('openEmpty'), sum.open || [], (p) => `<tr>
    <td class="mono">${esc(p.ticker)}</td>
    <td>${esc(p.side)}</td>
    <td>${p.contracts}</td>
    <td class="mono">${p.avg_price.toFixed(3)}</td>
    <td class="mono">${money(p.contracts * p.avg_price + (p.fee || 0))}</td>
  </tr>`);

  rows($('settledTable').tBodies[0], $('settledEmpty'), (sum.recent || []).slice(0, 12), (p) => `<tr>
    <td class="mono">${esc(p.ticker)}</td>
    <td>${esc(p.side)}</td>
    <td>${p.contracts}</td>
    <td class="mono">${p.avg_price.toFixed(3)}</td>
    <td>${esc(p.result || '?')}</td>
    <td class="mono ${cls(p.pnl)}">${signed(p.pnl)}</td>
  </tr>`);

  drawEquity();
}

function seriesSymbol(seriesTicker) {
  const m = /^KX([A-Z]+)15M$/.exec(seriesTicker);
  return m ? m[1] : seriesTicker;
}

function fmtLeft(sec) {
  if (sec < 0) return 'closed';
  const m = Math.floor(sec / 60), r = Math.round(sec % 60);
  return `${m}:${String(r).padStart(2, '0')}`;
}

// -- signals: the "why is it not trading" table ------------------------------

const signalRows = new Map();
function noteSignal(sig) {
  signalRows.set(sig.ticker, { ...sig, at: Date.now() });
  const list = [...signalRows.values()].sort((a, b) => b.at - a.at).slice(0, 12);
  rows($('signalTable').tBodies[0], $('signalEmpty'), list, (s) => `<tr>
    <td class="mono">${esc(s.ticker)}</td>
    <td class="mono">${s.modelP != null ? s.modelP.toFixed(3) : '--'}</td>
    <td class="mono ${s.edge != null ? cls(s.edge) : ''}">${s.edge != null ? s.edge.toFixed(4) : '--'}</td>
    <td class="muted">${esc(s.reason)}</td>
  </tr>`);
}

// -- equity chart -------------------------------------------------------------

let equityData = [];

async function refreshEquity() {
  try { equityData = await window.bot.equity(); } catch { equityData = []; }
  drawEquity();
}

function drawEquity() {
  const c = $('equityChart');
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = 150;
  if (c.width !== w * dpr || c.height !== h * dpr) {
    c.width = w * dpr; c.height = h * dpr;
  }
  const ctx = c.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const base = lastSnapshot ? lastSnapshot.bankroll : 19;
  const pts = equityData.map((r) => r.total);
  if (pts.length < 2) {
    ctx.fillStyle = '#8b97a8';
    ctx.font = '12px Segoe UI, system-ui, sans-serif';
    ctx.fillText('No equity history yet.', 8, 24);
    return;
  }

  const min = Math.min(...pts, base), max = Math.max(...pts, base);
  const pad = Math.max((max - min) * 0.15, 0.25);
  const lo = min - pad, hi = max + pad;
  const x = (i) => (i / (pts.length - 1)) * (w - 8) + 4;
  const y = (v) => h - 12 - ((v - lo) / (hi - lo)) * (h - 24);

  // starting-bankroll reference: above it is profit, below it is loss
  ctx.strokeStyle = '#384153';
  ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(0, y(base)); ctx.lineTo(w, y(base)); ctx.stroke();
  ctx.setLineDash([]);

  const last = pts[pts.length - 1];
  const colour = last >= base ? '#3fb950' : '#f85149';

  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, last >= base ? 'rgba(63,185,80,.22)' : 'rgba(248,81,73,.22)');
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.beginPath();
  ctx.moveTo(x(0), y(pts[0]));
  pts.forEach((v, i) => ctx.lineTo(x(i), y(v)));
  ctx.lineTo(x(pts.length - 1), h); ctx.lineTo(x(0), h); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();

  ctx.beginPath();
  ctx.moveTo(x(0), y(pts[0]));
  pts.forEach((v, i) => ctx.lineTo(x(i), y(v)));
  ctx.strokeStyle = colour; ctx.lineWidth = 1.6; ctx.stroke();

  ctx.fillStyle = '#8b97a8';
  ctx.font = '10px Segoe UI, system-ui, sans-serif';
  ctx.fillText(`$${hi.toFixed(2)}`, 4, 11);
  ctx.fillText(`$${lo.toFixed(2)}`, 4, h - 2);
}

// -- wiring -------------------------------------------------------------------

$('btnStart').onclick = async () => {
  $('btnStart').disabled = true;
  const r = await window.bot.start();
  if (!r.ok) {
    const b = $('banner');
    b.className = 'banner err';
    b.textContent = `Could not start: ${r.reason}`;
  }
  refresh();
};
$('btnStop').onclick = async () => { await window.bot.stop(); refresh(); };
$('btnMode').onclick = async () => {
  const next = lastSnapshot && lastSnapshot.mode === 'live' ? 'paper' : 'live';
  await window.bot.setMode(next);
  refresh();
};
$('btnKill').onclick = async () => {
  if (lastSnapshot && lastSnapshot.killed) await window.bot.revive();
  else await window.bot.kill('stopped from the tracker');
  refresh();
};

// The bot runs in its own process, so this window polls it rather than being
// pushed to. Child stdout is relayed straight through as well, which is the
// only way to see anything when the bot dies before its API ever answers.
function appendLog(text) {
  if (!text) return;
  const el = $('log');
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
  el.textContent += text.replace(/\n+$/, '') + '\n';
  const lines = el.textContent.split('\n');
  if (lines.length > 400) el.textContent = lines.slice(-400).join('\n');
  if (atBottom) el.scrollTop = el.scrollHeight;
}

window.bot.onChildLog((text) => appendLog(text));
window.bot.onChildExit(({ code }) => appendLog(`-- bot process exited (code ${code}) --`));

let lastLogTs = 0;

async function refresh() {
  const s = await window.bot.snapshot();
  paint(s);

  // Replay only the log lines we have not already shown.
  if (Array.isArray(s.logs) && s.logs.length) {
    const fresh = s.logs.filter((e) => e.ts > lastLogTs);
    if (fresh.length) {
      lastLogTs = fresh[fresh.length - 1].ts;
      appendLog(fresh
        .map((e) => `${new Date(e.ts).toLocaleTimeString()} ${e.source}: ${e.message}`)
        .join('\n'));
    }
  }
}

refresh();
refreshEquity();
setInterval(refresh, 2000);
setInterval(refreshEquity, 15000);
window.addEventListener('resize', drawEquity);
