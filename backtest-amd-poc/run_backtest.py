"""Backtest del setup AMD + retesteo del PoC.

Ejemplos:
  # Descargar datos de Dukascopy y probar en oro, 15m
  python run_backtest.py --duka XAUUSD --start 2020-01-01 --end 2025-12-31 --tf 15min --cost 0.35

  # Nasdaq (CFD Dukascopy) en modo apertura de Nueva York, 5m
  python run_backtest.py --duka USATECHIDXUSD --start 2020-01-01 --end 2025-12-31 --tf 5min --ny-open --cost 1.5

  # Con un CSV propio de velas de 1m (export de TradingView, MT5...)
  python run_backtest.py --csv data/NQ_1m.csv --tf 5min --cost 0.5

  # Barrido de temporalidades
  python run_backtest.py --duka XAUUSD --start 2020-01-01 --end 2025-12-31 --tf 5min,15min,1h
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from amd.backtest import simulate, summarize
from amd.data import download_dukascopy, load_csv, resample
from amd.report import build_report
from amd.strategy import Params, detect


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="CSV de velas de 1m")
    src.add_argument("--duka", help="símbolo Dukascopy (XAUUSD, USATECHIDXUSD...)")
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--tf", default="15min", help="una o varias separadas por coma: 5min,15min,1h")
    ap.add_argument("--cost", type=float, required=True, help="coste ida+vuelta en unidades de precio (spread+comisión+slippage)")
    ap.add_argument("--ny-open", action="store_true", help="modo apertura de Nueva York (9:30 ET)")
    ap.add_argument("--no-impulse", action="store_true", help="no exigir impulso previo al rango")
    ap.add_argument("--entry-touch", type=int, default=1)
    ap.add_argument("--split", type=float, default=0.7, help="fracción del histórico considerada 'dentro de muestra'")
    ap.add_argument("--out", default="reports")
    args = ap.parse_args()

    df1 = load_csv(args.csv) if args.csv else download_dukascopy(args.duka, args.start, args.end)
    if args.csv:
        df1 = df1.loc[args.start : args.end]
    name = Path(args.csv).stem if args.csv else args.duka
    print(f"{name}: {len(df1):,} velas 1m  {df1.index[0]} -> {df1.index[-1]}")
    split_time = df1.index[int(len(df1) * args.split)]
    Path(args.out).mkdir(exist_ok=True)

    overview = []
    for tf in args.tf.split(","):
        p = Params(tf=tf, ny_open=args.ny_open, require_impulse=not args.no_impulse, entry_touch=args.entry_touch)
        if args.ny_open:
            p.range_max_bars = 400  # el rango puede durar toda la madrugada
        dft = resample(df1, tf)
        setups, funnels = detect(dft, df1, p)
        trades = simulate(setups, df1, cost=args.cost)
        mode = "nyopen" if args.ny_open else "general"
        tag = f"{name}_{tf}_{mode}"
        trades.to_csv(Path(args.out) / f"{tag}_trades.csv", index=False)
        build_report(Path(args.out) / f"{tag}.html", f"AMD + PoC — {name} {tf} ({mode})", p, funnels, trades, setups, dft,
                     split_time=split_time)
        s = summarize(trades)
        if not s.empty:
            s.insert(0, "tf", tf)
            overview.append(s)
        print(f"\n== {tf} ({mode}) — setups: {len(setups)}")
        print(s.to_string(index=False) if not s.empty else "sin operaciones")
    if overview:
        pd.concat(overview).to_csv(Path(args.out) / f"{name}_resumen.csv", index=False)


if __name__ == "__main__":
    main()
