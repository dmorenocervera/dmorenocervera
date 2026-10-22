"""Informe HTML: métricas, curvas de equity, embudo del setup y gráficos de ejemplos."""
from __future__ import annotations

import base64
import io
from dataclasses import asdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .backtest import session_of, summarize


def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def plot_setup(df_tf: pd.DataFrame, st: dict, trade: pd.Series | None = None, pad: int = 25):
    i0 = max(st["range_start"] - pad, 0)
    i1 = min(st["signal_bar"] + 3 * pad, len(df_tf) - 1)
    d = df_tf.iloc[i0 : i1 + 1]
    x = np.arange(len(d))
    fig, ax = plt.subplots(figsize=(10, 4.2))
    up = d["close"] >= d["open"]
    for col, m in (("#26a69a", up), ("#ef5350", ~up)):
        ax.vlines(x[m], d["low"][m], d["high"][m], color=col, lw=0.8)
        ax.bar(x[m], (d["close"] - d["open"])[m].abs().clip(lower=1e-9), bottom=d[["open", "close"]].min(axis=1)[m],
               color=col, width=0.7)
    rs, re = st["range_start"] - i0, st["range_end"] - i0
    lo, hi = min(st["range_low"], st["range_high"]), max(st["range_low"], st["range_high"])
    ax.add_patch(plt.Rectangle((rs - 0.5, lo), re - rs + 1, hi - lo, color="#7e8ba3", alpha=0.18))
    ax.hlines(st["poc"], rs, len(d) - 1, color="#c2185b", lw=1.2, label="PoC")
    sb = st["signal_bar"] - i0
    ax.axvline(sb, color="#555", ls=":", lw=0.8)
    if trade is not None:
        ax.hlines(trade["sl"], sb, len(d) - 1, color="#ef5350", ls="--", lw=1, label="SL")
        if np.isfinite(trade["tp"]):
            ax.hlines(trade["tp"], sb, len(d) - 1, color="#26a69a", ls="--", lw=1, label="TP")
        ax.set_title(f"{st['side']}  {st['signal_time']:%Y-%m-%d %H:%M} UTC  ->  {trade['exit_reason']}  {trade['R']:+.2f}R",
                     fontsize=10)
    ax.legend(loc="upper left", fontsize=8)
    ax.set_xticks(x[:: max(len(x) // 8, 1)])
    ax.set_xticklabels([t.strftime("%m-%d %H:%M") for t in d.index[:: max(len(x) // 8, 1)]], fontsize=7)
    ax.grid(alpha=0.2)
    return fig


def equity_fig(trades: pd.DataFrame, split_time=None):
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for v, g in trades.sort_values("entry_time").groupby("variant"):
        ax.plot(g["entry_time"], g["R"].cumsum(), label=v, lw=1.2)
    if split_time is not None:
        ax.axvline(split_time, color="k", ls="--", lw=0.8)
        ax.text(split_time, ax.get_ylim()[1], " fuera de muestra →", va="top", fontsize=8)
    ax.axhline(0, color="#999", lw=0.6)
    ax.set_ylabel("R acumulado")
    ax.legend(fontsize=8, ncol=2)
    ax.grid(alpha=0.2)
    return fig


def _table(df: pd.DataFrame) -> str:
    if df.empty:
        return "<p><em>Sin operaciones.</em></p>"
    df = df.copy()
    fmt = {"win_rate": "{:.1%}", "ret_1pct_risk": "{:+.1%}", "avg_R": "{:+.3f}", "total_R": "{:+.1f}",
           "profit_factor": "{:.2f}", "max_dd_R": "{:.1f}", "t_stat": "{:.2f}"}
    for c, f in fmt.items():
        if c in df:
            df[c] = df[c].map(lambda v: f.format(v) if pd.notna(v) else "")
    return df.to_html(index=False, border=0, classes="t")


CSS = """
body{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222;background:#fff}
h1{font-size:22px}h2{font-size:18px;margin-top:32px;border-bottom:1px solid #ddd;padding-bottom:4px}
table.t{border-collapse:collapse;font-size:13px;margin:8px 0}table.t td,table.t th{padding:4px 8px;border-bottom:1px solid #eee;text-align:right}
table.t th{background:#f5f5f5}img{max-width:100%}.note{background:#fff8e1;padding:8px 12px;border-left:3px solid #f9a825;font-size:14px}
code{background:#f3f3f3;padding:1px 4px}
"""


def build_report(path, title, params, funnels, trades, setups, df_tf, split_time=None, n_examples=12, note=""):
    parts = [f"<!doctype html><meta charset=utf-8><title>{title}</title><style>{CSS}</style><h1>{title}</h1>"]
    if note:
        parts.append(f"<p class=note>{note}</p>")
    parts.append("<h2>Parámetros</h2><pre>" + "\n".join(f"{k} = {v}" for k, v in params.to_dict().items()) + "</pre>")
    fun = pd.DataFrame({s: asdict(f) for s, f in funnels.items()})
    parts.append("<h2>Embudo del setup</h2><p>Cuántos rangos llegan a cada fase.</p>" + fun.to_html(border=0, classes="t"))
    if trades.empty:
        parts.append("<p><strong>Sin operaciones con estos parámetros.</strong></p>")
    else:
        trades = trades.copy()
        if split_time is not None:
            trades["muestra"] = np.where(trades["entry_time"] < split_time, "1_dentro", "2_fuera")
        parts.append("<h2>Resultados por variante de TP</h2>" + _table(summarize(trades)))
        if split_time is not None:
            parts.append("<h2>Dentro vs fuera de muestra</h2>" + _table(summarize(trades, ["muestra"])))
        parts.append(f"<img src='{_png(equity_fig(trades, split_time))}'>")
        parts.append("<h2>Por lado</h2>" + _table(summarize(trades, ["side"])))
        trades["sesion"] = trades["entry_time"].map(session_of)
        parts.append("<h2>Por sesión de entrada</h2>" + _table(summarize(trades, ["sesion"])))
        trades["año"] = trades["entry_time"].dt.year
        parts.append("<h2>Por año</h2>" + _table(summarize(trades, ["año"])))
        # ejemplos
        parts.append(f"<h2>Ejemplos de setups detectados (variante C_3R)</h2>")
        ex = trades[trades["variant"] == "C_3R"].set_index("signal_time")
        sel = setups[:: max(len(setups) // n_examples, 1)][:n_examples]
        for st in sel:
            tr = ex.loc[st["signal_time"]] if st["signal_time"] in ex.index else None
            if isinstance(tr, pd.DataFrame):
                tr = tr.iloc[0]
            parts.append(f"<img src='{_png(plot_setup(df_tf, st, tr))}'>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
