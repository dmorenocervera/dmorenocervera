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
    range_max_frac: 0.4, range_max_atr: 5.0, pivot_len: 2, range_min_touches: 2, range_touch_zone: 0.2, sweep_min: 0.15, sweep_max: 1.5, reclaim_max_bars: 30,
    retest_max_bars: 30, touch_tol_atr: 0.1, wick_frac: 0.4, inval_close_atr: 0.25, entry_touch: 1,
    entry_mode: "close", break_bars: 3, sl_limit_atr: 1.0, trail_atr: 2.5, trail_start_R: 1.0, target_lookback: 40, tp_front_atr: 0, tl_buffer_atr: 0.1, tl_wait_pivot: true,
    sl_buffer_atr: 0.05, min_risk_atr: 0.1, poc_bins: 50, long: true, short: false,
    ny_open: false, open_window_min: 60, require_impulse: true,
    cost: 0.35, max_hold_min: 4320, min_rr: 1.0, split: 0.7,
  };

  function newFunnel() {
    return { ranges: 0, few_touches: 0, outside_open_window: 0, broke_wrong_side: 0, swept: 0, sweep_too_deep: 0, sweep_too_shallow: 0,
      no_reclaim: 0, reclaimed: 0, retest_invalid: 0, retest_timeout: 0, entries: 0 };
  }

  // Pivotes (fractales): máximo >= k velas anteriores y > k posteriores; mínimos al revés
  function pivots(H, L, k) {
    const n = H.length, ph = new Uint8Array(n), pl = new Uint8Array(n);
    for (let q = k; q < n - k; q++) {
      let a = -Infinity, b = -Infinity, c = Infinity, d = Infinity;
      for (let j = q - k; j < q; j++) { if (H[j] > a) a = H[j]; if (L[j] < c) c = L[j]; }
      for (let j = q + 1; j <= q + k; j++) { if (H[j] > b) b = H[j]; if (L[j] < d) d = L[j]; }
      ph[q] = H[q] >= a && H[q] > b ? 1 : 0;
      pl[q] = L[q] <= c && L[q] < d ? 1 : 0;
    }
    return { ph, pl };
  }

  // ---------- Detector (espacio largo) ----------
  function detectLong(O, H, L, C, V1, H1, L1, tTF, t1, nyMin, p, f) {
    const n = C.length;
    const A = atr(H, L, C, p.atr_len);
    const W = p.range_min_bars;
    const rHi = rollMax(H, W), rLo = rollMin(L, W);
    const ih = rollMax(H, p.impulse_bars);
    const impHi = (i) => (i - W >= 0 ? ih[i - W] : NaN);
    const { ph, pl } = pivots(H, L, p.pivot_len);
    const T = (k) => (k < n ? tTF[k] : Infinity);
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
      // Estructura: >= N máximos en la zona alta y >= N mínimos en la zona baja (pivotes confirmados antes de la ruptura)
      {
        const q1 = e - p.pivot_len + 1, zone = p.range_touch_zone * height;
        let nh = 0, nl = 0;
        for (let q = s; q < q1; q++) { if (ph[q] && H[q] >= rh - zone) nh++; if (pl[q] && L[q] <= rl + zone) nl++; }
        if (nh < p.range_min_touches || nl < p.range_min_touches) { f.few_touches++; i = j; continue; }
      }
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

      const limit = p.entry_mode === "limit";
      let m = reclaimed + 1, touches = 0, firstTouch = -1, entryBar = -1;
      fail = "timeout";
      while (m < n - 1 && m - reclaimed <= p.retest_max_bars) {
        const am = limit ? A[m - 1] : A[m];
        if (top > rh + height && H[m] >= top) { fail = "runaway"; break; }
        if (limit && L[m] <= pc + p.touch_tol_atr * am) { firstTouch = entryBar = m; break; }
        if (C[m] < pc - p.inval_close_atr * am) { fail = "invalid"; break; }
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
      const aE = limit ? A[entryBar - 1] : A[entryBar];
      let sl, level, eFrom, eUntil, ref;
      if (limit) {
        level = pc + p.touch_tol_atr * aE; sl = pc - p.sl_limit_atr * aE; eFrom = entryBar; eUntil = entryBar + 1; ref = level;
      } else {
        sl = Infinity;
        for (let q = firstTouch; q <= entryBar; q++) sl = Math.min(sl, L[q]);
        sl -= p.sl_buffer_atr * aE;
        if (p.entry_mode === "break") { level = H[entryBar]; eFrom = entryBar + 1; eUntil = Math.min(entryBar + 1 + p.break_bars, n); ref = level; }
        else { level = NaN; eFrom = entryBar + 1; eUntil = entryBar + 2; ref = C[entryBar]; }
      }
      if (ref - sl < p.min_risk_atr * aE) sl = ref - p.min_risk_atr * aE;
      const structLevels = [];
      for (let q = Math.max(0, s - p.target_lookback); q < s - p.pivot_len; q++) {
        if (ph[q] && H[q] > rh) structLevels.push(H[q]);
        if (pl[q] && L[q] > rh) structLevels.push(L[q]);
      }
      structLevels.sort((x, y) => x - y);
      setups.push({ struct_levels: structLevels, range_start: s, range_end: e, sweep_bar: j, sweep_low: lowMin, reclaim_bar: reclaimed, first_touch: firstTouch,
        signal_bar: entryBar, range_high: rh, range_low: rl, poc: pc, impulse_top: top, sl, atr: aE,
        entry_type: p.entry_mode, entry_level: level, entry_from: T(eFrom), entry_until: T(eUntil) });
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
        for (const k of ["range_high", "range_low", "poc", "impulse_top", "sl", "sweep_low", "entry_level"]) st[k] = sg * st[k];
        st.struct_levels = st.struct_levels.map((x) => sg * x);
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
    { id: "G_estructura_previa", label: "Estructura previa", kind: "struct" },
    { id: "H_directriz", label: "Directriz", kind: "trendline", structTp: false },
    { id: "I_directriz_o_estructura", label: "Directriz o estructura", kind: "trendline", structTp: true },
    { id: "C_2R", label: "2R fijo", kind: "r", r: 2 },
    { id: "C_3R", label: "3R fijo", kind: "r", r: 3 },
    { id: "C_5R", label: "5R fijo", kind: "r", r: 5 },
    { id: "D_parcial2R_BE_A", label: "50% a 2R + BE", kind: "partial", r: 2 },
    { id: "E_trailing_ATR", label: "Trailing ATR", kind: "trail_atr" },
    { id: "F_trailing_estructura", label: "Trailing estructura", kind: "trail_struct" },
  ];

  function exitLong(lo, hi, cl, a, b, sl, tp) {
    // primera vela [a,b) que toca SL o TP; SL gana si coinciden
    for (let i = a; i < b; i++) {
      if (lo[i] <= sl) return { px: sl, idx: i, why: "SL" };
      if (hi[i] >= tp) return { px: tp, idx: i, why: "TP" };
    }
    return { px: cl[b - 1], idx: b - 1, why: "tiempo" };
  }

  function exitTrailing(op, lo, hi, cl, a, b, entry, sl, candAt, act) {
    // stop dinámico: candAt(i, prevRun) propone nivel con info hasta la vela anterior; nunca baja
    let run = entry, stop = sl;
    for (let i = a; i < b; i++) {
      if (run >= act) { const c = candAt(i, run); if (c > stop) stop = c; }
      if (lo[i] <= stop) return { px: Math.min(stop, op[i]), idx: i, why: stop <= sl ? "SL" : "trailing", stop };
      if (hi[i] > run) run = hi[i];
    }
    return { px: cl[b - 1], idx: b - 1, why: "tiempo", stop };
  }

  // Salida por pérdida de la directriz (espacio largo). anchors: [[velaTF, precio], ...] mínimos crecientes.
  function exitTrendline(op, lo, hi, cl, a0, a1, sl, tp, bOf, Ltf, Ctf, pl, k, anchors, buf, wait) {
    const a = anchors.slice(), n0 = a.length;
    let prevB = bOf[a0];
    for (let i = a0; i < a1; i++) {
      const b = bOf[i];
      if (b !== prevB) {
        for (let bb = prevB; bb < b; bb++) {
          const q = bb - k, last = a[a.length - 1];
          if (q >= 0 && q > last[0] && pl[q] && Ltf[q] > last[1]) a.push([q, Ltf[q]]);
          if (wait && a.length === n0) continue; // aún no hay un mínimo más alto tras la entrada
          const [b1, p1] = a[a.length - 2], [b2, p2] = a[a.length - 1];
          const line = p2 + ((p2 - p1) / (b2 - b1)) * (bb - b2);
          if (Ctf[bb] < line - buf) return { px: op[i], idx: i, why: "directriz", tl: wait && a.length === n0 ? null : a.slice(-2) };
        }
        prevB = b;
      }
      if (lo[i] <= sl) return { px: sl, idx: i, why: "SL", tl: wait && a.length === n0 ? null : a.slice(-2) };
      if (hi[i] >= tp) return { px: tp, idx: i, why: "TP", tl: wait && a.length === n0 ? null : a.slice(-2) };
    }
    return { px: cl[a1 - 1], idx: a1 - 1, why: "tiempo", tl: wait && a.length === n0 ? null : a.slice(-2) };
  }

  function lastPivotLow(L, pl, k) {
    const n = L.length, lvl = new Float64Array(n).fill(NaN), idx = new Int32Array(n).fill(-1);
    let cl = NaN, ci = -1;
    for (let b = 0; b < n; b++) { const q = b - k; if (q >= 0 && pl[q]) { cl = L[q]; ci = q; } lvl[b] = cl; idx[b] = ci; }
    return { lvl, idx };
  }

  function fillOrder(st, op, lo, hi, kFrom, kUntil, sl, n1) {
    if (st.entry_type === "close") return kFrom < n1 ? { k: kFrom, px: op[kFrom] } : null;
    const level = st.levelLong;
    for (let k = kFrom; k < Math.min(kUntil, n1); k++) {
      if (st.entry_type === "break") {
        if (hi[k] >= level) return { k, px: Math.max(level, op[k]) };
        if (lo[k] <= sl) return null;
      } else if (lo[k] <= level) return { k, px: Math.min(level, op[k]) };
    }
    return null;
  }

  function simulate(setups, d1, p, dTF) {
    const rows = [];
    const t1 = d1.t, n1 = t1.length;
    const neg = (a) => a.map((x) => -x);
    const sp = { long: [d1.o, d1.l, d1.h, d1.c], short: [neg(d1.o), neg(d1.h), neg(d1.l), neg(d1.c)] };
    // trailing por estructura: último mínimo de pivote confirmado por vela de la TF, y vela TF de cada vela de 1m
    const struct = {};
    let bOf = null;
    if (dTF) {
      bOf = new Int32Array(n1);
      let b = 0;
      for (let i = 0; i < n1; i++) { while (b + 1 < dTF.t.length && dTF.t[b + 1] <= t1[i]) b++; bOf[i] = t1[i] >= dTF.t[0] ? b : -1; }
      for (const side of ["long", "short"]) {
        const H = side === "long" ? dTF.h : neg(dTF.l), L = side === "long" ? dTF.l : neg(dTF.h);
        const pl = pivots(H, L, p.pivot_len).pl;
        struct[side] = { ...lastPivotLow(L, pl, p.pivot_len), L, C: side === "long" ? dTF.c : neg(dTF.c), pl };
      }
    }
    let notFilled = 0;
    for (const st0 of setups) {
      const sg = st0.side === "long" ? 1 : -1;
      const [op, lo, hi, cl] = sp[st0.side];
      const st = { ...st0, levelLong: sg * st0.entry_level };
      const sl = sg * st.sl;
      const kFrom = lowerBound(t1, st.entry_from), kUntil = lowerBound(t1, st.entry_until);
      const fl = fillOrder(st, op, lo, hi, kFrom, kUntil, sl, n1);
      if (!fl) { notFilled++; continue; }
      const k0 = fl.k, entry = fl.px;
      const k1 = lowerBound(t1, t1[k0] + p.max_hold_min * MIN, k0);
      const risk = entry - sl;
      if (!(risk > 0)) continue;
      const rh = sg > 0 ? st.range_high : -st.range_low, rl = sg > 0 ? st.range_low : -st.range_high;
      const height = rh - rl, top = sg * st.impulse_top, cost = p.cost, a = st.atr;
      for (const v of VARIANTS) {
        let tp = NaN, R, ex;
        if (v.kind === "level") tp = top;
        else if (v.kind === "proj") tp = rh + v.mult * height;
        else if (v.kind === "r") tp = entry + v.r * risk;
        else if (v.kind === "struct" || (v.kind === "trendline" && v.structTp)) {
          const lv = st.struct_levels.map((x) => sg * x - p.tp_front_atr * a).sort((x, y) => x - y);
          tp = lv.find((x) => x - entry >= p.min_rr * risk);
          if (tp === undefined) { if (v.kind === "struct") continue; tp = NaN; }
        }
        if ((v.kind === "level" || v.kind === "proj") && tp - entry < p.min_rr * risk) continue;
        if (v.kind === "level" || v.kind === "proj" || v.kind === "r" || v.kind === "struct") {
          ex = exitLong(lo, hi, cl, k0, k1, sl, tp);
          R = (ex.px - entry - cost) / risk;
        } else if (v.kind === "partial") {
          const tp1 = entry + v.r * risk; tp = tp1;
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
        } else if (v.kind === "trendline") {
          if (!struct[st.side]) continue;
          const S = struct[st.side];
          let q0 = st.sweep_bar, q1 = st.first_touch;
          for (let q = st.sweep_bar; q <= st.reclaim_bar; q++) if (S.L[q] < S.L[q0]) q0 = q;
          for (let q = st.first_touch; q <= st.signal_bar; q++) if (S.L[q] < S.L[q1]) q1 = q;
          const anchors = q1 > q0 ? [[q0, S.L[q0]], [q1, S.L[q1]]] : [[q0 - 1, S.L[q0]], [q0, S.L[q0]]];
          ex = exitTrendline(op, lo, hi, cl, k0, k1, sl, isFinite(tp) ? tp : Infinity, bOf, S.L, S.C, S.pl, p.pivot_len, anchors, p.tl_buffer_atr * a, !!p.tl_wait_pivot);
          R = (ex.px - entry - cost) / risk;
          if (ex.tl) ex.tl = ex.tl.map(([b, pr]) => [b, sg * pr]);
        } else {
          const act = entry + p.trail_start_R * risk;
          let candAt;
          if (v.kind === "trail_atr") candAt = (i, run) => run - p.trail_atr * a;
          else {
            if (!struct[st.side]) continue;
            const S = struct[st.side];
            candAt = (i) => { const b = Math.max(bOf[i] - 1, 0); return S.idx[b] > st.signal_bar ? S.lvl[b] - p.sl_buffer_atr * a : -Infinity; };
          }
          ex = exitTrailing(op, lo, hi, cl, k0, k1, entry, sl, candAt, act);
          tp = ex.stop;
          R = (ex.px - entry - cost) / risk;
        }
        // diagnóstico: tras un SL, ¿el precio llegó después al objetivo?
        let tpAfterSl = false;
        if (ex.why === "SL" && (v.kind !== "trail_atr" && v.kind !== "trail_struct") && isFinite(tp)) {
          for (let i = ex.idx + 1; i < k1; i++) if (hi[i] >= tp) { tpAfterSl = true; break; }
        }
        rows.push({ exit_px: sg * ex.px, tl: ex.tl || null, tp_after_sl: tpAfterSl, setup: st0, variant: v.id, side: st.side, signal_time: st.signal_time, entry_time: t1[k0], entry: sg * entry,
          sl: st.sl, tp: isFinite(tp) ? sg * tp : NaN, risk, exit_time: t1[ex.idx], exit_reason: ex.why, R, cost_R: cost / risk });
      }
    }
    rows.notFilled = notFilled;
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
    const trades = simulate(setups, d1, p, dTF);
    const splitTime = d1.t[Math.floor(d1.t.length * p.split)];
    return { setups, funnels, trades, splitTime, notFilled: trades.notFilled };
  }

  const api = { pivots, parseCSV, clean, resample, atr, poc, detect, simulate, metrics, sessionOf, run, nyMinuteOfDay, DEFAULTS, VARIANTS, MIN };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AMD = api;
})(typeof window !== "undefined" ? window : globalThis);
