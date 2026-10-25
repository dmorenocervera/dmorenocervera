"""
Pullback variant of the Initial Balance setup (S&P 500, 1-min data 2005-2020).

Long (mirror for short):
  1. After 10:30 a candle CLOSES above the IB high (breakout).
  2. Price comes back to the IB high (low <= IBH + tol * IB range): the retest.
  3. Entry at the close of the first candle that closes back above the IB high.
     If price touches the stop before that, the setup is cancelled.
  Stop: 'mid'   -> IB midpoint
        'third' -> one third of the IB below the IB high, plus 10% of the IB as buffer
  Filter (optional): longs only if price was in the top third at 10:30, shorts only if bottom third.
  One trade per day, no new entries after 15:00 ET, flat at 16:00.
Signals on 5- or 15-min candles; stop/target checked on 1-min bars (stop first on ties).
"""
import sys, itertools
import numpy as np, pandas as pd
from ib_backtest import load, summary, COST_PTS

LAST_ENTRY = 15 * 60          # minutes after midnight ET


def prepare(df):
    out = []
    for d, g in df.groupby('date'):
        if len(g) < 360 or g['ib'].sum() < 55:
            continue
        ib, rest = g[g.ib], g[~g.ib]
        m = rest.index.hour * 60 + rest.index.minute
        x = dict(date=pd.Timestamp(d), ibh=ib.high.max(), ibl=ib.low.min(), px=ib.close.iloc[-1],
                 m=m.values, hi=rest.high.values, lo=rest.low.values, cl=rest.close.values)
        for tf in (5, 15):
            b = rest.resample(f'{tf}min', label='left', closed='left').agg(
                {'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
            bm = b.index.hour * 60 + b.index.minute + tf            # minute the candle closes
            x[tf] = (bm.values, b.high.values, b.low.values, b.close.values)
        out.append(x)
    return out


def entry(x, tf, side, sl, tol):
    """Return (minute of entry, entry price) or None."""
    bm, bh, bl, bc = x[tf]
    ibh, ibl, rng = x['ibh'], x['ibl'], x['ibh'] - x['ibl']
    edge = ibh if side == 1 else ibl
    state = 0
    for i in range(len(bc)):
        if bm[i] > LAST_ENTRY:
            return None
        out = bc[i] > edge if side == 1 else bc[i] < edge
        if state == 0:
            state = 1 if out else 0
            continue
        if (bl[i] <= sl) if side == 1 else (bh[i] >= sl):
            return None                                   # stop zone hit before entry
        if state == 1:
            touched = bl[i] <= edge + tol * rng if side == 1 else bh[i] >= edge - tol * rng
            if touched:
                state = 2
        if state == 2 and out:
            return bm[i], bc[i]
    return None


def manage(x, side, t0, e, sl, tp):
    rng = x['ibh'] - x['ibl']
    risk = (e - sl) * side
    if tp == 'close':
        tgt = None
    elif isinstance(tp, tuple):
        tgt = x['ibh'] + tp[1] * rng if side == 1 else x['ibl'] - tp[1] * rng
        if (tgt - e) * side <= 0:
            tgt = None
    else:
        tgt = e + side * tp * risk
    k = np.searchsorted(x['m'], t0)                       # first 1-min bar after entry candle
    hi, lo, cl = x['hi'][k:], x['lo'][k:], x['cl'][k:]
    pnl = None
    for i in range(len(hi)):
        if (lo[i] <= sl) if side == 1 else (hi[i] >= sl):
            pnl = (sl - e) * side; break
        if tgt is not None and ((hi[i] >= tgt) if side == 1 else (lo[i] <= tgt)):
            pnl = (tgt - e) * side; break
    if pnl is None:
        pnl = ((cl[-1] if len(cl) else e) - e) * side
    return (pnl - COST_PTS) / risk


def run(D, tf, stop, tol, filt, tp, sides=(1, -1)):
    res = []
    for x in D:
        ibh, ibl = x['ibh'], x['ibl']
        rng = ibh - ibl
        if rng <= 0:
            continue
        pos = (x['px'] - ibl) / rng
        cands = []
        for side in sides:
            if filt and not ((side == 1 and pos > 2/3) or (side == -1 and pos < 1/3)):
                continue
            if stop == 'mid':
                sl = (ibh + ibl) / 2
            else:
                sl = ibh - rng / 3 - 0.1 * rng if side == 1 else ibl + rng / 3 + 0.1 * rng
            en = entry(x, tf, side, sl, tol)
            if en:
                cands.append((en[0], side, en[1], sl))
        if not cands:
            continue
        t0, side, e, sl = min(cands)                      # first setup of the day only
        if (e - sl) * side <= 0:
            continue
        res.append((x['date'], side, manage(x, side, t0, e, sl, tp), (e - sl) * side))
    return pd.DataFrame(res, columns=['date', 'side', 'R', 'risk_pts'])


TPS = [1.0, 1.5, 2.0, 3.0, ('ext', 1.0), 'close']
GRID = list(itertools.product([5, 15], ['mid', 'third'], [0.0, 0.1], [True, False], TPS))

if __name__ == '__main__':
    pd.set_option('display.width', 220); pd.set_option('display.max_rows', 200)
    D = prepare(load())
    IS = [x for x in D if x['date'] < pd.Timestamp('2013-01-01')]
    OOS = [x for x in D if x['date'] >= pd.Timestamp('2013-01-01')]
    print(f'Days: {len(D)}  in-sample 2005-2012: {len(IS)}  out-of-sample 2013-2020: {len(OOS)}\n')

    rows = []
    for tf, stop, tol, filt, tp in GRID:
        a, b = run(IS, tf, stop, tol, filt, tp), run(OOS, tf, stop, tol, filt, tp)
        sa, sb = summary(a), summary(b)
        rows.append(dict(tf=tf, stop=stop, tol=tol, filter=filt, tp=str(tp),
                         IS_n=sa.get('trades'), IS_win=sa.get('winrate'), IS_avgR=sa.get('avgR'), IS_PF=sa.get('PF'),
                         OOS_n=sb.get('trades'), OOS_win=sb.get('winrate'), OOS_avgR=sb.get('avgR'), OOS_PF=sb.get('PF'),
                         risk_pts=round(pd.concat([a, b]).risk_pts.median(), 1)))
    g = pd.DataFrame(rows).sort_values('IS_avgR', ascending=False)
    g.to_csv('pullback_grid.csv', index=False)
    print(f'Configs with IS avgR > 0: {(g.IS_avgR > 0).sum()} / {len(g)};  OOS avgR > 0: {(g.OOS_avgR > 0).sum()} / {len(g)};'
          f'  both: {((g.IS_avgR > 0) & (g.OOS_avgR > 0)).sum()}\n')
    print('Top 15 by in-sample avgR, with their out-of-sample result:\n', g.head(15).to_string(index=False), '\n')

    print('Your base idea (5-min candles, touch the edge, 10:30 filter), full 2005-2020:')
    rows = []
    for stop in ['mid', 'third']:
        for tp in TPS:
            t = run(D, 5, stop, 0.0, True, tp)
            rows.append(dict(stop=stop, tp=str(tp), **summary(t), risk_pts=round(t.risk_pts.median(), 1)))
    print(pd.DataFrame(rows).to_string(index=False), '\n')

    best = g.iloc[0]
    tp = eval(best.tp) if best.tp.startswith('(') else (best.tp if best.tp == 'close' else float(best.tp))
    t = run(D, int(best.tf), best.stop, best.tol, bool(best['filter']), tp)
    print(f'Best IS config ({best.tf}m, {best.stop}, tol {best.tol}, filter {best["filter"]}, tp {best.tp}) per year:')
    print(t.groupby(t.date.dt.year).R.agg(n='count', sumR='sum', win=lambda r: (r > 0).mean()).round(2).to_string())
    print('\nLongs vs shorts:\n', t.groupby('side').R.agg(['count', 'mean', 'sum']).round(3))
