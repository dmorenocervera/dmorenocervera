"""Tests del motor con un patrón sintético plantado (sólo valida la lógica, no la rentabilidad)."""
import numpy as np
import pandas as pd

from amd.backtest import simulate
from amd.data import resample
from amd.profile import poc
from amd.strategy import Params, detect


def _path_1m(points, seed=0):
    """points: lista de (n_minutos, precio_destino). Devuelve serie de cierres 1m."""
    rng = np.random.default_rng(seed)
    px = [points[0][1]]
    for n, target in points[1:]:
        seg = np.linspace(px[-1], target, n + 1)[1:]
        px.extend(seg)
    px = np.array(px) + rng.normal(0, 0.02, len(px))
    return px


def _ohlc(close, start="2024-03-05 00:00"):
    idx = pd.date_range(start, periods=len(close), freq="1min", tz="UTC")
    o = np.r_[close[0], close[:-1]]
    h = np.maximum(o, close) + 0.01
    l = np.minimum(o, close) - 0.01
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": close, "volume": 1.0}, index=idx)


def _pattern(mirror=False):
    # Calentamiento: oscilación con ATR(15m) ~ 0.5
    pts = [(1, 110.0)]
    for k in range(60):
        pts.append((15, 110.3 if k % 2 else 109.7))
    pts += [(15 * 10, 100.0)]                              # impulso bajista de ~10
    for k in range(20):                                    # acumulación 99-101
        pts.append((15, 101.0 if k % 2 else 99.0))
    pts += [(15, 100.0)]
    pts += [(30, 97.8), (15, 98.2)]                        # manipulación: barre el SSL
    pts += [(45, 100.6), (30, 101.6)]                      # recupera y supera el PoC
    pts += [(8, 99.95), (7, 101.3)]                        # retesteo: mecha bajo el PoC y cierre arriba
    pts += [(15 * 20, 112.0), (15 * 10, 111.0)]            # expansión
    px = _path_1m(pts)
    if mirror:
        px = 220 - px
    return _ohlc(px)


def test_poc_is_where_volume_concentrates():
    h = np.array([10.0, 10.2, 10.1, 12.0])
    l = np.array([9.8, 9.9, 9.95, 8.0])
    v = np.array([100, 100, 100, 1])
    assert abs(poc(h, l, v, 8.0, 12.0, 40) - 10.0) < 0.2


def test_detects_long_setup_and_wins():
    df1 = _pattern()
    dft = resample(df1, "15min")
    setups, funnels = detect(dft, df1, Params(tf="15min"))
    longs = [s for s in setups if s["side"] == "long"]
    assert len(longs) >= 1, funnels
    st = longs[0]
    assert 98.5 < st["poc"] < 101.5
    assert st["sweep_low"] < st["range_low"]
    trades = simulate(longs, df1, cost=0.0)
    r = trades.set_index("variant")["R"]
    assert r["C_2R"] > 1.9


def test_mirror_short_setup():
    df1 = _pattern(mirror=True)
    dft = resample(df1, "15min")
    setups, funnels = detect(dft, df1, Params(tf="15min"))
    shorts = [s for s in setups if s["side"] == "short"]
    assert len(shorts) >= 1, funnels
    trades = simulate(shorts, df1, cost=0.0)
    assert trades.set_index("variant")["R"]["C_2R"] > 1.9


def test_sl_before_tp_same_bar_is_loss():
    from amd.backtest import _exit
    lo = np.array([99.0]); hi = np.array([110.0]); cl = np.array([105.0])
    px, _, why = _exit(lo, hi, cl, entry=100.0, sl=99.5, tp=105.0)
    assert why == "SL" and px == 99.5
