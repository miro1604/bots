# -*- coding: utf-8 -*-
"""quant_math.py — vedonlyönnin kvantitatiiviset core-funktiot.

Sisältö:
  - devig_shin(odds_list)        — Shin (1992) iteratiivinen z-parametri-metodi
  - devig_power(odds_list)       — Power-metodi, ratkaisee k iteratiivisesti
  - devig_basic(odds_list)       — Multiplicative (jaollinen) — vertailubaseline
  - calculate_fractional_kelly() — Kelly + fraktio + bankroll → € panos
  - stealth_stake_rounding()     — pyöristys lähimpään 5/10 € (gubbing-suoja)

Kirjastot: numpy, scipy.optimize, mahdollisuus käyttää 'shin'-pip-pakettia kun saatavilla.
"""
from __future__ import annotations

import math
import sys
from typing import Sequence

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np

try:
    import shin as shin_pkg
    _HAS_SHIN_PKG = True
except Exception:
    _HAS_SHIN_PKG = False

try:
    from scipy.optimize import brentq
    _HAS_SCIPY = True
except Exception:
    _HAS_SCIPY = False


# ---------------------------------------------------------------------------
# Devigging (poistetaan välittäjän kate)
# ---------------------------------------------------------------------------

def _to_implied(odds_list: Sequence[float]) -> np.ndarray:
    arr = np.asarray([1.0 / o for o in odds_list], dtype=float)
    if (arr <= 0).any():
        raise ValueError("Kertoimet täytyy olla positiivisia ja > 1.0")
    return arr


def devig_basic(odds_list: Sequence[float]) -> list[float]:
    """Multiplicative / "jakautuva" devig — vertailupohja, EI suositeltu lopulliseen.

    p_i_true = (1/odds_i) / sum(1/odds_j)
    """
    impl = _to_implied(odds_list)
    s = impl.sum()
    return list(impl / s)


def devig_shin(odds_list: Sequence[float], max_iter: int = 200,
               tol: float = 1e-9) -> list[float]:
    """Shin (1992, 1993) — iteratiivinen z-parametri-metodi.

    Ratkaise z ∈ (0, 1) joka kuvaa "informoitujen vetäjien" osuuden, ja
    palauta puhdistetut todennäköisyydet:
        p_i = (sqrt(z² + 4*(1-z) * π_i² / Π) - z) / (2*(1-z))
    missä π_i = 1/odds_i ja Π = sum(π_i).

    Käytä `shin`-pakettia jos asennettuna; fallback puhtaaseen NumPy-iteraatioon.
    """
    if _HAS_SHIN_PKG:
        try:
            # shin-pkg odottaa raw odds list → palauttaa dict {idx: prob}
            result = shin_pkg.calculate_implied_probabilities(list(odds_list))
            if isinstance(result, dict):
                return [result[i] for i in range(len(odds_list))]
            return list(result)
        except Exception:
            pass  # fall through manual implementation

    pi = _to_implied(odds_list)
    Pi = pi.sum()
    if Pi <= 1.0:
        # Ei katetta — palauta sellaisenaan
        return list(pi)

    # Shin z ratkaistaan kun: sum(p_i) = 1
    # p_i(z) = (sqrt(z² + 4*(1-z)*π_i²/Π) - z) / (2*(1-z))
    def sum_p(z: float) -> float:
        if z <= 0:
            return Pi  # devig_basic ei muuta summaa edelle
        if z >= 1:
            return float("inf")
        s = 0.0
        for p in pi:
            inside = z * z + 4.0 * (1.0 - z) * p * p / Pi
            s += (math.sqrt(inside) - z) / (2.0 * (1.0 - z))
        return s

    # Bisection-haku z ∈ (0, 0.999)
    lo, hi = 0.0, 0.999
    if _HAS_SCIPY:
        try:
            z = brentq(lambda z: sum_p(z) - 1.0, 1e-9, 0.999, xtol=tol, maxiter=max_iter)
        except Exception:
            z = _bisection(lambda z: sum_p(z) - 1.0, lo + 1e-9, hi, tol, max_iter)
    else:
        z = _bisection(lambda z: sum_p(z) - 1.0, lo + 1e-9, hi, tol, max_iter)

    out = []
    for p in pi:
        inside = z * z + 4.0 * (1.0 - z) * p * p / Pi
        out.append((math.sqrt(inside) - z) / (2.0 * (1.0 - z)))
    return out


def devig_power(odds_list: Sequence[float], max_iter: int = 200,
                tol: float = 1e-9) -> list[float]:
    """Power-metodi — ratkaise k jossa sum(π_i^k) = 1.

    Tulos: p_i = π_i^k.
    """
    pi = _to_implied(odds_list)

    def sum_pk(k: float) -> float:
        return float(np.sum(pi ** k))

    # Pi > 1 → tarvitaan k > 1; Pi < 1 → k < 1; Pi == 1 → k = 1
    if abs(sum_pk(1.0) - 1.0) < tol:
        return list(pi)

    if _HAS_SCIPY:
        try:
            k = brentq(lambda k: sum_pk(k) - 1.0, 0.1, 5.0, xtol=tol, maxiter=max_iter)
        except Exception:
            k = _bisection(lambda k: sum_pk(k) - 1.0, 0.1, 5.0, tol, max_iter)
    else:
        k = _bisection(lambda k: sum_pk(k) - 1.0, 0.1, 5.0, tol, max_iter)

    return list(pi ** k)


def _bisection(f, lo: float, hi: float, tol: float, max_iter: int) -> float:
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        # Ei merkkiä → palauta hi parhaana arviona
        return hi
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        fm = f(mid)
        if abs(fm) < tol:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# Kelly + stealth-pyöristys
# ---------------------------------------------------------------------------

def calculate_fractional_kelly(true_prob: float, soft_odds: float,
                                fraction: float = 0.25, bankroll: float = 1000.0) -> float:
    """Optimaalinen Kelly = ((bp - q) / b) missä b = soft_odds - 1, p = true_prob, q = 1-p.

    Skaalataan fractional-osuudella varianssin hallintaan ja kerrotaan bankrollilla.
    Palauttaa ehdotetun panoksen euroissa (negatiivinen = ei pelata).
    """
    if soft_odds <= 1.0:
        return 0.0
    if true_prob <= 0.0 or true_prob >= 1.0:
        return 0.0
    b = soft_odds - 1.0
    p = true_prob
    q = 1.0 - p
    kelly = (b * p - q) / b
    if kelly <= 0:
        return 0.0
    fk = kelly * fraction
    stake = fk * bankroll
    return float(stake)


def stealth_stake_rounding(raw_stake: float, base: float = 5.0) -> float:
    """Pyöristä panos lähimpään tasalukuun (oletus 5 €) — viihdepelaajan profiilin matkinta.

    Jos panos < base/2 → 0 (ei pelata).
    Suuremmat panokset (>= 50 €) pyöristetään lähimpään 10 €.
    """
    if raw_stake <= 0:
        return 0.0
    if raw_stake < base / 2.0:
        return 0.0
    if raw_stake >= 50.0:
        base = 10.0
    return float(round(raw_stake / base) * base)


# ---------------------------------------------------------------------------
# Convenience: edge-laskuri
# ---------------------------------------------------------------------------

def calculate_edge(true_prob: float, soft_odds: float) -> float:
    """Edge % = (true_prob * soft_odds) - 1, palauta prosenttiyksikköinä."""
    return float((true_prob * soft_odds - 1.0) * 100.0)


if __name__ == "__main__":
    # Smoke-testi: Pinnacle-tyyppinen 1X2 pelaa
    pinnacle = [2.10, 3.50, 3.80]  # vig-katteinen
    print("Multiplicative:", devig_basic(pinnacle))
    print("Shin (z-iter):", devig_shin(pinnacle))
    print("Power (k-iter):", devig_power(pinnacle))

    # Soft-bookmakerin parempi kerroin
    true_p_home = devig_shin(pinnacle)[0]
    soft = 2.30  # parempi soft kerroin
    edge = calculate_edge(true_p_home, soft)
    print(f"Edge: {edge:.2f}% (true={true_p_home:.4f}, soft={soft})")

    if 0 < edge < 10.0:  # sääntö <10%
        stake = calculate_fractional_kelly(true_p_home, soft, fraction=0.25, bankroll=1000)
        rounded = stealth_stake_rounding(stake)
        print(f"Kelly stake: {stake:.2f}€ → stealth-rounded: {rounded}€")
