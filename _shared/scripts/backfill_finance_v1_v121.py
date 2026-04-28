# -*- coding: utf-8 -*-
r"""backfill_finance_v1_v121.py — kaikkien v1-v121 historiallinen tieto portfolioon.

Käyttäjän mandaatti 2026-04-28: kaikki finance-agentin simulaatiot v1-v121
kirjataan uudelleen tieto-portfolioon uusilla method-tyypeillä.

Lähteet:
  - finance/high_cagr_v*_results.txt (55 tiedostoa) — backtest-tulokset
  - finance/market_knowledge/learnings.jsonl — strukturoidut opit (L001-L090)
  - finance/market_knowledge/lessons/what_works.md, what_doesnt_work.md
  - finance/market_knowledge/experiments.jsonl — kokeilu-yhteenvedot

Kategoriat (kaikki kirjataan):
  1. EFFECT_STRONG: validoituja edge:jä (VXX-decay, UNG-decay, jne)
  2. EFFECT_MODERATE: kohtuullisia mutta tarvitsee vahvistusta
  3. NO_EFFECT: todistettu ei-vaikutus (FX-alpha, 30%-trailing, jne) — ei testata uudelleen
  4. INCONCLUSIVE: data-virheet (v101_cache TIE/MEE-spike-bugi)
  5. Multi-feature interactiot (regime × momentum)
  6. Conditional regime (Stockbee TI BULL_NORMAL)
  7. Sequence patternit (gap-down → fade)
"""
from __future__ import annotations
import sys, re, json
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_shared" / "scripts"))

from feature_portfolio import (
    record_observation, record_regime_conditional,
    record_multi_feature_interaction, record_nonlinear_threshold,
    record_pair_relationship, record_sequence_pattern, summarize
)

FINANCE = ROOT / "finance"
LEARN = FINANCE / "market_knowledge" / "learnings.jsonl"


def parse_results_files() -> dict:
    """Parsi kaikki v*_results.txt → versio → tilastot."""
    results = {}
    for p in sorted(FINANCE.glob("high_cagr_v*_results.txt")):
        m = re.search(r"v(\d+[a-z]?)", p.stem)
        if not m: continue
        version = m.group(1)
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # Etsi CAGR, N
        cagr_matches = re.findall(r"CAGR\s*[:=]?\s*([+-]?\d+\.?\d*)\s*%", content)
        n_matches = re.findall(r"N\s*=\s*(\d+)", content)
        results[version] = {
            "path": p.name,
            "n_cagr_mentions": len(cagr_matches),
            "max_cagr": max([float(c) for c in cagr_matches], default=0),
            "median_cagr": sorted([float(c) for c in cagr_matches])[len(cagr_matches)//2] if cagr_matches else 0,
            "n_total_mentions": sum(int(n) for n in n_matches if int(n) < 10000),
            "content_size": len(content)
        }
    return results


def parse_learnings() -> list[dict]:
    """Parsi learnings.jsonl → kriittisimmät havainnot."""
    if not LEARN.exists(): return []
    learnings = []
    with open(LEARN, encoding="utf-8") as f:
        for line in f:
            try:
                learnings.append(json.loads(line))
            except Exception:
                pass
    return learnings


def main():
    n_total = 0

    print("="*70)
    print("BACKFILL FINANCE v1-v121 → FEATURE_PORTFOLIO")
    print("="*70)

    # === 1. Parsi v*_results.txt:t ===
    results = parse_results_files()
    print(f"\n[1/3] Parsittu {len(results)} v_results-tiedostoa")
    for ver, r in sorted(results.items())[:20]:
        print(f"  v{ver}: max-cagr {r['max_cagr']:.1f}%, mentions={r['n_cagr_mentions']}")
    if len(results) > 20:
        print(f"  ... ({len(results)-20} muuta)")

    # Kirjaa per-version yhteenveto
    for ver, r in results.items():
        # Verdict per-version: max_cagr ratkaisee
        if r["max_cagr"] > 30:
            verdict = "EFFECT_STRONG"
        elif r["max_cagr"] > 10:
            verdict = "EFFECT_MODERATE"
        elif r["max_cagr"] > 5:
            verdict = "EFFECT_WEAK"
        else:
            verdict = "INCONCLUSIVE"
        record_observation(
            feature=f"high_cagr_v{ver}_strategy",
            target="portfolio_cagr",
            target_class="portfolio_metric",
            domain="systematic_equity",
            method="other",
            value=r["max_cagr"] / 100.0,
            raw_value=r["max_cagr"],
            n_samples=max(r["n_total_mentions"], 1),
            applies_to="S&P500",
            bot="finance",
            strategy_id=f"high_cagr_v{ver}",
            verdict=verdict,
            confidence_pct=70 if verdict in ("EFFECT_STRONG", "EFFECT_MODERATE") else 50,
            metadata={
                "source_file": r["path"],
                "median_cagr": r["median_cagr"],
                "note": "Historiallinen versio, ennen 2026-blueprint-validointia"
            }
        )
        n_total += 1

    # === 2. Parsi learnings.jsonl:n kriittiset opit ===
    learnings = parse_learnings()
    print(f"\n[2/3] Parsittu {len(learnings)} learnings-merkintää")
    n_critical = 0
    for L in learnings:
        importance = L.get("importance_score", 5)
        category = L.get("category", "unknown")
        if importance < 7 and category not in ("strategy", "data_quality", "results"):
            continue
        observation = L.get("observation", "")[:200]
        confidence = L.get("confidence", 0.7)
        ctx = L.get("context", {})
        if isinstance(ctx, dict) and ctx:
            applies_to = "_".join(str(v)[:20] for v in list(ctx.values())[:3])
        else:
            applies_to = "general"

        # Auto-detect verdict
        if "EI TOIMI" in observation.upper() or "EI TOIMINEET" in observation or "DATA-VIRHE" in observation.upper():
            verdict = "NO_EFFECT"
        elif "VAHVA" in observation or "VALIDATED" in observation or importance >= 9:
            verdict = "EFFECT_STRONG"
        else:
            verdict = "EFFECT_MODERATE"

        record_observation(
            feature=f"finance_learning_{L.get('id', 'unknown')}",
            target="strategy_insight",
            target_class="other",
            domain=category,
            method="other",
            value=float(confidence),
            raw_value=float(importance),
            n_samples=1,
            applies_to=applies_to,
            bot="finance",
            strategy_id=L.get("id", "unknown"),
            verdict=verdict,
            confidence_pct=float(confidence) * 100,
            metadata={
                "observation": observation,
                "importance": importance,
                "tags": L.get("tags", []),
                "source": "learnings.jsonl"
            }
        )
        n_total += 1
        n_critical += 1
    print(f"  Kirjattu {n_critical} kriittistä oppia (importance >= 7 tai strategia/data/tulos)")

    # === 3. Kriittiset multi-feature & strukturoidut löydökset ===
    print(f"\n[3/3] Multi-feature, regime-conditional, sequence patterns...")

    # 3a. Stockbee TI regime-conditional (L084)
    record_regime_conditional(
        feature="stockbee_ti_avg_pnl_per_trade",
        target="next_day_return",
        regime="BULL_NORMAL",
        value=0.0544, n_samples=53025, p_value=0.001,
        target_class="stock_return", domain="micro",
        bot="finance", strategy_id="stockbee_ti",
        metadata={"source": "L084", "alt_regimes": {"EXTREME_BEAR": 0.0001, "CAUTION": 0.0011, "BULL_HOT": -0.0182}}
    )
    n_total += 1

    # 3b. Multi-feature: regime × momentum (v117d → cleaned data)
    record_multi_feature_interaction(
        features=["3m1m_momentum_lookback63d_skip21d", "regime_filter_skip_extreme_bear"],
        target="portfolio_cagr_cleaned_data",
        interaction_value=0.04,    # 4pp lisävaikutus regime-suodattimella
        main_effects_sum=0.10,
        n_samples=21,
        p_value=0.05,
        target_class="portfolio_metric", domain="systematic_equity",
        bot="finance", strategy_id="momentum_v117d",
        metadata={"source": "L089", "cleaned_data": True}
    )
    n_total += 1

    # 3c. NONLINEAR THRESHOLD: VIX-regime-vaikutus (L068-L070)
    record_nonlinear_threshold(
        feature="VIX",
        target="strategy_pnl_S&P_growth",
        threshold_value=30.0,
        effect_below=0.005,    # +0.5% normaalisti
        effect_above=-0.025,    # -2.5% kun VIX > 30
        n_below=4500, n_above=180,
        target_class="portfolio_metric", domain="risk_regime",
        bot="finance", strategy_id="vix_filter",
        metadata={"source": "L068-L070", "rule": "skip_new_buys_above_30"}
    )
    n_total += 1

    # 3d. Pair-spread: VXX vs spot-VIX (rakenteellinen contango)
    record_pair_relationship(
        asset_a="VXX", asset_b="spot_VIX",
        relationship_type="pair_ratio",
        target="VXX_decay_per_year",
        target_class="commodity_term_structure",
        value=-0.30, n_samples=2520, p_value=0.0001,
        domain="structural", bot="finance",
        strategy_id="vxx_decay_structural",
        metadata={"explanation": "VXX rullaustappio → -30% per vuosi keskimäärin",
                   "note": "Strukturaalinen — ei katoa"}
    )
    n_total += 1

    # 3e. Sequence pattern: gap-down → fade (universal)
    record_sequence_pattern(
        sequence=["gap_down_minus_2pct", "low_vol_environment", "fade_long"],
        target="60d_forward_return",
        hit_rate=0.78, expected_random_rate=0.55, n_occurrences=420,
        target_class="stock_return", domain="behavioral",
        bot="finance", strategy_id="gap_down_fade_universal",
        metadata={"source": "what_works.md", "applicable_to": ["IWM","XLE","XLF","XBI","EEM"]}
    )
    n_total += 1

    # 3f. NO_EFFECT: FX daily alpha (20000 testattu, ei toimi)
    record_observation(
        feature="fx_daily_alpha_20000_param_search",
        target="fx_strategy_return",
        target_class="fx_rate", domain="systematic",
        method="no_effect_test",
        value=0.0, raw_value=0.0, p_value=0.50, n_samples=20000,
        applies_to="EURUSD_GBPUSD_USDJPY", bot="finance",
        strategy_id="fx_alpha_failed",
        verdict="NO_EFFECT", confidence_pct=92,
        metadata={"source": "v56", "note": "Vol liian pieni → daily-alpha ei skaalaudu", "kategoria": "structural_failure"}
    )
    n_total += 1

    # 3g. NO_EFFECT: pre-cleaned data (L088 — TIE/MEE-spike)
    record_observation(
        feature="yfinance_v101_cache_uncleaned",
        target="momentum_cagr_inflation",
        target_class="portfolio_metric", domain="data_quality",
        method="no_effect_test",
        value=0.30, raw_value=30.0,  # 30pp inflation
        p_value=0.001, n_samples=1000,
        applies_to="S&P500_yfinance_v101", bot="finance",
        strategy_id="data_quality_check",
        verdict="EFFECT_STRONG",  # TODISTETTU vaikutus (vääristäjä)
        confidence_pct=99,
        metadata={
            "source": "L088",
            "note": "TIE +155 000% / MEE +5660% spike-bugit -> 30pp CAGR inflation",
            "rule": "blacklist >300% daily moves before any momentum backtest"
        }
    )
    n_total += 1

    # 3h. EFFECT_STRONG: realistinen ceiling (L090)
    record_observation(
        feature="us_momentum_realistic_ceiling_21yr",
        target="portfolio_cagr_no_leverage",
        target_class="portfolio_metric", domain="results",
        method="other",
        value=0.14, raw_value=14.0,
        n_samples=21, applies_to="S&P_PIT_cleaned",
        bot="finance", strategy_id="realistic_ceiling",
        verdict="EFFECT_STRONG", confidence_pct=92, replication_count=2,
        metadata={
            "source": "L090",
            "max_dd_pct": -32, "method": "K10_VT40_LB84_SK10",
            "note": "Realistinen no-leverage Pareto-frontier 21v: max ~14% CAGR",
            "implication": "40-50% CAGR EI saavutettavissa pelkällä S&P-momentumilla puhtaalla datalla"
        }
    )
    n_total += 1

    print(f"  Kirjattu 8 strukturoitua multi-feature / regime / sequence -havaintoa")

    # === Yhteenveto ===
    print(f"\n{'='*70}")
    print(f"BACKFILL VALMIS — {n_total} havaintoa kirjattu")
    print(f"{'='*70}")

    s = summarize()
    print(f"\nFEATURE_PORTFOLIO TILA:")
    print(f"  Total: {s['n_observations']}")
    print(f"  By verdict: {s['by_verdict']}")


if __name__ == "__main__":
    main()
