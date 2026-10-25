"""Cost sensitivity and significance for the best pullback configs."""
import numpy as np, pandas as pd
import ib_backtest, ib_pullback as pb
D = pb.prepare(ib_backtest.load())
cfgs = [(15, 'mid', 0.0, True, 'close'), (15, 'mid', 0.1, True, 'close'),
        (15, 'third', 0.0, True, 'close'), (5, 'mid', 0.0, True, 'close')]
rows = []
for cost in [0.0, 0.25, 0.5, 1.0]:
    pb.COST_PTS = cost
    for c in cfgs:
        t = pb.run(D, *c)
        r = t.R
        oos = t[t.date >= '2013'].R
        rows.append(dict(cost_pts=cost, cfg=f'{c[0]}m {c[1]} tol{c[2]}', n=len(r), avgR=round(r.mean(), 3),
                         t_stat=round(r.mean() / r.std() * np.sqrt(len(r)), 2), PF=round(r[r > 0].sum() / -r[r <= 0].sum(), 2),
                         OOS_avgR=round(oos.mean(), 3), totalR=round(r.sum(), 1)))
print(pd.DataFrame(rows).to_string(index=False))
