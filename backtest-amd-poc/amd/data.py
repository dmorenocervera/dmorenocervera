"""Carga de datos: descarga de Dukascopy (velas 1m), CSV genéricos/TradingView y resampleo."""
from __future__ import annotations

import lzma
import struct
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

DUKA_URL = "https://datafeed.dukascopy.com/datafeed/{sym}/{y:04d}/{m:02d}/{d:02d}/BID_candles_min_1.bi5"

# Rango de precios esperado para autodetectar el divisor de Dukascopy
EXPECTED_PRICE = {
    "XAUUSD": (200, 20_000),
    "USATECHIDXUSD": (1_000, 100_000),  # Nasdaq 100 CFD
    "USA500IDXUSD": (500, 20_000),
}


def _fetch_day(sym: str, day: date, cache: Path, session) -> np.ndarray | None:
    f = cache / sym / f"{day:%Y%m%d}.bi5"
    if not f.exists():
        url = DUKA_URL.format(sym=sym, y=day.year, m=day.month - 1, d=day.day)  # mes 0-indexado
        r = session.get(url, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(r.content)
    raw = f.read_bytes()
    if not raw:
        return None
    data = lzma.decompress(raw)
    n = len(data) // 24
    rec = np.array(struct.unpack(">" + "iiiiif" * n, data[: n * 24]), dtype=np.float64).reshape(n, 6)
    base = pd.Timestamp(day).value // 10**9
    rec[:, 0] += base
    return rec


def _infer_fields(rec: np.ndarray) -> tuple[int, int]:
    """Detecta qué columnas son open y close (open[t] ~= close[t-1])."""
    best, pair = -1.0, (1, 2)
    for a in range(1, 5):
        for b in range(1, 5):
            if a == b:
                continue
            score = np.mean(np.isclose(rec[1:, a], rec[:-1, b]))
            if score > best:
                best, pair = score, (a, b)
    return pair


def download_dukascopy(sym: str, start: str, end: str, cache_dir: str = "data/cache", workers: int = 8) -> pd.DataFrame:
    import requests

    cache = Path(cache_dir)
    d0, d1 = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    days = [d0 + timedelta(i) for i in range((d1 - d0).days + 1)]
    session = requests.Session()
    with ThreadPoolExecutor(workers) as ex:
        parts = list(ex.map(lambda d: _fetch_day(sym, d, cache, session), days))
    parts = [p for p in parts if p is not None and len(p)]
    if not parts:
        raise RuntimeError(f"Sin datos para {sym} entre {start} y {end}")
    rec = np.vstack(parts)
    oi, ci = _infer_fields(rec)
    px = rec[:, 1:5]
    df = pd.DataFrame(
        {
            "open": rec[:, oi],
            "high": px.max(axis=1),
            "low": px.min(axis=1),
            "close": rec[:, ci],
            "volume": rec[:, 5],
        },
        index=pd.to_datetime(rec[:, 0], unit="s", utc=True),
    )
    # Divisor de precio
    lo, hi = EXPECTED_PRICE.get(sym, (0, np.inf))
    med = df["close"].median()
    for div in (1, 10, 100, 1_000, 10_000, 100_000):
        if lo <= med / div <= hi:
            df[["open", "high", "low", "close"]] /= div
            break
    return clean(df)


def load_csv(path: str) -> pd.DataFrame:
    """CSV con columnas time/date, open, high, low, close, volume (export de TradingView, MT5, etc.)."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower().lstrip("<").rstrip(">") for c in df.columns]
    tcol = next(c for c in df.columns if c in ("time", "datetime", "date", "timestamp", "gmt time", "local time"))
    t = df[tcol]
    if pd.api.types.is_numeric_dtype(t):
        idx = pd.to_datetime(t, unit="s" if t.iloc[0] < 1e11 else "ms", utc=True)
    else:
        idx = pd.to_datetime(t, utc=True, dayfirst=False, format="mixed")
    vcol = next((c for c in df.columns if c in ("volume", "vol", "tickvol", "tick_volume")), None)
    out = pd.DataFrame(
        {
            "open": df["open"].astype(float).values,
            "high": df["high"].astype(float).values,
            "low": df["low"].astype(float).values,
            "close": df["close"].astype(float).values,
            "volume": df[vcol].astype(float).values if vcol else 1.0,
        },
        index=idx,
    )
    return clean(out)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df[~df.index.duplicated()].sort_index()
    # minutos de mercado cerrado (precio plano y sin volumen)
    flat = (df["high"] == df["low"]) & (df["volume"] <= 0)
    df = df[~flat]
    df.index.name = "time"
    return df


def resample(df1m: pd.DataFrame, tf: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df1m.resample(tf, label="left", closed="left").agg(agg).dropna(subset=["open"])
    return out
