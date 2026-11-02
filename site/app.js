import { lineChart, heatmap, stripPlot, showTip, hideTip } from './chart.js';

const GPU_COLORS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)'];
const MARKET_LABEL = { spot: 'spot', on_demand: 'on-demand' };
const RANGES = { '1d': 1, '7d': 7, '30d': 30, '90d': 90, '1y': 365, all: Infinity };
const PERIODS = ['24h', '7d', '30d'];
const STOCK = ['none left', 'low', 'medium', 'high'];
const DAY = 86400e3;

const state = { market: 'spot', range: '7d', trendMode: 'pct', gpu: null };
const $ = s => document.querySelector(s);
const tip = () => $('#tooltip');
const histCache = new Map();
let S;

// ---------- formatting ----------
const fmtPrice = (v, step) => {
  if (v == null) return '—';
  const dp = step ? Math.min(3, Math.max(2, -Math.floor(Math.log10(step)))) : v < 1 ? 3 : 2;
  return '$' + v.toFixed(dp);
};
const fmtPct = (v, step) => {
  if (v == null) return '—';
  const dp = step ? Math.max(0, Math.min(2, -Math.floor(Math.log10(step)))) : 1;
  const s = Math.abs(v).toFixed(dp);
  return (+s === 0 ? '' : v > 0 ? '+' : '−') + s + '%';
};
const fmtUtil = v => (v == null ? '—' : `${Math.round(v)}%`);
const fmtWhen = ms => new Date(ms).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const fmtDay = ms => new Date(ms).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
const tsMs = iso => Date.parse(iso.replace(/Z$/, ':00Z'));
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const plural = (n, word) => `${n.toLocaleString()} ${n === 1 ? word.replace(/s$/, '') : word}`;

function delta(pct, period) {
  const s = el('span');
  if (pct == null) { s.className = 'delta-na'; s.textContent = '—'; s.title = `Not enough history for a ${period} change yet`; }
  else if (Math.abs(pct) < 0.05) { s.className = 'delta-flat'; s.textContent = '0.0%'; }
  else {
    const down = pct < 0;
    s.className = down ? 'delta-down' : 'delta-up';
    s.textContent = `${down ? '▼' : '▲'} ${Math.abs(pct).toFixed(1)}%`;
    s.title = `${down ? 'Down' : 'Up'} ${Math.abs(pct).toFixed(2)}% vs ${period} ago`;
  }
  return s;
}
const keyEl = color => { const k = el('span', 'key'); k.style.background = color; return k; };

const byName = name => S.gpus.find(g => g.gpu === name);
const bySlug = slug => S.gpus.find(g => g.slug === slug);
const colorOf = name => { const i = S.featured.indexOf(name); return i >= 0 ? GPU_COLORS[i] : 'var(--ink-2)'; };

// ---------- history ----------
function loadHist(slug) {
  if (!histCache.has(slug)) histCache.set(slug, fetch(`data/gpu/${slug}.json`, { cache: 'no-cache' }).then(r => r.json()));
  return histCache.get(slug);
}

/** Pick hourly or daily resolution for the current range; returns aligned timestamps and a column picker. */
function slice(h) {
  const days = RANGES[state.range];
  const span = tsMs(S.last_ts) - tsMs(S.first_ts);
  const block = days > 31 && span > 31 * DAY ? h.daily : h.hourly;
  const lastT = h.hourly.t.at(-1) * 1000;
  const from = days === Infinity ? -Infinity : lastT - days * DAY;
  const idx = [];
  block.t.forEach((t, i) => { if (t * 1000 > from) idx.push(i); });
  return { daily: block === h.daily, block, t: idx.map(i => block.t[i] * 1000), pick: arr => idx.map(i => arr?.[i] ?? null) };
}

/** Average into at most maxPts buckets; the newest point stays as-is so line ends match the headline numbers. */
function thin(t, cols, maxPts) {
  if (t.length <= maxPts) return { t, cols };
  const size = Math.ceil((t.length - 1) / (maxPts - 1));
  const nt = [t.at(-1)], out = cols.map(c => [c.at(-1)]);
  for (let end = t.length - 1; end > 0; end -= size) {
    const start = Math.max(0, end - size);
    nt.unshift(t[end - 1]);
    cols.forEach((c, k) => {
      const vs = c.slice(start, end).filter(v => v != null);
      out[k].unshift(vs.length ? vs.reduce((a, b) => a + b, 0) / vs.length : null);
    });
  }
  return { t: nt, cols: out };
}
const lastVal = arr => { for (let i = arr.length - 1; i >= 0; i--) if (arr[i] != null) return arr[i]; return null; };
const firstVal = arr => arr.find(v => v != null) ?? null;

// ---------- header ----------
function renderHeader() {
  const first = tsMs(S.first_ts), last = tsMs(S.last_ts);
  $('#stamp').textContent = `Updated ${fmtWhen(last)} · ${plural(S.snapshots, 'hourly snapshots')}`;
  $('#sub').textContent = `Hourly rental prices and demand for ${S.gpus.length} GPU models from ${Object.keys(S.sources).length} sources — ` +
    'open marketplaces, decentralized networks, GPU clouds and hyperscalers.';
  const days = (last - first) / DAY;
  const missing = [[1, '24-hour'], [7, '7-day'], [30, '30-day']].filter(([d]) => days < d).map(([, l]) => l);
  const n = $('#notice');
  n.hidden = !missing.length;
  if (missing.length) n.textContent = `History is still building — tracking started ${fmtDay(first)}. ` +
    `Lines grow with every hourly snapshot; ${missing.join(', ').replace(/, ([^,]*)$/, ' and $1')} changes appear once enough history exists.`;
}

// ---------- flagship cards ----------
function demandPill(g, color) {
  const d = g.demand || {};
  if (d.util != null) {
    const p = el('span', 'pill');
    const m = el('span', 'meter'); const i = el('i'); i.style.width = `${d.util}%`; m.style.setProperty('--c', color); m.append(i);
    p.append(m, `${fmtUtil(d.util)} rented`);
    p.title = `${d.rented} of ${d.total} listed GPUs rented on open marketplaces`;
    return p;
  }
  const stock = Object.values(d.stock || {});
  if (stock.length) return el('span', 'pill', `Best stock: ${STOCK[Math.max(...stock)]}`);
  return el('span');
}

function signals(g) {
  const d = g.demand || {}, lines = [];
  const by = Object.entries(d.by_source || {}).sort((a, b) => (b[1].total || 0) - (a[1].total || 0));
  if (by.length) lines.push(['Rented', by.slice(0, 3).map(([s, v]) => `${s} ${fmtUtil(v.util)}${v.total ? ` of ${v.total}` : ''}`).join(' · ') +
    (by.length > 3 ? ` · +${by.length - 3} more` : '')]);
  if (d.aws) {
    const parts = [];
    if (d.aws.interrupt_pct != null) parts.push(`~${Math.round(d.aws.interrupt_pct)}% interruptions`);
    if (d.aws.spot_discount_pct != null) parts.push(`${Math.round(d.aws.spot_discount_pct)}% below on-demand`);
    lines.push(['AWS spot', parts.join(', ')]);
  }
  const stock = Object.entries(d.stock || {});
  if (stock.length) lines.push(['Stock', stock.map(([s, v]) => `${s} ${STOCK[v]}`).join(' · ')]);
  const inStock = Object.entries(d.in_stock || {});
  if (inStock.length) lines.push(['In stock', inStock.map(([s, v]) => `${Math.round(v)}% of ${s} listings`).join(' · ')]);
  const box = el('div', 'card-signals');
  for (const [k, v] of lines) { const row = el('div'); row.append(el('b', null, `${k}: `), v); box.append(row); }
  return box;
}

async function renderCards() {
  const box = $('#cards');
  const draws = [];
  box.replaceChildren(...S.featured.map(name => {
    const g = byName(name), m = g.market[state.market], c = colorOf(name);
    const card = el('div', 'card');
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-pressed', String(state.gpu === g.slug));
    card.style.setProperty('--c', c);
    const top = el('div', 'card-top');
    const id = el('div'); id.append(el('div', 'card-name', g.gpu), el('div', 'card-meta', `${g.vram} GB · ${g.tier}`));
    top.append(id, demandPill(g, c));
    card.append(top);
    if (m) {
      const price = el('div', 'card-price', fmtPrice(m.typical)); price.append(el('small', null, '/ GPU-hr'));
      const chart = el('div', 'card-chart');
      const ds = el('div', 'card-deltas');
      for (const p of PERIODS) { const s = el('span'); s.append(el('span', 'k', p), delta(m.chg[p], p)); ds.append(s); }
      card.append(price, el('div', 'card-from', `typical ${MARKET_LABEL[state.market]} · from ${fmtPrice(m.cheapest)} at ${m.cheapest_src}`), chart, ds);
      draws.push([g, chart, c]);
    } else {
      card.append(el('div', 'card-empty', `No ${MARKET_LABEL[state.market]} prices tracked for ${g.gpu} yet.`));
    }
    card.append(signals(g));
    const go = () => selectGpu(g.slug, true);
    card.addEventListener('click', go);
    card.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    return card;
  }));
  for (const [g, host, color] of draws) {
    const h = await loadHist(g.slug);
    const r = slice(h);
    const { t, cols: [typ] } = thin(r.t, [r.pick(r.block.typical[state.market])], Math.round(host.clientWidth / 3));
    lineChart(host, { t, series: [{ name: `${g.gpu} typical`, color, values: typ, area: true }], compact: true,
      fmt: fmtPrice, tfmt: r.daily ? fmtDay : fmtWhen, tip: tip(), ariaLabel: `${g.gpu} typical price trend`,
      emptyText: 'Collecting…' });
  }
}

// ---------- price trend ----------
async function renderTrend() {
  const host = $('#trend-chart');
  const pct = state.trendMode === 'pct';
  $('#trend-sub').textContent = pct
    ? `Change in the typical ${MARKET_LABEL[state.market]} price since the start of the range — chained across every source, so providers joining or dropping out don't cause jumps.`
    : `Typical ${MARKET_LABEL[state.market]} price per GPU-hour.`;
  const gs = S.featured.map(byName).filter(g => g.market[state.market]);
  const hs = await Promise.all(gs.map(g => loadHist(g.slug)));
  if (!hs.length) return lineChart(host, { t: [], series: [], emptyText: `No ${MARKET_LABEL[state.market]} prices for flagship GPUs.` });
  const r = slice(hs[0]);
  const raw = hs.map(h => slice(h).pick(h[r.daily ? 'daily' : 'hourly'].typical[state.market]));
  const { t, cols } = thin(r.t, raw, Math.round(host.clientWidth / 3));
  const series = gs.map((g, i) => {
    let values = cols[i];
    if (pct) { const base = firstVal(values); values = values.map(v => (v == null || !base ? null : (v / base - 1) * 100)); }
    const last = lastVal(values);
    return { name: g.gpu, color: colorOf(g.gpu), values, label: `${g.gpu}  ${pct ? fmtPct(last) : fmtPrice(last)}` };
  });
  const legend = $('#trend-legend');
  legend.replaceChildren(...series.map(s => { const sp = el('span'); sp.append(keyEl(s.color), s.name); return sp; }));
  lineChart(host, { t, series, endLabels: true, ref: pct ? 0 : null, fmt: pct ? fmtPct : fmtPrice,
    tfmt: r.daily ? fmtDay : fmtWhen, tip: tip(), ariaLabel: 'Flagship GPU price trend',
    emptyText: 'Waiting for the first snapshots.' });
}

// ---------- demand ----------
function renderBars() {
  const rows = S.gpus.filter(g => g.demand?.util != null).sort((a, b) => b.demand.util - a.demand.util);
  const box = $('#util-bars');
  if (!rows.length) { box.replaceChildren(el('p', 'muted', 'No marketplace utilization data yet.')); return; }
  box.replaceChildren(...rows.map(g => {
    const d = g.demand, featured = S.featured.includes(g.gpu);
    const row = el('div', `bar-row${featured ? ' featured' : ''}`);
    row.style.setProperty('--c', featured ? colorOf(g.gpu) : 'var(--ink-2)');
    const track = el('div', 'track');
    if (d.util > 0) { const u = el('i', 'used'); u.style.width = `${d.util}%`; track.append(u); }
    if (d.util < 100) track.append(el('i', 'free'));
    const v = el('span', 'v'); v.append(el('b', null, fmtUtil(d.util)), ` of ${d.total.toLocaleString()}`);
    row.append(el('span', 'n', g.gpu), track, v);
    row.addEventListener('pointermove', e => showTip(tip(), e.clientX, e.clientY, `${g.gpu} · rented now`,
      Object.entries(d.by_source).map(([s, x]) => ({ value: fmtUtil(x.util), label: x.total ? `${s} · ${x.rented} of ${x.total} GPUs` : `${s} (not in total)` }))));
    row.addEventListener('pointerleave', () => hideTip(tip()));
    row.addEventListener('click', () => selectGpu(g.slug, true));
    return row;
  }));
}

async function renderUtilChart() {
  const host = $('#util-chart');
  const gs = S.featured.map(byName);
  const hs = await Promise.all(gs.map(g => loadHist(g.slug)));
  const r = slice(hs[0]);
  const raw = hs.map(h => slice(h).pick(h[r.daily ? 'daily' : 'hourly'].util));
  const { t, cols } = thin(r.t, raw, Math.round(host.clientWidth / 3));
  const series = gs.map((g, i) => ({ name: g.gpu, color: colorOf(g.gpu), values: cols[i], label: `${g.gpu}  ${fmtUtil(lastVal(cols[i]))}` }))
    .filter(s => s.values.some(v => v != null));
  $('#util-legend').replaceChildren(...series.map(s => { const sp = el('span'); sp.append(keyEl(s.color), s.name); return sp; }));
  lineChart(host, { t, series, endLabels: true, yDomain: [0, 100], fmt: v => `${Math.round(v)}%`,
    tfmt: r.daily ? fmtDay : fmtWhen, tip: tip(), ariaLabel: 'Flagship GPU utilization',
    emptyText: 'No marketplace utilization data for the flagship GPUs yet.' });
}

const heatColor = v => `var(--seq-${Math.min(7, Math.floor(v / (100 / 7)) + 1)})`;
function renderHeat() {
  const span = tsMs(S.last_ts) - tsMs(S.first_ts);
  const daily = span >= 3 * DAY;
  const t0 = tsMs(daily ? S.heat.day0 : S.heat.hour0);
  const step = daily ? DAY : DAY / 24;
  const fit = Math.max(7, Math.floor(($('#heat').clientWidth - 108) / 14));  // keep cells at least ~14px wide
  const rows = Object.entries(S.heat.rows)
    .map(([name, v]) => { const vals = daily ? v.d : v.h; return { name, values: vals.slice(-fit), now: byName(name)?.demand?.util ?? -1, n: vals.length }; })
    .sort((a, b) => b.now - a.now);
  const skip = Math.max(0, (rows[0]?.n || 0) - fit);
  const cols = (rows[0]?.values || []).map((_, i) => t0 + (i + skip) * step);
  const shown = rows[0]?.values.length || 0;
  $('#heat-h').textContent = daily ? `Utilization by day, last ${shown} days` : `Utilization by hour, last ${shown} hours`;
  const scale = $('#heat-scale');
  const ramp = el('span', 'ramp');
  for (let i = 1; i <= 7; i++) { const c = el('i'); c.style.background = `var(--seq-${i})`; ramp.append(c); }
  scale.replaceChildren('0%', ramp, '100% rented');
  heatmap($('#heat'), { rows, cols, color: heatColor, fmt: v => `${v.toFixed(1)}% rented`, valueLabel: '',
    tfmt: daily ? fmtDay : fmtWhen,
    colLabel: ms => daily ? new Date(ms).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
      : new Date(ms).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }),
    tip: tip(), onRow: name => selectGpu(byName(name).slug, true), ariaLabel: 'GPU utilization heatmap' });
}

// ---------- price spread ----------
function renderStrip() {
  $('#spread-sub').textContent = `Every source's median ${MARKET_LABEL[state.market]} price per GPU-hour, right now (log scale). Hover a dot to follow that source across all GPUs.`;
  const leg = $('#strip-legend');
  const item = (cls, txt) => { const s = el('span'); s.append(el('i', cls), txt); return s; };
  leg.replaceChildren(item('d', 'one source'), item('d low', 'cheapest'), item('t', 'typical'));
  const rows = S.gpus.filter(g => g.market[state.market]).map(g => {
    const m = g.market[state.market];
    return { name: g.gpu, group: g.tier, typical: m.typical, color: S.featured.includes(g.gpu) ? colorOf(g.gpu) : null,
      points: m.prices.map(p => ({ source: p.source, value: p.median, min: p.min, n: p.n, unit: S.sources[p.source]?.unit })) };
  });
  stripPlot($('#strip'), { rows, fmt: v => (v < 1 ? `$${v.toFixed(2)}` : `$${+v.toFixed(2)}`), tip: tip(),
    onRow: name => selectGpu(byName(name).slug, true), ariaLabel: 'Price per source for every GPU' });
}

// ---------- by-source detail ----------
async function renderDetail() {
  const g = bySlug(state.gpu);
  const pick = $('#gpu-pick');
  if (!pick.options.length) for (const x of S.gpus) pick.append(new Option(x.gpu, x.slug));
  pick.value = state.gpu;
  $('#detail-h').textContent = `${g.gpu} by source`;
  const m = g.market[state.market];
  $('#detail-sub').textContent = m
    ? `Median ${MARKET_LABEL[state.market]} price per GPU-hour at each source. Hover a source to pick out its line.`
    : `No ${MARKET_LABEL[state.market]} prices tracked for ${g.gpu}.`;
  const host = $('#detail-chart');
  const h = await loadHist(g.slug);
  if (state.gpu !== g.slug) return;
  const r = slice(h);
  const keys = Object.keys(r.block.sources).filter(k => k.endsWith(`|${state.market}`));
  const raw = [...keys.map(k => r.pick(r.block.sources[k])), r.pick(r.block.typical[state.market])];
  const { t, cols } = thin(r.t, raw, Math.round(host.clientWidth / 3));
  const color = S.featured.includes(g.gpu) ? colorOf(g.gpu) : 'var(--accent)';
  const series = keys.map((k, i) => ({ name: k.split('|')[0], key: k.split('|')[0], color: 'var(--line-muted)', width: 1.5, values: cols[i] }));
  if (m) series.push({ name: 'Typical', key: 'Typical', color, width: 2.5, values: cols.at(-1), label: `Typical ${fmtPrice(lastVal(cols.at(-1)))}` });
  const legend = $('#detail-legend');
  legend.replaceChildren(...series.map(s => {
    const b = el('button'); b.type = 'button';
    b.append(keyEl(s.color), s.name);
    const on = () => { host.classList.add('hl'); host.querySelector(`.series[data-series="${CSS.escape(s.key)}"]`)?.classList.add('on'); };
    const off = () => { host.classList.remove('hl'); host.querySelectorAll('.series.on').forEach(n => n.classList.remove('on')); };
    b.addEventListener('pointerenter', on); b.addEventListener('pointerleave', off);
    b.addEventListener('focus', on); b.addEventListener('blur', off);
    return b;
  }));
  lineChart(host, { t, series, endLabels: true, fmt: fmtPrice, tfmt: r.daily ? fmtDay : fmtWhen, tip: tip(),
    ariaLabel: `${g.gpu} price by source`, emptyText: `No ${MARKET_LABEL[state.market]} prices for ${g.gpu}.` });

  const table = $('#detail-table');
  const head = el('thead'); const hr = el('tr');
  ['Source', 'Type', 'Median', 'Lowest', 'Based on', 'Rented'].forEach((x, i) => hr.append(el('th', i < 2 ? 'l' : null, x)));
  head.append(hr);
  const body = el('tbody');
  for (const p of (m?.prices || []).slice().sort((a, b) => a.median - b.median)) {
    const meta = S.sources[p.source] || {};
    const tr = el('tr');
    const name = el('td', 'l');
    if (meta.url) { const a = el('a', null, p.source); a.href = meta.url; a.target = '_blank'; a.rel = 'noopener'; a.title = meta.about || ''; name.append(a); }
    else name.append(p.source);
    const u = g.demand?.by_source?.[p.source];
    tr.append(name, el('td', 'l muted', meta.kind || ''), el('td', null, fmtPrice(p.median)), el('td', 'muted', fmtPrice(p.min)),
      el('td', 'muted', plural(p.n, meta.unit || 'offers')), el('td', u ? null : 'muted', u ? fmtUtil(u.util) : '—'));
    body.append(tr);
  }
  table.replaceChildren(head, body);
}

// ---------- secondary table ----------
function renderAllTable() {
  const table = $('#all-table');
  const head = el('thead'); const hr = el('tr');
  ['GPU', 'Typical', 'Cheapest', 'Where', '24h', '7d', '30d', 'Rented'].forEach((x, i) => hr.append(el('th', i === 3 ? 'l' : null, x)));
  head.append(hr);
  const body = el('tbody');
  for (const g of S.gpus) {
    const m = g.market[state.market];
    const tr = el('tr');
    tr.append(el('td', null, g.gpu), el('td', null, fmtPrice(m?.typical)), el('td', null, fmtPrice(m?.cheapest)), el('td', 'l muted', m?.cheapest_src || ''));
    for (const p of PERIODS) { const td = el('td'); td.append(delta(m?.chg[p], p)); tr.append(td); }
    tr.append(el('td', g.demand?.util != null ? null : 'muted', fmtUtil(g.demand?.util)));
    body.append(tr);
  }
  table.replaceChildren(head, body);
}

function renderFooter() {
  const f = $('#foot');
  const dl = el('dl');
  for (const [name, meta] of Object.entries(S.sources)) {
    const dt = el('dt'); const a = el('a', null, name); a.href = meta.url; a.target = '_blank'; a.rel = 'noopener'; dt.append(a);
    dl.append(dt, el('dd', 'kind', meta.kind), el('dd', null, meta.about));
  }
  f.replaceChildren(el('h2', null, 'Sources & method'), dl, el('p', null,
    'All prices are USD per GPU per hour; multi-GPU instances are divided by their GPU count and EUR prices are converted at the ECB reference rate. ' +
    '"Typical" is the lower median of the sources\' median prices; a source priced below a quarter of the others is treated as a stale listing and ignored. ' +
    'Trends and 24h/7d/30d changes use a chained index: each hour every source is compared with its own previous price, so a provider joining or dropping out never moves the line. ' +
    '"Rented" is rented ÷ listed GPUs on open marketplaces that publish both. Times are in your local time zone.'));
}

// ---------- wiring ----------
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get('market') in MARKET_LABEL) state.market = p.get('market');
  if (p.get('range') in RANGES) state.range = p.get('range');
  if (['pct', 'usd'].includes(p.get('trend'))) state.trendMode = p.get('trend');
  if (p.get('gpu')) state.gpu = p.get('gpu');
}
function writeHash() {
  history.replaceState(null, '', '#' + new URLSearchParams({ market: state.market, range: state.range, trend: state.trendMode, gpu: state.gpu }));
}
function renderSegs() {
  document.querySelectorAll('.seg').forEach(seg => seg.querySelectorAll('button')
    .forEach(b => b.setAttribute('aria-checked', String(state[seg.dataset.filter] === b.dataset.value))));
}

function renderAll() {
  renderSegs();
  writeHash();
  renderCards();
  renderTrend();
  renderBars();
  renderUtilChart();
  renderHeat();
  renderStrip();
  renderDetail();
  renderAllTable();
}

function selectGpu(slug, scroll) {
  state.gpu = slug;
  writeHash();
  document.querySelectorAll('.card').forEach((c, i) => c.setAttribute('aria-pressed', String(byName(S.featured[i])?.slug === slug)));
  renderDetail();
  if (scroll) $('#detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

document.querySelectorAll('.seg').forEach(seg => seg.addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  state[seg.dataset.filter] = b.dataset.value;
  if (seg.dataset.filter === 'trendMode') { renderSegs(); writeHash(); renderTrend(); } else renderAll();
}));
$('#gpu-pick').addEventListener('change', e => selectGpu(e.target.value, false));
let resizeTimer, lastW = window.innerWidth;
window.addEventListener('resize', () => {
  if (window.innerWidth === lastW) return;
  lastW = window.innerWidth;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(renderAll, 150);
});

(async function init() {
  readHash();
  try {
    S = await fetch('data/summary.json', { cache: 'no-cache' }).then(r => r.json());
  } catch {
    const n = $('#notice'); n.hidden = false;
    n.textContent = 'No data yet. Run collector/collect.py and collector/build.py to generate site/data.';
    return;
  }
  if (!bySlug(state.gpu)) state.gpu = (byName(S.featured[0]) || S.gpus[0]).slug;
  renderHeader();
  renderFooter();
  renderAll();
})();
