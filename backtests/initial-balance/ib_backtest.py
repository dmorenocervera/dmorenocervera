"""
Backtest of the "Initial Balance thirds" setup on the S&P 500.

IB = high/low of 9:30-10:30 ET. At 10:30 look where price is inside the IB:
  - top third    -> long,  stop below IB low  (bet: day low is already in)
  - bottom third -> short, stop above IB high (bet: day high is already in)
  - middle third -> no trade
Data: Oanda SPX500_USD 1-minute bars (UTC), 2005-2020,
from github.com/FutureSharks/financial-data.
"""
import glob, sys
import numpy as np, pandas as pd

DATA = sys.argv[1] if len(sys.argv) > 1 else \
    '/home/user/futuresharks/financial-data/pyfinancialdata/data/currencies/oanda/SPX500_USD'
COST_PTS = 0.5          # round-trip spread + slippage + commission, index points
MIN_BARS = 360          # skip days with gaps / half sessions (390 = full session)


def load():
    files = sorted(glob.glob(f'{DATA}/*/*.csv'))
    df = pd.concat(pd.read_csv(f, parse_dates=['time']) for f in files)
    df = df.drop_duplicates('time').set_index('time').sort_index()
    df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')
    t = df.index.hour * 60 + df.index.minute
    df = df[(t >= 570) & (t < 960)]                       # 9:30-16:00 ET
    df['date'] = df.index.date
    df['ib'] = (df.index.hour * 60 + df.index.minute) < 630  # before 10:30
    return df


def days(df):
    out = []
    for d, g in df.groupby('date'):
        if len(g) < MIN_BARS or g['ib'].sum() < 55:
            continue
        ib, rest = g[g.ib], g[~g.ib]
        out.append(dict(date=pd.Timestamp(d), ibh=ib.high.max(), ibl=ib.low.min(),
                        px=ib.close.iloc[-1], hi=rest.high.values, lo=rest.low.values,
                        cl=rest.close.values, dh=g.high.max(), dl=g.low.min()))
    return out


def stats(D):
    rows = []
    for x in D:
        rng = x['ibh'] - x['ibl']
        pos = (x['px'] - x['ibl']) / rng if rng > 0 else .5
        zone = 'top' if pos > 2/3 else 'bottom' if pos < 1/3 else 'mid'
        rows.append(dict(date=x['date'], zone=zone, ib_rng=rng,
                         low_in_ib=x['lo'].min() >= x['ibl'],     # IB low never broken later
                         high_in_ib=x['hi'].max() <= x['ibh']))
    s = pd.DataFrame(rows)
    s['either'] = s.low_in_ib | s.high_in_ib
    return s


def trade(x, side, stop_mode, tp):
    """Return P&L in R (after costs). tp: float R multiple, 'close', or ('ext', k)."""
    entry, ibh, ibl, rng = x['px'], x['ibh'], x['ibl'], x['ibh'] - x['ibl']
    if stop_mode == 'ib':
        sl = ibl - 0.25 if side == 1 else ibh + 0.25
    else:                                                  # 'third': edge of the middle third
        sl = ibl + rng / 3 if side == 1 else ibh - rng / 3
    risk = (entry - sl) * side
    if risk <= 0:
        return None
    if tp == 'close':
        tgt = None
    elif isinstance(tp, tuple):                            # IB extension: IBH + k*range
        tgt = ibh + tp[1] * rng if side == 1 else ibl - tp[1] * rng
    else:
        tgt = entry + side * tp * risk
    hi, lo, cl = x['hi'], x['lo'], x['cl']
    for i in range(len(hi)):
        stop_hit = lo[i] <= sl if side == 1 else hi[i] >= sl
        tgt_hit = tgt is not None and (hi[i] >= tgt if side == 1 else lo[i] <= tgt)
        if stop_hit:                                       # same bar -> assume stop first
            pnl = (sl - entry) * side; break
        if tgt_hit:
            pnl = (tgt - entry) * side; break
    else:
        pnl = (cl[-1] - entry) * side                      # flat at the close
    return (pnl - COST_PTS) / risk


def run(D, stop_mode, tp, zones=('top', 'bottom')):
    res = []
    for x in D:
        rng = x['ibh'] - x['ibl']
        if rng <= 0:
            continue
        pos = (x['px'] - x['ibl']) / rng
        side = 1 if pos > 2/3 else -1 if pos < 1/3 else 0
        if side == 0 or ('top' if side == 1 else 'bottom') not in zones:
            continue
        r = trade(x, side, stop_mode, tp)
        if r is not None:
            res.append((x['date'], side, r))
    return pd.DataFrame(res, columns=['date', 'side', 'R'])


def summary(t):
    if t.empty:
        return {}
    eq = t.R.cumsum()
    win, loss = t.R[t.R > 0], t.R[t.R <= 0]
    return dict(trades=len(t), winrate=round((t.R > 0).mean() * 100, 1),
                avgR=round(t.R.mean(), 3), PF=round(win.sum() / -loss.sum(), 2) if len(loss) else np.inf,
                totalR=round(t.R.sum(), 1), maxDD_R=round((eq.cummax() - eq).max(), 1))


if __name__ == '__main__':
    pd.set_option('display.width', 200)
    D = days(load())
    s = stats(D)
    print(f'Days: {len(s)}  ({s.date.min().date()} -> {s.date.max().date()})')
    print(f'High OR low of the day set in the IB: {s.either.mean():.1%}\n')
    z = s.groupby('zone')[['low_in_ib', 'high_in_ib']].mean().mul(100).round(1)
    z['days'] = s.zone.value_counts()
    print('Where price is at 10:30 -> % days IB low / IB high held all day\n', z, '\n')

    TPS = [0.5, 1.0, 1.5, 2.0, 3.0, ('ext', 0.5), ('ext', 1.0), 'close']
    rows = []
    for sm in ['ib', 'third']:
        for tp in TPS:
            t = run(D, sm, tp)
            rows.append(dict(stop=sm, tp=str(tp), **summary(t)))
    print('All trades (long top + short bottom), costs included:\n', pd.DataFrame(rows), '\n')

    for zone in ['top', 'bottom']:
        rows = [dict(tp=str(tp), **summary(run(D, 'ib', tp, (zone,)))) for tp in TPS]
        print(f'Only {zone} ({"long" if zone=="top" else "short"}), stop at IB edge:\n', pd.DataFrame(rows), '\n')

    # stability: split halves + per year for one reference config
    for name, (a, b) in {'2005-2012': ('2005', '2012-12-31'), '2013-2020': ('2013', '2020-12-31')}.items():
        sub = [x for x in D if pd.Timestamp(a) <= x['date'] <= pd.Timestamp(b)]
        rows = [dict(tp=str(tp), **summary(run(sub, 'ib', tp))) for tp in TPS]
        print(f'{name}, stop at IB edge:\n', pd.DataFrame(rows), '\n')
    t = run(D, 'ib', 'close')
    print('Per year, stop IB edge, exit at close:\n',
          t.groupby(t.date.dt.year).R.agg(['count', 'sum', lambda r: (r > 0).mean()]).round(2))
