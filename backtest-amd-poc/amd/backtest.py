"""Simulación de entradas y salidas sobre velas de 1m y métricas."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .strategy import pivots

# Variantes de salida
VARIANTS = {
    "A_origen_impulso": dict(kind="level", level="impulse_top"),
    "B_rango_x1": dict(kind="proj", mult=1.0),
    "B_rango_x2": dict(kind="proj", mult=2.0),
    "C_2R": dict(kind="r", r=2.0),
    "C_3R": dict(kind="r", r=3.0),
    "C_5R": dict(kind="r", r=5.0),
    "D_parcial2R_BE_A": dict(kind="partial", r=2.0),
    "E_trailing_ATR": dict(kind="trail_atr"),
    "F_trailing_estructura": dict(kind="trail_struct"),
}


def _first_hit(lo, hi, sl, tp):
    """Índice de la primera vela que toca SL / TP (o len si ninguno). En largo."""
    n = len(lo)
    a = np.flatnonzero(lo <= sl)
    b = np.flatnonzero(hi >= tp) if np.isfinite(tp) else np.array([], int)
    i_sl = a[0] if len(a) else n
    i_tp = b[0] if len(b) else n
    return i_sl, i_tp


def _exit(lo, hi, cl, entry, sl, tp):
    """Devuelve (precio_salida, idx_salida, motivo). Si SL y TP en la misma vela de 1m -> SL (conservador)."""
    i_sl, i_tp = _first_hit(lo, hi, sl, tp)
    n = len(lo)
    if i_sl == n and i_tp == n:
        return cl[-1], n - 1, "tiempo"
    if i_sl <= i_tp:
        return sl, i_sl, "SL"
    return tp, i_tp, "TP"


def _exit_trailing(op, lo, hi, cl, entry, sl, cand, act_level):
    """Stop dinámico. cand[t] = nivel de stop propuesto con info hasta la vela anterior.
    Sólo se aplica cuando el máximo alcanzado (hasta t-1) llega a act_level. El stop nunca baja.
    Salida al primer low <= stop, al precio del stop (o a la apertura si abre por debajo)."""
    run = np.maximum.accumulate(hi)
    prev = np.r_[entry, run[:-1]]
    prev = np.maximum(prev, entry)
    c = np.where(prev >= act_level, cand, -np.inf)
    stop = np.maximum.accumulate(np.maximum(c, sl))
    hit = np.flatnonzero(lo <= stop)
    if not len(hit):
        return cl[-1], len(lo) - 1, "tiempo", stop[-1]
    t = hit[0]
    px = min(stop[t], op[t])
    return px, t, ("SL" if stop[t] <= sl else "trailing"), stop[t]


def _last_pivot_low(L, ph_pl, k):
    """Para cada vela b: nivel e índice del último mínimo de pivote confirmado al cierre de b (q + k <= b)."""
    n = len(L)
    lvl = np.full(n, np.nan)
    idx = np.full(n, -1)
    cur_l, cur_i = np.nan, -1
    for b in range(n):
        q = b - k
        if q >= 0 and ph_pl[q]:
            cur_l, cur_i = L[q], q
        lvl[b], idx[b] = cur_l, cur_i
    return lvl, idx


def _fill(st, t1, op, lo, hi, k_from, k_until, sl):
    """Ejecuta la orden de entrada (en espacio largo). Devuelve (k0, precio) o None."""
    typ, level = st["entry_type"], st["entry_level_long"]
    if typ == "close":
        return (k_from, op[k_from]) if k_from < len(t1) else None
    for k in range(k_from, min(k_until, len(t1))):
        if typ == "break":
            if hi[k] >= level:
                return k, max(level, op[k])
            if lo[k] <= sl:  # invalidado antes de activarse
                return None
        else:  # limit
            if lo[k] <= level:
                return k, min(level, op[k])
    return None


def simulate(setups, df_1m: pd.DataFrame, cost: float, max_hold_min: int = 3 * 24 * 60, min_rr: float = 1.0,
             df_tf: pd.DataFrame | None = None, p=None):
    """cost = coste total ida+vuelta (spread + comisión + slippage) en unidades de precio.
    df_tf y p (Params) son necesarios para el trailing por estructura."""
    t1 = df_1m.index
    n1 = len(t1)
    raw = {k: df_1m[k].to_numpy() for k in ("open", "high", "low", "close")}
    space = {
        "long": (raw["open"], raw["low"], raw["high"], raw["close"]),
        "short": (-raw["open"], -raw["high"], -raw["low"], -raw["close"]),
    }
    trail_atr = getattr(p, "trail_atr", 2.5)
    trail_start = getattr(p, "trail_start_R", 1.0)
    sl_buf = getattr(p, "sl_buffer_atr", 0.05)
    struct = {}
    if df_tf is not None and p is not None:
        tf_t = df_tf.index
        b_of_1m = np.searchsorted(tf_t.values, t1.values, "right") - 1
        for side, sg in (("long", 1.0), ("short", -1.0)):
            H = df_tf["high"].to_numpy() if sg > 0 else -df_tf["low"].to_numpy()
            L = df_tf["low"].to_numpy() if sg > 0 else -df_tf["high"].to_numpy()
            _, pl = pivots(H, L, p.pivot_len)
            struct[side] = _last_pivot_low(L, pl, p.pivot_len)
    rows, not_filled = [], 0
    for st in setups:
        side = st["side"]
        sg = 1.0 if side == "long" else -1.0
        op, lo_all, hi_all, cl_all = space[side]
        st = dict(st, entry_level_long=sg * st["entry_level"] if np.isfinite(st["entry_level"]) else np.nan)
        sl = sg * st["sl"]
        k_from = t1.searchsorted(st["entry_from"])
        k_until = t1.searchsorted(st["entry_until"])
        f = _fill(st, t1, op, lo_all, hi_all, k_from, k_until, sl)
        if f is None:
            not_filled += 1
            continue
        k0, entry = f
        k1 = t1.searchsorted(t1[k0] + pd.Timedelta(minutes=max_hold_min))
        lo, hi, cl, o = lo_all[k0:k1], hi_all[k0:k1], cl_all[k0:k1], op[k0:k1]
        risk = entry - sl
        if risk <= 0:
            continue
        rh, rl = (sg * st["range_high"], sg * st["range_low"]) if sg > 0 else (sg * st["range_low"], sg * st["range_high"])
        height = rh - rl
        top = sg * st["impulse_top"]
        a = st["atr"]
        base = dict(
            side=side, signal_time=st["signal_time"], entry_time=t1[k0], entry=sg * entry, sl=st["sl"],
            risk=risk, poc=st["poc"], range_high=st["range_high"], range_low=st["range_low"],
            impulse_top=st["impulse_top"], cost_R=cost / risk,
        )
        for name, v in VARIANTS.items():
            kind = v["kind"]
            tp = np.nan
            if kind == "level":
                tp = top
            elif kind == "proj":
                tp = rh + v["mult"] * height
            elif kind == "r":
                tp = entry + v["r"] * risk
            if kind in ("level", "proj") and (tp - entry) < min_rr * risk:
                continue  # el objetivo no compensa el riesgo: no se opera esta variante
            if kind in ("level", "proj", "r"):
                px, idx, why = _exit(lo, hi, cl, entry, sl, tp)
                R = (px - entry - cost) / risk
            elif kind == "partial":
                tp1 = entry + v["r"] * risk
                tp = tp1
                px, idx, why = _exit(lo, hi, cl, entry, sl, tp1)
                if why != "TP":
                    R = (px - entry - cost) / risk
                else:
                    final_tp = max(top, tp1)
                    if final_tp <= tp1 or idx + 1 >= len(lo):
                        R = (tp1 - entry - cost) / risk
                    else:
                        px2, idx2, why2 = _exit(lo[idx + 1 :], hi[idx + 1 :], cl[idx + 1 :], entry, entry, final_tp)
                        idx = idx + 1 + idx2
                        tp = final_tp
                        R = 0.5 * (tp1 - entry) / risk + 0.5 * (px2 - entry) / risk - cost / risk
                        why = "TP1+" + why2
            else:
                act = entry + trail_start * risk
                if kind == "trail_atr":
                    run = np.maximum.accumulate(hi)
                    cand = np.r_[entry, run[:-1]] - trail_atr * a
                else:
                    if side not in struct:
                        continue
                    lvl, pidx = struct[side]
                    b = b_of_1m[k0:k1] - 1  # última vela de la TF ya cerrada
                    b = np.clip(b, 0, None)
                    ok = pidx[b] > st["signal_bar"]
                    cand = np.where(ok, lvl[b] - sl_buf * a, -np.inf)
                px, idx, why, tp = _exit_trailing(o, lo, hi, cl, entry, sl, cand, act)
                R = (px - entry - cost) / risk
            rows.append(dict(base, variant=name, tp=sg * tp if np.isfinite(tp) else np.nan, exit_time=t1[k0 + idx],
                             exit_reason=why, R=R, bars_held_min=int(idx)))
    df = pd.DataFrame(rows)
    df.attrs["not_filled"] = not_filled
    return df


def session_of(ts: pd.Timestamp) -> str:
    h = ts.tz_convert("America/New_York").hour + ts.tz_convert("America/New_York").minute / 60
    if 9.5 <= h < 16:
        return "NY"
    if 3 <= h < 9.5:
        return "Londres/pre-NY"
    if 18 <= h or h < 3:
        return "Asia"
    return "Otro"


def metrics(R: pd.Series) -> dict:
    R = R.dropna()
    if len(R) == 0:
        return dict(trades=0)
    eq = R.cumsum()
    dd = (eq - eq.cummax()).min()
    wins, losses = R[R > 0], R[R <= 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else np.inf
    # equity compuesta arriesgando 1% por operación
    comp = (1 + 0.01 * R).prod() - 1
    return dict(
        trades=len(R), win_rate=len(wins) / len(R), avg_R=R.mean(), total_R=R.sum(), profit_factor=pf,
        max_dd_R=dd, ret_1pct_risk=comp,
        t_stat=R.mean() / (R.std(ddof=1) / np.sqrt(len(R))) if len(R) > 2 and R.std() > 0 else np.nan,
    )


def summarize(trades: pd.DataFrame, by: list[str] | None = None) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    keys = ["variant"] + (by or [])
    rows = [dict(zip(keys, k), **metrics(g["R"])) for k, g in trades.groupby(keys)]
    return pd.DataFrame(rows)
