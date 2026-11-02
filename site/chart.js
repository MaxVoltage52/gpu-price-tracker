// Small dependency-free SVG chart kit: line/area charts, heatmap, strip plot, sparkline, tooltip.
const NS = 'http://www.w3.org/2000/svg';
const HOUR = 3600e3, DAY = 24 * HOUR;
let uid = 0;

export function svg(tag, attrs = {}, parent) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}
const text = (parent, str, attrs) => { const t = svg('text', attrs, parent); t.textContent = str; return t; };

// ---------- tooltip ----------
export function showTip(tip, clientX, clientY, title, rows) {
  tip.replaceChildren();
  const head = document.createElement('div');
  head.className = 't'; head.textContent = title;
  tip.appendChild(head);
  for (const r of rows) {
    const row = document.createElement('div'); row.className = 'r';
    const key = document.createElement('span'); key.className = 'key'; key.style.background = r.color || 'var(--muted)';
    const val = document.createElement('b'); val.textContent = r.value;
    const name = document.createElement('span'); name.textContent = r.label;
    row.append(key, val, name);
    tip.appendChild(row);
  }
  tip.hidden = false;
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  let left = clientX + 16;
  if (left + tw > window.innerWidth - 8) left = clientX - tw - 16;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${Math.min(Math.max(8, clientY - th / 2), window.innerHeight - th - 8)}px`;
}
export const hideTip = tip => { tip.hidden = true; };

// ---------- scales ----------
export function niceTicks(lo, hi, count = 5) {
  if (lo === hi) { const pad = Math.abs(lo) * 0.1 || 1; lo -= pad; hi += pad; }
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
  const start = Math.floor(lo / step) * step, end = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = start; v <= end + step / 2; v += step) ticks.push(+v.toFixed(10));
  return { lo: start, hi: end, step, ticks };
}

const T_STEPS = [
  [1, 'h', HOUR], [3, 'h', 3 * HOUR], [6, 'h', 6 * HOUR], [12, 'h', 12 * HOUR],
  [1, 'd', DAY], [2, 'd', 2 * DAY], [7, 'w', 7 * DAY], [14, 'w', 14 * DAY],
  [1, 'M', 30 * DAY], [3, 'M', 91 * DAY], [6, 'M', 182 * DAY], [1, 'y', 365 * DAY],
];
export function timeTicks(t0, t1, target = 6) {
  const [n, unit] = T_STEPS.find(s => (t1 - t0) / s[2] <= target) || T_STEPS.at(-1);
  const d = new Date(t0);
  d.setMinutes(0, 0, 0);
  if (unit === 'h') d.setHours(Math.floor(d.getHours() / n) * n);
  else {
    d.setHours(0);
    if (unit === 'w') d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    if (unit === 'M' || unit === 'y') d.setDate(1);
    if (unit === 'M') d.setMonth(Math.floor(d.getMonth() / n) * n);
    if (unit === 'y') d.setMonth(0);
  }
  const ticks = [];
  for (let guard = 0; guard < 400 && +d <= t1; guard++) {
    if (+d >= t0) ticks.push(+d);
    if (unit === 'h') d.setHours(d.getHours() + n);
    else if (unit === 'd' || unit === 'w') d.setDate(d.getDate() + n);
    else if (unit === 'M') d.setMonth(d.getMonth() + n);
    else d.setFullYear(d.getFullYear() + n);
  }
  return { ticks, unit };
}
export function tickLabel(t, unit) {
  const d = new Date(t);
  if (unit === 'h' && (d.getHours() || d.getMinutes()))
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  if (unit === 'M') return d.toLocaleDateString(undefined, { month: 'short', year: d.getMonth() === 0 ? 'numeric' : undefined });
  if (unit === 'y') return String(d.getFullYear());
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function emptyState(host, msg) {
  const p = document.createElement('div');
  p.className = 'empty';
  p.textContent = msg;
  host.appendChild(p);
}

/**
 * Line / area chart with crosshair tooltip.
 * o: t (ms[]), series [{ name, color, values, key?, area?, width?, label?, noDot? }], fmt(v, step), tfmt(ms), tip,
 *    yDomain? [lo, hi], ref? (value drawn as a reference line), endLabels?, compact?, emptyText, ariaLabel
 */
export function lineChart(host, o) {
  host.replaceChildren();
  const all = o.series.flatMap(s => s.values.filter(v => v != null));
  if (!o.t.length || !all.length) return emptyState(host, o.emptyText || 'No data for this selection yet.');
  const W = host.clientWidth, H = host.clientHeight;
  const labelW = o.endLabels ? Math.min(140, Math.max(0, ...o.series.map(s => (s.label || '').length * 6.8 + 18))) : 0;
  const m = o.compact ? { l: 2, r: 6 + labelW, t: 8, b: 4 } : { l: 52, r: 14 + labelW, t: 12, b: 26 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  let lo = Math.min(...all), hi = Math.max(...all);
  if (o.ref != null) { lo = Math.min(lo, o.ref); hi = Math.max(hi, o.ref); }
  let yt;
  if (o.yDomain) yt = { ...niceTicks(o.yDomain[0], o.yDomain[1], 4), lo: o.yDomain[0], hi: o.yDomain[1] };
  else if (o.compact) { const pad = (hi - lo) * 0.18 || Math.abs(hi) * 0.04 || 1; yt = { lo: lo - pad, hi: hi + pad, ticks: [] }; }
  else yt = niceTicks(lo, hi, H < 260 ? 4 : 5);
  let t0 = o.t[0], t1 = o.t.at(-1);
  if (t0 === t1) { t0 -= 6 * HOUR; t1 += 6 * HOUR; }
  const x = v => m.l + ((v - t0) / (t1 - t0)) * iw;
  const y = v => m.t + ih - ((v - yt.lo) / (yt.hi - yt.lo)) * ih;

  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': o.ariaLabel || 'Chart' }, host);
  const defs = svg('defs', {}, root);
  if (!o.compact) {
    const axis = svg('g', { class: 'axis' }, root);
    yt.ticks.forEach(v => {
      if (v < yt.lo - 1e-9 || v > yt.hi + 1e-9) return;
      svg('line', { x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v), stroke: 'var(--grid)', 'shape-rendering': 'crispEdges' }, axis);
      text(axis, o.fmt(v, yt.step), { x: m.l - 8, y: y(v), 'text-anchor': 'end', 'dominant-baseline': 'middle' });
    });
    svg('line', { x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih, stroke: 'var(--axis)', 'shape-rendering': 'crispEdges' }, axis);
    const xt = timeTicks(t0, t1, Math.max(4, Math.floor(iw / 80)));
    xt.ticks.forEach(v => text(axis, tickLabel(v, xt.unit), { x: x(v), y: H - 6, 'text-anchor': 'middle' }));
  }
  if (o.ref != null) svg('line', { x1: m.l, x2: m.l + iw, y1: y(o.ref), y2: y(o.ref), stroke: 'var(--axis)', 'stroke-width': 1.5 }, root);

  const marks = svg('g', {}, root);
  const ends = [];
  for (const s of o.series) {
    const g = svg('g', { 'data-series': s.key || s.name, class: 'series' }, marks);
    const runs = [];
    let run = [];
    s.values.forEach((v, i) => {
      if (v == null) { if (run.length) runs.push(run); run = []; } else run.push([x(o.t[i]), y(v)]);
    });
    if (run.length) runs.push(run);
    if (s.area) {
      const id = `grad${++uid}`;
      const grad = svg('linearGradient', { id, x1: 0, x2: 0, y1: 0, y2: 1 }, defs);
      svg('stop', { offset: '0%', 'stop-color': s.color, 'stop-opacity': 0.2 }, grad);
      svg('stop', { offset: '100%', 'stop-color': s.color, 'stop-opacity': 0 }, grad);
      for (const r of runs) if (r.length > 1) {
        const d = `M${r[0][0]},${m.t + ih}` + r.map(p => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('') + `L${r.at(-1)[0]},${m.t + ih}Z`;
        svg('path', { d, fill: `url(#${id})` }, g);
      }
    }
    for (const r of runs) {
      if (r.length === 1) svg('circle', { cx: r[0][0], cy: r[0][1], r: 3, fill: s.color }, g);
      else svg('path', { d: r.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(''), fill: 'none',
        stroke: s.color, 'stroke-width': s.width || 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, g);
    }
    const last = s.values.findLastIndex(v => v != null);
    if (last >= 0 && !s.noDot) {
      svg('circle', { cx: x(o.t[last]), cy: y(s.values[last]), r: 4, fill: s.color, stroke: 'var(--surface)', 'stroke-width': 2 }, g);
    }
    if (last >= 0 && o.endLabels && s.label) ends.push({ s, g, px: x(o.t[last]), py: y(s.values[last]) });
  }

  // direct end labels; when they would collide, spread them and draw leader lines back to the line end
  if (ends.length) {
    ends.sort((a, b) => a.py - b.py);
    const gap = 16;
    ends.forEach((e, i) => { e.ly = Math.max(e.py, i ? ends[i - 1].ly + gap : m.t + 6); });
    const over = ends.at(-1).ly - (m.t + ih - 4);
    if (over > 0) for (let i = ends.length - 1; i >= 0; i--)
      ends[i].ly = Math.min(ends[i].ly - over, i < ends.length - 1 ? ends[i + 1].ly - gap : Infinity);
    const lx = m.l + iw + 16;
    for (const e of ends) {
      if (Math.abs(e.ly - e.py) > 2) svg('path', { d: `M${e.px + 6},${e.py}L${lx - 5},${e.ly}`, stroke: 'var(--axis)', fill: 'none' }, e.g);
      text(e.g, e.s.label, { x: lx, y: e.ly, 'dominant-baseline': 'middle', class: 'end-label' });
    }
  }

  // hover layer
  const hover = svg('g', { display: 'none', 'pointer-events': 'none' }, root);
  const cross = svg('line', { y1: m.t, y2: m.t + ih, stroke: 'var(--axis)' }, hover);
  const dots = o.series.map(s => svg('circle', { r: 4, fill: s.color, stroke: 'var(--surface)', 'stroke-width': 2 }, hover));
  const plot = svg('rect', { class: 'plot', x: m.l, y: m.t, width: iw, height: ih, fill: 'transparent', tabindex: o.compact ? -1 : 0,
    'aria-label': 'Chart. Use left and right arrow keys to read values.' }, root);
  let idx = o.t.length - 1;
  const nearest = px => {
    const tv = t0 + ((px - m.l) / iw) * (t1 - t0);
    let a = 0, b = o.t.length - 1;
    while (b - a > 1) { const mid = (a + b) >> 1; if (o.t[mid] < tv) a = mid; else b = mid; }
    return Math.abs(o.t[a] - tv) <= Math.abs(o.t[b] - tv) ? a : b;
  };
  const show = (i, cx, cy) => {
    idx = i;
    const px = x(o.t[i]);
    cross.setAttribute('x1', px); cross.setAttribute('x2', px);
    o.series.forEach((s, k) => {
      const v = s.values[i];
      dots[k].setAttribute('display', v == null ? 'none' : 'inline');
      if (v != null) { dots[k].setAttribute('cx', px); dots[k].setAttribute('cy', y(v)); }
    });
    hover.setAttribute('display', 'inline');
    const rows = o.series.map(s => ({ s, v: s.values[i] })).filter(r => r.v != null).sort((a, b) => b.v - a.v)
      .map(({ s, v }) => ({ color: s.color, value: o.fmt(v), label: s.name }));
    const box = root.getBoundingClientRect();
    showTip(o.tip, cx ?? box.left + (px / W) * box.width, cy ?? box.top + m.t + ih / 2, o.tfmt(o.t[i]),
      rows.length ? rows : [{ value: '—', label: 'no data' }]);
  };
  const hide = () => { hover.setAttribute('display', 'none'); hideTip(o.tip); };
  const local = e => { const b = root.getBoundingClientRect(); return ((e.clientX - b.left) / b.width) * W; };
  plot.addEventListener('pointermove', e => show(nearest(local(e)), e.clientX, e.clientY));
  plot.addEventListener('pointerleave', hide);
  plot.addEventListener('focus', () => show(idx));
  plot.addEventListener('blur', hide);
  plot.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      show(Math.min(o.t.length - 1, Math.max(0, idx + (e.key === 'ArrowLeft' ? -1 : 1))));
    } else if (e.key === 'Escape') hide();
  });
}

/**
 * Heatmap. o: rows [{ name, values }], cols ms[], color(v), fmt(v), tfmt(ms), colLabel(ms), tip, onRow?(name)
 */
export function heatmap(host, o) {
  host.replaceChildren();
  if (!o.rows.length) return emptyState(host, o.emptyText || 'No data yet.');
  const W = host.clientWidth;
  const m = { l: 104, r: 4, t: 2, b: 22 };
  const rowH = 22;
  const H = m.t + o.rows.length * rowH + m.b;
  const cw = (W - m.l - m.r) / o.cols.length;
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': o.ariaLabel || 'Heatmap' }, host);
  const axis = svg('g', { class: 'axis' }, root);
  o.rows.forEach((r, ri) => {
    const yy = m.t + ri * rowH;
    const lab = text(axis, r.name, { x: m.l - 10, y: yy + rowH / 2, 'text-anchor': 'end', 'dominant-baseline': 'middle', class: 'row-label' });
    if (o.onRow) { lab.style.cursor = 'pointer'; lab.addEventListener('click', () => o.onRow(r.name)); }
    r.values.forEach((v, ci) => {
      const rect = svg('rect', { x: m.l + ci * cw + 1, y: yy + 1, width: Math.max(1, cw - 2), height: rowH - 2, rx: 3,
        fill: v == null ? 'var(--wash)' : o.color(v), class: 'cell' }, root);
      rect.addEventListener('pointermove', e => showTip(o.tip, e.clientX, e.clientY, `${r.name} · ${o.tfmt(o.cols[ci])}`,
        [{ value: v == null ? 'no data' : o.fmt(v), label: o.valueLabel || '', color: v == null ? null : o.color(v) }]));
      rect.addEventListener('pointerleave', () => hideTip(o.tip));
    });
  });
  const every = Math.ceil(70 / cw);
  o.cols.forEach((c, ci) => {
    if ((o.cols.length - 1 - ci) % every) return;
    text(axis, o.colLabel(c), { x: m.l + ci * cw + cw / 2, y: H - 6, 'text-anchor': 'middle' });
  });
}

/**
 * Strip plot: one row per GPU, one dot per source, log price axis.
 * o: rows [{ name, group, color, typical, points: [{ source, value, min, n, unit }] }], fmt, tip, onRow?(name)
 */
export function stripPlot(host, o) {
  host.replaceChildren();
  const vals = o.rows.flatMap(r => r.points.map(p => p.value));
  if (!vals.length) return emptyState(host, o.emptyText || 'No prices yet.');
  const W = host.clientWidth;
  const m = { l: 112, r: 16, t: 4, b: 26 };
  const rowH = 28, groupH = 28;
  const groups = [...new Set(o.rows.map(r => r.group))];
  const H = m.t + o.rows.length * rowH + groups.length * groupH + m.b;
  const lo = Math.log10(Math.min(...vals) * 0.8), hi = Math.log10(Math.max(...vals) * 1.25);
  const x = v => m.l + ((Math.log10(v) - lo) / (hi - lo)) * (W - m.l - m.r);
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', class: 'strip',
    'aria-label': o.ariaLabel || 'Prices by source' }, host);
  const axis = svg('g', { class: 'axis' }, root);
  const ticks = [];
  for (const v of [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100]) {
    if (Math.log10(v) >= lo && Math.log10(v) <= hi && (!ticks.length || x(v) - x(ticks.at(-1)) >= 40)) ticks.push(v);
  }
  ticks.forEach(v => {
    svg('line', { x1: x(v), x2: x(v), y1: m.t, y2: H - m.b, stroke: 'var(--grid)', 'shape-rendering': 'crispEdges' }, axis);
    text(axis, o.fmt(v), { x: x(v), y: H - 6, 'text-anchor': 'middle' });
  });
  const bySource = new Map();
  const highlight = src => {
    for (const [s, list] of bySource) for (const c of list) c.classList.toggle('hl', s === src);
    root.classList.toggle('hovering', !!src);
  };
  let yy = m.t;
  for (const g of groups) {
    text(axis, g, { x: 0, y: yy + groupH - 9, class: 'group-label' });
    yy += groupH;
    for (const r of o.rows.filter(r => r.group === g)) {
      const cy = yy + rowH / 2;
      const lab = text(axis, r.name, { x: m.l - 14, y: cy, 'text-anchor': 'end', 'dominant-baseline': 'middle', class: 'row-label' });
      if (o.onRow) { lab.style.cursor = 'pointer'; lab.addEventListener('click', () => o.onRow(r.name)); }
      const ps = r.points.map(p => p.value);
      if (ps.length > 1) svg('line', { x1: x(Math.min(...ps)), x2: x(Math.max(...ps)), y1: cy, y2: cy,
        stroke: 'var(--grid)', 'stroke-width': 8, 'stroke-linecap': 'round' }, root);
      if (r.typical) svg('line', { x1: x(r.typical), x2: x(r.typical), y1: cy - 9, y2: cy + 9,
        stroke: r.color || 'var(--ink-2)', 'stroke-width': 2.5, 'stroke-linecap': 'round' }, root);
      const cheapest = Math.min(...ps);
      for (const p of [...r.points].sort((a, b) => b.value - a.value)) {
        const low = p.value === cheapest;
        const c = svg('circle', { cx: x(p.value), cy, r: low ? 5.5 : 4.5, class: `dot${low ? ' low' : ''}`, tabindex: 0,
          stroke: 'var(--surface)', 'stroke-width': 2, 'aria-label': `${r.name} at ${p.source}: ${o.fmt(p.value)}` }, root);
        const hit = svg('circle', { cx: x(p.value), cy, r: 12, fill: 'transparent' }, root);
        if (!bySource.has(p.source)) bySource.set(p.source, []);
        bySource.get(p.source).push(c);
        const enter = e => {
          highlight(p.source);
          const b = c.getBoundingClientRect();
          showTip(o.tip, e?.clientX ?? b.right, e?.clientY ?? b.top, `${r.name} · ${p.source}`, [
            { value: o.fmt(p.value), label: 'median', color: 'var(--ink)' },
            ...(p.min != null && p.min < p.value ? [{ value: o.fmt(p.min), label: 'lowest' }] : []),
            { value: String(p.n), label: p.unit || 'offers' },
          ]);
        };
        const leave = () => { highlight(null); hideTip(o.tip); };
        for (const el of [hit, c]) { el.addEventListener('pointermove', enter); el.addEventListener('pointerleave', leave); }
        c.addEventListener('focus', () => enter());
        c.addEventListener('blur', leave);
      }
      yy += rowH;
    }
  }
}

/** points: [[ms, value], ...] */
export function sparkline(points, { w = 96, h = 26, label = '', color = 'var(--spark)' } = {}) {
  const root = svg('svg', { width: w, height: h, viewBox: `0 0 ${w} ${h}`, class: 'spark', role: 'img', 'aria-label': label });
  if (!points.length) return root;
  const vs = points.map(p => p[1]);
  const lo = Math.min(...vs), hi = Math.max(...vs);
  const t0 = points[0][0], t1 = points.at(-1)[0];
  const x = t => (t1 === t0 ? w - 4 : 3 + ((t - t0) / (t1 - t0)) * (w - 7));
  const y = v => (hi === lo ? h / 2 : 3 + (1 - (v - lo) / (hi - lo)) * (h - 6));
  if (points.length > 1) {
    const d = points.map(([t, v], i) => `${i ? 'L' : 'M'}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join('');
    svg('path', { d, fill: 'none', stroke: color, 'stroke-width': 1.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, root);
  }
  const [lt, lv] = points.at(-1);
  svg('circle', { cx: x(lt), cy: y(lv), r: 2.5, fill: 'var(--accent)' }, root);
  return root;
}
