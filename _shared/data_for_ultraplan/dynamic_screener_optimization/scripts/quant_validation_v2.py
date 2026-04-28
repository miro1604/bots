# -*- coding: utf-8 -*-
r"""quant_validation_v2.py — 2026 backtesting blueprint -kirjasto.

PAKOLLINEN kirjasto kaikkiin strategia-validointeihin (finance, crypto-finance, betting).
Korvaa raaka-Sharpe ja yksinkertainen walk-forward.

Sisältö:
  - probabilistic_sharpe (PSR)        — Bailey-de Prado
  - deflated_sharpe (DSR)             — multi-test korjaus
  - minimum_track_record_length (MinTRL)
  - bayesian_fdr                      — false discovery rate -korjaus
  - sample_size_required              — Z-pohjainen otoskoko-vaatimus
  - cpcv_split                        — Combinatorial Purged Cross-Validation
  - bootstrap_path_dependency         — sequence-risk-testi
  - sobol_screening_morris            — parametri-tärkeyden esiseulonta
  - aedl_dynamic_horizon              — vol-skaalattu hold-horisontti
  - vix_filter                        — risk-off-laukaisin osakkeisiin
  - validate_strategy                 — yhdistetty pakollinen tarkistuslista

Käyttö:
  from _shared.scripts.quant_validation_v2 import validate_strategy
  result = validate_strategy(trades_df, strategy_meta, n_trials_in_grid=12000)
  if result["edge_validated"]:
      ... # vain VAHVISTETUT edge:t pääsevät knowledge/strategies.jsonl:iin

Lähde: Gemini-research-tutkimukset 2026-04-28
       (López de Prado 2024-2026, Rajamanickam CPA 2026, Bailey-de Prado DSR)
"""
from __future__ import annotations
import math
import warnings
from typing import Optional, Iterable

import numpy as np
import pandas as pd
from scipy import stats


# ============================================================================
# 1. PSR / DSR / MinTRL — López de Prado / Bailey 2026 standardi
# ============================================================================

def probabilistic_sharpe_ratio(returns: np.ndarray, sr_benchmark: float = 0.0) -> float:
    """PSR: P(SR_true > sr_benchmark | havaittu data, sis. skewness+kurtosis).

    Returns: float ∈ [0, 1]. > 0.95 = vahva luottamus että edge on aito.
    """
    n = len(returns)
    if n < 30: return 0.0
    sr = returns.mean() / returns.std() if returns.std() > 0 else 0.0
    # Skewness, kurtosis (excess)
    skew = stats.skew(returns)
    kurt = stats.kurtosis(returns)  # fisher (excess)
    # Bailey-de Prado-kaava
    sr_adj_se = math.sqrt((1 - skew * sr + (kurt / 4) * sr**2) / (n - 1))
    if sr_adj_se == 0: return 0.5
    z = (sr - sr_benchmark) / sr_adj_se
    return float(stats.norm.cdf(z))


def deflated_sharpe_ratio(returns: np.ndarray, n_trials: int,
                            sr_benchmark: Optional[float] = None) -> float:
    """DSR: PSR korjattuna multi-test-bias:lla.

    n_trials = parametri-yhdistelmien lukumäärä jotka testattiin.
    Esim. crypto-grid 12000 ajoa → n_trials = 12000.
    """
    if n_trials <= 0: return probabilistic_sharpe_ratio(returns)
    n = len(returns)
    if n < 30: return 0.0
    sr = returns.mean() / returns.std() if returns.std() > 0 else 0.0
    skew = stats.skew(returns)
    kurt = stats.kurtosis(returns)
    # Deflated SR_benchmark: odotusarvo max-SR:lle n_trials kokeesta (Bailey)
    if sr_benchmark is None:
        emc = 0.5772156649  # Euler-Mascheroni
        max_sr_expected = (
            (1 - emc) * stats.norm.ppf(1 - 1/n_trials) +
            emc * stats.norm.ppf(1 - 1/(n_trials * math.e))
        )
        sr_benchmark_eff = max_sr_expected / math.sqrt(n)
    else:
        sr_benchmark_eff = sr_benchmark
    sr_adj_se = math.sqrt((1 - skew * sr + (kurt / 4) * sr**2) / (n - 1))
    if sr_adj_se == 0: return 0.5
    z = (sr - sr_benchmark_eff) / sr_adj_se
    return float(stats.norm.cdf(z))


def minimum_track_record_length(sr: float, sr_benchmark: float = 0.0,
                                  skew: float = 0.0, kurt: float = 0.0,
                                  conf: float = 0.95) -> float:
    """MinTRL: vähimmäismäärä havaintoja jotta SR > sr_benchmark conf:n tasolla.

    Palauttaa lukumääränä havaintoja (yleensä päiviä tai treidejä).
    """
    if sr <= sr_benchmark: return float("inf")
    z = stats.norm.ppf(conf)
    return float(1 + (1 - skew * sr + (kurt / 4) * sr**2) * (z / (sr - sr_benchmark))**2)


# ============================================================================
# 2. Otoskoko-laskuri (käyttäjän käytäntö: tutkimuksen kaava)
# ============================================================================

def sample_size_required(win_rate: float, conf: float = 0.95,
                          margin_of_error: float = 0.05) -> int:
    """n = (Z² × p × (1-p)) / E²"""
    z = stats.norm.ppf((1 + conf) / 2)  # two-sided
    p = win_rate
    return int(math.ceil((z**2 * p * (1 - p)) / margin_of_error**2))


# ============================================================================
# 3. Bayesian False Discovery Rate (FDR)
# ============================================================================

def bayesian_fdr(p_values: list[float], threshold: float = 0.05) -> tuple[float, list[bool]]:
    """Benjamini-Hochberg FDR-korjaus.

    Palauttaa (q-value-treshold, ehdot per testi: True = merkitsevä FDR-korjattu)
    """
    arr = np.asarray(p_values, dtype=float)
    n = len(arr)
    if n == 0: return threshold, []
    sorted_idx = np.argsort(arr)
    significant = np.zeros(n, dtype=bool)
    bh_threshold_used = threshold
    for rank_idx, i in enumerate(sorted_idx, start=1):
        bh_threshold = (rank_idx / n) * threshold
        if arr[i] <= bh_threshold:
            significant[i] = True
            bh_threshold_used = bh_threshold
        else:
            break
    return float(bh_threshold_used), significant.tolist()


# ============================================================================
# 4. CPCV — Combinatorial Purged Cross-Validation (López de Prado)
# ============================================================================

def cpcv_split(n_obs: int, n_splits: int = 6, n_test_groups: int = 2,
                hold_horizon: int = 0, embargo_pct: float = 0.01) -> list[tuple[np.ndarray, np.ndarray]]:
    """CPCV-splittien generointi.

    n_obs: havaintojen kokonaismäärä
    n_splits: kuinka moneen lohkoon data jaetaan
    n_test_groups: kuinka monta lohkoa otetaan test-setiksi per fold
    hold_horizon: kuinka moneen indeksiin tämä havainto vaikuttaa eteenpäin
                  (purging-vaatimus). Esim. v32:lla hold=680.
    embargo_pct: kuinka suuri kiinteä embargo testijakson loppuun (% datasta)

    Palauttaa: lista (train_idx, test_idx) -tupleja.
    """
    from itertools import combinations
    block_size = n_obs // n_splits
    blocks = [(i * block_size, (i + 1) * block_size if i < n_splits - 1 else n_obs)
              for i in range(n_splits)]
    embargo_size = int(n_obs * embargo_pct)
    splits = []
    for test_combo in combinations(range(n_splits), n_test_groups):
        test_idx_list = []
        for ti in test_combo:
            test_idx_list.extend(range(blocks[ti][0], blocks[ti][1]))
        test_idx = np.array(sorted(test_idx_list))
        # Purging: poista training:sta havainnot joiden hold-jakso menee test:iin
        train_mask = np.ones(n_obs, dtype=bool)
        train_mask[test_idx] = False
        if hold_horizon > 0:
            for ti in test_idx:
                purge_start = max(0, ti - hold_horizon)
                train_mask[purge_start:ti] = False
        # Embargoing: kiinteä raja test:n jälkeen
        if embargo_size > 0:
            for ti in test_combo:
                embargo_end = min(n_obs, blocks[ti][1] + embargo_size)
                train_mask[blocks[ti][1]:embargo_end] = False
        train_idx = np.where(train_mask)[0]
        splits.append((train_idx, test_idx))
    return splits


# ============================================================================
# 5. Bootstrap polkuriippuvuus (sequence-risk -testi)
# ============================================================================

def bootstrap_path_dependency(returns: np.ndarray, n_iter: int = 1000,
                                seed: int = 42, dd_window: int = None) -> dict:
    """Resamplaa returnsit satunnaisessa järjestyksessä, vertaa MAX DRAWDOWNia
    alkuperäiseen.

    Kompound-tuotto ei ole järjestys-riippuva, mutta MAX DD on. Jos alkuperäinen
    DD on selvästi parempi kuin shuffled-mediaani → strategia hyödynsi *sequence*
    luckyä; jos huonompi → strategia kestää random-ordering:n.

    Jos alpha katoaa kun järjestys muuttuu → polkuriippuvuus → fake edge.
    """
    rng = np.random.default_rng(seed)
    n = len(returns)
    if n < 30: return {"valid": False, "reason": "sample too small"}
    def max_dd(rets: np.ndarray) -> float:
        cum = np.cumprod(1 + rets)
        peak = np.maximum.accumulate(cum)
        return float((cum / peak - 1).min())
    original_dd = max_dd(returns)
    bootstrapped_dd = np.zeros(n_iter)
    for i in range(n_iter):
        shuffled = rng.permutation(returns)
        bootstrapped_dd[i] = max_dd(shuffled)
    bootstrapped_dd.sort()
    # Onko alkuperäinen DD parempi kuin esim. 90% shuffled? → sequence-luck
    p_better_than_random = (bootstrapped_dd <= original_dd).mean()
    # Jos > 0.90, alkuperäinen järjestys oli erityisen "onnekas"
    return {
        "valid": True,
        "original_max_dd": original_dd,
        "median_bootstrap_dd": float(np.median(bootstrapped_dd)),
        "p10_bootstrap_dd": float(bootstrapped_dd[int(n_iter * 0.10)]),
        "p90_bootstrap_dd": float(bootstrapped_dd[int(n_iter * 0.90)]),
        "p_better_than_random": float(p_better_than_random),
        "alpha_path_dependent": p_better_than_random > 0.85,  # erityisen onnekas
    }


# ============================================================================
# 6. AEDL — dynaaminen hold-horisontti volatiliteetin perusteella
# ============================================================================

def aedl_dynamic_horizon(close_series: pd.Series, h_max: int = 680,
                          vol_window: int = 60, max_vol_window: int = 504) -> pd.Series:
    """h_t = h_max × (σ_max / σ_t) per-bar.

    Korkea vol → lyhyempi hold, matala vol → pidempi.
    """
    ret = close_series.pct_change()
    sigma_t = ret.ewm(span=vol_window).std()
    sigma_max = sigma_t.rolling(max_vol_window, min_periods=vol_window).max()
    h_t = h_max * (sigma_max / sigma_t.replace(0, np.nan))
    return h_t.clip(upper=h_max * 2, lower=h_max // 8).fillna(h_max).astype(int)


# ============================================================================
# 7. VIX-suodatin osakeoperaatioille
# ============================================================================

def vix_risk_off_filter(vix_series: pd.Series, sma_window: int = 60) -> pd.Series:
    """True = risk-off (älä avaa uusia ostoja); False = ok.

    Toteutus: VIX_t > SMA(VIX, 60)_t
    """
    return vix_series > vix_series.rolling(sma_window).mean()


# ============================================================================
# 8. Sobol-screening (Morrisin menetelmä) — kevyt versio
# ============================================================================

def morris_elementary_effects(simulator_fn, param_grid: dict[str, list],
                                 n_trajectories: int = 10, seed: int = 42) -> dict:
    """Morris elementary effects -seulonta.

    simulator_fn: (params: dict) -> float (esim. Sharpe-arvo)
    param_grid: {param_name: [low, high, ...]} — vain low ja high tarvitaan

    Palauttaa: {param_name: {"mu_star": ..., "sigma": ...}}
    """
    rng = np.random.default_rng(seed)
    param_names = list(param_grid.keys())
    bounds = {k: (min(v), max(v)) for k, v in param_grid.items()}
    elem_effects = {p: [] for p in param_names}

    for _ in range(n_trajectories):
        # Random base point
        base = {p: rng.uniform(*bounds[p]) for p in param_names}
        try:
            f_base = simulator_fn(base)
        except Exception:
            continue
        for p in param_names:
            new_params = dict(base)
            delta = (bounds[p][1] - bounds[p][0]) * 0.1  # 10% step
            new_params[p] = min(base[p] + delta, bounds[p][1])
            try:
                f_new = simulator_fn(new_params)
                ee = (f_new - f_base) / delta
                elem_effects[p].append(ee)
            except Exception:
                pass

    summary = {}
    for p, ees in elem_effects.items():
        if ees:
            arr = np.array(ees)
            summary[p] = {
                "mu_star": float(np.mean(np.abs(arr))),  # tärkeysmittari
                "sigma": float(np.std(arr)),  # epälineaarisuus
                "n": len(arr)
            }
        else:
            summary[p] = {"mu_star": 0.0, "sigma": 0.0, "n": 0}
    return summary


# ============================================================================
# 9. Yhdistetty validointi-checklist
# ============================================================================

def validate_strategy(returns: np.ndarray, strategy_meta: dict,
                       n_trials_in_grid: int = 1) -> dict:
    """Pakollinen tarkistuslista. Returns: dict joka kattaa kaikki kriteerit.

    strategy_meta: {
      "name": str,
      "win_rate": float,
      "n_trades": int,
      "hold_horizon": int (päivinä, jos saatavilla)
    }
    """
    n = len(returns)
    if n == 0:
        return {"edge_validated": False, "reason": "no trades"}

    win_rate = (returns > 0).mean()
    sr = returns.mean() / returns.std() if returns.std() > 0 else 0.0

    # 1. Otoskoko
    min_n = sample_size_required(win_rate)
    n_pass = n >= 100  # minimumiraja 100 (paras: 369)

    # 2. PSR
    psr = probabilistic_sharpe_ratio(returns)
    psr_pass = psr > 0.95

    # 3. DSR (multi-test korjattu)
    dsr = deflated_sharpe_ratio(returns, n_trials=n_trials_in_grid)
    dsr_pass = dsr > 0.50

    # 4. MinTRL
    skew = stats.skew(returns)
    kurt = stats.kurtosis(returns)
    mintrl = minimum_track_record_length(sr, 0.0, skew, kurt, conf=0.95)
    mintrl_pass = n >= mintrl

    # 5. Bootstrap polkuriippuvuus
    boot = bootstrap_path_dependency(returns)
    boot_pass = boot.get("valid", False) and not boot.get("alpha_path_dependent", True)

    # Yhteenveto
    all_pass = n_pass and psr_pass and dsr_pass and mintrl_pass and boot_pass

    return {
        "strategy_name": strategy_meta.get("name", "unnamed"),
        "n_trades": n,
        "win_rate": float(win_rate),
        "raw_sharpe": float(sr),
        "n_required_for_winrate": min_n,
        "psr": float(psr),
        "dsr": float(dsr),
        "n_trials_in_grid": n_trials_in_grid,
        "mintrl": float(mintrl),
        "skewness": float(skew),
        "kurtosis": float(kurt),
        "bootstrap": boot,
        "checks": {
            "n_sufficient (n>=100)": n_pass,
            "psr > 0.95": psr_pass,
            "dsr > 0.50 (multi-test)": dsr_pass,
            "n >= MinTRL": mintrl_pass,
            "no path dependency": boot_pass,
        },
        "edge_validated": bool(all_pass),
        "verdict": "VALIDATED" if all_pass else
                    "HYPOTHESIS" if n < min_n else
                    "REJECTED"
    }


# ============================================================================
# Demo / CLI
# ============================================================================

if __name__ == "__main__":
    # Sanity-test: simuloi 100 treidiä (60% win-rate, 25% avg-win, -10% avg-loss)
    rng = np.random.default_rng(42)
    n = 100
    wins = rng.random(n) < 0.60
    returns = np.where(wins, 0.25 + rng.normal(0, 0.05, n),
                        -0.10 + rng.normal(0, 0.05, n))

    print("=== quant_validation_v2 demo ===")
    result = validate_strategy(returns, {"name": "demo_strategy"}, n_trials_in_grid=100)
    import json
    print(json.dumps({k: v for k, v in result.items() if k != "bootstrap"},
                      indent=2, default=str))
    print("\nbootstrap:", json.dumps(result["bootstrap"], indent=2, default=str))
