/* Motor AMD + PoC en JavaScript. Port 1:1 de amd/strategy.py y amd/backtest.py.
   Funciona en el navegador (window.AMD) y en Node (module.exports). */
(function (root) {
  "use strict";
  const MIN = 60000;

  // ---------- Zonas horarias ----------
  const _offCache = new Map();
  function tzOffsetMs(zone, tUtcMs) {
    // offset (local - UTC) de la zona en el instante dado, cacheado por hora
    if (zone === "UTC") return 0;
    const key = zone + "|" + Math.floor(tUtcMs / 3600000);
    let v = _offCache.get(key);
    if (v !== undefined) return v;
    const fmt = _fmt(zone);
    const parts = {};
    for (const p of fmt.formatToParts(new Date(tUtcMs))) parts[p.type] = p.value;
    const asUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour % 24, +parts.minute, +parts.second);
    v = asUtc - Math.floor(tUtcMs / 1000) * 1000;
    _offCache.set(key, v);
    return v;
  }
  const _fmts = {};
  function _fmt(zone) {
    return (_fmts[zone] ||= new Intl.DateTimeFormat("en-US", {
      timeZone: zone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    }));
  }
  function nyMinuteOfDay(tUtcMs) {
    const loc = tUtcMs + tzOffsetMs("America/New_York", tUtcMs);
    return Math.floor((((loc % 86400000) + 86400000) % 86400000) / MIN);
  }

  // ---------- CSV ----------
  function parseDate(s) {
    s = s.trim().replace(/^"|"$/g, "");
    if (/^\d+(\.\d+)?$/.test(s)) {
      const x = +s;
      return x < 1e11 ? x * 1000 : x; // unix s o ms
    }
    let m;
    // 2024.01.02 o 2024-01-02 o 2024/01/02 [ T]HH:MM[:SS[.mmm]] [Z|+hh:mm]
    if ((m = s.match(/^(\d{4})[.\-\/](\d{2})[.\-\/](\d{2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?\s*(Z|[+\-]\d{2}:?\d{2})?$/))) {
      let t = Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
      if (m[8] && m[8] !== "Z") {
        const sg = m[8][0] === "-" ? -1 : 1, hh = +m[8].slice(1, 3), mm = +m[8].slice(-2);
        t -= sg * (hh * 60 + mm) * MIN;
        return { t, abs: true };
      }
      return { t, abs: !!m[8] };
    }
    // 02.01.2024 00:00:00.000 (Dukascopy, día primero)
    if ((m = s.match(/^(\d{2})[.\-\/](\d{2})[.\-\/](\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?/))) {
      return { t: Date.UTC(+m[3], +m[2] - 1, +m[1], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0)), abs: false };
    }
    const t = Date.parse(s);
    return isNaN(t) ? null : { t, abs: true };
  }

  function parseCSV(text, zone = "UTC") {
    const lines = text.split(/\r?\n/).filter((l) => l.trim().length);
    const first = lines[0];
    const delim = [",", ";", "\t"].sort((a, b) => first.split(b).length - first.split(a).length)[0];
    const head = first.split(delim).map((h) => h.trim().toLowerCase().replace(/[<>"]/g, ""));
    const find = (...names) => head.findIndex((h) => names.includes(h));
    let iT = find("time", "datetime", "date", "timestamp", "gmt time", "local time", "fecha");
    const iTime2 = head.indexOf("time") !== -1 && head.indexOf("date") !== -1 ? head.indexOf("time") : -1; // MT5: <DATE> <TIME>
    if (iTime2 !== -1) iT = head.indexOf("date");
    const iO = find("open"), iH = find("high"), iL = find("low"), iC = find("close");
    const iV = find("volume", "vol", "tickvol", "tick_volume", "tick volume", "real_volume");
    if ([iT, iO, iH, iL, iC].some((x) => x < 0)) throw new Error("No encuentro las columnas time/open/high/low/close en la cabecera: " + head.join(", "));
    const n = lines.length - 1;
    const t = new Float64Array(n), o = new Float64Array(n), h = new Float64Array(n), l = new Float64Array(n), c = new Float64Array(n), v = new Float64Array(n);
    let k = 0, hasVol = false;
    for (let r = 1; r <= n; r++) {
      const f = lines[r].split(delim);
      const ds = iTime2 !== -1 ? f[iT] + " " + f[iTime2] : f[iT];
      const d = parseDate(ds);
      if (!d) continue;
      let tt = d.t;
      if (!d.abs && zone !== "UTC") tt -= tzOffsetMs(zone, tt);
      t[k] = tt; o[k] = +f[iO]; h[k] = +f[iH]; l[k] = +f[iL]; c[k] = +f[iC];
      v[k] = iV >= 0 ? +f[iV] || 0 : 1;
      if (v[k] > 0 && iV >= 0) hasVol = true;
      if (!isFinite(o[k]) || !isFinite(c[k])) continue;
      k++;
    }
    return clean({ t: t.subarray(0, k), o: o.subarray(0, k), h: h.subarray(0, k), l: l.subarray(0, k), c: c.subarray(0, k), v: v.subarray(0, k), hasVol });
  }

  function clean(d) {
    const n = d.t.length;
    const idx = Array.from({ length: n }, (_, i) => i);
    let sorted = true;
    for (let i = 1; i < n; i++) if (d.t[i] < d.t[i - 1]) { sorted = false; break; }
    if (!sorted) idx.sort((a, b) => d.t[a] - d.t[b]);
    const keep = [];
    let last = -Infinity;
    for (const i of idx) {
      if (d.t[i] === last) continue;
      if (d.h[i] === d.l[i] && d.v[i] <= 0) continue; // mercado cerrado
      keep.push(i); last = d.t[i];
    }
    const pick = (a) => Float64Array.from(keep, (i) => a[i]);
    return { t: pick(d.t), o: pick(d.o), h: pick(d.h), l: pick(d.l), c: pick(d.c), v: pick(d.v), hasVol: d.hasVol };
  }

  function resample(d, tfMin) {
    const step = tfMin * MIN, n = d.t.length;
    const T = [], O = [], H = [], L = [], C = [], V = [];
    let cur = null;
    for (let i = 0; i < n; i++) {
      const b = Math.floor(d.t[i] / step) * step;
      if (b !== cur) {
        T.push(b); O.push(d.o[i]); H.push(d.h[i]); L.push(d.l[i]); C.push(d.c[i]); V.push(d.v[i]); cur = b;
      } else {
        const j = T.length - 1;
        if (d.h[i] > H[j]) H[j] = d.h[i];
        if (d.l[i] < L[j]) L[j] = d.l[i];
        C[j] = d.c[i]; V[j] += d.v[i];
      }
    }
    const F = (a) => Float64Array.from(a);
    return { t: F(T), o: F(O), h: F(H), l: F(L), c: F(C), v: F(V) };
  }

  // ---------- Indicadores ----------
  function atr(H, L, C, n) {
    const N = C.length, out = new Float64Array(N).fill(NaN);
    let sum = 0;
    const tr = new Float64Array(N);
    for (let i = 0; i < N; i++) {
      const pc = i ? C[i - 1] : C[0];
      tr[i] = Math.max(H[i] - L[i], Math.abs(H[i] - pc), Math.abs(L[i] - pc));
      sum += tr[i];
      if (i >= n) sum -= tr[i - n];
      if (i >= n - 1) out[i] = sum / n;
    }
    return out;
  }
  function rollMax(a, w) {
    const out = new Float64Array(a.length).fill(NaN);
    for (let i = w - 1; i < a.length; i++) { let m = -Infinity; for (let j = i - w + 1; j <= i; j++) if (a[j] > m) m = a[j]; out[i] = m; }
    return out;
  }
  function rollMin(a, w) {
    const out = new Float64Array(a.length).fill(NaN);
    for (let i = w - 1; i < a.length; i++) { let m = Infinity; for (let j = i - w + 1; j <= i; j++) if (a[j] < m) m = a[j]; out[i] = m; }
    return out;
  }
  function lowerBound(a, x, lo = 0) {
    let hi = a.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] < x) lo = m + 1; else hi = m; }
    return lo;
  }

  // ---------- Volume Profile ----------
  function poc(H, L, V, m0, m1, lo, hi, bins) {
    const prof = new Float64Array(bins);
    const w = (hi - lo) / bins;
    let vs = 0;
    for (let i = m0; i < m1; i++) vs += V[i] > 0 ? V[i] : 0;
    const useVol = vs > 0;
    for (let i = m0; i < m1; i++) {
      const vol = useVol ? Math.max(V[i], 0) : 1;
      const h = H[i], l = L[i], span = h - l;
      if (span <= 1e-12) {
        let b = Math.floor((l - lo) / w); b = Math.min(Math.max(b, 0), bins - 1);
        prof[b] += vol; continue;
      }
      let b0 = Math.max(Math.floor((l - lo) / w), 0), b1 = Math.min(Math.floor((h - lo) / w), bins - 1);
      for (let b = b0; b <= b1; b++) {
        const e0 = lo + b * w, e1 = e0 + w;
        const ov = Math.min(h, e1) - Math.max(l, e0);
        if (ov > 0) prof[b] += (vol * ov) / span;
      }
    }
    let best = 0;
    for (let b = 1; b < bins; b++) if (prof[b] > prof[best]) best = b;
    return { poc: lo + (best + 0.5) * w, prof };
  }

  // ---------- Parámetros ----------
  const DEFAULTS = {
    tf: 15, atr_len: 50, impulse_bars: 20, impulse_atr: 4.0, range_min_bars: 12, range_max_bars: 60,
    range_max_frac: 0.4, range_max_atr: 5.0, sweep_min: 0.15, sweep_max: 1.5, reclaim_max_bars: 30,
    retest_max_bars: 30, touch_tol_atr: 0.1, wick_frac: 0.4, inval_close_atr: 0.25, entry_touch: 1,
    sl_buffer_atr: 0.05, min_risk_atr: 0.1, poc_bins: 50, long: true, short: true,
    ny_open: false, open_window_min: 60, require_impulse: true,
    cost: 0.35, max_hold_min: 4320, min_rr: 1.0, split: 0.7,
  };

  function newFunnel() {
    return { ranges: 0, outside_open_window: 0, broke_wrong_side: 0, swept: 0, sweep_too_deep: 0, sweep_too_shallow: 0,
      no_reclaim: 0, reclaimed: 0, retest_invalid: 0, retest_timeout: 0, entries: 0 };
  }

  // ---------- Detector (espacio largo) ----------
  function detectLong(O, H, L, C, V1, H1, L1, tTF, t1, nyMin, p, f) {
    const n = C.length;
    const A = atr(H, L, C, p.atr_len);
    const W = p.range_min_bars;
    const rHi = rollMax(H, W), rLo = rollMin(L, W);
    const ih = rollMax(H, p.impulse_bars);
    const impHi = (i) => (i - W >= 0 ? ih[i - W] : NaN);
    const rangeMax = p.ny_open ? Math.max(p.range_max_bars, 400) : p.range_max_bars;
    const setups = [];
    let i = Math.max(p.atr_len, p.impulse_bars) + W;
    while (i < n - 1) {
      const s = i - W + 1, a = A[s - 1], rh = rHi[i], rl = rLo[i], top = impHi(i);
      if (!(isFinite(a) && isFinite(top) && a > 0)) { i++; continue; }
      const height = rh - rl, imp = top - rl;
      let ok = height > 0 && height <= p.range_max_atr * a;
      if (p.require_impulse) ok = ok && imp >= p.impulse_atr * a && height <= p.range_max_frac * imp;
      if (p.ny_open) ok = ok && nyMin[i] < 570 && nyMin[i] >= 240;
      if (!ok) { i++; continue; }

      f.ranges++;
      let e = i, j = i + 1;
      while (j < n && j - s + 1 <= rangeMax && L[j] >= rl && H[j] <= rh) { e = j; j++; }
      if (j >= n) break;
      if (L[j] >= rl && H[j] <= rh) { i = j; continue; }
      if (p.ny_open && !(nyMin[j] >= 570 && nyMin[j] < 570 + p.open_window_min)) { f.outside_open_window++; i = j + 1; continue; }
      if (L[j] >= rl || H[j] > rh) { f.broke_wrong_side++; i = j + 1; continue; }

      const m0 = lowerBound(t1, tTF[s]);
      const m1 = e + 1 < n ? lowerBound(t1, tTF[e + 1]) : t1.length;
      if (m1 - m0 < 2) { i = j + 1; continue; }
      const pc = poc(H1, L1, V1, m0, m1, rl, rh, p.poc_bins).poc;

      f.swept++;
      let lowMin = L[j], k = j, reclaimed = -1, fail = null;
      while (k < n && k - j <= p.reclaim_max_bars) {
        lowMin = Math.min(lowMin, L[k]);
        if (lowMin < rl - p.sweep_max * height) { fail = "deep"; break; }
        if (C[k] > pc) {
          if (rl - lowMin >= p.sweep_min * height) reclaimed = k; else fail = "shallow";
          break;
        }
        k++;
      }
      if (reclaimed < 0) {
        if (fail === "deep") f.sweep_too_deep++; else if (fail === "shallow") f.sweep_too_shallow++; else f.no_reclaim++;
        i = k + 1; continue;
      }
      f.reclaimed++;

      let m = reclaimed + 1, touches = 0, firstTouch = -1, entryBar = -1;
      fail = "timeout";
      while (m < n - 1 && m - reclaimed <= p.retest_max_bars) {
        const am = A[m];
        if (C[m] < pc - p.inval_close_atr * am) { fail = "invalid"; break; }
        if (top > rh + height && H[m] >= top) { fail = "runaway"; break; }
        if (L[m] <= pc + p.touch_tol_atr * am) {
          if (firstTouch < 0) firstTouch = m;
          const rng = H[m] - L[m], wick = Math.min(O[m], C[m]) - L[m];
          if (C[m] > pc && rng > 0 && wick >= p.wick_frac * rng) {
            touches++;
            if (touches >= p.entry_touch) { entryBar = m; break; }
          }
        }
        m++;
      }
      if (entryBar < 0) {
        if (fail === "invalid") f.retest_invalid++; else f.retest_timeout++;
        i = m + 1; continue;
      }
      let sl = Infinity;
      for (let q = firstTouch; q <= entryBar; q++) sl = Math.min(sl, L[q]);
      sl -= p.sl_buffer_atr * A[entryBar];
      if (C[entryBar] - sl < p.min_risk_atr * A[entryBar]) sl = C[entryBar] - p.min_risk_atr * A[entryBar];
      setups.push({ range_start: s, range_end: e, sweep_bar: j, sweep_low: lowMin, reclaim_bar: reclaimed, first_touch: firstTouch,
        signal_bar: entryBar, range_high: rh, range_low: rl, poc: pc, impulse_top: top, sl, atr: A[entryBar] });
      f.entries++;
      i = entryBar + 1;
    }
    return setups;
  }

  function detect(dTF, d1, p) {
    const n = dTF.t.length;
    const nyMin = new Int16Array(n);
    for (let i = 0; i < n; i++) nyMin[i] = nyMinuteOfDay(dTF.t[i]);
    const neg = (a) => a.map((x) => -x);
    const out = [], funnels = {};
    for (const side of ["long", "short"]) {
      if (!p[side]) continue;
      const sg = side === "long" ? 1 : -1;
      const O = sg > 0 ? dTF.o : neg(dTF.o), C = sg > 0 ? dTF.c : neg(dTF.c);
      const H = sg > 0 ? dTF.h : neg(dTF.l), L = sg > 0 ? dTF.l : neg(dTF.h);
      const H1 = sg > 0 ? d1.h : neg(d1.l), L1 = sg > 0 ? d1.l : neg(d1.h);
      const f = newFunnel();
      for (const st of detectLong(O, H, L, C, d1.v, H1, L1, dTF.t, d1.t, nyMin, p, f)) {
        st.side = side;
        st.signal_time = dTF.t[st.signal_bar];
        st.entry_time = dTF.t[st.signal_bar + 1];
        for (const k of ["range_high", "range_low", "poc", "impulse_top", "sl", "sweep_low"]) st[k] = sg * st[k];
        if (sg < 0) [st.range_high, st.range_low] = [st.range_low, st.range_high];
        out.push(st);
      }
      funnels[side] = f;
    }
    out.sort((a, b) => a.signal_time - b.signal_time);
    return { setups: out, funnels };
  }

  // ---------- Simulación ----------
  const VARIANTS = [
    { id: "A_origen_impulso", label: "Origen del impulso", kind: "level" },
    { id: "B_rango_x1", label: "Rango ×1", kind: "proj", mult: 1 },
    { id: "B_rango_x2", label: "Rango ×2", kind: "proj", mult: 2 },
    { id: "C_2R", label: "2R fijo", kind: "r", r: 2 },
    { id: "C_3R", label: "3R fijo", kind: "r", r: 3 },
    { id: "C_5R", label: "5R fijo", kind: "r", r: 5 },
    { id: "D_parcial2R_BE_A", label: "50% a 2R + BE", kind: "partial", r: 2 },
  ];

  function exitLong(lo, hi, cl, a, b, sl, tp) {
    // primera vela [a,b) que toca SL o TP; SL gana si coinciden
    for (let i = a; i < b; i++) {
      if (lo[i] <= sl) return { px: sl, idx: i, why: "SL" };
      if (hi[i] >= tp) return { px: tp, idx: i, why: "TP" };
    }
    return { px: cl[b - 1], idx: b - 1, why: "tiempo" };
  }

  function simulate(setups, d1, p) {
    const rows = [];
    const t1 = d1.t, n1 = t1.length;
    const negLo = d1.h.map((x) => -x), negHi = d1.l.map((x) => -x), negC = d1.c.map((x) => -x);
    for (const st of setups) {
      const sg = st.side === "long" ? 1 : -1;
      const k0 = lowerBound(t1, st.entry_time);
      if (k0 >= n1) continue;
      const k1 = lowerBound(t1, t1[k0] + p.max_hold_min * MIN, k0);
      const lo = sg > 0 ? d1.l : negLo, hi = sg > 0 ? d1.h : negHi, cl = sg > 0 ? d1.c : negC;
      const entry = sg * d1.o[k0], sl = sg * st.sl, risk = entry - sl;
      if (!(risk > 0)) continue;
      const rh = sg > 0 ? st.range_high : -st.range_low, rl = sg > 0 ? st.range_low : -st.range_high;
      const height = rh - rl, top = sg * st.impulse_top, cost = p.cost;
      for (const v of VARIANTS) {
        let tp = null;
        if (v.kind === "level") tp = top;
        else if (v.kind === "proj") tp = rh + v.mult * height;
        else if (v.kind === "r") tp = entry + v.r * risk;
        if ((v.kind === "level" || v.kind === "proj") && tp - entry < p.min_rr * risk) continue;
        let R, ex;
        if (v.kind !== "partial") {
          ex = exitLong(lo, hi, cl, k0, k1, sl, tp);
          R = (ex.px - entry - cost) / risk;
        } else {
          const tp1 = entry + v.r * risk;
          ex = exitLong(lo, hi, cl, k0, k1, sl, tp1);
          if (ex.why !== "TP") R = (ex.px - entry - cost) / risk;
          else {
            const finalTp = Math.max(top, tp1);
            if (finalTp <= tp1 || ex.idx + 1 >= k1) R = (tp1 - entry - cost) / risk;
            else {
              const ex2 = exitLong(lo, hi, cl, ex.idx + 1, k1, entry, finalTp);
              R = (0.5 * (tp1 - entry)) / risk + (0.5 * (ex2.px - entry)) / risk - cost / risk;
              ex = { px: ex2.px, idx: ex2.idx, why: "TP1+" + ex2.why };
              tp = finalTp;
            }
          }
          if (tp === null) tp = tp1;
        }
        rows.push({ setup: st, variant: v.id, side: st.side, signal_time: st.signal_time, entry_time: t1[k0], entry: sg * entry,
          sl: st.sl, tp: sg * tp, risk, exit_time: t1[ex.idx], exit_reason: ex.why, R, cost_R: cost / risk });
      }
    }
    return rows;
  }

  // ---------- Métricas ----------
  function metrics(R) {
    const n = R.length;
    if (!n) return { trades: 0 };
    let sum = 0, wins = 0, gw = 0, gl = 0, eq = 0, peak = 0, dd = 0, comp = 1;
    for (const r of R) {
      sum += r; eq += r; peak = Math.max(peak, eq); dd = Math.min(dd, eq - peak); comp *= 1 + 0.01 * r;
      if (r > 0) { wins++; gw += r; } else gl -= r;
    }
    const mean = sum / n;
    let ss = 0;
    for (const r of R) ss += (r - mean) ** 2;
    const sd = n > 1 ? Math.sqrt(ss / (n - 1)) : 0;
    return { trades: n, win_rate: wins / n, avg_R: mean, total_R: sum, profit_factor: gl > 0 ? gw / gl : Infinity,
      max_dd_R: dd, ret_1pct_risk: comp - 1, t_stat: n > 2 && sd > 0 ? mean / (sd / Math.sqrt(n)) : NaN };
  }

  function sessionOf(tUtc) {
    const h = nyMinuteOfDay(tUtc) / 60;
    if (h >= 9.5 && h < 16) return "Nueva York";
    if (h >= 3 && h < 9.5) return "Londres / pre-NY";
    if (h >= 18 || h < 3) return "Asia";
    return "Tarde NY";
  }

  function run(d1, dTF, p) {
    const { setups, funnels } = detect(dTF, d1, p);
    const trades = simulate(setups, d1, p);
    const splitTime = d1.t[Math.floor(d1.t.length * p.split)];
    return { setups, funnels, trades, splitTime };
  }

  const api = { parseCSV, clean, resample, atr, poc, detect, simulate, metrics, sessionOf, run, nyMinuteOfDay, DEFAULTS, VARIANTS, MIN };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AMD = api;
})(typeof window !== "undefined" ? window : globalThis);
