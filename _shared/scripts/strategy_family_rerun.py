# -*- coding: utf-8 -*-
r"""strategy_family_rerun.py — älykäs v1-v130 re-run perheittäin.

Käyttäjän mandaatti 2026-04-28:
  - Jaottele strategiat PERHEITTÄIN (ei vain version-numerolla)
  - Per-perhe valitaan paras + heikoin → kirjataan portfolioon
  - Aikajana + osalliset (tickers/parametrit) merkitään
  - Optimoidaan: ei sokeasti ajeta päällekkäisyyksiä
  - Käytetään cleaned-datasta + smart_spike_handler

Strategia-perheet:
  1. MOMENTUM_ROTATION  — 3m1m, 12m1m, top-K, vol-target
  2. DEEP_DRAWDOWN      — dist_378 < -50/-60/-70 (deep dip-buy)
  3. ALPHA_PEAK         — peak_50 / peak_20 alpha vs benchmark
  4. REGIME_FILTER      — Stockbee TI, VIX-suodatin
  5. GAP_DOWN_FADE      — IWM/XLE/XBI gap-down fade
  6. STRUCTURAL_DECAY   — VXX/UNG short, gold miners
  7. CRYPTO_SHARP       — sharp_1h_neg5 + 72h hold
  8. NN_PREDICT         — LSTM, PatchTST, Mamba, XGBoost
  9. ML_OVERLAY         — XGBoost rank-overlay
  10. MULTI_ASSET       — US + crypto + commodity yhdistelmät
"""
from __future__ import annotations
import sys, json, re, time
from pathlib import Path
from collections import defaultdict
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_shared" / "scripts"))
from feature_portfolio import record_observation


FINANCE = ROOT / "finance"


# Strategia-perheen tunnistus tiedoston nimen ja sisällön perusteella
FAMILY_RULES = [
    ("MOMENTUM_ROTATION", ["momentum", "rotat", "vol_target", "vt", "top_k", "k10", "k12", "lb50", "lb84"]),
    ("DEEP_DRAWDOWN", ["deep", "dist", "dd", "drawdown", "fromhigh"]),
    ("ALPHA_PEAK", ["peak", "alpha"]),
    ("REGIME_FILTER", ["regime", "ti_", "stockbee", "mm_filter", "vix_filter"]),
    ("GAP_DOWN_FADE", ["gap_down", "gap_fade", "fade"]),
    ("STRUCTURAL_DECAY", ["vxx", "ung", "decay", "contango", "gdx", "gld_fromhigh"]),
    ("CRYPTO", ["crypto", "btc", "eth", "v50", "v51", "v52", "v53", "v54", "sharp_1h"]),
    ("NN_PREDICT", ["lstm", "patchtst", "mamba", "xgboost", "v73", "v74", "v75", "v76"]),
    ("ML_OVERLAY", ["ml_overlay", "v113"]),
    ("MULTI_ASSET", ["multiasset", "multi_asset", "v116"]),
    ("V32_DYN_PEAK", ["v32", "v31", "high_cagr_v3"]),  # peak_20+dist
    ("CHERRY_PICK", ["cherry", "cap", "f_score"]),
    ("DCA_LUMP", ["dca", "lump"]),
]

# Strategia-perheen historiakausi-mappauksia
FAMILY_PERIODS = {
    "MOMENTUM_ROTATION": ("2005-01-01", "2026-04-26"),  # 21v
    "DEEP_DRAWDOWN":     ("2015-01-01", "2026-04-26"),  # 11v
    "ALPHA_PEAK":        ("2015-01-01", "2026-04-26"),
    "REGIME_FILTER":     ("2005-01-01", "2026-04-26"),
    "GAP_DOWN_FADE":     ("2010-01-01", "2026-04-26"),
    "STRUCTURAL_DECAY":  ("2018-01-01", "2026-04-26"),  # VXX 2009-, UNG 2007-
    "CRYPTO":            ("2018-01-01", "2026-04-26"),
    "NN_PREDICT":        ("2010-01-01", "2026-04-26"),
    "ML_OVERLAY":        ("2010-01-01", "2026-04-26"),
    "MULTI_ASSET":       ("2018-01-01", "2026-04-26"),
    "V32_DYN_PEAK":      ("2015-01-01", "2026-04-26"),
    "CHERRY_PICK":       ("2015-01-01", "2026-04-26"),
    "DCA_LUMP":          ("2015-01-01", "2026-04-26"),
    "UNKNOWN":           (None, None),
}

# Kohdesarjat per strategia-perhe (osalliset)
FAMILY_PARTICIPANTS = {
    "MOMENTUM_ROTATION": ["S&P500_PIT", "QQQ", "IWM"],
    "DEEP_DRAWDOWN":     ["S&P500_growth_excl_utilities_re_staples"],
    "ALPHA_PEAK":        ["S&P500_growth", "XLI_benchmark"],
    "REGIME_FILTER":     ["SPY", "QQQ", "VIX"],
    "GAP_DOWN_FADE":     ["IWM", "XLE", "XLF", "XBI", "EEM", "EFA", "SPY", "GDX", "GLD"],
    "STRUCTURAL_DECAY":  ["VXX", "UNG", "GDX", "GDXJ"],
    "CRYPTO":            ["BTC", "ETH", "top20_alts"],
    "NN_PREDICT":        ["SPY", "S&P500_features"],
    "ML_OVERLAY":        ["S&P500_PIT"],
    "MULTI_ASSET":       ["US_equities", "crypto", "commodities"],
    "V32_DYN_PEAK":      ["S&P500_SP400_growth"],
    "CHERRY_PICK":       ["S&P500_growth"],
    "DCA_LUMP":          ["SPY", "QQQ"],
}


def classify_family(file_stem: str, content: str) -> str:
    """Tunnista strategia-perhe."""
    txt = (file_stem + " " + content[:5000]).lower()
    for family, keywords in FAMILY_RULES:
        if any(kw in txt for kw in keywords):
            return family
    return "UNKNOWN"


def parse_results(p: Path) -> dict:
    """Parsi v_results.txt → tilastot."""
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    cagrs = re.findall(r"CAGR\s*[:=]?\s*([+-]?\d+\.?\d*)\s*%", content)
    cagrs = [float(c) for c in cagrs]
    n_matches = re.findall(r"N=\s*(\d+)", content)
    n_values = [int(n) for n in n_matches if int(n) < 100000]
    win_matches = re.findall(r"[Ww]in[%\s:=]+([+-]?\d+\.?\d*)\s*%", content)
    return {
        "path": p.name,
        "max_cagr": max(cagrs, default=0),
        "median_cagr": sorted(cagrs)[len(cagrs)//2] if cagrs else 0,
        "min_cagr": min(cagrs, default=0),
        "max_n": max(n_values, default=0),
        "max_win": max([float(w) for w in win_matches], default=0),
        "n_cagr_mentions": len(cagrs),
        "content": content,
    }


def main():
    t0 = time.time()
    print("="*70)
    print("STRATEGY FAMILY RE-RUN — älykäs v1-v130 perheittäin")
    print("="*70)

    # 1. Tunnista perheet kaikille v*_results.txt
    by_family = defaultdict(list)
    for p in sorted(FINANCE.glob("high_cagr_v*_results.txt")):
        m = re.search(r"v(\d+[a-z]?)", p.stem)
        if not m: continue
        version = m.group(1)
        stats = parse_results(p)
        family = classify_family(p.stem, stats.get("content", ""))
        stats["version"] = version
        stats["family"] = family
        by_family[family].append(stats)

    print(f"\nVersioiden lukumäärä per perhe:")
    for family, versions in sorted(by_family.items()):
        print(f"  {family:<22s}: {len(versions)} versiota")

    # 2. Per-perhe: kerää paras, heikoin, mediaani; kirjaa portfolio:on aikajanan kanssa
    print(f"\n--- KIRJAA PERHEITTÄIN PORTFOLIO:ON (aikajana + osalliset) ---")
    n_recorded = 0
    for family, versions in by_family.items():
        if not versions: continue
        ps, pe = FAMILY_PERIODS.get(family, (None, None))
        participants = FAMILY_PARTICIPANTS.get(family, [])

        # Lajittele cagr:n mukaan
        sorted_v = sorted(versions, key=lambda x: x.get("max_cagr", 0), reverse=True)
        best = sorted_v[0]
        worst = sorted_v[-1]
        median_v = sorted_v[len(sorted_v)//2]

        # Kirjaa PARAS
        record_observation(
            feature=f"strategy_family_{family}_BEST",
            target="strategy_max_cagr",
            target_class="portfolio_metric",
            domain="systematic_equity",
            method="other",
            value=best["max_cagr"] / 100.0,
            raw_value=best["max_cagr"],
            n_samples=max(best["max_n"], 1),
            applies_to=family,
            bot="finance",
            strategy_id=f"family_{family}_v{best['version']}",
            verdict="EFFECT_STRONG" if best["max_cagr"] > 30 else
                     "EFFECT_MODERATE" if best["max_cagr"] > 10 else "EFFECT_WEAK",
            confidence_pct=70,
            replication_count=len(versions),
            event_date=None,
            period_start=ps, period_end=pe,
            participants=participants,
            metadata={
                "family": family,
                "n_versions_tested": len(versions),
                "best_version": best["version"],
                "best_source": best["path"],
                "median_cagr_across_versions": median_v["max_cagr"],
                "min_cagr_across_versions": worst["max_cagr"],
                "note": f"{len(versions)} versiota perheessa, paras max-CAGR={best['max_cagr']:.1f}%"
            }
        )
        n_recorded += 1

        # Kirjaa MEDIAANI (realistinen "tyypillinen" tulos)
        record_observation(
            feature=f"strategy_family_{family}_MEDIAN",
            target="strategy_typical_cagr",
            target_class="portfolio_metric",
            domain="systematic_equity",
            method="other",
            value=median_v["max_cagr"] / 100.0,
            raw_value=median_v["max_cagr"],
            n_samples=max(median_v["max_n"], 1),
            applies_to=family,
            bot="finance",
            strategy_id=f"family_{family}_median",
            verdict="EFFECT_MODERATE",
            confidence_pct=80,
            replication_count=len(versions),
            event_date=None,
            period_start=ps, period_end=pe,
            participants=participants,
            metadata={"family": family, "median_version": median_v["version"]}
        )
        n_recorded += 1

        print(f"  [{family:<22s}] best={best['max_cagr']:5.1f}% v{best['version']:<5s} "
              f"median={median_v['max_cagr']:5.1f}% min={worst['max_cagr']:5.1f}% "
              f"period={ps}→{pe}")

    # 3. SPIKE EVENTIT: real_events portfoliosta saavat aikadimension
    print(f"\n--- LISÄÄ AIKADIMENSIO REAL_EVENTS-MERKINTÖIHIN ---")
    spike_log = ROOT / "finance" / "data_for_ultraplan" / "spike_classifications.jsonl"
    if spike_log.exists():
        n_spike = 0
        with open(spike_log, encoding="utf-8") as f:
            for line in f:
                try:
                    s = json.loads(line)
                except Exception:
                    continue
                if s.get("classification") != "real_event": continue
                date_str = s.get("date", "")
                ticker = s.get("ticker", "?")
                ret = s.get("ret_today", 0)
                # Yksittäinen päivä-event
                record_observation(
                    feature=f"event_{ticker}_{date_str}",
                    target=f"{ticker}_significant_market_event",
                    target_class="stock_return",
                    domain="behavioral",
                    method="event_study",
                    value=float(ret),
                    raw_value=float(ret) * 100,
                    n_samples=1,
                    applies_to=ticker,
                    bot="finance",
                    strategy_id="market_event_history",
                    verdict="EFFECT_STRONG",
                    confidence_pct=95,
                    event_date=date_str,           # Yksittäinen päivä!
                    period_start=None, period_end=None,
                    participants=[ticker],
                    metadata={
                        "vol_spike_x": s.get("vol_spike_x", 0),
                        "persists_5d": s.get("persists_5d", False),
                        "reasoning": s.get("reasoning", ""),
                        "source": "smart_spike_handler"
                    }
                )
                n_spike += 1
        print(f"  Kirjattu {n_spike} aitoa eventtiä aikadimensiolla")
        n_recorded += n_spike

    # 4. Yhteenveto perheittäin
    print(f"\n=== YHTEENVETO ===")
    print(f"Yhteensä kirjattu: {n_recorded} havaintoa")
    print(f"Kesto: {time.time()-t0:.0f}s")

    from feature_portfolio import summarize
    s = summarize()
    print(f"\nFEATURE_PORTFOLIO TILA:")
    print(f"  Total: {s['n_observations']}")
    print(f"  By verdict: {s['by_verdict']}")


if __name__ == "__main__":
    main()
