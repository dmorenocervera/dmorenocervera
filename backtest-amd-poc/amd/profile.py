"""Volume Profile de rango fijo y PoC."""
from __future__ import annotations

import numpy as np


def volume_profile(high: np.ndarray, low: np.ndarray, volume: np.ndarray, lo: float, hi: float, bins: int = 50):
    """Reparte el volumen de cada vela uniformemente entre su low y su high.

    Devuelve (centros_de_bin, volumen_por_bin). Si no hay volumen (CFD sin datos), usa
    tiempo (TPO): cada vela pesa 1.
    """
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.asarray(volume, dtype=float)
    if not np.isfinite(vol).all() or vol.sum() <= 0:
        vol = np.ones_like(vol)
    h = np.asarray(high, float)[:, None]
    l = np.asarray(low, float)[:, None]
    span = np.maximum(h - l, 1e-12)
    overlap = np.clip(np.minimum(h, edges[None, 1:]) - np.maximum(l, edges[None, :-1]), 0, None)
    # velas de rango cero: todo al bin que las contiene
    zero = (h - l)[:, 0] <= 1e-12
    w = overlap / span
    if zero.any():
        idx = np.clip(np.searchsorted(edges, l[zero, 0], side="right") - 1, 0, bins - 1)
        w[zero] = 0
        w[np.where(zero)[0], idx] = 1
    prof = (w * vol[:, None]).sum(axis=0)
    centers = (edges[:-1] + edges[1:]) / 2
    return centers, prof


def poc(high, low, volume, lo, hi, bins: int = 50) -> float:
    centers, prof = volume_profile(high, low, volume, lo, hi, bins)
    return float(centers[int(np.argmax(prof))])
