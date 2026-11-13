import { lineChart, heatmap, stripPlot, showTip, hideTip } from './chart.js';

const GPU_COLORS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)'];
const MARKET_LABEL = { spot: 'spot', on_demand: 'on-demand' };
const RANGES = { '1d': 1, '7d': 7, '30d': 30, '90d': 90, '1y': 365, all: Infinity };
const PERIODS = ['24h', '7d', '30d'];
const STOCK = ['none left', 'low', 'medium', 'high'];
const DAY = 86400e3;

const state = { market: 'on_demand', srange: '1y', range: '7d', trendMode: 'pct', heatMode: 'util', genMode: 'pflop',
  tightMode: 'tight', outGpu: null, gpu: null };
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
const colorOf = name => { const i = S.featured.indexOf(name); return i >= 0 ? GPU_COLORS[i % GPU_COLORS.length] : 'var(--ink-2)'; };

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
  $('#stamp').textContent = `Updated ${fmtWhen(last)} · prices from ${Object.keys(S.sources).length} providers, checked every hour`;
  const days = (last - first) / DAY;
  const missing = [[1, '24-hour'], [7, '7-day'], [30, '30-day']].filter(([d]) => days < d).map(([, l]) => l);
  const n = $('#notice');
  n.hidden = !missing.length;
  if (missing.length) n.textContent = `History is still building — tracking started ${fmtDay(first)}. ` +
    `Lines grow with every hourly snapshot; ${missing.join(', ').replace(/, ([^,]*)$/, ' and $1')} changes appear once enough history exists.`;
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

// ---------- simple view: one plain card per flagship GPU ----------
const SRANGE = { '1w': 7, '1m': 30, '1y': 365, '3y': 1095 };
const SRANGE_TEXT = { '1w': 'last week', '1m': 'last month', '1y': 'last 12 months', '3y': 'last 3 years' };
const DISPLAY_NAME = { 'H100 SXM': 'H100' };
const BLURB = {
  B300: 'Newest generation (Blackwell Ultra)',
  B200: 'Current generation (Blackwell)',
  H200: 'Previous generation with more memory (Hopper)',
  'H100 SXM': 'Previous generation (Hopper) — the workhorse of the AI boom',
};
const monthMsOf = (ym, nowMs) => (ym === S.last_ts.slice(0, 7) ? nowMs : Date.parse(`${ym}-15T00:00:00Z`));

function availability(util) {
  if (util == null) return null;
  if (util >= 90) return 'Very hard to get';
  if (util >= 75) return 'Hard to get';
  if (util >= 50) return 'Some available';
  return 'Easy to get';
}

/** % change between the newest monthly price and the one about `months` earlier, or null. */
function monthlyChange(hist, months) {
  if (!hist?.length) return null;
  const idx = ym => Number(ym.slice(0, 4)) * 12 + Number(ym.slice(5, 7));
  const last = hist.at(-1);
  const then = [...hist].reverse().find(([ym]) => idx(last[0]) - idx(ym) >= months);
  return then && idx(last[0]) - idx(then[0]) <= months + 1 ? (last[1] / then[1] - 1) * 100 : null;
}

function fact(k, v, muted) {
  const li = el('li');
  li.append(el('span', 'k', k), el('span', muted ? 'muted' : null, v));
  return li;
}

async function renderSimple() {
  const od = state.market === 'on_demand';
  const draws = [];
  $('#gpus').replaceChildren(...S.featured.map(name => {
    const g = byName(name), m = g.market[state.market], color = colorOf(name);
    const seg = m?.segments?.neocloud, out = S.outlook?.[name];
    const hist = od ? out?.history : null;
    const card = el('article', 'gpu');
    card.style.setProperty('--c', color);

    const info = el('div', 'gpu-info');
    const h = el('h2', 'gpu-name', DISPLAY_NAME[name] || name);
    h.append(el('small', null, `${g.vram} GB`));
    info.append(h, el('p', 'gpu-blurb', BLURB[name] || ''));
    if (!m) {
      info.append(el('p', 'gpu-price-sub', `No ${od ? 'normal' : 'interruptible'} prices for this GPU right now.`));
      card.append(info);
      return card;
    }
    const price = seg?.typical ?? m.typical;
    const p = el('div', 'gpu-price', fmtPrice(price)); p.append(el('small', null, '/ hour'));
    info.append(p, el('div', 'gpu-price-sub', seg ? 'typical price at GPU clouds' : 'typical price across providers'));

    // the one-line answer: rising, falling or stable, over 3 months (or a week while history is short)
    const c3 = monthlyChange(hist, 3), c7 = (seg?.chg ?? m.chg)['7d'];
    const [chg, over] = c3 != null ? [c3, 'in 3 months'] : c7 != null ? [c7, 'this week'] : [null, null];
    const v = el('div', `verdict ${chg == null ? '' : chg > 3 ? 'up' : chg < -3 ? 'down' : ''}`);
    if (chg == null) v.append('Not enough history yet');
    else {
      v.append(chg > 3 ? '▲ Rising' : chg < -3 ? '▼ Falling' : '▬ Stable');
      v.append(el('span', null, `${fmtPct(chg)} ${over}`));
    }
    info.append(v);

    const facts = el('ul', 'facts');
    const c12 = monthlyChange(hist, 12);
    if (c12 != null) facts.append(fact('Last 12 months', `${fmtPct(c12)}`));
    const avail = availability(g.demand?.util);
    facts.append(avail ? fact('Availability', `${avail} — ${fmtUtil(g.demand.util)} of listed GPUs are rented`)
      : fact('Availability', 'not measured for this GPU', true));
    if (out?.analog && od) facts.append(fact('In a year', `${fmtPrice(out.analog.lo.at(-1))}–${fmtPrice(out.analog.hi.at(-1))} if it follows older GPUs`));
    const ann = S.announced.find(a => a.gpu === name && a.market === state.market);
    if (ann) facts.append(fact('Announced', `${ann.source} raises to ${fmtPrice(ann.price)} on ${fmtDay(Date.parse(`${ann.effective}T00:00:00Z`))}`));
    info.append(facts);

    const chartBox = el('div', 'gpu-chart');
    const head = el('div', 'gpu-chart-head');
    head.append(el('h3', null, `Price per hour, ${SRANGE_TEXT[state.srange]}`));
    const legend = el('div', 'legend');
    head.append(legend);
    const host = el('div', 'chart');
    const note = el('div', 'gpu-note');
    chartBox.append(head, host, note);

    const cheap = el('div', 'cheap');
    cheap.append(el('h3', null, 'Cheapest places to rent it right now'));
    const list = el('ol', 'cheap-list');
    for (const q of [...m.prices].sort((a, b) => a.min - b.min).slice(0, 5)) {
      const li = el('li');
      const pp = el('div', 'p', fmtPrice(q.min)); pp.append(el('small', null, ' /hr'));
      li.append(pp, el('div', 's', q.source), el('div', 't', (S.sources[q.source]?.kind || '').toLowerCase()));
      list.append(li);
    }
    cheap.append(list);

    card.append(info, chartBox, cheap);
    draws.push({ g, host, legend, note, color, hist, out, od });
    return card;
  }));

  for (const d of draws) await drawSimpleChart(d);
}

async function drawSimpleChart({ g, host, legend, note, color, hist, out, od }) {
  const nowMs = tsMs(S.last_ts), days = SRANGE[state.srange];
  const from = nowMs - days * DAY;
  const keyItem = (c, label, band) => { const s = el('span'); const k = el('span', band ? 'sw' : 'key'); k.style.background = c; if (band) k.style.opacity = 0.35; s.append(k, label); return s; };
  let t, series;
  if (days >= 365 && hist?.length > 1) {
    // long view: monthly history from archived price lists, plus the likely range for the next year
    const pts = hist.map(([ym, v]) => [monthMsOf(ym, nowMs), v]).filter(([ms]) => ms >= from);
    const fut = out?.analog ? out.analog.months.map((ym, i) => [Date.parse(`${ym}-15T00:00:00Z`), out.analog.lo[i], out.analog.hi[i]]) : [];
    t = [...new Set([...pts.map(p => p[0]), ...fut.map(f => f[0])])].sort((a, b) => a - b);
    const at = (list, i) => { const m = new Map(list.map(p => [p[0], p[i]])); return t.map(ms => m.get(ms) ?? null); };
    const now = pts.at(-1)?.[1];
    series = [{ name: 'Price', color, values: at(pts, 1), area: true, connect: true, width: 2.5 }];
    if (fut.length) {
      const lo = at(fut, 1), hi = at(fut, 2), i0 = t.indexOf(nowMs);
      if (i0 >= 0) { lo[i0] = now; hi[i0] = now; }
      series.push({ name: 'Likely range', color, values: hi, band: { lo }, connect: true });
    }
    legend.replaceChildren(keyItem(color, 'price'), ...(fut.length ? [keyItem(color, 'likely range if it ages like older GPUs', true)] : []));
    note.textContent = `Monthly, from GPU clouds' published price lists (archived since ${fmtDay(monthMsOf(hist[0][0], nowMs))}).`;
  } else {
    // short view: the hourly prices we have collected
    const h = await loadHist(g.slug);
    const b = h.hourly;
    const vals = b.seg?.[`neocloud|${state.market}`]?.some(v => v != null) ? b.seg[`neocloud|${state.market}`] : b.typical[state.market] || [];
    const idx = b.t.map((x, i) => i).filter(i => b.t[i] * 1000 > from);
    t = idx.map(i => b.t[i] * 1000);
    series = [{ name: 'Price', color, values: idx.map(i => vals[i] ?? null), area: true, width: 2.5 }];
    legend.replaceChildren(keyItem(color, 'price'));
    const first = tsMs(S.first_ts);
    note.textContent = first > from ? `Hourly prices — tracking started ${fmtDay(first)}, so this fills in over time.`
      : 'Hourly prices from GPU clouds.';
    if (!od && days >= 365) note.textContent = 'Interruptible prices have only been tracked since ' + fmtDay(first) + '; the archive covers normal prices.';
  }
  if (series.length === 1) {  // hourly data can be dense: average it down to the chart's width
    const th = thin(t, [series[0].values], Math.round(host.clientWidth / 3));
    t = th.t;
    series[0].values = th.cols[0];
  }
  lineChart(host, { t, series, fmt: fmtPrice, tip: tip(),
    tfmt: ms => (days >= 365 ? (ms > nowMs ? `${new Date(ms).toLocaleDateString(undefined, { month: 'short', year: 'numeric' })} (projected)` : new Date(ms).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })) : fmtWhen(ms)),
    marker: days >= 365 ? nowMs : null, markerLabel: 'today', ariaLabel: `${g.gpu} price per hour`,
    emptyText: 'No price history for this view yet.' });
}

// ---------- outlook ----------
const TERM_LABEL = { 0: 'On-demand', 30: '1 mo', 90: '3 mo', 180: '6 mo', 365: '1 yr', 1095: '3 yr', 1825: '5 yr' };
const TERM_ORDER = [0, 30, 90, 180, 365, 1095, 1825];
const spanText = (a, b) => `${a ? `${a >= 365 ? `${a / 365} yr` : `${Math.round(a / 30)} mo`}` : 'now'}–${b >= 365 ? `${b / 365} yr` : `${Math.round(b / 30)} mo`}`;

function kpi(label, color, value, pct, sub, off) {
  const box = el('div', `kpi${off ? ' off' : ''}`);
  const k = el('div', 'k'); if (color) { const sw = el('span', 'sw'); sw.style.background = color; k.append(sw); } k.append(label);
  const v = el('div', 'v', value);
  if (pct != null) { const s = el('small'); s.append(delta(pct, 'today')); v.append(s); }
  box.append(k, v, el('div', 's', sub));
  return box;
}

async function renderOutlook() {
  const tabs = $('#out-tabs');
  if (!tabs.children.length) tabs.replaceChildren(...S.featured.map(n => { const b = el('button', null, n); b.setAttribute('role', 'radio'); b.dataset.value = n; return b; }));
  if (!byName(state.outGpu)) state.outGpu = S.featured.includes('H100 SXM') ? 'H100 SXM' : S.featured[0];
  renderSegs();
  const g = byName(state.outGpu), color = colorOf(g.gpu);
  const out = S.outlook?.[g.gpu], fwd = g.forward, tr = g.trend?.on_demand;
  const nowMs = tsMs(S.last_ts), nowMonth = S.last_ts.slice(0, 7), endMs = nowMs + 395 * DAY;
  const anchor = out?.history?.at(-1)?.[1] ?? g.market.on_demand?.segments?.neocloud?.typical ?? g.market.on_demand?.typical;
  const monthMs = ym => (ym === nowMonth ? nowMs : Date.parse(`${ym}-15T00:00:00Z`));
  const ann = S.announced.filter(a => a.gpu === g.gpu && a.market === 'on_demand');
  const path = (fwd?.path || []).filter(p => nowMs + p.from * DAY < endMs);
  const ratioOf = p => p.ratio * anchor;

  // one shared time grid; every series is drawn with connect so it skips the other series' points
  const T = new Set([nowMs]);
  (out?.history || []).forEach(([m]) => T.add(monthMs(m)));
  (out?.analog?.months || []).forEach(m => T.add(monthMs(m)));
  path.forEach(p => { T.add(nowMs + p.from * DAY + (p.from ? HOUR_MS : 0)); T.add(Math.min(endMs, nowMs + p.to * DAY)); });
  ann.forEach(a => T.add(Date.parse(`${a.effective}T00:00:00Z`)));
  (tr?.proj || []).forEach(p => T.add(nowMs + p.days * DAY));
  const t = [...T].sort((a, b) => a - b);
  const col = fill => { const v = t.map(() => null); fill((ms, val) => { const i = t.indexOf(ms); if (i >= 0) v[i] = val; }); return v; };

  const series = [];
  if (out?.history?.length) series.push({ name: 'History (neocloud list price)', color, width: 2.5, connect: true,
    values: col(set => { out.history.forEach(([m, p]) => set(monthMs(m), p)); set(nowMs, anchor); }) });
  else if (anchor) series.push({ name: 'Today (neocloud)', color, values: col(set => set(nowMs, anchor)), dotRadius: 5 });
  if (out?.analog) {
    const a = out.analog;
    series.push({ name: `Generation analog range (${a.analogs.join(', ')})`, color, connect: true,
      values: col(set => { set(nowMs, anchor); a.months.forEach((m, i) => set(monthMs(m), a.hi[i])); }),
      band: { lo: col(set => { set(nowMs, anchor); a.months.forEach((m, i) => set(monthMs(m), a.lo[i])); }) } });
    series.push({ name: 'Generation analog (median)', color: `color-mix(in srgb, ${color} 55%, var(--surface))`, width: 2,
      connect: true, noDot: true,
      values: col(set => { set(nowMs, anchor); a.months.forEach((m, i) => set(monthMs(m), a.mid[i])); }) });
  }
  if (path.length && anchor) series.push({ name: 'Market-implied (from commitment prices)', color: 'var(--ink-2)', width: 2, connect: true, noDot: true,
    values: col(set => path.forEach(p => { set(nowMs + p.from * DAY + (p.from ? HOUR_MS : 0), ratioOf(p)); set(Math.min(endMs, nowMs + p.to * DAY), ratioOf(p)); })) });
  for (const src of [...new Set(ann.map(a => a.source))]) series.push({ name: `Announced (${src})`, color: 'var(--ink)', dotRadius: 5,
    values: col(set => ann.filter(a => a.source === src).forEach(a => set(Date.parse(`${a.effective}T00:00:00Z`), a.price))) });
  if (tr) {
    const base = g.market.on_demand?.typical;
    series.push({ name: 'Live trend range', color: 'var(--muted)', connect: true,
      values: col(set => { set(nowMs, base); tr.proj.forEach(p => set(nowMs + p.days * DAY, p.hi)); }),
      band: { lo: col(set => { set(nowMs, base); tr.proj.forEach(p => set(nowMs + p.days * DAY, p.lo)); }) } });
  }
  $('#out-legend').replaceChildren(...series.filter(s => !s.name.startsWith('Generation analog (')).map(s => {
    const sp = el('span');
    const k = el('span', s.band ? 'sw' : 'key'); k.style.background = s.color; if (s.band) k.style.opacity = 0.35;
    sp.append(k, s.name); return sp;
  }));
  lineChart($('#out-chart'), { t, series, fmt: fmtPrice, tfmt: ms => (ms > nowMs ? fmtDay(ms) : ms === nowMs ? 'Today' : new Date(ms).toLocaleDateString(undefined, { month: 'short', year: 'numeric' })),
    tip: tip(), marker: nowMs, markerLabel: 'today', ariaLabel: `${g.gpu} price outlook`, emptyText: `No on-demand prices for ${g.gpu} yet.` });

  // headline numbers
  const k = [];
  const seg = path.find(p => p.from >= 180 && p.to <= 365) || path.at(-1);
  k.push(seg && anchor
    ? kpi('Commitments imply', 'var(--ink-2)', fmtPrice(ratioOf(seg)), (seg.ratio - 1) * 100,
      `average for ${spanText(seg.from, seg.to)} out, from ${(n => (n === 1 ? "1 source's" : `${n} sources'`))(Math.max(...fwd.points.map(p => p.n)))} commitment prices`)
    : kpi('Commitments imply', null, 'No commitment prices yet', null, 'appears when sources publish term prices', true));
  const an = out?.analog;
  k.push(an
    ? kpi('Older generations suggest', color, fmtPrice(an.mid.at(-1)), (an.mid.at(-1) / anchor - 1) * 100,
      `in 12 months, if it ages like ${an.analogs.join(' / ')} did from month ${an.age}`)
    : kpi('Older generations suggest', null, 'Needs archived history', null, 'archived pricing pages fill this in', true));
  const a0 = ann[0];
  k.push(a0
    ? kpi('Announced', 'var(--ink)', `${fmtPrice(a0.price)}`, a0.pct, `${a0.source} from ${fmtDay(Date.parse(`${a0.effective}T00:00:00Z`))}`)
    : kpi('Announced', null, 'Nothing announced', null, 'future price lists show up here', true));
  const days = Math.max(0, Math.floor((nowMs - tsMs(S.first_ts)) / DAY));
  k.push(tr
    ? kpi('Live trend', 'var(--muted)', `${fmtPct(tr.per_week)}/wk`, null, `30 days: ${fmtPrice(tr.proj.at(-1).mid)} (${fmtPrice(tr.proj.at(-1).lo)}–${fmtPrice(tr.proj.at(-1).hi)})`)
    : kpi('Live trend', null, `Starts after 14 days`, null, `${days} day${days === 1 ? '' : 's'} of hourly data so far`, true));
  $('#out-kpis').replaceChildren(...k);

  renderCommitCurve(g, color);
  renderVintage();
}
const HOUR_MS = 3600e3;

function renderCommitCurve(g, color) {
  const host = $('#curve-chart'), fwd = g.forward;
  if (!fwd) { $('#curve-legend').replaceChildren(); return lineChart(host, { t: [], series: [], emptyText: `No commitment prices for ${g.gpu} yet.` }); }
  const pos = TERM_ORDER.filter(d => d === 0 || fwd.points.some(p => p.days === d) || Object.values(fwd.sources).some(s => s[d] != null));
  const t = pos.map((_, i) => i * DAY);
  const series = Object.entries(fwd.sources).map(([s, v]) => ({ name: s, key: s, color: 'var(--line-muted)', width: 1.5, connect: true,
    values: pos.map(d => (d === 0 ? 100 : v[d] != null ? v[d] / v.on_demand * 100 : null)) }));
  const med = { name: 'Median across sources', key: 'Median', color, width: 2.5, connect: true,
    values: pos.map(d => (d === 0 ? 100 : (fwd.points.find(p => p.days === d)?.ratio ?? null) * 100 || null)) };
  med.label = `${Math.round(med.values.filter(v => v != null).at(-1))}%`;
  series.push(med);
  const legend = $('#curve-legend');
  legend.replaceChildren(...series.map(s => {
    const b = el('button'); b.type = 'button'; b.append(keyEl(s.color), s.name);
    const on = () => { host.classList.add('hl'); host.querySelector(`.series[data-series="${CSS.escape(s.key)}"]`)?.classList.add('on'); };
    const off = () => { host.classList.remove('hl'); host.querySelectorAll('.series.on').forEach(n => n.classList.remove('on')); };
    b.addEventListener('pointerenter', on); b.addEventListener('pointerleave', off); b.addEventListener('focus', on); b.addEventListener('blur', off);
    return b;
  }));
  lineChart(host, { t, series, endLabels: true, ref: 100, fmt: v => `${Math.round(v)}%`, tfmt: ms => TERM_LABEL[pos[Math.round(ms / DAY)]],
    xTicks: pos.map((d, i) => ({ t: i * DAY, label: TERM_LABEL[d] })), tip: tip(), ariaLabel: `${g.gpu} commitment curve` });
}

function renderVintage() {
  const host = $('#vin-chart');
  const gpus = COMPUTE_GPUS.filter(n => S.vintage?.[n]?.length > 1);
  if (!gpus.length) { $('#vin-legend').replaceChildren(); return lineChart(host, { t: [], series: [], emptyText: 'Builds from archived pricing pages plus live data — needs at least two months per GPU.' }); }
  const maxAge = Math.max(...gpus.flatMap(n => S.vintage[n].map(p => p[0])));
  const ages = Array.from({ length: maxAge + 1 }, (_, i) => i);
  const series = gpus.map(n => {
    const m = new Map(S.vintage[n]);
    const values = ages.map(a => m.get(a) ?? null);
    return { name: n, color: S.featured.includes(n) ? colorOf(n) : 'var(--muted)', values, connect: true, label: `${n} ${Math.round(values.filter(v => v != null).at(-1))}%` };
  });
  $('#vin-legend').replaceChildren(...series.map(s => { const sp = el('span'); sp.append(keyEl(s.color), s.name); return sp; }));
  const step = maxAge > 36 ? 12 : maxAge > 12 ? 6 : 2;
  lineChart(host, { t: ages.map(a => a * 30 * DAY), series, endLabels: true, ref: 100, fmt: v => `${Math.round(v)}%`,
    tfmt: ms => `Month ${Math.round(ms / 30 / DAY)} after first listing`,
    xTicks: ages.filter(a => a % step === 0).map(a => ({ t: a * 30 * DAY, label: a ? `${a} mo` : 'first listed' })),
    tip: tip(), ariaLabel: 'Generation age curves' });
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
    const mk = MARKET_LABEL[m.market] || (m.market.startsWith('term_') ? `${TERM_LABEL[parseInt(m.market.slice(5), 10)]} commitment` : m.market);
    what.append(el('b', null, m.source), ` · ${m.gpu} · ${mk}`);
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
    'Outlook: "market-implied" turns each source\'s commitment discounts (Together, DigitalOcean, Hyperbolic, Azure reservations) into the average price they imply ' +
    'for each future period, like forward rates from a yield curve — it includes a discount for certainty, so it leans low. "Generation analog" follows older GPUs\' ' +
    'month-by-month list-price path (from archived pricing pages) from the same age. The live trend is a robust 30-day trend, shown after 14 days of data. ' +
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
  if (p.get('period') in SRANGE) state.srange = p.get('period');
  if (['pct', 'usd'].includes(p.get('trend'))) state.trendMode = p.get('trend');
  if (['util', 'soldout'].includes(p.get('heat'))) state.heatMode = p.get('heat');
  if (['pflop', 'rel'].includes(p.get('gen'))) state.genMode = p.get('gen');
  if (['tight', 'discount'].includes(p.get('tight'))) state.tightMode = p.get('tight');
  if (p.get('out')) state.outGpu = p.get('out');
  if (p.get('gpu')) state.gpu = p.get('gpu');
}
function writeHash() {
  history.replaceState(null, '', '#' + new URLSearchParams({ market: state.market, period: state.srange, range: state.range, trend: state.trendMode,
    heat: state.heatMode, gen: state.genMode, tight: state.tightMode, out: state.outGpu || '', gpu: state.gpu }));
}
function renderSegs() {
  document.querySelectorAll('.seg').forEach(seg => seg.querySelectorAll('button')
    .forEach(b => b.setAttribute('aria-checked', String(state[seg.dataset.filter] === b.dataset.value))));
}

function renderAll() {
  renderSegs();
  writeHash();
  renderSimple();
  renderMoves();
  if ($('#advanced').open) renderAdvanced();
}

// the advanced charts only render while their section is open (closed, they have no size to draw into)
function renderAdvanced() {
  renderOutlook();
  renderTrend();
  renderSegments();
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
  const only = { srange: renderSimple, range: renderAdvanced, trendMode: renderTrend, heatMode: renderHeat,
    genMode: renderGeneration, tightMode: renderTightness, outGpu: renderOutlook }[seg.dataset.filter];
  if (only) { renderSegs(); writeHash(); only(); } else renderAll();
}));
$('#advanced').addEventListener('toggle', () => { if ($('#advanced').open) renderAdvanced(); });
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
