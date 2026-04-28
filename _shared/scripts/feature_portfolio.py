# -*- coding: utf-8 -*-
r"""feature_portfolio.py — kasvava tieto-portfolio joka oppii joka simulaatiosta.

Filosofia (käyttäjän pysyvä mandaatti 2026-04-28):
  Jokainen simulaatio ja backtest kirjoittaa havaintoja tähän kumulatiiviseen
  tietokantaan. Sekä todistetusti vaikuttavat että ei-vaikuttavat asiat
  kerätään. Ajan myötä portfolio kasvaa ja paljastaa markkinan rakennetta.

Tallennus:
  _shared/knowledge/feature_portfolio.jsonl   — yksi rivi per havainto
  _shared/knowledge/feature_portfolio_summary.json — aggregaatti per feature

Käyttö (kaikilla bot-agenteilla):
  from _shared.scripts.feature_portfolio import record_observation, summarize
  record_observation(
      feature="peak_50",
      target="next_month_return",
      method="spearman",
      value=0.12,
      p_value=0.04,
      n_samples=1000,
      applies_to="S&P500",
      bot="finance",
      strategy_id="v32_dyn",
      metadata={"hold_days": 680},
  )

JOKAINEN screen/backtest/grid-haku KIRJAA tästä eteenpäin AINA. Ei poikkeuksia.
"""
from __future__ import annotations
import json, sys, math
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
KNOW = ROOT / "_shared" / "knowledge"
KNOW.mkdir(parents=True, exist_ok=True)

PORTFOLIO_PATH = KNOW / "feature_portfolio.jsonl"
SUMMARY_PATH = KNOW / "feature_portfolio_summary.json"


# Kelvolliset method-arvot (laajennetaan tarvittaessa)
VALID_METHODS = {
    "pearson", "spearman", "kendall",
    "distance_correlation",  # epälineaarinen
    "mutual_information",    # ei-monotoninen
    "granger_causality",     # ajallinen
    "information_coefficient", "rank_ic",
    "sobol_main", "sobol_total",  # parametrit
    "morris_mu_star",        # parametri-tärkeys
    "regime_effect",         # regiimi-spesifinen
    "factor_loading",        # faktori-malli
    "covariance_matrix",     # multi-feature
    "cholesky_decomposition",
    "ate",                   # average treatment effect (CPA)
    "no_effect_test",        # placeholder negative-finding
    "regression_beta",       # OLS/GLS-kerroin
    "elasticity",            # %-vaikutus / %-muutos
    "event_study",           # ennen/jälkeen-tilan vertailu
    "cointegration",         # pitkän aikavälin yhteys (Engle-Granger / Johansen)
    "garch_volatility_link", # vol-shock siirtyy → toinen sarja
    "transfer_entropy",      # kausaalinen suuntaaminen
    "lead_lag",              # X(t) ennustaa Y(t+k)
    "other",                 # custom-metodit
}

# Target-luokat — mihin tahansa mitattavaan ilmiöön voidaan kohdistaa
VALID_TARGET_CLASSES = {
    "stock_price", "stock_return", "stock_volatility", "stock_volume",
    "stock_pair_spread",        # esim. AAPL-MSFT spread
    "stock_pair_corr",          # rolling korrelaatio
    "index_price", "index_return", "index_volatility",
    "sector_etf", "sector_rotation",
    "fund_nav", "fund_flow", "etf_flow",
    "macro_indicator",          # CPI, FFR, M2, NFP, PMI, jne
    "macro_surprise",           # actual vs consensus
    "rates_yield_curve",        # 2-10y spread
    "fx_rate", "fx_carry",
    "commodity_price", "commodity_term_structure",
    "crypto_price", "crypto_funding", "crypto_oi", "crypto_dominance",
    "options_iv", "options_skew", "options_put_call_ratio",
    "fundamental_eps", "fundamental_pe", "fundamental_buyback",
    "sentiment_retail", "sentiment_institutional", "sentiment_news",
    "alt_data",                 # satelliitti, web-traffic, patent jne
    "regime_state",             # bull/bear/range
    "trade_count_per_day",      # liquidity-proxy
    "spread_bid_ask",
    "portfolio_metric",         # Sharpe, DD, CAGR
    "other",
}


def record_observation(
    feature: str,
    target: str,
    method: str,
    value: float,
    n_samples: int,
    applies_to: str = "unknown",
    bot: str = "unknown",
    strategy_id: str = "",
    p_value: Optional[float] = None,
    confidence_interval: Optional[tuple[float, float]] = None,
    verdict: Optional[str] = None,
    target_class: str = "other",       # uusi: mihin luokkaan kohde kuuluu
    domain: str = "unknown",            # uusi: macro/micro/fundamental/sentiment/alt
    replication_count: int = 1,         # uusi: kuinka monta kertaa sama havainto on toistettu
    raw_value: Optional[float] = None,  # uusi: täsmälleen mitä mitattiin (sama kuin value oletuksena)
    confidence_pct: Optional[float] = None,  # uusi: 0-100% varmuus
    metadata: Optional[dict] = None,
):
    """Kirjaa yksi havainto. Verdict = AUTO päättelee jos ei annettu.

    KAIKKI todistettavat ilmiöt kelpaavat — ei vain hinta-kohteet:
    talousluvut, volatility, volume, fundament, alt-data, mikä tahansa.

    verdict-arvot:
      "EFFECT_STRONG"   — |value| > 0.20 + p < 0.05
      "EFFECT_MODERATE" — |value| > 0.05 + p < 0.10
      "EFFECT_WEAK"     — pelkkä p < 0.10
      "NO_EFFECT"       — p > 0.20 / |value| < 0.05 (älä testaa uudestaan)
      "INCONCLUSIVE"    — n_samples < 30 tai value puuttuu

    confidence_pct (0-100): jos ei annettu, lasketaan automaattisesti:
      = 100 × (1 - p_value)        jos p_value on
      = sqrt(n) × |value|-perusteinen heuristiikka muuten
      × replication_count-bonus    (max +20%)
    """
    if method not in VALID_METHODS:
        print(f"WARN: tuntematon method '{method}', käytetään 'other'", file=sys.stderr)
        method = "other"
    if target_class not in VALID_TARGET_CLASSES:
        print(f"WARN: tuntematon target_class '{target_class}', käytetään 'other'", file=sys.stderr)
        target_class = "other"

    if verdict is None:
        verdict = _auto_verdict(value, p_value, n_samples)

    if confidence_pct is None:
        confidence_pct = _auto_confidence(value, p_value, n_samples, replication_count)

    if raw_value is None:
        raw_value = float(value) if value is not None and not np.isnan(value) else None

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "feature": feature,
        "target": target,
        "target_class": target_class,
        "domain": domain,
        "method": method,
        "value": float(value) if value is not None and not np.isnan(value) else None,
        "raw_value": float(raw_value) if raw_value is not None and not np.isnan(raw_value) else None,
        "p_value": float(p_value) if p_value is not None and not np.isnan(p_value) else None,
        "n_samples": int(n_samples),
        "confidence_pct": round(float(confidence_pct), 1),
        "replication_count": int(replication_count),
        "applies_to": applies_to,
        "bot": bot,
        "strategy_id": strategy_id,
        "verdict": verdict,
        "metadata": metadata or {}
    }
    if confidence_interval:
        record["ci_lo"] = float(confidence_interval[0])
        record["ci_hi"] = float(confidence_interval[1])

    with open(PORTFOLIO_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _auto_confidence(value: Optional[float], p_value: Optional[float],
                       n_samples: int, replication_count: int = 1) -> float:
    """Auto-laske varmuusaste 0-100%.

    Kerros 1: p_value-pohjainen → 100 × (1 - p_value)
    Kerros 2: jos ei p_value: heuristiikka |value| × sqrt(n)/sqrt(n+100)
    Kerros 3: replication-bonus +5% per lisätoisto, max +20%
    Cap: 0-99% (ei koskaan 100%, kun aineisto on rajallinen)
    """
    if value is None or np.isnan(value):
        return 0.0
    if n_samples < 30:
        # Liian pieni N — varmuus rajoitettu < 50%
        base = min(40, abs(value) * 50)
    elif p_value is not None and not np.isnan(p_value):
        # P-value-pohjainen
        base = 100 * (1 - p_value)
    else:
        # Heuristiikka: vahva |value| + iso N → korkea varmuus
        sample_factor = math.sqrt(n_samples) / (math.sqrt(n_samples) + 10)
        base = min(95, abs(value) * 100 * sample_factor + 50 * sample_factor)
    # Replication bonus
    rep_bonus = min(20, (replication_count - 1) * 5)
    confidence = min(99.0, base + rep_bonus)
    return max(0.0, confidence)


def record_batch(observations: list[dict]):
    """Bulk-tallennus useammasta havainnosta kerralla."""
    for obs in observations:
        record_observation(**obs)


def record_per_ticker_decomposition(
    trades: "list[dict] | pd.DataFrame",
    strategy_id: str,
    bot: str,
    target_class: str = "stock_return",
    domain: str = "systematic_equity",
    min_trades_per_ticker: int = 5,
    metadata: Optional[dict] = None,
):
    """KRIITTINEN: hajottaa portfolio-tason tulokset per-ticker-tasolle.

    Käyttäjän test case 2026-04-28: jos kokonais-CAGR on heikko (esim. 1.2%)
    mutta yksittäiset osakkeet tuottavat luotettavasti (esim. NVDA CAGR 60%),
    nämä per-ticker-EDGE:t TÄYTYY kirjata erikseen tietopankkiin — muuten
    arvokas tieto jää piiloon koko-strategian heikon tuloksen alle.

    Args:
        trades: lista dict:ejä tai pd.DataFrame, jossa per-trade-rivit.
                Pakolliset kentät: ticker, pnl_pct (tai pnl_eur+invested)
        strategy_id: strategian tunniste (esim. "v32_dyn_nosl_cap200")
        bot: "finance" / "crypto-finance" / "betting"
        target_class: "stock_return" / "crypto_price" / jne
        min_trades_per_ticker: alle tämän → INCONCLUSIVE (ei kirjata)

    Kirjaa per-ticker:
      - mean_pnl_pct (per kauppa)
      - n_trades
      - win_rate
      - approx-CAGR jos meta-tieto vuosista on annettu

    Lisäksi kirjaa "concentration"-havainto: kuinka paljon top-3 ticker
    tuottaa kokonaisstrategian PnL:stä (tärkeää LOO-arvioon).
    """
    import pandas as pd
    if not isinstance(trades, pd.DataFrame):
        if not trades: return 0
        df = pd.DataFrame(trades)
    else:
        df = trades.copy()

    if "ticker" not in df.columns:
        print("WARN: trades sisällä ei 'ticker'-kenttää, ei voi tehdä decomposition")
        return 0
    if "pnl_pct" not in df.columns:
        if "pnl_eur" in df.columns and "invested" in df.columns:
            df["pnl_pct"] = (df["pnl_eur"] / df["invested"]) * 100
        else:
            print("WARN: ei pnl_pct eikä pnl_eur+invested → ei decomposition")
            return 0

    n_records = 0
    by_ticker = df.groupby("ticker")
    total_pnl = df["pnl_pct"].sum()

    # 1) Per-ticker rivit
    for tk, sub in by_ticker:
        n = len(sub)
        if n < min_trades_per_ticker: continue
        mean_pnl = float(sub["pnl_pct"].mean())
        median_pnl = float(sub["pnl_pct"].median())
        win = float((sub["pnl_pct"] > 0).mean())
        std = float(sub["pnl_pct"].std()) if n >= 2 else 0.0
        # T-test vs 0
        if std > 0:
            t_stat = mean_pnl / (std / np.sqrt(n))
            p_value = 2 * (1 - 0.5 * (1 + math.erf(abs(t_stat) / math.sqrt(2))))
        else:
            p_value = None
        # Verdict per ticker
        if abs(mean_pnl) > 5 and (p_value is None or p_value < 0.05):
            verdict = "EFFECT_STRONG"
        elif abs(mean_pnl) > 2 and (p_value is None or p_value < 0.10):
            verdict = "EFFECT_MODERATE"
        elif p_value is not None and p_value > 0.30:
            verdict = "NO_EFFECT"
        else:
            verdict = "EFFECT_WEAK"

        record_observation(
            feature=f"{strategy_id}__per_ticker",
            target=f"{tk}_per_trade_pnl_pct",
            target_class=target_class,
            domain=domain,
            method="other",
            value=mean_pnl / 100.0,
            raw_value=mean_pnl,
            p_value=p_value,
            n_samples=n,
            applies_to=tk,
            bot=bot,
            strategy_id=strategy_id,
            verdict=verdict,
            metadata={
                "median_pnl_pct": median_pnl,
                "win_rate": win,
                "std_pnl_pct": std,
                "decomposition_source": "per_ticker_breakdown",
                **(metadata or {})
            }
        )
        n_records += 1

    # 2) Concentration-havainto (top-3 osuus)
    by_ticker_sum = df.groupby("ticker")["pnl_pct"].sum().sort_values(ascending=False)
    top3_pnl = by_ticker_sum.head(3).sum()
    top3_share = (top3_pnl / total_pnl) if total_pnl != 0 else 0.0
    record_observation(
        feature=f"{strategy_id}__top3_concentration",
        target="strategy_pnl_concentration",
        target_class="portfolio_metric",
        domain="systematic",
        method="other",
        value=float(top3_share),
        raw_value=float(top3_share * 100),
        n_samples=len(df),
        applies_to=str(by_ticker_sum.head(3).index.tolist()),
        bot=bot,
        strategy_id=strategy_id,
        verdict="EFFECT_STRONG" if abs(top3_share) > 0.50 else "EFFECT_MODERATE",
        metadata={
            "top3_tickers": by_ticker_sum.head(3).index.tolist(),
            "top3_pnl_pct_sum": float(top3_pnl),
            "total_pnl_pct_sum": float(total_pnl),
            "n_unique_tickers": int(by_ticker.ngroups),
            "concentration_warning": abs(top3_share) > 0.50,
        }
    )
    n_records += 1

    return n_records


def _auto_verdict(value: float, p_value: Optional[float], n_samples: int) -> str:
    """Heuristinen verdict."""
    if n_samples < 30:
        return "INCONCLUSIVE"
    if value is None or np.isnan(value):
        return "INCONCLUSIVE"
    abs_val = abs(value)
    if p_value is None:
        if abs_val > 0.20: return "EFFECT_STRONG"
        if abs_val > 0.10: return "EFFECT_MODERATE"
        if abs_val > 0.05: return "EFFECT_WEAK"
        return "NO_EFFECT"
    if p_value < 0.05 and abs_val > 0.20: return "EFFECT_STRONG"
    if p_value < 0.10 and abs_val > 0.05: return "EFFECT_MODERATE"
    if p_value < 0.10: return "EFFECT_WEAK"
    if p_value > 0.20: return "NO_EFFECT"
    return "INCONCLUSIVE"


def summarize() -> dict:
    """Aggregoi feature_portfolio.jsonl → summary."""
    if not PORTFOLIO_PATH.exists():
        return {"n_observations": 0, "by_feature": {}, "by_method": {}, "by_verdict": {}}

    observations = []
    with open(PORTFOLIO_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                observations.append(json.loads(line))
            except Exception:
                pass

    by_feature = {}
    by_method = {}
    by_verdict = {}
    for o in observations:
        feat = o.get("feature", "unknown")
        meth = o.get("method", "unknown")
        verd = o.get("verdict", "unknown")
        by_feature[feat] = by_feature.get(feat, 0) + 1
        by_method[meth] = by_method.get(meth, 0) + 1
        by_verdict[verd] = by_verdict.get(verd, 0) + 1

    # Top features by EFFECT_STRONG count
    feature_strength = {}
    for o in observations:
        feat = o.get("feature", "unknown")
        verd = o.get("verdict", "")
        feature_strength.setdefault(feat, {"strong": 0, "moderate": 0, "weak": 0, "no_effect": 0})
        if verd == "EFFECT_STRONG":
            feature_strength[feat]["strong"] += 1
        elif verd == "EFFECT_MODERATE":
            feature_strength[feat]["moderate"] += 1
        elif verd == "EFFECT_WEAK":
            feature_strength[feat]["weak"] += 1
        elif verd == "NO_EFFECT":
            feature_strength[feat]["no_effect"] += 1

    summary = {
        "n_observations": len(observations),
        "first_recorded": observations[0]["ts"] if observations else None,
        "last_recorded": observations[-1]["ts"] if observations else None,
        "by_feature": dict(sorted(by_feature.items(), key=lambda x: -x[1])[:50]),
        "by_method": by_method,
        "by_verdict": by_verdict,
        "feature_strength": feature_strength,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def is_already_known_no_effect(feature: str, target: str, applies_to: str) -> bool:
    """Tarkistaa onko tämä yhdistelmä jo todistetusti NO_EFFECT.

    Käyttö: ennen kuin agentit/skriptit testaavat (feature, target, applies_to)
    -kombinaation, kysyvät tästä → säästää compute-aikaa.
    """
    if not PORTFOLIO_PATH.exists():
        return False
    with open(PORTFOLIO_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                o = json.loads(line)
                if (o.get("feature") == feature and
                        o.get("target") == target and
                        o.get("applies_to") == applies_to and
                        o.get("verdict") == "NO_EFFECT" and
                        o.get("n_samples", 0) >= 100):
                    return True
            except Exception:
                pass
    return False


def get_known_strong_features(target: str, applies_to: str = None,
                                 min_strong_count: int = 2) -> list[dict]:
    """Hae jo löydetyt EFFECT_STRONG-piirteet target:ille.

    Hyödyllinen kun rakennetaan uutta strategiaa: katsotaan mitä piirteitä
    on jo todistetusti vaikuttaviksi.
    """
    if not PORTFOLIO_PATH.exists():
        return []
    by_feature = {}
    with open(PORTFOLIO_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                o = json.loads(line)
                if o.get("target") != target: continue
                if applies_to and o.get("applies_to") != applies_to: continue
                if o.get("verdict") != "EFFECT_STRONG": continue
                feat = o.get("feature")
                by_feature.setdefault(feat, []).append(o)
            except Exception:
                pass
    strong = []
    for feat, obs in by_feature.items():
        if len(obs) >= min_strong_count:
            avg_value = sum(o["value"] for o in obs if o.get("value") is not None) / len(obs)
            strong.append({
                "feature": feat,
                "n_strong_findings": len(obs),
                "avg_value": avg_value,
                "methods_used": list(set(o["method"] for o in obs)),
                "applies_to_set": list(set(o["applies_to"] for o in obs)),
            })
    return sorted(strong, key=lambda x: x["n_strong_findings"], reverse=True)


# ============================================================================
# Demo / CLI
# ============================================================================

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["summarize", "demo", "list_strong", "test_no_effect"])
    ap.add_argument("--target", default="stock_return_30d")
    args = ap.parse_args()

    if args.action == "demo":
        # Simuloi pari havaintoa
        record_observation(
            feature="peak_50_alpha", target="stock_return_30d",
            method="spearman", value=0.12, p_value=0.04, n_samples=1000,
            applies_to="S&P500", bot="finance", strategy_id="v32_dyn"
        )
        record_observation(
            feature="moon_phase", target="stock_return_30d",
            method="pearson", value=0.003, p_value=0.87, n_samples=5040,
            applies_to="S&P500", bot="finance"
        )
        record_observation(
            feature="vix_zscore", target="next_week_return",
            method="distance_correlation", value=0.22, n_samples=2520,
            applies_to="SPY", bot="finance"
        )
        print("Demo-havainnot kirjattu.")
        print(json.dumps(summarize(), indent=2, default=str))

    elif args.action == "summarize":
        s = summarize()
        print(json.dumps(s, indent=2, default=str))

    elif args.action == "list_strong":
        strong = get_known_strong_features(args.target)
        print(f"EFFECT_STRONG-piirteet target='{args.target}':")
        for s in strong:
            print(f"  {s['feature']:<30s} (n_findings={s['n_strong_findings']}, avg={s['avg_value']:.3f})")

    elif args.action == "test_no_effect":
        already = is_already_known_no_effect("moon_phase", "stock_return_30d", "S&P500")
        print(f"moon_phase → stock_return_30d (S&P500) on jo NO_EFFECT? {already}")
