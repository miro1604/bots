# -*- coding: utf-8 -*-
r"""quant_validation.py — kvantitatiiviset validointityökalut shared-tasolla.

Käyttäjän PDF-dokumenttien (2026-04-26) pohjalta toteutetut institutionaalisen
tason backtestaus- ja arbitraasi-validointityökalut KAIKKIEN finance-pohjaisten
agenttien (finance, crypto-finance, betting) käyttöön.

Sisältö:
  CPCV  — Combinatorial Purged Cross-Validation (López de Prado)
  PBO   — Probability of Backtest Overfitting (Bailey)
  Almgren market impact (väliaikainen + pysyvä)
  Obizhaeva-Wang shock+decay
  Adversarial-injektorit (slippage, reject, signal-inversion)
  Net EV -laskuri arbitraasille (fees + slippage + gas)
"""
from __future__ import annotations

import math
import random
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from dataclasses import dataclass, field
from itertools import combinations
from typing import Callable, Sequence

import numpy as np


# ---------------------------------------------------------------------------
# Combinatorial Purged Cross-Validation (López de Prado)
# ---------------------------------------------------------------------------

@dataclass
class CPCVResult:
    train_indices: list[np.ndarray]
    test_indices: list[np.ndarray]
    n_paths: int
    n_groups: int
    n_test_groups: int


def cpcv_split(n_samples: int, n_groups: int = 10, n_test_groups: int = 2,
               embargo_pct: float = 0.01,
               event_horizons: Sequence[int] | None = None) -> CPCVResult:
    """Combinatorial Purged Cross-Validation.

    Args:
        n_samples: havaintojen kokonaismäärä
        n_groups: jako N saraketta (esim. 10)
        n_test_groups: kuinka monta sijoitetaan testiin per kombinaatio (esim. 2)
        embargo_pct: koulutusalueesta poistetaan testirajan ympäriltä N% saartoksi
        event_horizons: per-sample event-horisontti (kuinka monta seuraavaa havaintoa
            päällekkäisiä) — käytetään purgingissa. Jos None, ei purge-päällekkäisyyksiä.

    Tuottaa C(n_groups, n_test_groups) kombinaatiota, joissa kustakin saadaan
    out-of-sample-polut. Esim. 10C2 = 45 polkua (jokainen testattu 9 kertaa eri
    naapureilla).

    Returns:
        CPCVResult — train/test-indeksilistat per polku.
    """
    if n_test_groups >= n_groups:
        raise ValueError("n_test_groups < n_groups vaaditaan")
    embargo = max(1, int(n_samples * embargo_pct))

    # Jaa indeksit groupeihin
    group_size = n_samples // n_groups
    groups = []
    for g in range(n_groups):
        start = g * group_size
        end = (g + 1) * group_size if g < n_groups - 1 else n_samples
        groups.append(np.arange(start, end))

    train_paths, test_paths = [], []
    for combo in combinations(range(n_groups), n_test_groups):
        test_idx = np.concatenate([groups[g] for g in combo])
        # Embargon laajennus testin reunoille
        embargo_set = set()
        for g in combo:
            for ix in groups[g]:
                for delta in range(-embargo, embargo + 1):
                    j = ix + delta
                    if 0 <= j < n_samples:
                        embargo_set.add(int(j))
        # Purgeing: poista samples joiden event-horisontti osuu test-jakson sisään
        if event_horizons is not None:
            test_set = set(int(x) for x in test_idx)
            for i, h in enumerate(event_horizons):
                if i in test_set:
                    continue
                # Jos sample i:n vaikutushorisontti ulottuu test-set:in
                end_i = i + int(h)
                if any(j in test_set for j in range(i, end_i + 1)):
                    embargo_set.add(i)

        train_idx = np.array([i for i in range(n_samples) if i not in embargo_set
                              and i not in set(int(x) for x in test_idx)])
        train_paths.append(train_idx)
        test_paths.append(test_idx)

    return CPCVResult(
        train_indices=train_paths,
        test_indices=test_paths,
        n_paths=len(train_paths),
        n_groups=n_groups,
        n_test_groups=n_test_groups,
    )


# ---------------------------------------------------------------------------
# Probability of Backtest Overfitting (Bailey)
# ---------------------------------------------------------------------------

def pbo_score(performance_matrix: np.ndarray) -> float:
    """Probability of Backtest Overfitting.

    Args:
        performance_matrix: shape (n_strategies, n_periods) — esim. Sharpe per period
            per strategia.

    Computes: jokaisessa S-osajoukossa (CSCV) valitse paras IS-strategia, mittaa
    se OOS:ssa, laske rank. PBO = todennäköisyys, että IS-paras tuottaa OOS:ssa
    keskimääräistä huonomman ranking:n.

    Returns:
        PBO ∈ [0, 1]. PBO > 0.5 → strategia hylätään.
    """
    pm = np.asarray(performance_matrix, dtype=float)
    n_strat, n_periods = pm.shape
    if n_periods < 4:
        return 0.5  # liian vähän periodia luotettavaan PBO-arvioon
    # Even split — combinatorically symmetric
    if n_periods % 2 != 0:
        pm = pm[:, :-1]
        n_periods -= 1

    half = n_periods // 2
    period_indices = list(range(n_periods))
    # Kaikki kombinaatiot (n choose n/2) — leikkaa max 100 jos suuri
    combos = list(combinations(period_indices, half))
    if len(combos) > 100:
        random.seed(42)
        combos = random.sample(combos, 100)

    losses = 0
    total = 0
    for c in combos:
        is_idx = list(c)
        oos_idx = [i for i in period_indices if i not in is_idx]
        is_perf = pm[:, is_idx].mean(axis=1)
        oos_perf = pm[:, oos_idx].mean(axis=1)
        is_best = int(np.argmax(is_perf))
        # Ranking OOS:ssa (rank = 0 paras → n-1 huonoin)
        oos_ranks = np.argsort(-oos_perf)  # desc
        rank_of_best = int(np.where(oos_ranks == is_best)[0][0])
        # logit-formulation per Bailey: omega = rank/(n+1); jos omega > 0.5 → loss
        omega = (rank_of_best + 1) / (n_strat + 1)
        if omega > 0.5:
            losses += 1
        total += 1
    return losses / total if total else 0.5


# ---------------------------------------------------------------------------
# Almgren market impact (Almgren et al. 2005)
# ---------------------------------------------------------------------------

def almgren_temporary_impact(order_size: float, volatility: float,
                              daily_volume: float, eta: float = 0.142,
                              alpha: float = 0.6) -> float:
    """Väliaikainen markkinavaikutus prosentteina hinnasta.

    h(x) = eta * sigma * (X / V)^alpha
    missä X = order_size, V = daily_volume, sigma = päivittäinen volatiliteetti
    ja kertoimet eta, alpha kalibroitu empiirisestä datasta (Almgren 2005).

    Returns:
        impact_pct — siirtymä per share, prosenttiyksikköinä
    """
    if daily_volume <= 0:
        return float("inf")
    return eta * volatility * (abs(order_size) / daily_volume) ** alpha


def almgren_permanent_impact(order_size: float, daily_volume: float,
                              gamma: float = 0.314) -> float:
    """Pysyvä markkinavaikutus.

    g(x) = gamma * (X / V) — lineaarinen pysyvä paine.
    """
    if daily_volume <= 0:
        return float("inf")
    return gamma * abs(order_size) / daily_volume


# ---------------------------------------------------------------------------
# Obizhaeva-Wang shock + decay
# ---------------------------------------------------------------------------

def obizhaeva_wang_impact(order_flow: np.ndarray, kappa: float = 0.1,
                           rho: float = 0.5) -> np.ndarray:
    """Obizhaeva-Wang dynamic impact: shokki + eksponentiaalinen palautuminen.

    I(t) = sum over s<=t: kappa * order_flow(s) * exp(-rho * (t-s))

    Args:
        order_flow: order-volyymivirta aikajanan yli (positiiviset = buy)
        kappa: shokin amplitudi (per yksikkö volyymia)
        rho: palautumisnopeus

    Returns:
        impact-aikasarja samalla pituudella
    """
    n = len(order_flow)
    impact = np.zeros(n)
    for t in range(n):
        for s in range(t + 1):
            impact[t] += kappa * order_flow[s] * np.exp(-rho * (t - s))
    return impact


# ---------------------------------------------------------------------------
# Adversarial-injektorit
# ---------------------------------------------------------------------------

@dataclass
class AdversarialConfig:
    extra_slippage_bps: float = 5.0
    reject_probability: float = 0.03
    api_lag_seconds: float = 0.0
    fill_partial_probability: float = 0.10
    min_partial_fraction: float = 0.3
    seed: int | None = 42


def adversarial_execute(order_size: float, target_price: float,
                          config: AdversarialConfig) -> dict:
    """Simuloi tahallisesti pessimististä toteutusta strategian stress-testaamiseen.

    Returns:
        {'filled_size': ..., 'avg_price': ..., 'rejected': bool, 'lag_s': ...}
    """
    rng = random.Random(config.seed)
    rejected = rng.random() < config.reject_probability
    if rejected:
        return {"filled_size": 0.0, "avg_price": target_price,
                "rejected": True, "lag_s": config.api_lag_seconds}
    partial = rng.random() < config.fill_partial_probability
    if partial:
        frac = rng.uniform(config.min_partial_fraction, 0.95)
        filled = order_size * frac
    else:
        filled = order_size
    slippage = config.extra_slippage_bps / 10000.0
    avg_price = target_price * (1.0 + slippage * (1 if order_size > 0 else -1))
    return {"filled_size": filled, "avg_price": avg_price,
            "rejected": False, "lag_s": config.api_lag_seconds}


# ---------------------------------------------------------------------------
# Net Expected Value -laskuri arbitraasille (PDF3-pohjainen)
# ---------------------------------------------------------------------------

def calculate_arbitrage_net_ev(bid_a: float, ask_b: float, size: float,
                                 fee_a_bps: float = 10.0, fee_b_bps: float = 10.0,
                                 slippage_bps: float = 5.0,
                                 gas_cost_usd: float = 0.0,
                                 reject_probability: float = 0.0) -> dict:
    """Net EV cross-exchange-arbitraasille.

    Args:
        bid_a: paras bid pörssillä A (myyntipuoli)
        ask_b: paras ask pörssillä B (ostopuoli)
        size: tarkoitus-volyymi
        fee_a_bps, fee_b_bps: pörssien fees basis-pisteinä
        slippage_bps: dynaaminen slippage-arvio
        gas_cost_usd: lohkoketju-kaasumaksu (DEX)
        reject_probability: odotettu reject-todennäköisyys (luo opportunity cost)

    Returns:
        {'gross_pct': ..., 'net_pct': ..., 'profitable': bool, 'breakdown': {...}}
    """
    if ask_b <= 0 or bid_a <= 0:
        return {"gross_pct": 0, "net_pct": 0, "profitable": False, "breakdown": {}}
    gross_per_unit = bid_a - ask_b
    gross_pct = gross_per_unit / ask_b
    fee_total = (fee_a_bps + fee_b_bps) / 10000.0
    slippage_total = slippage_bps / 10000.0
    gas_pct = gas_cost_usd / (ask_b * size) if size > 0 else 0
    reject_drag = reject_probability * gross_pct  # opportunity-cost approxim.
    net_pct = gross_pct - fee_total - slippage_total - gas_pct - reject_drag
    return {
        "gross_pct": gross_pct,
        "net_pct": net_pct,
        "profitable": net_pct > 0,
        "breakdown": {
            "gross_pct": gross_pct,
            "fees": fee_total,
            "slippage": slippage_total,
            "gas_pct": gas_pct,
            "reject_drag": reject_drag,
        },
    }


if __name__ == "__main__":
    # Smoke-tests
    print("=== CPCV ===")
    r = cpcv_split(n_samples=1000, n_groups=10, n_test_groups=2)
    print(f"  paths={r.n_paths}, esim. train shape={r.train_indices[0].shape}, "
          f"test shape={r.test_indices[0].shape}")

    print("\n=== PBO ===")
    rng = np.random.default_rng(42)
    pm = rng.standard_normal((20, 12))
    pbo = pbo_score(pm)
    print(f"  random PBO = {pbo:.3f} (lähellä 0.5 = ei erityistä signaalia)")

    print("\n=== Almgren ===")
    impact = almgren_temporary_impact(order_size=10000, volatility=0.02,
                                      daily_volume=1_000_000)
    print(f"  10k osaketta 1M ADV:llä, vol 2% → temp impact = {impact*100:.4f}%")

    print("\n=== Net EV arbitrage ===")
    ev = calculate_arbitrage_net_ev(bid_a=100.50, ask_b=100.00, size=100,
                                      fee_a_bps=10, fee_b_bps=10,
                                      slippage_bps=5, gas_cost_usd=2.0)
    print(f"  Gross {ev['gross_pct']*100:.2f}% → Net {ev['net_pct']*100:.2f}% "
          f"(profitable={ev['profitable']})")
