"""
Break-even and trailing-stop exits on top of the pullback setup (ib_pullback.py).

Stops are in R (1R = distance entry -> initial stop):
  be(X)        : once price has moved X·R in favour, stop goes to entry (+ costs, so BE is really flat)
  trail(A, Y)  : once price has moved A·R in favour (A=0: from the start), stop trails Y·R behind
                 the best price reached. The stop never moves back.
Anything still open goes flat at 16:00.
Conservative fills: each 1-min bar first checks the stop that was in place before the bar,
and only then updates the best price with that bar's high/low.
"""
import numpy as np, pandas as pd
import ib_backtest, ib_pullback as pb


def manage(x, side, t0, e, sl0, rule, cost):
    risk = (e - sl0) * side
    k = np.searchsorted(x['m'], t0)
    hi, lo, cl = x['hi'][k:], x['lo'][k:], x['cl'][k:]
    sl, best = sl0, e
    for i in range(len(hi)):
        if (lo[i] <= sl) if side == 1 else (hi[i] >= sl):
            return ((sl - e) * side - cost) / risk
        best = max(best, hi[i]) if side == 1 else min(best, lo[i])
        run = (best - e) * side / risk                      # max favourable move, in R
        kind = rule[0]
        if kind == 'be' and run >= rule[1]:
            new = e + side * cost
        elif kind == 'trail' and run >= rule[1]:
            new = best - side * rule[2] * risk
        else:
            continue
        sl = max(sl, new) if side == 1 else min(sl, new)
    last = cl[-1] if len(cl) else e
    return ((last - e) * side - cost) / risk


def run(D, cfg, rule, cost):
    tf, stop, tol, filt = cfg
    res = []
    for x in D:
        ibh, ibl = x['ibh'], x['ibl']; rng = ibh - ibl
        if rng <= 0:
            continue
        pos = (x['px'] - ibl) / rng
        cands = []
        for side in (1, -1):
            if filt and not ((side == 1 and pos > 2/3) or (side == -1 and pos < 1/3)):
                continue
            sl = (ibh + ibl) / 2 if stop == 'mid' else (
                ibh - rng / 3 - 0.1 * rng if side == 1 else ibl + rng / 3 + 0.1 * rng)
            en = pb.entry(x, tf, side, sl, tol)
            if en:
                cands.append((en[0], side, en[1], sl))
        if not cands:
            continue
        t0, side, e, sl = min(cands)
        if (e - sl) * side > 0:
            res.append((x['date'], manage(x, side, t0, e, sl, rule, cost)))
    return pd.DataFrame(res, columns=['date', 'R'])


def stat(t):
    r = t.R
    eq = r.cumsum()
    return dict(n=len(r), win=round((r > 0).mean() * 100, 1), avgR=round(r.mean(), 3),
                t=round(r.mean() / r.std() * np.sqrt(len(r)), 2),
                PF=round(r[r > 0].sum() / -r[r <= 0].sum(), 2), maxDD=round((eq.cummax() - eq).max(), 1),
                IS=round(r[t.date < '2013'].mean(), 3), OOS=round(r[t.date >= '2013'].mean(), 3))


RULES = [('none',)] + [('be', x) for x in (0.5, 1.0, 1.5, 2.0)] + \
        [('trail', a, y) for a in (0.0, 0.5, 1.0, 1.5) for y in (0.5, 1.0, 1.5, 2.0)]

if __name__ == '__main__':
    pd.set_option('display.width', 200)
    D = pb.prepare(ib_backtest.load())
    for cfg in [(15, 'mid', 0.0, True), (5, 'mid', 0.0, True)]:
        for cost in (0.5, 0.25):
            rows = [dict(rule=' '.join(map(str, r)), **stat(run(D, cfg, r, cost))) for r in RULES]
            print(f'\n=== {cfg[0]}-min candles, stop {cfg[1]}, filter on, cost {cost} pts ===')
            print(pd.DataFrame(rows).to_string(index=False))
