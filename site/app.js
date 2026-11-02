import { lineChart, heatmap, stripPlot, showTip, hideTip } from './chart.js';

const GPU_COLORS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)'];
const MARKET_LABEL = { spot: 'spot', on_demand: 'on-demand' };
const RANGES = { '1d': 1, '7d': 7, '30d': 30, '90d': 90, '1y': 365, all: Infinity };
const PERIODS = ['24h', '7d', '30d'];
const STOCK = ['none left', 'low', 'medium', 'high'];
const DAY = 86400e3;

const state = { market: 'spot', range: '7d', trendMode: 'pct', heatMode: 'util', genMode: 'pflop', tightMode: 'tight', gpu: null };
const SEG_LABEL = { neocloud: 'Neoclouds', market: 'Open market', hyperscaler: 'Hyperscalers' };
const GEN_COLOR = { Blackwell: 'var(--seq-7)', Hopper: 'var(--seq-5)', Ampere: 'var(--seq-3)' };  // ordinal: newest darkest
const COMPUTE_GPUS = ['B300', 'B200', 'H200', 'H100 SXM', 'A100 80GB'];
const RANGE_TEXT = { '1d': '24 hours', '7d': '7 days', '30d': '30 days', '90d': '90 days', '1y': 'year', all: 'all time' };
const SOURCE_WINDOW = { '1d': '1d', '7d': '7d' };  // per-source sold-out shares exist for 1d / 7d / 30d
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
  if (d.tight) {
    const p = el('span', 'pill');
    const m = el('span', 'meter'); const i = el('i'); i.style.width = `${d.tight.now}%`; m.style.setProperty('--c', color); m.append(i);
    p.append(m, `Tightness ${Math.round(d.tight.now)}`);
    p.title = `0–100, average of: ${[d.tight.rented != null && `rented ${fmtUtil(d.tight.rented)}`,
      d.tight.soldout != null && `sources sold out ${fmtUtil(d.tight.soldout)}`,
      d.tight.discount != null && `100 − spot discount ${fmtUtil(d.tight.discount)}`].filter(Boolean).join(', ')}`;
    return p;
  }
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
  if (d.soldout?.of) {
    const so = d.soldout;
    lines.push(['Sold out', so.now.length
      ? `${so.now.length} of ${so.of} sources now (${so.now.slice(0, 3).join(', ')}${so.now.length > 3 ? '…' : ''})`
      : `available at all ${so.of} sources now`]);
  }
  if (d.aws) {
    const parts = [];
    if (d.aws.interrupt_pct != null) parts.push(`~${Math.round(d.aws.interrupt_pct)}% interruptions`);
    if (d.aws.spot_discount_pct != null) parts.push(`${Math.round(d.aws.spot_discount_pct)}% below on-demand`);
    lines.push(['AWS spot', parts.join(', ')]);
  }
  const stock = Object.entries(d.stock || {}).filter(([, v]) => v > 0);
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

// ---------- neoclouds vs open market ----------
const segKey = (seg) => `${seg}|${state.market}`;

async function renderSegments() {
  const gs = S.featured.map(byName).filter(g => g.market[state.market]?.segments?.neocloud);
  const prem = g => { const s = g.market[state.market].segments; return s.market ? (s.neocloud.typical / s.market.typical - 1) * 100 : null; };
  const parts = gs.filter(g => prem(g) != null).map(g => `${g.gpu} ${fmtPct(prem(g))}`);
  $('#seg-sub').textContent = `Typical ${MARKET_LABEL[state.market]} price at neoclouds (GPU clouds like CoreWeave, Lambda, Nebius, Crusoe), ` +
    `open marketplaces and decentralized networks, and hyperscalers (AWS, Azure, Oracle).` +
    (parts.length ? ` Neocloud premium right now: ${parts.join(' · ')}.` : '');
  const cells = gs.map(g => {
    const cell = el('div', 'mini'); cell.style.setProperty('--c', colorOf(g.gpu));
    const head = el('div', 'mini-head');
    const s = g.market[state.market].segments;
    const short = { neocloud: 'neo', market: 'mkt', hyperscaler: 'hyper' };
    head.append(el('b', null, g.gpu), el('span', null, `sources: ${Object.entries(s).map(([k, v]) => `${v.n} ${short[k]}`).join(' · ')}`));
    const chart = el('div', 'chart'); cell.append(head, chart);
    return [g, chart, cell];
  });
  $('#seg-multiples').replaceChildren(...(cells.length ? cells.map(c => c[2])
    : [el('p', 'muted', `No ${MARKET_LABEL[state.market]} neocloud prices for the flagship GPUs yet.`)]));
  const premSeries = [];
  let premT = [], premDaily = false;
  for (const [g, host] of cells) {
    const h = await loadHist(g.slug);
    const r = slice(h);
    const segs = ['neocloud', 'market', 'hyperscaler'];
    const raw = segs.map(seg => r.pick(r.block.seg?.[segKey(seg)]));
    const { t, cols } = thin(r.t, raw, Math.round(host.clientWidth / 3));
    const style = { neocloud: [colorOf(g.gpu), 2.5, 'Neo'], market: ['var(--muted)', 1.5, 'Market'], hyperscaler: ['var(--line-muted)', 1.5, 'Hyper'] };
    const series = segs.map((seg, i) => ({ name: SEG_LABEL[seg], color: style[seg][0], width: style[seg][1], values: cols[i],
      area: seg === 'neocloud', label: `${style[seg][2]} ${fmtPrice(lastVal(cols[i]))}` })).filter(s => s.values.some(v => v != null));
    lineChart(host, { t, series, compact: true, endLabels: true, fmt: fmtPrice, tfmt: r.daily ? fmtDay : fmtWhen, tip: tip(),
      ariaLabel: `${g.gpu} typical price by segment` });
    const neo = raw[0], mkt = raw[1];
    premT = r.t; premDaily = r.daily;
    premSeries.push({ name: g.gpu, color: colorOf(g.gpu),
      values: neo.map((v, i) => (v != null && mkt[i] ? (v / mkt[i] - 1) * 100 : null)) });
  }
  const host = $('#prem-chart');
  const { t, cols } = thin(premT, premSeries.map(s => s.values), Math.round(host.clientWidth / 3));
  const series = premSeries.map((s, i) => ({ ...s, values: cols[i], label: `${s.name}  ${fmtPct(lastVal(cols[i]))}` }))
    .filter(s => s.values.some(v => v != null));
  $('#prem-legend').replaceChildren(...series.map(s => { const sp = el('span'); sp.append(keyEl(s.color), s.name); return sp; }));
  lineChart(host, { t, series, endLabels: true, ref: 0, fmt: fmtPct, tfmt: premDaily ? fmtDay : fmtWhen, tip: tip(),
    ariaLabel: 'Neocloud premium over the open market', emptyText: 'Needs both neocloud and marketplace prices for a flagship GPU.' });
}

// ---------- price moves ----------
let movesAll = false;
function renderMoves() {
  const list = $('#moves');
  const fmtShort = d => new Date(`${d}T00:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  const items = [
    ...S.announced.map(a => ({ when: `from ${fmtShort(a.effective)}`, tag: ['Announced', 'ann'], ...a, from: a.current, to: a.price })),
    ...S.moves.map(m => ({ ...m, when: fmtWhen(m.t * 1000),
      tag: m.kind === 'added' ? ['New offer', ''] : m.kind === 'removed' ? ['Withdrawn', ''] : null })),
  ];
  if (!items.length) {
    list.replaceChildren(el('li', 'empty-li', `No list-price changes since tracking started ${fmtDay(tsMs(S.first_ts))}. ` +
      'Neocloud list prices change every few weeks; each change will show up here.'));
    $('#moves-more').hidden = true;
    return;
  }
  const shown = movesAll ? items : items.slice(0, 12);
  list.replaceChildren(...shown.map(m => {
    const li = el('li');
    const what = el('span', 'what');
    if (m.tag) what.append(el('span', `tag ${m.tag[1]}`, m.tag[0]));
    what.append(el('b', null, m.source), ` · ${m.gpu} · ${MARKET_LABEL[m.market]}`);
    const px = el('span', 'px');
    if (m.kind === 'added') px.append(`listed at ${fmtPrice(m.to)}`);
    else if (m.kind === 'removed') px.append(`was ${fmtPrice(m.from)}`);
    else {
      px.append(`${fmtPrice(m.from)} → ${fmtPrice(m.to)}`);
      if (m.pct != null) { const d = delta(m.pct, 'previous'); d.title = ''; px.append(d); }
    }
    li.append(el('span', 'when', m.when), what, px);
    return li;
  }));
  const more = $('#moves-more');
  more.hidden = items.length <= 12;
  more.textContent = movesAll ? 'Show fewer' : `Show all ${items.length}`;
}

// ---------- generation shift ----------
let marketData;
async function renderGeneration() {
  marketData ||= fetch('data/market.json', { cache: 'no-cache' }).then(r => r.json());
  const md = await marketData;
  const days = RANGES[state.range], span = tsMs(S.last_ts) - tsMs(S.first_ts);
  const block = days > 31 && span > 31 * DAY ? md.daily : md.hourly;
  const lastT = md.hourly.t.at(-1) * 1000, from = days === Infinity ? -Infinity : lastT - days * DAY;
  const idx = []; block.t.forEach((t, i) => { if (t * 1000 > from) idx.push(i); });
  const gens = ['Blackwell', 'Hopper', 'Ampere'];
  const counts = gens.map(g => idx.map(i => block.gen[g]?.[i] ?? null));
  const shares = idx.map((_, j) => { const tot = counts.reduce((a, c) => a + (c[j] || 0), 0); return tot ? counts.map(c => (c[j] || 0) / tot * 100) : null; });
  const host = $('#mix-chart');
  // stacked: draw the full stack (Blackwell on top) first, then the layers below it
  const cum = gens.map((_, k) => shares.map(s => (s ? s.slice(k).reduce((a, b) => a + b, 0) : null)));
  const series = gens.map((g, k) => ({ name: g, color: GEN_COLOR[g], fill: 'solid', width: 1.5, noDot: true,
    values: cum[k], tipValues: shares.map(s => (s ? s[k] : null)) }));
  $('#mix-legend').replaceChildren(...gens.map(g => { const sp = el('span'); const k = el('span', 'key'); k.style.background = GEN_COLOR[g];
    k.style.height = '8px'; k.style.width = '10px'; sp.append(k, g); return sp; }));
  lineChart(host, { t: idx.map(i => block.t[i] * 1000), series, yDomain: [0, 100], fmt: v => `${Math.round(v)}%`,
    tfmt: block === md.daily ? fmtDay : fmtWhen, tip: tip(), ariaLabel: 'Listed data-center GPUs by generation',
    emptyText: 'No marketplace counts for data-center GPUs yet.' });

  // price per unit of compute, or relative to B200
  const rel = state.genMode === 'rel';
  const gs = COMPUTE_GPUS.map(byName).filter(g => g && g.pflops && g.market[state.market]);
  const hs = await Promise.all(gs.map(g => loadHist(g.slug)));
  const pfHost = $('#pf-chart');
  $('#pf-h').textContent = rel ? 'Price relative to B200' : 'Price per unit of compute';
  $('#pf-sub').textContent = rel
    ? `Typical ${MARKET_LABEL[state.market]} price as a % of B200's (B200 = 100%). Falling = older cards losing value.`
    : `Typical ${MARKET_LABEL[state.market]} price ÷ dense BF16 PFLOPS. B300's extra speed is in FP4, which this doesn't count.`;
  if (!hs.length) return lineChart(pfHost, { t: [], series: [], emptyText: 'No prices yet.' });
  const r = slice(hs[0]);
  const typ = hs.map(h => slice(h).pick(h[r.daily ? 'daily' : 'hourly'].typical[state.market]));
  const b200 = typ[gs.findIndex(g => g.gpu === 'B200')];
  const raw = gs.map((g, i) => typ[i].map((v, j) => (v == null ? null : rel ? (b200?.[j] ? v / b200[j] * 100 : null) : v / g.pflops)));
  const { t, cols } = thin(r.t, raw, Math.round(pfHost.clientWidth / 3));
  const pf = gs.map((g, i) => ({ name: g.gpu, color: S.featured.includes(g.gpu) ? colorOf(g.gpu) : 'var(--muted)', values: cols[i],
    label: `${g.gpu}  ${rel ? fmtUtil(lastVal(cols[i])) : fmtPrice(lastVal(cols[i]))}` }))
    .filter(s => !(rel && s.name === 'B200') && s.values.some(v => v != null));
  $('#pf-legend').replaceChildren(...pf.map(s => { const sp = el('span'); sp.append(keyEl(s.color), s.name); return sp; }));
  lineChart(pfHost, { t, series: pf, endLabels: true, ref: rel ? 100 : null, fmt: rel ? v => `${Math.round(v)}%` : fmtPrice,
    tfmt: r.daily ? fmtDay : fmtWhen, tip: tip(), ariaLabel: rel ? 'Price relative to B200' : 'Price per PFLOP-hour',
    emptyText: rel ? 'Needs a B200 price in this market.' : 'No prices yet.' });
}

// ---------- tightness ----------
async function renderTightness() {
  const disc = state.tightMode === 'discount';
  $('#tight-h').textContent = disc ? 'Spot discount' : 'Market tightness';
  $('#tight-sub').textContent = disc
    ? 'How far below their own on-demand price sources sell spot (median across sources). A shrinking discount means spare capacity is drying up.'
    : '0–100: the average of the share of listed GPUs rented, the share of sources sold out, and 100 minus the spot discount. Higher = harder to get.';
  const gs = S.featured.map(byName);
  const hs = await Promise.all(gs.map(g => loadHist(g.slug)));
  const host = $('#tight-chart');
  const r = slice(hs[0]);
  const raw = hs.map(h => slice(h).pick(h[r.daily ? 'daily' : 'hourly'][disc ? 'discount' : 'tight']));
  const { t, cols } = thin(r.t, raw, Math.round(host.clientWidth / 3));
  const fmt = disc ? v => `${Math.round(v)}%` : v => String(Math.round(v));
  const series = gs.map((g, i) => ({ name: g.gpu, color: colorOf(g.gpu), values: cols[i],
    label: `${g.gpu}  ${lastVal(cols[i]) == null ? '—' : fmt(lastVal(cols[i]))}` })).filter(s => s.values.some(v => v != null));
  $('#tight-legend').replaceChildren(...series.map(s => { const sp = el('span'); sp.append(keyEl(s.color), s.name); return sp; }));
  lineChart(host, { t, series, endLabels: true, yDomain: [0, 100], fmt,
    tfmt: r.daily ? fmtDay : fmtWhen, tip: tip(), ariaLabel: disc ? 'Spot discount' : 'Market tightness',
    emptyText: disc ? 'No source sells both spot and on-demand for the flagships yet.' : 'No demand data yet.' });
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

const srcWindow = range => SOURCE_WINDOW[range] || '30d';
const winText = w => ({ '1d': '24 hours', '7d': '7 days', '30d': '30 days' })[w];

function renderSoldoutBars() {
  const box = $('#soldout-bars');
  $('#soldout-h').textContent = `Hardest to get, last ${RANGE_TEXT[state.range]}`;
  const rows = S.gpus.filter(g => g.demand?.soldout?.share?.[state.range] != null)
    .sort((a, b) => b.demand.soldout.share[state.range] - a.demand.soldout.share[state.range] || b.demand.soldout.now.length - a.demand.soldout.now.length);
  if (!rows.length) { box.replaceChildren(el('p', 'muted', 'No availability data yet.')); return; }
  box.replaceChildren(...rows.map(g => {
    const so = g.demand.soldout, share = so.share[state.range], featured = S.featured.includes(g.gpu);
    const row = el('div', `bar-row${featured ? ' featured' : ''}`);
    row.style.setProperty('--c', featured ? colorOf(g.gpu) : 'var(--ink-2)');
    const track = el('div', 'track');
    if (share > 0) { const u = el('i', 'used'); u.style.width = `${share}%`; track.append(u); }
    if (share < 100) track.append(el('i', 'free'));
    const v = el('span', 'v'); v.append(el('b', null, `${Math.round(share)}%`), ' ', el('small', null, `· ${so.now.length}/${so.of} now`));
    row.append(el('span', 'n', g.gpu), track, v);
    const w = srcWindow(state.range);
    row.addEventListener('pointermove', e => showTip(tip(), e.clientX, e.clientY, `${g.gpu} · sold out, last ${winText(w)}`,
      Object.entries(so.sources).sort((a, b) => (b[1][w] ?? -1) - (a[1][w] ?? -1))
        .map(([s, x]) => ({ value: x[w] == null ? '—' : `${Math.round(x[w])}%`, label: `${s}${so.now.includes(s) ? ' · sold out now' : ''}`,
          color: so.now.includes(s) ? 'var(--series-2)' : null }))));
    row.addEventListener('pointerleave', () => hideTip(tip()));
    row.addEventListener('click', () => selectGpu(g.slug, true));
    return row;
  }));
}

async function renderSupply() {
  const box = $('#supply');
  const gs = S.featured.map(byName).filter(g => g.demand?.total);
  if (!gs.length) { box.replaceChildren(el('p', 'muted', 'No marketplace counts for the flagship GPUs yet.')); return; }
  const cells = gs.map(g => {
    const cell = el('div', 'mini'); cell.style.setProperty('--c', colorOf(g.gpu));
    const head = el('div', 'mini-head');
    head.append(el('b', null, g.gpu), el('span', null, `${g.demand.rented.toLocaleString()} of ${g.demand.total.toLocaleString()} rented`));
    const chart = el('div', 'chart');
    cell.append(head, chart);
    return [g, chart, cell];
  });
  box.replaceChildren(...cells.map(c => c[2]));
  for (const [g, host] of cells) {
    const h = await loadHist(g.slug);
    const r = slice(h);
    const { t, cols: [listed, rented] } = thin(r.t, [r.pick(r.block.listed), r.pick(r.block.rented)], Math.round(host.clientWidth / 3));
    const top = Math.max(1, ...listed.filter(v => v != null));
    lineChart(host, { t, compact: true, yDomain: [0, top * 1.1], tip: tip(), tfmt: r.daily ? fmtDay : fmtWhen,
      fmt: v => Math.round(v).toLocaleString(), ariaLabel: `${g.gpu} listed and rented GPUs`,
      series: [
        { name: 'Listed', color: 'var(--line-muted)', values: listed, width: 1.5, noDot: true },
        { name: 'Rented', color: colorOf(g.gpu), values: rented, area: true },
      ] });
  }
}

const rampColor = (name, v) =>`var(--${name}-${Math.min(7, Math.floor(v / (100 / 7)) + 1)})`;
function renderHeat() {
  const sold = state.heatMode === 'soldout';
  const span = tsMs(S.last_ts) - tsMs(S.first_ts);
  const daily = span >= 3 * DAY;
  const t0 = tsMs(daily ? S.heat.day0 : S.heat.hour0);
  const step = daily ? DAY : DAY / 24;
  const fit = Math.max(7, Math.floor(($('#heat').clientWidth - 108) / 14));  // keep cells at least ~14px wide
  const key = sold ? (daily ? 'sd' : 'sh') : (daily ? 'd' : 'h');
  const rank = name => (sold ? byName(name)?.demand?.soldout?.share?.[state.range] : byName(name)?.demand?.util) ?? -1;
  const rows = Object.entries(S.heat.rows)
    .filter(([, v]) => (v[key] || []).some(x => x != null))
    .map(([name, v]) => ({ name, values: v[key].slice(-fit), now: rank(name), n: v[key].length }))
    .sort((a, b) => b.now - a.now);
  const skip = Math.max(0, (rows[0]?.n || 0) - fit);
  const cols = (rows[0]?.values || []).map((_, i) => t0 + (i + skip) * step);
  const shown = rows[0]?.values.length || 0;
  const what = sold ? 'Share of sources sold out' : 'Utilization';
  $('#heat-h').textContent = daily ? `${what} by day, last ${shown} days` : `${what} by hour, last ${shown} hours`;
  const ramp = el('span', 'ramp');
  for (let i = 1; i <= 7; i++) { const c = el('i'); c.style.background = `var(--${sold ? 'hot' : 'seq'}-${i})`; ramp.append(c); }
  $('#heat-scale').replaceChildren('0%', ramp, sold ? 'all sources sold out' : '100% rented');
  heatmap($('#heat'), { rows, cols, color: v => rampColor(sold ? 'hot' : 'seq', v),
    fmt: v => (sold ? `${Math.round(v)}% of sources sold out` : `${v.toFixed(1)}% rented`), valueLabel: '',
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
  renderAvailability(g, r);
}

function renderAvailability(g, r) {
  const d = g.demand || {}, so = d.soldout;
  const box = $('#avail');
  box.hidden = !d.total && !so;
  if (box.hidden) return;
  $('#avail-sub').textContent = [
    d.total ? `${d.rented.toLocaleString()} of ${d.total.toLocaleString()} listed GPUs rented right now on open marketplaces.` : '',
    so ? (so.now.length ? `Sold out at ${so.now.length} of ${so.of} sources right now.` : `Available at all ${so.of} sources right now.`) : '',
  ].join(' ');
  const host = $('#avail-chart');
  const { t, cols: [listed, rented] } = thin(r.t, [r.pick(r.block.listed), r.pick(r.block.rented)], Math.round(host.clientWidth / 3));
  const color = S.featured.includes(g.gpu) ? colorOf(g.gpu) : 'var(--accent)';
  const series = [
    { name: 'Listed GPUs', color: 'var(--line-muted)', values: listed, width: 1.5, label: `Listed ${Math.round(lastVal(listed) ?? 0).toLocaleString()}` },
    { name: 'Rented GPUs', color, values: rented, area: true, label: `Rented ${Math.round(lastVal(rented) ?? 0).toLocaleString()}` },
  ].filter(s => s.values.some(v => v != null));
  $('#avail-legend').replaceChildren(...series.map(s => { const sp = el('span'); sp.append(keyEl(s.color), s.name); return sp; }));
  const top = Math.max(1, ...series.flatMap(s => s.values.filter(v => v != null)));
  lineChart(host, { t, series, endLabels: true, yDomain: [0, niceTop(top)], fmt: v => Math.round(v).toLocaleString(),
    tfmt: r.daily ? fmtDay : fmtWhen, tip: tip(), ariaLabel: `${g.gpu} listed and rented GPUs`,
    emptyText: `No marketplace GPU counts for ${g.gpu} — availability comes only from stock levels.` });

  const table = $('#avail-table');
  if (!so) { table.replaceChildren(); return; }
  const head = el('thead'); const hr = el('tr');
  ['Source', 'Now', 'Sold out 24h', '7d', '30d'].forEach((x, i) => hr.append(el('th', i < 2 ? 'l' : null, x)));
  head.append(hr);
  const body = el('tbody');
  for (const [s, x] of Object.entries(so.sources)) {
    const tr = el('tr');
    const now = so.now.includes(s);
    const bs = d.by_source?.[s];
    const nowText = now ? 'sold out' : bs?.total ? `${(bs.total - bs.rented).toLocaleString()} of ${bs.total.toLocaleString()} free`
      : d.stock?.[s] != null ? `${STOCK[d.stock[s]]} stock` : 'available';
    tr.append(el('td', 'l', s), el('td', `l${now ? '' : ' muted'}`, nowText),
      ...['1d', '7d', '30d'].map(w => el('td', x[w] ? null : 'muted', x[w] == null ? '—' : `${Math.round(x[w])}%`)));
    body.append(tr);
  }
  table.replaceChildren(head, body);
}
const niceTop = v => { const m = 10 ** Math.floor(Math.log10(v)); return Math.ceil((v * 1.08) / m) * m; };

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
    'Segments: neoclouds = GPU clouds and aggregators, open market = marketplaces and decentralized networks, hyperscalers = AWS, Azure, Oracle; each has its own index. ' +
    'Tightness = average of rented share, share of sources sold out, and 100 − spot discount. Compute prices use dense BF16 PFLOPS from NVIDIA datasheets. ' +
    '"Rented" is rented ÷ listed GPUs on open marketplaces that publish both. ' +
    '"Sold out" means a source had no free GPUs of that model (marketplace counts) or reported no stock (Scaleway shortage); ' +
    '"hardest to get" is the share of source-hours that were sold out in the range. io.net reports almost everything as hired, so it counts as sold out most of the time. ' +
    'Times are in your local time zone.'));
}

// ---------- wiring ----------
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get('market') in MARKET_LABEL) state.market = p.get('market');
  if (p.get('range') in RANGES) state.range = p.get('range');
  if (['pct', 'usd'].includes(p.get('trend'))) state.trendMode = p.get('trend');
  if (['util', 'soldout'].includes(p.get('heat'))) state.heatMode = p.get('heat');
  if (['pflop', 'rel'].includes(p.get('gen'))) state.genMode = p.get('gen');
  if (['tight', 'discount'].includes(p.get('tight'))) state.tightMode = p.get('tight');
  if (p.get('gpu')) state.gpu = p.get('gpu');
}
function writeHash() {
  history.replaceState(null, '', '#' + new URLSearchParams({ market: state.market, range: state.range, trend: state.trendMode,
    heat: state.heatMode, gen: state.genMode, tight: state.tightMode, gpu: state.gpu }));
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
  renderSegments();
  renderMoves();
  renderGeneration();
  renderTightness();
  renderBars();
  renderUtilChart();
  renderSoldoutBars();
  renderSupply();
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
  const only = { trendMode: renderTrend, heatMode: renderHeat, genMode: renderGeneration, tightMode: renderTightness }[seg.dataset.filter];
  if (only) { renderSegs(); writeHash(); only(); } else renderAll();
}));
$('#gpu-pick').addEventListener('change', e => selectGpu(e.target.value, false));
$('#moves-more').addEventListener('click', () => { movesAll = !movesAll; renderMoves(); });
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
