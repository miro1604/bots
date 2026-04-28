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
import json, sys
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
    "other",                 # custom-metodit
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
    metadata: Optional[dict] = None,
):
    """Kirjaa yksi havainto. Verdict = AUTO päättelee jos ei annettu.

    verdict-arvot:
      "EFFECT_STRONG" — |value| > 0.20 + p < 0.05
      "EFFECT_MODERATE" — |value| > 0.05 + p < 0.10
      "EFFECT_WEAK" — |value| < 0.05 mutta p < 0.10
      "NO_EFFECT" — p > 0.20 (todistettu ei-vaikutus, älä testaa uudestaan)
      "INCONCLUSIVE" — n_samples liian pieni
    """
    if method not in VALID_METHODS:
        print(f"WARN: tuntematon method '{method}', käytetään 'other'", file=sys.stderr)
        method = "other"

    if verdict is None:
        verdict = _auto_verdict(value, p_value, n_samples)

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "feature": feature,
        "target": target,
        "method": method,
        "value": float(value) if not np.isnan(value) else None,
        "p_value": float(p_value) if p_value is not None and not np.isnan(p_value) else None,
        "n_samples": int(n_samples),
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


def record_batch(observations: list[dict]):
    """Bulk-tallennus useammasta havainnosta kerralla."""
    for obs in observations:
        record_observation(**obs)


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
