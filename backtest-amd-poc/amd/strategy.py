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
    pivot_len: int = 2              # un máximo/mínimo es un pivote de N velas a cada lado
    range_min_touches: int = 2      # máximos en la zona alta Y mínimos en la zona baja
    range_touch_zone: float = 0.2   # zona de toque: fracción de la altura del rango desde cada borde
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
    # Modo de entrada:
    #   close -> a mercado, apertura de la vela siguiente a la vela de reacción
    #   break -> orden stop en el máximo de la vela de reacción, válida break_bars velas
    #   limit -> orden limitada en el PoC en el primer toque tras recuperarlo (sin esperar reacción)
    entry_mode: str = "close"
    break_bars: int = 3
    sl_limit_atr: float = 1.0       # SL del modo limit: PoC - x*ATR
    sl_buffer_atr: float = 0.05
    min_risk_atr: float = 0.10      # descarta SL ridículamente pequeños
    # Trailing stops (variantes de salida E y F)
    trail_atr: float = 2.5          # E: stop = máximo alcanzado - x*ATR
    trail_start_R: float = 1.0      # el trailing se activa cuando el precio llega a +x R
    target_lookback: int = 40       # G: velas antes del rango donde se buscan niveles de estructura
    tp_front_atr: float = 0.0       # G: adelantar el TP x*ATR antes del nivel
    tl_buffer_atr: float = 0.1      # H/I: el cierre debe quedar x*ATR por debajo de la directriz
    tl_wait_pivot: bool = True      # H/I: la directriz sólo cuenta tras un nuevo mínimo más alto después de entrar
    poc_bins: int = 50
    sides: tuple = ("long",)            # ("long", "short") para incluir cortos
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
    few_touches: int = 0
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


def pivots(H, L, k):
    """Pivotes (fractales): máximo >= las k velas anteriores y > las k posteriores (mínimos al revés)."""
    n = len(H)
    ph = np.zeros(n, bool)
    pl = np.zeros(n, bool)
    for q in range(k, n - k):
        ph[q] = H[q] >= H[q - k : q].max() and H[q] > H[q + 1 : q + k + 1].max()
        pl[q] = L[q] <= L[q - k : q].min() and L[q] < L[q + 1 : q + k + 1].min()
    return ph, pl


def _struct_levels(H, L, ph, pl, s, rh, p):
    """Niveles de estructura previos al rango (máximos y mínimos de pivote) por encima del rango, de menor a mayor."""
    lv = []
    for q in range(max(0, s - p.target_lookback), s - p.pivot_len):
        if ph[q] and H[q] > rh:
            lv.append(H[q])
        if pl[q] and L[q] > rh:
            lv.append(L[q])
    return sorted(lv)


def _detect_long(O, H, L, C, V1, H1, L1, t_tf, t_1m, ny_min, p: Params, funnel: Funnel):
    """Devuelve lista de setups (en 'espacio largo')."""
    n = len(C)
    A = atr(H, L, C, p.atr_len)
    W = p.range_min_bars
    roll_hi = pd.Series(H).rolling(W).max().to_numpy()
    roll_lo = pd.Series(L).rolling(W).min().to_numpy()
    imp_hi = pd.Series(H).rolling(p.impulse_bars).max().shift(W).to_numpy()  # máximo antes del rango
    ph, pl = pivots(H, L, p.pivot_len)

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
        # Estructura de rango: >= N máximos en la zona alta y >= N mínimos en la zona baja,
        # sólo pivotes ya confirmados antes de la ruptura (q + pivot_len <= e)
        q1 = e - p.pivot_len + 1
        zone = p.range_touch_zone * height
        n_hi = int(np.count_nonzero(ph[s:q1] & (H[s:q1] >= rh - zone))) if q1 > s else 0
        n_lo = int(np.count_nonzero(pl[s:q1] & (L[s:q1] <= rl + zone))) if q1 > s else 0
        if n_hi < p.range_min_touches or n_lo < p.range_min_touches:
            funnel.few_touches += 1
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
        limit = p.entry_mode == "limit"
        m, touches, first_touch, entry_bar, fail = reclaimed + 1, 0, None, None, "timeout"
        while m < n - 1 and m - reclaimed <= p.retest_max_bars:
            am = A[m - 1] if limit else A[m]  # en limit la orden se coloca antes de que cierre la vela
            if top > rh + height and H[m] >= top:  # llega al objetivo sin retestear: se pierde el trade
                fail = "runaway"
                break
            if limit and L[m] <= pc + p.touch_tol_atr * am:
                first_touch = entry_bar = m
                break
            if C[m] < pc - p.inval_close_atr * am:
                fail = "invalid"
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

        a_e = A[entry_bar - 1] if limit else A[entry_bar]
        if limit:
            level = pc + p.touch_tol_atr * a_e
            sl = pc - p.sl_limit_atr * a_e
            e_from, e_until = entry_bar, entry_bar + 1
            ref = level
        else:
            sl = L[first_touch : entry_bar + 1].min() - p.sl_buffer_atr * a_e
            if p.entry_mode == "break":
                level = H[entry_bar]
                e_from, e_until = entry_bar + 1, min(entry_bar + 1 + p.break_bars, n)
                ref = level
            else:
                level = np.nan
                e_from, e_until = entry_bar + 1, entry_bar + 2
                ref = C[entry_bar]
        if ref - sl < p.min_risk_atr * a_e:
            sl = ref - p.min_risk_atr * a_e  # SL mínimo para no tener riesgos absurdos
        setups.append(
            dict(
                range_start=s, range_end=e, sweep_bar=j, sweep_low=low_min, reclaim_bar=reclaimed,
                first_touch=first_touch, signal_bar=entry_bar, range_high=rh, range_low=rl, poc=pc,
                impulse_top=top, sl=sl, atr=a_e, entry_type=p.entry_mode,
                struct_levels=_struct_levels(H, L, ph, pl, s, rh, p), entry_level=level,
                entry_from=t_tf[e_from], entry_until=t_tf[e_until],
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
            for k in ("entry_from", "entry_until"):
                st[k] = pd.Timestamp(int(st[k]), tz="UTC") if st[k] < np.iinfo(np.int64).max else pd.Timestamp.max.tz_localize("UTC")
            for k in ("range_high", "range_low", "poc", "impulse_top", "sl", "sweep_low", "entry_level"):
                st[k] = sg * st[k]
            st["struct_levels"] = [sg * x for x in st["struct_levels"]]
            if sg < 0:
                st["range_high"], st["range_low"] = st["range_low"], st["range_high"]
            out.append(st)
        funnels[side] = f
    return out, funnels
