"""Combinations of the filters that held up in both halves (see results_filters.txt)."""
import numpy as np, pandas as pd
import ib_backtest, ib_pullback as pb, ib_filters as fl

T, F = fl.features(fl.daily_table(fl.load_all()))
pb.COST_PTS = 0.25
R = pb.run(pb.prepare(ib_backtest.load()), 15, 'mid', 0.0, True, 'close').set_index('date').R
sig = T[T.d != 0].dropna(subset=['pwh'])
f = {k: v.reindex(sig.index) for k, v in F.items()}
TR, OD, ON, NF = f['trend with (close vs SMA20)'], f['open at opposite IB extreme (open drive)'], \
    f['10:30 beyond overnight extreme'], f['no FVG against']
NOT_NARROW, NOT_FRI = ~f['narrow IB (<0.7x avg)'], ~f['day = Fri']
combos = {'BASE': pd.Series(True, index=sig.index), 'trend': TR, 'open drive': OD,
          'trend + open drive': TR & OD, 'trend + no FVG against': TR & NF,
          'open drive + beyond overnight': OD & ON, 'trend + beyond overnight': TR & ON,
          'trend + not narrow IB': TR & NOT_NARROW, 'trend + open drive + not narrow': TR & OD & NOT_NARROW,
          'trend + not Friday': TR & NOT_FRI}
rows = []
for k, m in combos.items():
    S = sig[m]; r = R.reindex(S.index).dropna()
    iso = r.index < '2013'
    rows.append(dict(combo=k, days=len(S), hit=round(S.hit.mean() * 100, 1),
                     hit_IS=round(S[S.index < '2013'].hit.mean() * 100, 1), hit_OOS=round(S[S.index >= '2013'].hit.mean() * 100, 1),
                     trades=len(r), per_year=round(len(r) / 15.4), win=round((r > 0).mean() * 100, 1), avgR=round(r.mean(), 3),
                     t=round(r.mean() / r.std() * np.sqrt(len(r)), 2), R_IS=round(r[iso].mean(), 3), R_OOS=round(r[~iso].mean(), 3),
                     PF=round(r[r > 0].sum() / -r[r <= 0].sum(), 2),
                     maxDD=round((r.cumsum().cummax() - r.cumsum()).max(), 1)))
pd.set_option('display.width', 220)
print(pd.DataFrame(rows).to_string(index=False))
