# -*- coding: utf-8 -*-
r"""screener_to_portfolio.py — muuttaa screener-ajot feature_portfolio-merkinnöiksi.

Käyttäjän mandaatti 2026-04-28: jokaisen simulaation pitää kirjata havaintoja
kasvavaan tieto-portfolioon. Tämä skripti:

  1. Lukee olemassa olevat screener-runs (finance + crypto-finance)
  2. Muuttaa ne feature_portfolio.record_observation()-kutsuiksi
  3. Tallentaa havainnot _shared/knowledge/feature_portfolio.jsonl:iin

Käyttö:
  python _shared/scripts/screener_to_portfolio.py --backfill   # kaikki vanhat
  python _shared/scripts/screener_to_portfolio.py --bot finance
  python _shared/scripts/screener_to_portfolio.py --bot crypto-finance
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_shared" / "scripts"))
from feature_portfolio import record_observation


def process_finance_screener():
    """finance/knowledge/screener_runs.jsonl → portfolio."""
    p = ROOT / "finance" / "knowledge" / "screener_runs.jsonl"
    if not p.exists():
        print(f"  finance: {p} ei ole, skipataan")
        return 0
    n = 0
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            idea = r.get("idea_id", "unknown")
            ticker = r.get("ticker", "unknown")
            sig_param = r.get("sig_param", "")
            hold = r.get("hold_days", 0)
            n_trades = r.get("n_trades", 0)
            median = r.get("median_pnl_pct", 0)
            cagr = r.get("cagr", 0)
            freq = r.get("freq_per_year", 0)
            cat = r.get("category", "unknown")

            # Kirjaa per-ticker median-pnl havainto
            verdict = "EFFECT_STRONG" if cat == "EDGE_LÖYDETTY" else \
                      "EFFECT_MODERATE" if cat == "HIGH_FREQ" else \
                      "EFFECT_WEAK" if cat == "EDGE_PIILOSSA" else \
                      "NO_EFFECT" if cat == "EI_EDGE" else "INCONCLUSIVE"

            record_observation(
                feature=f"{idea}__sig{sig_param}_hold{hold}",
                target=f"{ticker}_per_trade_pnl_pct",
                method="other",
                value=float(median) / 100.0 if median else 0.0,
                n_samples=int(n_trades),
                applies_to=ticker,
                bot="finance",
                strategy_id=idea,
                verdict=verdict,
                metadata={
                    "category": cat,
                    "cagr": cagr,
                    "freq_per_year": freq,
                    "sig_param": str(sig_param),
                    "hold_days": hold,
                    "source": "run_finance_screener.py"
                }
            )
            n += 1
    return n


def process_crypto_screener():
    """crypto-finance/knowledge/screener_runs.jsonl → portfolio."""
    p = ROOT / "crypto-finance" / "knowledge" / "screener_runs.jsonl"
    if not p.exists():
        print(f"  crypto-finance: {p} ei ole, skipataan")
        return 0
    n = 0
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            sid = r.get("strategy_id", "unknown")
            pair = r.get("pair", "unknown")
            params = r.get("params", {})
            n_trades = r.get("n", 0)
            median = r.get("median", 0)
            win = r.get("win", 0)
            freq_pm = r.get("freq_per_month", 0)
            cat = r.get("category", "unknown")

            verdict = "EFFECT_STRONG" if cat == "EDGE_LÖYDETTY" else \
                      "EFFECT_MODERATE" if cat == "HIGH_FREQ" else \
                      "EFFECT_WEAK" if cat == "EDGE_PIILOSSA" else \
                      "NO_EFFECT" if cat == "EI_EDGE" else "INCONCLUSIVE"

            param_str = "_".join(f"{k}{v}" for k, v in sorted(params.items()))[:60]

            record_observation(
                feature=f"{sid}__{param_str}",
                target=f"{pair}_leveraged_pnl_pct",
                method="other",
                value=float(median) / 100.0 if median else 0.0,
                n_samples=int(n_trades),
                applies_to=pair,
                bot="crypto-finance",
                strategy_id=sid,
                verdict=verdict,
                metadata={
                    "category": cat,
                    "win_rate": win,
                    "freq_per_month": freq_pm,
                    "params": params,
                    "source": "run_crypto_screener.py"
                }
            )
            n += 1
    return n


def process_5y_deep_grid():
    """crypto-finance/knowledge/crypto_5y_deep_grid.json → portfolio."""
    p = ROOT / "crypto-finance" / "knowledge" / "crypto_5y_deep_grid.json"
    if not p.exists():
        print(f"  5y-grid: {p} ei ole, skipataan")
        return 0
    data = json.loads(p.read_text(encoding="utf-8"))
    n = 0
    for pair, r in data.items():
        best = r.get("best_overall", {})
        params = best.get("params", {})
        n_trades = best.get("n", 0)
        win = best.get("win", 0)
        median = best.get("median", 0)
        mean = best.get("mean", 0)
        # Strong edge jos N > 100 ja mean > 0
        verdict = "EFFECT_STRONG" if (n_trades > 100 and mean > 0) else "EFFECT_MODERATE"
        record_observation(
            feature=f"{best.get('strategy', 'unknown')}__lev{params.get('lev', 0)}",
            target=f"{pair}_leveraged_pnl_pct",
            method="other",
            value=float(mean) / 100.0,
            n_samples=int(n_trades),
            applies_to=pair,
            bot="crypto-finance",
            strategy_id="5y_deep_grid",
            verdict=verdict,
            metadata={
                "win_rate": win,
                "median": median,
                "params": params,
                "freq_per_year": r.get("freq_per_year", 0),
                "source": "crypto_5y_deep_grid.py"
            }
        )
        n += 1
    return n


def process_kelly_walkforward():
    """v32_kelly_walkforward → portfolio."""
    p = ROOT / "finance" / "data_for_ultraplan" / "v32_kelly_walkforward_results.json"
    if not p.exists(): return 0
    data = json.loads(p.read_text(encoding="utf-8"))
    n = 0
    for label, r in data.items():
        if label == "SPY_DCA": continue
        cagr = r.get("cagr_pct", 0)
        n_trades = r.get("n_trades_total", 0)
        record_observation(
            feature=f"v32_bayesian_kelly_{label.lower().replace(' ', '_')}",
            target="portfolio_cagr_pct",
            method="other",
            value=float(cagr) / 100.0,
            n_samples=int(n_trades),
            applies_to="S&P500_growth",
            bot="finance",
            strategy_id="v32_bayesian_kelly",
            verdict="EFFECT_STRONG" if cagr > 10 and n_trades > 30 else "EFFECT_MODERATE",
            metadata={
                "label": label,
                "max_dd_pct": r.get("max_dd_pct", 0),
                "final_value": r.get("final_value", 0),
                "source": "v32_kelly_walkforward.py"
            }
        )
        n += 1
    return n


def process_per_stock_screener():
    """v32_kelly_per_stock_results → portfolio."""
    p = ROOT / "finance" / "data_for_ultraplan" / "v32_kelly_per_stock_results.json"
    if not p.exists(): return 0
    r = json.loads(p.read_text(encoding="utf-8"))
    record_observation(
        feature="v32_kelly_per_stock_super_screener_3layer",
        target="portfolio_cagr_pct",
        method="other",
        value=float(r.get("cagr_pct", 0)) / 100.0,
        n_samples=int(r.get("n_trades_total", 0)),
        applies_to="S&P500_growth",
        bot="finance",
        strategy_id="b2_per_stock",
        verdict="EFFECT_STRONG",
        metadata={
            "n_signals_total": r.get("n_signals_total", 0),
            "n_avoidance_skip": r.get("n_avoidance_skip", 0),
            "source": "v32_kelly_per_stock_screener.py"
        }
    )
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="Aja kaikki olemassa olevat screen-ajot")
    ap.add_argument("--bot", choices=["finance", "crypto-finance"], default=None)
    args = ap.parse_args()

    print("=" * 60)
    print("SCREENER → FEATURE_PORTFOLIO BACKFILL")
    print("=" * 60)

    total = 0
    if args.backfill or args.bot == "finance":
        n = process_finance_screener()
        print(f"  finance/screener_runs.jsonl → {n} havaintoa")
        total += n
        n = process_kelly_walkforward()
        print(f"  v32_kelly_walkforward → {n} havaintoa")
        total += n
        n = process_per_stock_screener()
        print(f"  v32_per_stock → {n} havaintoa")
        total += n
    if args.backfill or args.bot == "crypto-finance":
        n = process_crypto_screener()
        print(f"  crypto-finance/screener_runs.jsonl → {n} havaintoa")
        total += n
        n = process_5y_deep_grid()
        print(f"  crypto_5y_deep_grid → {n} havaintoa")
        total += n

    print(f"\nTotal kirjattu: {total} havaintoa")

    # Generoi summary
    from feature_portfolio import summarize
    s = summarize()
    print(f"\nFEATURE_PORTFOLIO SUMMARY:")
    print(f"  Total observations: {s['n_observations']}")
    print(f"  By verdict: {s['by_verdict']}")


if __name__ == "__main__":
    main()
