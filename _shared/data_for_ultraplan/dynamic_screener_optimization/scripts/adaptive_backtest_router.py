# -*- coding: utf-8 -*-
r"""adaptive_backtest_router.py — taajuus-adaptiivinen validointi-spec.

Käyttäjän mandaatti 2026-04-28:
  Eri kaupankäynti-taajuuksilla on eri tilastolliset bias:t. Älä käytä yhtä
  fixed-metodia kaikille — valitse OIKEA metodi taajuuden mukaan.

Käyttö:
  from _shared.scripts.adaptive_backtest_router import recommend_validation_methods
  spec = recommend_validation_methods(trades_per_year=50, hold_horizon_days=30)
  # spec → dict jossa pakolliset metodit + parametrit
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class ValidationSpec:
    tier: str
    description: str
    pakolliset_metodit: list[str]
    cpcv_n_splits: int
    cpcv_n_test_groups: int
    cpcv_embargo_pct: float
    bootstrap_iter: int
    monte_carlo_iter: int
    walk_forward_folds: int
    sobol_screening: bool
    psr_required: bool
    dsr_required: bool
    mintrl_required: bool
    fdr_required: bool
    realistic_costs: dict
    optimistic_buffer: dict
    notes: list[str]


def recommend_validation_methods(
    trades_per_year: float,
    hold_horizon_days: float = 1.0,
    instrument_class: str = "equity",  # equity / crypto_perp / fx / commodity
    n_trials_in_grid: int = 1,
) -> dict:
    """Valitsee oikea validointi-metodi-spec.

    Args:
        trades_per_year: kuinka monta toteutunutta kauppaa per vuosi
        hold_horizon_days: position keskimääräinen hold-pituus päivinä
        instrument_class: "equity", "crypto_perp", "fx", "commodity"
        n_trials_in_grid: kuinka monta parametri-yhdistelmää on testattu
                          (tärkeä DSR multi-test-korjaukselle)
    """
    if trades_per_year <= 12:
        spec = _spec_very_low_freq(hold_horizon_days, n_trials_in_grid)
    elif trades_per_year <= 100:
        spec = _spec_low_freq(hold_horizon_days, n_trials_in_grid)
    elif trades_per_year <= 1000:
        spec = _spec_medium_freq(hold_horizon_days, n_trials_in_grid)
    elif trades_per_year <= 10000:
        spec = _spec_high_freq(hold_horizon_days, n_trials_in_grid)
    else:
        spec = _spec_hft(hold_horizon_days, n_trials_in_grid)

    spec.realistic_costs = _costs_for_instrument(instrument_class, spec.tier)
    spec.optimistic_buffer = _optimistic_buffer(spec.tier)
    return asdict(spec)


# ============================================================================
# Per-tier spec
# ============================================================================

def _spec_very_low_freq(hold_days: float, n_trials: int) -> ValidationSpec:
    """1-12 kauppaa/v: makro, value, long-term position."""
    return ValidationSpec(
        tier="VERY_LOW_FREQ",
        description="1-12 kauppaa/v — makro/value/long-term position",
        pakolliset_metodit=[
            "bootstrap_resampling_1000+",
            "monte_carlo_path_simulation",
            "parametri_robustness_sweep",
            "sequence_risk_test",
            "DSR_multi_test_corrected",
        ],
        cpcv_n_splits=0,  # ei riitä dataa CPCV:lle
        cpcv_n_test_groups=0,
        cpcv_embargo_pct=0,
        bootstrap_iter=2000,  # extra robust koska pieni N
        monte_carlo_iter=5000,
        walk_forward_folds=0,
        sobol_screening=False,
        psr_required=True,
        dsr_required=True,
        mintrl_required=True,
        fdr_required=n_trials > 10,
        realistic_costs={},  # täytetään myöhemmin
        optimistic_buffer={},
        notes=[
            "Pelkkä historiallinen ajo EI riitä — bootstrapping pakollinen",
            "Sequence-risk-testi: jos alpha katoaa kun järjestys vaihtuu, fake edge",
            f"Monte Carlo: 5000 polku-simulaatiota path-luck:n eliminoimiseksi",
            "Hold-jakso voi ulottua useisiin vuosiin → pidä bias-suoja päällä",
        ],
    )


def _spec_low_freq(hold_days: float, n_trials: int) -> ValidationSpec:
    """12-100 kauppaa/v: swing trading, momentum-rotation."""
    return ValidationSpec(
        tier="LOW_FREQ",
        description="12-100 kauppaa/v — swing/momentum-rotation",
        pakolliset_metodit=[
            "walk_forward_5_fold",
            "CPCV_purging_embargo",
            "bootstrap_path_dependency",
            "DSR_multi_test_corrected",
            "PSR_skew_kurt_corrected",
            "MinTRL_check",
        ],
        cpcv_n_splits=6,
        cpcv_n_test_groups=2,
        cpcv_embargo_pct=0.02,  # 2%
        bootstrap_iter=1000,
        monte_carlo_iter=2000,
        walk_forward_folds=5,
        sobol_screening=True,
        psr_required=True,
        dsr_required=True,
        mintrl_required=True,
        fdr_required=n_trials > 50,
        realistic_costs={},
        optimistic_buffer={},
        notes=[
            f"Hold {hold_days}d → CPCV embargo PAKOLLINEN (info leak -riski)",
            "Sobol: tunnista mitkä parametrit ovat tärkeitä (älä optimoi kohinaa)",
            "Sample size-vaatimus: n >= 100, mieluiten 369",
        ],
    )


def _spec_medium_freq(hold_days: float, n_trials: int) -> ValidationSpec:
    """100-1000 kauppaa/v: päivittäinen, intraday."""
    return ValidationSpec(
        tier="MEDIUM_FREQ",
        description="100-1000 kauppaa/v — päivittäinen/intraday",
        pakolliset_metodit=[
            "CPCV_k_5_to_10",
            "DSR_multi_test_corrected",
            "PSR_skew_kurt_corrected",
            "Sobol_parametrisalkku",
            "regime_aware_split",
            "bootstrap_path_dependency",
        ],
        cpcv_n_splits=10,
        cpcv_n_test_groups=2,
        cpcv_embargo_pct=0.01,
        bootstrap_iter=1000,
        monte_carlo_iter=1000,
        walk_forward_folds=10,
        sobol_screening=True,
        psr_required=True,
        dsr_required=True,
        mintrl_required=True,
        fdr_required=True,  # monitestaus aina ongelma tällä taajuudella
        realistic_costs={},
        optimistic_buffer={},
        notes=[
            "Riittävä N tilastollisille mittareille (yleensä > 369 helposti)",
            "FDR-korjaus pakollinen (helposti 100+ parametri-yhdistelmää)",
            "Parametrisalkku 3-5 varianttia stabiililla tasangolla",
        ],
    )


def _spec_high_freq(hold_days: float, n_trials: int) -> ValidationSpec:
    """1000-10000 kauppaa/v: kvasi-HFT, statistical arbitrage."""
    return ValidationSpec(
        tier="HIGH_FREQ",
        description="1000-10000 kauppaa/v — statistical arb / kvasi-HFT",
        pakolliset_metodit=[
            "DSR_multi_test_corrected",
            "Bayesian_FDR",
            "Sobol_full",
            "slippage_jitter_model",
            "regime_aware_split",
            "intraday_volume_constraint",
        ],
        cpcv_n_splits=15,
        cpcv_n_test_groups=3,
        cpcv_embargo_pct=0.005,
        bootstrap_iter=500,
        monte_carlo_iter=500,
        walk_forward_folds=20,
        sobol_screening=True,
        psr_required=True,
        dsr_required=True,
        mintrl_required=True,
        fdr_required=True,
        realistic_costs={},
        optimistic_buffer={},
        notes=[
            "N riittää helposti, fokus mikrorakenteeseen + slippage-jitter",
            "Volyymi-constraint: position <= 5% kynttilän volyymistä",
            "Funding/borrow-kustannukset eksplisiittisesti mallinnettava",
        ],
    )


def _spec_hft(hold_days: float, n_trials: int) -> ValidationSpec:
    """10000+ kauppaa/v: HFT, market-making."""
    return ValidationSpec(
        tier="HFT",
        description="10000+ kauppaa/v — HFT/market-making",
        pakolliset_metodit=[
            "tapahtumapohjainen_simulaatio",
            "level_2_3_order_book",
            "jitter_aware_latency",
            "queue_position_model",
            "fill_probability_model",
            "DSR_multi_test_corrected",
            "Bayesian_FDR",
        ],
        cpcv_n_splits=20,
        cpcv_n_test_groups=4,
        cpcv_embargo_pct=0.001,
        bootstrap_iter=200,
        monte_carlo_iter=200,
        walk_forward_folds=30,
        sobol_screening=True,
        psr_required=True,
        dsr_required=True,
        mintrl_required=True,
        fdr_required=True,
        realistic_costs={},
        optimistic_buffer={},
        notes=[
            "VEKTORIPOHJAINEN testaus EI RIITÄ — tarvitaan event-driven engine",
            "Latenssi (jitter) log-normaalijakaumasta",
            "Tilauskirjan jonosijoituksen heikkeneminen mikrosekunneissa",
            "Tämä tier ei käytännössä sovi meidän nykyiseen ekosysteemiin (Python+pandas)",
        ],
    )


# ============================================================================
# Kustannus-mallit per instrumentti-luokka (optimistic-realistic)
# ============================================================================

def _costs_for_instrument(instrument: str, tier: str) -> dict:
    """Realistinen mutta ei-pessimistinen kustannusmalli."""
    if instrument == "equity":
        return {
            "tc_bps_per_side": 4,        # 0.04% per side, S&P500-likvidi
            "slippage_bps": 0,            # OHLCV-tasolla 0 (4bps kattaa spreadin)
            "borrow_cost_pa": 0.005,      # short-borrow 0.5% / vuosi
            "t_plus_1_execution": tier in ("LOW_FREQ", "VERY_LOW_FREQ", "MEDIUM_FREQ"),
            "volume_cap_pct": 5,          # 5% kynttilävolyymistä max
        }
    if instrument == "crypto_perp":
        return {
            "tc_bps_per_side": 6,                # Binance maker/taker keskim
            "slippage_bps_per_leverage": 5,      # 5bps × leverage = liquidation-puskuri
            "funding_bps_per_8h_per_lev": 2,     # 0.02% × leverage / 8h
            "liquidation_threshold_pct": 95,     # 95% maintenance margin
            "t_plus_1_execution": tier in ("LOW_FREQ", "VERY_LOW_FREQ", "MEDIUM_FREQ"),
            "volume_cap_pct": 10,
        }
    if instrument == "fx":
        return {
            "tc_bps_per_side": 1,         # major pairs, ECN
            "slippage_bps": 0.5,
            "swap_rate_pa": 0.0,
            "t_plus_1_execution": True,
            "volume_cap_pct": 20,
        }
    if instrument == "commodity":
        return {
            "tc_bps_per_side": 5,
            "slippage_bps": 2,
            "rollover_cost_pa": 0.02,     # kontango/backwardation
            "t_plus_1_execution": True,
            "volume_cap_pct": 5,
        }
    return {"tc_bps_per_side": 5, "slippage_bps": 1}


def _optimistic_buffer(tier: str) -> dict:
    """Kuinka paljon edge:ä saa olla yli pessimistisen rajan ja silti pelata.

    Käyttäjän mandaatti: ÄLÄ ole pessimistinen.
    """
    if tier in ("VERY_LOW_FREQ", "LOW_FREQ"):
        return {
            "min_psr_threshold": 0.85,    # ei 0.95 (pessimistinen)
            "min_dsr_threshold": 0.50,    # vaatii silti multi-test-korjattu
            "tolerance_for_path_dependency": 0.85,  # ei 0.50
        }
    if tier == "MEDIUM_FREQ":
        return {
            "min_psr_threshold": 0.90,
            "min_dsr_threshold": 0.55,
            "tolerance_for_path_dependency": 0.85,
        }
    return {
        "min_psr_threshold": 0.95,
        "min_dsr_threshold": 0.60,
        "tolerance_for_path_dependency": 0.85,
    }


# ============================================================================
# CLI demo
# ============================================================================

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("ADAPTIIVINEN BACKTEST-ROUTER — esimerkit")
    print("=" * 60)

    cases = [
        ("Macro-strategia, 5 kauppaa/v", 5, 90, "equity"),
        ("Swing-momentum, 50 kauppaa/v", 50, 30, "equity"),
        ("V32_dyn (per-osake B2)", 169, 680, "equity"),
        ("Bayesian Kelly v32", 61, 680, "equity"),
        ("BNB sweep+rejection (5v)", 410, 1, "crypto_perp"),
        ("LINK 5y deep grid", 3167, 1, "crypto_perp"),
        ("HFT market-making", 50000, 0.001, "crypto_perp"),
    ]

    for name, n, hold, ic in cases:
        print(f"\n--- {name} (N/v={n}, hold={hold}d, {ic}) ---")
        spec = recommend_validation_methods(n, hold, ic, n_trials_in_grid=100)
        print(f"  TIER: {spec['tier']}")
        print(f"  Kuvaus: {spec['description']}")
        print(f"  Pakolliset metodit:")
        for m in spec['pakolliset_metodit']:
            print(f"    • {m}")
        print(f"  CPCV n_splits={spec['cpcv_n_splits']}, embargo={spec['cpcv_embargo_pct']*100:.1f}%")
        print(f"  Realistic costs: {spec['realistic_costs']}")
        print(f"  Optimistic buffer: {spec['optimistic_buffer']}")
