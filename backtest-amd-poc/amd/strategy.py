"""Detector del setup AMD + PoC (Acumulación -> Manipulación -> retesteo del PoC).

Toda la lógica está escrita para LARGOS. Los cortos se obtienen invirtiendo el precio
(p -> -p): un barrido del BSL con retesteo del PoC como resistencia es exactamente un
barrido del SSL con retesteo como soporte en el gráfico invertido.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .profile import poc as compute_poc


@dataclass
class Params:
    tf: str = "15min"
    atr_len: int = 50
    # Contexto: impulso previo
    impulse_bars: int = 20          # ventana antes del rango donde se busca el impulso
    impulse_atr: float = 4.0        # tamaño mínimo del impulso (en ATR)
    # Acumulación
    range_min_bars: int = 12
    range_max_bars: int = 60
    range_max_frac: float = 0.4     # altura del rango <= frac * impulso
    range_max_atr: float = 5.0      # altura del rango <= X ATR
    # Manipulación (barrido del SSL)
    sweep_min: float = 0.15         # profundidad mínima, fracción de la altura del rango
    sweep_max: float = 1.5          # más profundo que esto = ruptura real, se descarta
    reclaim_max_bars: int = 30      # velas máximas desde el barrido hasta cerrar sobre el PoC
    # Retesteo del PoC
    retest_max_bars: int = 30
    touch_tol_atr: float = 0.10     # tolerancia para considerar tocado el PoC
    wick_frac: float = 0.4          # mecha inferior >= frac * rango de la vela
    inval_close_atr: float = 0.25   # cierre por debajo de PoC - x*ATR invalida
    entry_touch: int = 1            # entrar en la 1ª o 2ª reacción válida
    sl_buffer_atr: float = 0.05
    min_risk_atr: float = 0.10      # descarta SL ridículamente pequeños
    poc_bins: int = 50
    sides: tuple = ("long", "short")
    # Modo apertura de Nueva York: el rango llega intacto a las 9:30 ET y la
    # manipulación ocurre dentro de la primera ventana tras la apertura.
    ny_open: bool = False
    open_window_min: int = 60
    require_impulse: bool = True

    def to_dict(self):
        return asdict(self)


@dataclass
class Funnel:
    ranges: int = 0
    outside_open_window: int = 0
    broke_wrong_side: int = 0
    swept: int = 0
    sweep_too_deep: int = 0
    sweep_too_shallow: int = 0
    no_reclaim: int = 0
    reclaimed: int = 0
    retest_invalid: int = 0
    retest_timeout: int = 0
    entries: int = 0

    def add(self, other: "Funnel"):
        for k, v in asdict(other).items():
            setattr(self, k, getattr(self, k) + v)


def atr(h, l, c, n):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(n, min_periods=n).mean().to_numpy()


def _detect_long(O, H, L, C, V1, H1, L1, t_tf, t_1m, ny_min, p: Params, funnel: Funnel):
    """Devuelve lista de setups (en 'espacio largo')."""
    n = len(C)
    A = atr(H, L, C, p.atr_len)
    W = p.range_min_bars
    roll_hi = pd.Series(H).rolling(W).max().to_numpy()
    roll_lo = pd.Series(L).rolling(W).min().to_numpy()
    imp_hi = pd.Series(H).rolling(p.impulse_bars).max().shift(W).to_numpy()  # máximo antes del rango

    setups = []
    i = max(p.atr_len, p.impulse_bars) + W
    while i < n - 1:
        s = i - W + 1
        a = A[s - 1]
        rh, rl = roll_hi[i], roll_lo[i]
        top = imp_hi[i]
        if not (np.isfinite(a) and np.isfinite(top) and a > 0):
            i += 1
            continue
        height = rh - rl
        imp = top - rl
        ok = height > 0 and height <= p.range_max_atr * a
        if p.require_impulse:
            ok = ok and imp >= p.impulse_atr * a and height <= p.range_max_frac * imp
        if p.ny_open:
            ok = ok and ny_min[i] < 9 * 60 + 30 and ny_min[i] >= 4 * 60  # rango formado antes de la apertura
        if not ok:
            i += 1
            continue

        # Rango válido: extender mientras el precio siga dentro
        funnel.ranges += 1
        e, j = i, i + 1
        while j < n and (j - s + 1) <= p.range_max_bars and L[j] >= rl and H[j] <= rh:
            e, j = j, j + 1
        if j >= n:
            break
        if L[j] >= rl and H[j] <= rh:  # rango demasiado largo
            i = j
            continue
        if p.ny_open and not (9 * 60 + 30 <= ny_min[j] < 9 * 60 + 30 + p.open_window_min):
            funnel.outside_open_window += 1  # el rango se rompe fuera de la apertura
            i = j + 1
            continue
        if L[j] >= rl or H[j] > rh:  # rompe por arriba (o vela exterior): no es manipulación bajista
            funnel.broke_wrong_side += 1
            i = j + 1
            continue

        # PoC del rango con velas de 1m
        m0 = np.searchsorted(t_1m, t_tf[s], "left")
        m1 = np.searchsorted(t_1m, t_tf[e + 1], "left")
        if m1 - m0 < 2:
            i = j + 1
            continue
        pc = compute_poc(H1[m0:m1], L1[m0:m1], V1[m0:m1], rl, rh, p.poc_bins)

        # Manipulación: barrido y recuperación del PoC
        funnel.swept += 1
        low_min, k, reclaimed, fail = L[j], j, None, None
        while k < n and k - j <= p.reclaim_max_bars:
            low_min = min(low_min, L[k])
            if low_min < rl - p.sweep_max * height:
                fail = "deep"
                break
            if C[k] > pc:
                if rl - low_min >= p.sweep_min * height:
                    reclaimed = k
                else:
                    fail = "shallow"
                break
            k += 1
        if reclaimed is None:
            if fail == "deep":
                funnel.sweep_too_deep += 1
            elif fail == "shallow":
                funnel.sweep_too_shallow += 1
            else:
                funnel.no_reclaim += 1
            i = k + 1
            continue
        funnel.reclaimed += 1

        # Retesteo del PoC con reacción (mecha que perfora + cierre por encima)
        m, touches, first_touch, entry_bar, fail = reclaimed + 1, 0, None, None, "timeout"
        while m < n - 1 and m - reclaimed <= p.retest_max_bars:
            am = A[m]
            if C[m] < pc - p.inval_close_atr * am:
                fail = "invalid"
                break
            if top > rh + height and H[m] >= top:  # llega al objetivo sin retestear: se pierde el trade
                fail = "runaway"
                break
            if L[m] <= pc + p.touch_tol_atr * am:
                if first_touch is None:
                    first_touch = m
                rng = H[m] - L[m]
                wick = min(O[m], C[m]) - L[m]
                if C[m] > pc and rng > 0 and wick >= p.wick_frac * rng:
                    touches += 1
                    if touches >= p.entry_touch:
                        entry_bar = m
                        break
            m += 1
        if entry_bar is None:
            if fail == "invalid":
                funnel.retest_invalid += 1
            else:
                funnel.retest_timeout += 1
            i = m + 1
            continue

        sl = L[first_touch : entry_bar + 1].min() - p.sl_buffer_atr * A[entry_bar]
        if C[entry_bar] - sl < p.min_risk_atr * A[entry_bar]:
            sl = C[entry_bar] - p.min_risk_atr * A[entry_bar]  # SL mínimo para no tener riesgos absurdos
        setups.append(
            dict(
                range_start=s, range_end=e, sweep_bar=j, sweep_low=low_min, reclaim_bar=reclaimed,
                first_touch=first_touch, signal_bar=entry_bar, range_high=rh, range_low=rl, poc=pc,
                impulse_top=top, sl=sl, atr=A[entry_bar],
            )
        )
        funnel.entries += 1
        i = entry_bar + 1
    return setups


def detect(df_tf: pd.DataFrame, df_1m: pd.DataFrame, p: Params):
    """Detecta setups largos y cortos. Precios devueltos en escala real."""
    t_tf = df_tf.index.values.astype("datetime64[ns]").astype(np.int64)
    t_1m = df_1m.index.values.astype("datetime64[ns]").astype(np.int64)
    t_tf = np.r_[t_tf, np.iinfo(np.int64).max]  # sentinela para la última vela
    ny = df_tf.index.tz_convert("America/New_York")
    ny_min = np.asarray(ny.hour * 60 + ny.minute)
    out, funnels = [], {}
    for side in p.sides:
        sg = 1.0 if side == "long" else -1.0
        O, C = sg * df_tf["open"].to_numpy(), sg * df_tf["close"].to_numpy()
        H, L = (df_tf["high"].to_numpy(), df_tf["low"].to_numpy()) if sg > 0 else (-df_tf["low"].to_numpy(), -df_tf["high"].to_numpy())
        H1, L1 = (df_1m["high"].to_numpy(), df_1m["low"].to_numpy()) if sg > 0 else (-df_1m["low"].to_numpy(), -df_1m["high"].to_numpy())
        f = Funnel()
        for st in _detect_long(O, H, L, C, df_1m["volume"].to_numpy(), H1, L1, t_tf, t_1m, ny_min, p, f):
            st["side"] = side
            st["signal_time"] = df_tf.index[st["signal_bar"]]
            # la entrada es la apertura de la siguiente vela de la TF
            st["entry_time"] = df_tf.index[st["signal_bar"] + 1]
            for k in ("range_high", "range_low", "poc", "impulse_top", "sl", "sweep_low"):
                st[k] = sg * st[k]
            if sg < 0:
                st["range_high"], st["range_low"] = st["range_low"], st["range_high"]
            out.append(st)
        funnels[side] = f
    return out, funnels
