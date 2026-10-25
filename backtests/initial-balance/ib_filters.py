"""
Which extra filters improve the Initial Balance direction bias?

Signal day: price at 10:30 in the top third (d=+1) or bottom third (d=-1) of the IB.
"Hit" = the opposite IB extreme holds for the rest of the session
        (top third -> IB low never broken; bottom third -> IB high never broken).
Every filter is aligned with d, e.g. "trend with" = top day in an uptrend or bottom day in a downtrend.
For each filter we report the hit rate and the avg R of the pullback strategy
(15-min candles, stop at IB mid, exit at the close, 0.25 pt cost) on the days that pass,
split 2005-2012 / 2013-2020.
"""
import glob, sys
import numpy as np, pandas as pd
import ib_backtest, ib_pullback as pb

DATA = ib_backtest.DATA


def load_all():
    files = sorted(glob.glob(f'{DATA}/*/*.csv'))
    df = pd.concat(pd.read_csv(f, parse_dates=['time']) for f in files)
    df = df.drop_duplicates('time').set_index('time').sort_index()
    df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')
    return df


def fvgs(h, l, c):
    """Fair value gaps and inversions on a candle series. Returns counts (bull, bear, bull_ifvg, bear_ifvg)."""
    bull, bear, bi, be = 0, 0, 0, 0
    for i in range(2, len(h)):
        if l[i] > h[i - 2]:                     # bullish FVG zone (h[i-2], l[i])
            bull += 1
            if (c[i + 1:] < h[i - 2]).any():    # closed below it -> bearish IFVG
                be += 1
        if h[i] < l[i - 2]:                     # bearish FVG zone (h[i], l[i-2])
            bear += 1
            if (c[i + 1:] > l[i - 2]).any():    # closed above it -> bullish IFVG
                bi += 1
    return bull, bear, bi, be


def daily_table(df):
    t = df.index.hour * 60 + df.index.minute
    rth = df[(t >= 570) & (t < 960)].copy()
    rth['date'] = rth.index.date
    rows, prev_close_ts = [], None
    for d, g in rth.groupby('date'):
        if len(g) < 360:
            prev_close_ts = None
            continue
        gm = g.index.hour * 60 + g.index.minute
        ib, rest = g[gm < 630], g[gm >= 630]
        if len(ib) < 55:
            continue
        on = df[(df.index > prev_close_ts) & (df.index < g.index[0])] if prev_close_ts is not None else df.iloc[0:0]
        b5 = ib.resample('5min').agg({'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
        bull, bear, bi, be = fvgs(b5.high.values, b5.low.values, b5.close.values)
        rows.append(dict(date=pd.Timestamp(d), open=g.open.iloc[0], high=g.high.max(), low=g.low.min(),
                         close=g.close.iloc[-1], ibh=ib.high.max(), ibl=ib.low.min(), px=ib.close.iloc[-1],
                         ib_vol=ib.volume.sum(), rest_hi=rest.high.max(), rest_lo=rest.low.min(),
                         on_h=on.high.max() if len(on) > 30 else np.nan, on_l=on.low.min() if len(on) > 30 else np.nan,
                         fvg_bull=bull, fvg_bear=bear, ifvg_bull=bi, ifvg_bear=be))
        prev_close_ts = g.index[-1]
    return pd.DataFrame(rows).set_index('date')


def features(T):
    T = T.copy()
    rng = T.ibh - T.ibl
    T['pos'] = (T.px - T.ibl) / rng
    T['d'] = np.where(T.pos > 2/3, 1, np.where(T.pos < 1/3, -1, 0))
    T['hit'] = np.where(T.d == 1, T.rest_lo >= T.ibl, T.rest_hi <= T.ibh)
    d = T.d
    pc = T.close.shift(1)
    sma20 = T.close.shift(1).rolling(20).mean()
    wk = T.index.to_period('W')
    W = T.groupby(wk).agg(wh=('high', 'max'), wl=('low', 'min'))
    W.index = W.index + 1                                       # previous week's levels
    T['pwh'] = W.wh.reindex(wk).values; T['pwl'] = W.wl.reindex(wk).values
    T['pdh'] = T.high.shift(1); T['pdl'] = T.low.shift(1)
    rel_rng = rng / rng.shift(1).rolling(20).mean()
    rel_vol = T.ib_vol / T.ib_vol.shift(1).rolling(20).mean()
    gap = (T.open - pc) / rng
    f = {}
    f['trend with (close vs SMA20)'] = np.sign(pc - sma20) == d
    f['trend against'] = np.sign(pc - sma20) == -d
    f['prev day same color'] = np.sign(T.close.shift(1) - T.open.shift(1)) == d
    f['gap with (open vs prev close)'] = np.sign(gap) == d
    f['gap against'] = np.sign(gap) == -d
    f['big gap with (>0.5 IB)'] = gap * d > 0.5
    f['10:30 beyond prev-day extreme'] = np.where(d == 1, T.px > T.pdh, T.px < T.pdl)
    f['10:30 inside prev-day range'] = (T.px < T.pdh) & (T.px > T.pdl)
    f['10:30 beyond prev-week extreme'] = np.where(d == 1, T.px > T.pwh, T.px < T.pwl)
    f['prev-week extreme within 1 IB ahead'] = np.where(d == 1, (T.pwh > T.px) & (T.pwh - T.px < rng),
                                                         (T.pwl < T.px) & (T.px - T.pwl < rng))
    f['10:30 beyond overnight extreme'] = np.where(d == 1, T.px > T.on_h, T.px < T.on_l)
    f['IB inside overnight range'] = (T.ibh <= T.on_h) & (T.ibl >= T.on_l)
    f['narrow IB (<0.7x avg)'] = rel_rng < 0.7
    f['wide IB (>1.3x avg)'] = rel_rng > 1.3
    f['high IB volume (>1.2x avg)'] = rel_vol > 1.2
    f['low IB volume (<0.8x avg)'] = rel_vol < 0.8
    f['open at opposite IB extreme (open drive)'] = np.where(d == 1, (T.open - T.ibl) / rng < 0.2, (T.ibh - T.open) / rng < 0.2)
    f['FVG with (5m, in IB)'] = np.where(d == 1, T.fvg_bull > 0, T.fvg_bear > 0)
    f['no FVG against'] = np.where(d == 1, T.fvg_bear == 0, T.fvg_bull == 0)
    f['IFVG with (5m, in IB)'] = np.where(d == 1, T.ifvg_bull > 0, T.ifvg_bear > 0)
    f['IFVG against'] = np.where(d == 1, T.ifvg_bear > 0, T.ifvg_bull > 0)
    for name in ['Mon', 'Fri']:
        f[f'day = {name}'] = T.index.day_name().str[:3] == name
    return T, {k: pd.Series(np.asarray(v, dtype=bool), index=T.index) for k, v in f.items()}


def row(name, S, R):
    iso = S.index < '2013'
    r = R.reindex(S.index).dropna()
    return dict(filter=name, days=len(S), share=round(len(S) / NBASE * 100), hit=round(S.hit.mean() * 100, 1),
                hit_IS=round(S[iso].hit.mean() * 100, 1), hit_OOS=round(S[~iso].hit.mean() * 100, 1),
                trades=len(r), avgR=round(r.mean(), 3) if len(r) else np.nan,
                R_IS=round(r[r.index < '2013'].mean(), 3) if len(r) else np.nan,
                R_OOS=round(r[r.index >= '2013'].mean(), 3) if len(r) else np.nan)


if __name__ == '__main__':
    pd.set_option('display.width', 220)
    T, F = features(daily_table(load_all()))
    pb.COST_PTS = 0.25
    t = pb.run(pb.prepare(ib_backtest.load()), 15, 'mid', 0.0, True, 'close')
    R = t.set_index('date').R
    sig = T[T.d != 0].dropna(subset=['pwh'])
    NBASE = len(sig)
    out = [row('BASE (all signal days)', sig, R)]
    out += [row(k, sig[v.reindex(sig.index)], R) for k, v in F.items()]
    res = pd.DataFrame(out)
    res['hit_delta'] = (res.hit - res.hit.iloc[0]).round(1)
    print(res.to_string(index=False))
    res.to_csv('filters.csv', index=False)
    T.to_csv('daily_features.csv')
