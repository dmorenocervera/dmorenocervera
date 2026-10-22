"""Detector del setup AMD + PoC (Acumulación -> Manipulación -> retesteo del PoC).

Un único recorrido evalúa las dos direcciones: la dirección la decide qué lado del rango
acaba siendo la manipulación confirmada. Los pinchazos que no se confirman no cierran el
rango: se absorben y el rango se amplía (rango elástico).

La fase de retesteo se escribe para LARGOS y los cortos usan el precio invertido (p -> -p).
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
    reclaim_max_bars: int = 30      # velas máximas desde el barrido hasta confirmar
    # Confirmación de la manipulación:
    #   poc      -> cierre al otro lado del PoC
    #   opuesto  -> cierre fuera del rango por el lado contrario al barrido
    confirm_mode: str = "opuesto"
    confirm_buffer_atr: float = 0.1  # en modo opuesto, el cierre debe superar el borde por x*ATR
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
    absorbed: int = 0
    outside_open_window: int = 0
    expired: int = 0
    swept: int = 0
    sweep_too_deep: int = 0
    confirmed: int = 0
    other_side: int = 0
    retest_invalid: int = 0
    retest_timeout: int = 0
    entries: int = 0


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
    """Niveles de estructura previos al rango (máximos y mínimos de pivote) por encima del rango, de menor a mayor.
    Arrays en espacio de la dirección (para cortos, precio invertido)."""
    lv = []
    for q in range(max(0, s - p.target_lookback), s - p.pivot_len):
        if ph[q] and H[q] > rh:
            lv.append(H[q])
        if pl[q] and L[q] > rh:
            lv.append(L[q])
    return sorted(lv)


def _retest(Od, Hd, Ld, Cd, A, pc, rh, height, top, k, n, p):
    """Fase de retesteo del PoC en espacio largo. Devuelve (entry_bar, first_touch, fail, m)."""
    limit = p.entry_mode == "limit"
    m, touches, first_touch = k + 1, 0, None
    while m < n - 1 and m - k <= p.retest_max_bars:
        am = A[m - 1] if limit else A[m]
        if top > rh + height and Hd[m] >= top:  # llega al objetivo sin retestear
            return None, None, "runaway", m
        if limit and Ld[m] <= pc + p.touch_tol_atr * am:
            return m, m, None, m
        if Cd[m] < pc - p.inval_close_atr * am:
            return None, None, "invalid", m
        if Ld[m] <= pc + p.touch_tol_atr * am:
            if first_touch is None:
                first_touch = m
            rng = Hd[m] - Ld[m]
            wick = min(Od[m], Cd[m]) - Ld[m]
            if Cd[m] > pc and rng > 0 and wick >= p.wick_frac * rng:
                touches += 1
                if touches >= p.entry_touch:
                    return m, first_touch, None, m
        m += 1
    return None, None, "timeout", m


def _detect(O, H, L, C, V1, H1, L1, t_tf, t_1m, ny_min, p: Params, f: Funnel):
    n = len(C)
    A = atr(H, L, C, p.atr_len)
    W = p.range_min_bars
    roll_hi = pd.Series(H).rolling(W).max().to_numpy()
    roll_lo = pd.Series(L).rolling(W).min().to_numpy()
    imp_hi = pd.Series(H).rolling(p.impulse_bars).max().shift(W).to_numpy()  # máximo antes del rango
    imp_lo = pd.Series(L).rolling(p.impulse_bars).min().shift(W).to_numpy()  # mínimo antes del rango
    ph, pl = pivots(H, L, p.pivot_len)
    # espacio de cada dirección: +1 largo (precio real), -1 corto (precio invertido)
    D = {1: (O, H, L, C, ph, pl), -1: (-O, -L, -H, -C, pl, ph)}
    enabled = {1: "long" in p.sides, -1: "short" in p.sides}
    max_bars = max(p.range_max_bars, 400) if p.ny_open else p.range_max_bars
    OPEN = 9 * 60 + 30

    def range_ok(rh, rl, a, top, bot):
        height = rh - rl
        ok = 0 < height <= p.range_max_atr * a
        if p.require_impulse:
            imp = max(top - rl, rh - bot)
            ok = ok and imp >= p.impulse_atr * a and height <= p.range_max_frac * imp
        return ok

    setups = []
    i = max(p.atr_len, p.impulse_bars) + W
    while i < n - 1:
        s = i - W + 1
        a = A[s - 1]
        rh, rl, top, bot = roll_hi[i], roll_lo[i], imp_hi[i], imp_lo[i]
        if not (np.isfinite(a) and np.isfinite(top) and a > 0) or not range_ok(rh, rl, a, top, bot) or (
            p.ny_open and not (4 * 60 <= ny_min[i] < OPEN)
        ):
            i += 1
            continue
        f.ranges += 1
        e, j, nxt = i, i + 1, None
        while nxt is None:
            if j >= n:
                nxt = n
                break
            if j - s + 1 > max_bars:
                f.expired += 1
                nxt = j
                break
            if L[j] >= rl and H[j] <= rh:
                e, j = j, j + 1
                continue
            up, dn = H[j] > rh, L[j] < rl
            if up and dn:  # vela exterior: el rango deja de ser válido
                f.expired += 1
                nxt = j + 1
                break
            d = 1 if dn else -1  # barrido por abajo -> largo; por arriba -> corto
            height = rh - rl
            # ¿puede esta excursión ser la manipulación?
            can = True
            if p.ny_open:
                if ny_min[j] >= OPEN + p.open_window_min or ny_min[j] < 4 * 60:  # pasada la ventana de apertura
                    f.outside_open_window += 1
                    nxt = j + 1
                    break
                if ny_min[j] < OPEN:
                    can = False
            # el rango tiene que estar establecido (N máximos arriba y N mínimos abajo) antes de la excursión;
            # si no lo está, no hay rango que absorba nada: se reinicia la búsqueda
            q1 = j - p.pivot_len
            zone = p.range_touch_zone * height
            n_hi = int(np.count_nonzero(ph[s:q1] & (H[s:q1] >= rh - zone))) if q1 > s else 0
            n_lo = int(np.count_nonzero(pl[s:q1] & (L[s:q1] <= rl + zone))) if q1 > s else 0
            if n_hi < p.range_min_touches or n_lo < p.range_min_touches:
                f.few_touches += 1
                nxt = j
                break
            pc = None
            if can:
                m0 = np.searchsorted(t_1m, t_tf[s], "left")
                m1 = np.searchsorted(t_1m, t_tf[e + 1], "left")
                if m1 - m0 < 2:
                    can = False
                else:
                    pc = compute_poc(H1[m0:m1], L1[m0:m1], V1[m0:m1], rl, rh, p.poc_bins)
                    f.swept += 1
            # seguir la excursión
            k, ext, outcome = j, None, None
            while k < n and k - j <= p.reclaim_max_bars:
                if d == 1:
                    ext = L[k] if ext is None else min(ext, L[k])
                else:
                    ext = H[k] if ext is None else max(ext, H[k])
                depth = (rl - ext) if d == 1 else (ext - rh)
                if depth > p.sweep_max * height:
                    outcome = "deep"
                    break
                if can:
                    if p.confirm_mode == "poc":
                        conf = C[k] > pc if d == 1 else C[k] < pc
                    else:
                        cb = p.confirm_buffer_atr * a
                        conf = C[k] > rh + cb if d == 1 else C[k] < rl - cb
                    if conf:
                        outcome = "confirmed" if depth >= p.sweep_min * height else "shallow"
                        break
                elif rl <= C[k] <= rh:
                    outcome = "back"
                    break
                if k > j and depth < p.sweep_min * height and (H[k] > rh if d == 1 else L[k] < rl):
                    outcome = "flip"  # excursión insuficiente que acaba pinchando el lado contrario: manda la nueva
                    break
                k += 1
            if outcome == "deep":
                f.sweep_too_deep += 1
                nxt = k + 1
                break
            if outcome == "confirmed":
                f.confirmed += 1
                if not enabled[d]:
                    f.other_side += 1
                    nxt = k + 1
                    break
                Od, Hd, Ld, Cd, phd, pld = D[d]
                pcd = d * pc
                rhd, rld = (rh, rl) if d == 1 else (-rl, -rh)
                topd = top if d == 1 else -bot
                eb, ft, fail, m = _retest(Od, Hd, Ld, Cd, A, pcd, rhd, height, topd, k, n, p)
                if eb is None:
                    if fail == "invalid":
                        f.retest_invalid += 1
                    else:
                        f.retest_timeout += 1
                    nxt = m + 1
                    break
                limit = p.entry_mode == "limit"
                a_e = A[eb - 1] if limit else A[eb]
                if limit:
                    level = pcd + p.touch_tol_atr * a_e
                    sl = pcd - p.sl_limit_atr * a_e
                    e_from, e_until, ref = eb, eb + 1, level
                else:
                    sl = Ld[ft : eb + 1].min() - p.sl_buffer_atr * a_e
                    if p.entry_mode == "break":
                        level = Hd[eb]
                        e_from, e_until, ref = eb + 1, min(eb + 1 + p.break_bars, n), level
                    else:
                        level = np.nan
                        e_from, e_until, ref = eb + 1, eb + 2, Cd[eb]
                if ref - sl < p.min_risk_atr * a_e:
                    sl = ref - p.min_risk_atr * a_e
                levels = _struct_levels(Hd, Ld, phd, pld, s, rhd, p)
                setups.append(dict(
                    side="long" if d == 1 else "short", range_start=s, range_end=e, sweep_bar=j,
                    sweep_low=ext, reclaim_bar=k, first_touch=ft, signal_bar=eb, range_high=rh, range_low=rl,
                    poc=pc, impulse_top=top if d == 1 else bot, sl=d * sl, atr=a_e, entry_type=p.entry_mode,
                    entry_level=d * level, entry_from=t_tf[e_from], entry_until=t_tf[e_until],
                    struct_levels=sorted(d * x for x in levels),
                ))
                f.entries += 1
                nxt = eb + 1
                break
            # no confirmada (vuelve dentro, poca profundidad, se agota el tiempo o pincha el otro lado):
            # se absorbe en el rango
            if k >= n:
                nxt = n
                break
            if outcome == "flip":
                k -= 1
            rl = min(rl, L[j : k + 1].min())
            rh = max(rh, H[j : k + 1].max())
            if not range_ok(rh, rl, a, top, bot):
                f.expired += 1
                nxt = k + 1
                break
            f.absorbed += 1
            e, j = k, k + 1
        i = nxt
    return setups


def detect(df_tf: pd.DataFrame, df_1m: pd.DataFrame, p: Params):
    """Detecta setups (largos y/o cortos según p.sides). Precios devueltos en escala real."""
    t_tf = df_tf.index.values.astype("datetime64[ns]").astype(np.int64)
    t_1m = df_1m.index.values.astype("datetime64[ns]").astype(np.int64)
    t_tf = np.r_[t_tf, np.iinfo(np.int64).max]  # sentinela para la última vela
    ny = df_tf.index.tz_convert("America/New_York")
    ny_min = np.asarray(ny.hour * 60 + ny.minute)
    f = Funnel()
    out = _detect(
        df_tf["open"].to_numpy(), df_tf["high"].to_numpy(), df_tf["low"].to_numpy(), df_tf["close"].to_numpy(),
        df_1m["volume"].to_numpy(), df_1m["high"].to_numpy(), df_1m["low"].to_numpy(), t_tf, t_1m, ny_min, p, f,
    )
    for st in out:
        st["signal_time"] = df_tf.index[st["signal_bar"]]
        for k in ("entry_from", "entry_until"):
            st[k] = pd.Timestamp(int(st[k]), tz="UTC") if st[k] < np.iinfo(np.int64).max else pd.Timestamp.max.tz_localize("UTC")
    return out, {"total": f}
