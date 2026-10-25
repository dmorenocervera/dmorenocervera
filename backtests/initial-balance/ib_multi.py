"""Same IB pullback setup + filters on SPX500, NAS100 and US2000 (Oanda 1-min, 2005-2020).
Cost = 1.25 bps of price per round trip (= 0.25 pt on the S&P at 2,000)."""
import numpy as np, pandas as pd
import ib_backtest, ib_pullback as pb, ib_filters as fl

ROOT = '/home/user/futuresharks/financial-data/pyfinancialdata/data/currencies/oanda/'
allR = {}
rows = []
for inst in ['SPX500_USD', 'NAS100_USD', 'US2000_USD']:
    ib_backtest.DATA = fl.DATA = ROOT + inst
    raw = fl.load_all()
    T, F = fl.features(fl.daily_table(raw))
    pb.COST_PTS = 0.000125 * raw.close.median()
    R = pb.run(pb.prepare(ib_backtest.load()), 15, 'mid', 0.0, True, 'close').set_index('date').R
    sig = T[T.d != 0].dropna(subset=['pwh'])
    TR = F['trend with (close vs SMA20)'].reindex(sig.index)
    OD = F['open at opposite IB extreme (open drive)'].reindex(sig.index)
    for name, m in {'base': pd.Series(True, index=sig.index), 'trend': TR, 'trend+open drive': TR & OD}.items():
        r = R.reindex(sig.index[m]).dropna()
        allR[(inst, name)] = r
        rows.append(dict(inst=inst, rule=name, trades=len(r), per_year=round(len(r) / 15.4), win=round((r > 0).mean() * 100, 1),
                         avgR=round(r.mean(), 3), t=round(r.mean() / r.std() * np.sqrt(len(r)), 2),
                         R_IS=round(r[r.index < '2013'].mean(), 3), R_OOS=round(r[r.index >= '2013'].mean(), 3),
                         R_per_year=round(r.sum() / 15.4, 1), maxDD=round((r.cumsum().cummax() - r.cumsum()).max(), 1)))
pd.set_option('display.width', 220)
print(pd.DataFrame(rows).to_string(index=False))
for name in ['base', 'trend', 'trend+open drive']:
    P = pd.concat({i: allR[(i, name)] for i in ['SPX500_USD', 'NAS100_USD', 'US2000_USD']}, axis=1, sort=True)
    day = P.sum(axis=1)                              # same R risk on each market
    print(f'\nPortfolio 3 markets, rule {name}: trades/yr {P.count().sum() / 15.4:.0f}, R/yr {day.sum() / 15.4:.1f}, '
          f'maxDD {(day.cumsum().cummax() - day.cumsum()).max():.1f}R, '
          f'years positive {(day.groupby(day.index.year).sum() > 0).sum()}/{day.index.year.nunique()}')
    print('  correlation of same-day R:\n', P.corr().round(2).to_string())
    print('  R per year:', day.groupby(day.index.year).sum().round(1).to_dict())
