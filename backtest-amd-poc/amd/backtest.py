"""Simulación de salidas sobre velas de 1m y métricas."""
from __future__ import annotations

import numpy as np
import pandas as pd

# Variantes de take profit
VARIANTS = {
    "A_origen_impulso": dict(kind="level", level="impulse_top"),
    "B_rango_x1": dict(kind="proj", mult=1.0),
    "B_rango_x2": dict(kind="proj", mult=2.0),
    "C_2R": dict(kind="r", r=2.0),
    "C_3R": dict(kind="r", r=3.0),
    "C_5R": dict(kind="r", r=5.0),
    "D_parcial2R_BE_A": dict(kind="partial", r=2.0),
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


def simulate(setups, df_1m: pd.DataFrame, cost: float, max_hold_min: int = 3 * 24 * 60, min_rr: float = 1.0):
    """cost = coste total ida+vuelta (spread + comisión + slippage) en unidades de precio."""
    t1 = df_1m.index
    O1 = df_1m["open"].to_numpy()
    rows = []
    for st in setups:
        sg = 1.0 if st["side"] == "long" else -1.0
        k0 = t1.searchsorted(st["entry_time"])
        if k0 >= len(t1):
            continue
        k1 = t1.searchsorted(t1[k0] + pd.Timedelta(minutes=max_hold_min))
        if sg > 0:
            lo, hi = df_1m["low"].to_numpy()[k0:k1], df_1m["high"].to_numpy()[k0:k1]
        else:
            lo, hi = -df_1m["high"].to_numpy()[k0:k1], -df_1m["low"].to_numpy()[k0:k1]
        cl = sg * df_1m["close"].to_numpy()[k0:k1]
        entry = sg * O1[k0]
        sl = sg * st["sl"]
        risk = entry - sl
        if risk <= 0:
            continue
        rh, rl = (sg * st["range_high"], sg * st["range_low"]) if sg > 0 else (sg * st["range_low"], sg * st["range_high"])
        height = rh - rl
        top = sg * st["impulse_top"]
        base = dict(
            side=st["side"], signal_time=st["signal_time"], entry_time=t1[k0], entry=sg * entry, sl=st["sl"],
            risk=risk, poc=st["poc"], range_high=st["range_high"], range_low=st["range_low"],
            impulse_top=st["impulse_top"], cost_R=cost / risk,
        )
        for name, v in VARIANTS.items():
            if v["kind"] == "level":
                tp = top
            elif v["kind"] == "proj":
                tp = rh + v["mult"] * height
            elif v["kind"] == "r":
                tp = entry + v["r"] * risk
            else:
                tp = None
            if v["kind"] in ("level", "proj") and (tp - entry) < min_rr * risk:
                continue  # el objetivo no compensa el riesgo: no se opera esta variante
            if v["kind"] != "partial":
                px, idx, why = _exit(lo, hi, cl, entry, sl, tp)
                R = (px - entry - cost) / risk
            else:
                tp1 = entry + v["r"] * risk
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
                        R = 0.5 * (tp1 - entry) / risk + 0.5 * (px2 - entry) / risk - cost / risk
                        why = "TP1+" + why2
            rows.append(dict(base, variant=name, tp=sg * tp if tp is not None else np.nan, exit_time=t1[k0 + idx],
                             exit_reason=why, R=R, bars_held_min=int(idx)))
    return pd.DataFrame(rows)


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
