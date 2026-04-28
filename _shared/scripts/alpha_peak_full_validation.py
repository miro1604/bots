# -*- coding: utf-8 -*-
r"""alpha_peak_full_validation.py — täysi 2026-blueprint-validointi olemassa oleville trade-listoille.

Käyttäjä huomautti 2026-04-28: pelkkä Sharpe ei riitä uusissa simulaatio-opeissamme.
Pakollinen tarkistuslista (quant_validation_v2):
  PSR (Bailey-de Prado: skew + kurt -korjattu)
  DSR (Deflated SR: multi-test bias)
  MinTRL (kuinka monta kauppaa riittää)
  Path-dependency Max-DD bootstrap (sequence-luck-testi)
  CPCV-walk-forward (purged + embargo)
  Sample size required (Z-pohjainen)

Lukee:
  artifacts/alpha_peak_filtered/trades_filtered.jsonl
  artifacts/alpha_peak_filtered/trades_unfiltered.jsonl

Tulokset:
  artifacts/alpha_peak_filtered/full_validation.json + stdout-vertailu
"""
from __future__ import annotations
import sys, json, math
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path("C:/Users/puros/bots")
sys.path.insert(0, str(ROOT / "_shared/scripts"))
import quant_validation_v2 as qv
import feature_portfolio as fp

OUT = ROOT / "_shared/artifacts/alpha_peak_filtered"

def load_trades(path: Path) -> list[dict]:
    if not path.exists(): return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]

def validate(trades: list[dict], label: str, n_trials_in_search: int) -> dict:
    if not trades:
        return {"label": label, "n_trades": 0}
    df = pd.DataFrame(trades)
    # Sort by exit date for sequential bootstrap
    df["exit_dt"] = pd.to_datetime(df["exit_date"])
    df = df.sort_values("exit_dt").reset_index(drop=True)
    rets = df["net_ret_sized"].values.astype(float)
    n = len(rets)

    sr = float(rets.mean() / rets.std()) if rets.std() > 0 else 0.0
    # Annualize using avg holding period (in trading days)
    avg_hold_days = float(df["hold_days"].mean())
    sr_ann = sr * math.sqrt(252 / max(1.0, avg_hold_days))

    # PSR & DSR
    psr = qv.probabilistic_sharpe_ratio(rets, sr_benchmark=0.0)
    dsr = qv.deflated_sharpe_ratio(rets, n_trials=n_trials_in_search, sr_benchmark=0.0)

    # MinTRL
    from scipy import stats as scistats
    skew_v = float(scistats.skew(rets))
    kurt_v = float(scistats.kurtosis(rets))
    min_trl = qv.minimum_track_record_length(sr, sr_benchmark=0.0,
                                               skew=skew_v, kurt=kurt_v, conf=0.95)

    # Sample size required for win-rate
    win_rate = float((rets > 0).mean())
    n_req = qv.sample_size_required(win_rate, conf=0.95, margin_of_error=0.05)

    # Path dependency Max-DD bootstrap
    pd_test = qv.bootstrap_path_dependency(rets, n_iter=2000, seed=42)

    # CPCV with purging matched to hold horizon (avg)
    n_splits = 6
    cpcv_folds = qv.cpcv_split(n_obs=n, n_splits=n_splits, n_test_groups=2,
                                hold_horizon=int(avg_hold_days), embargo_pct=0.01)
    fold_means = []
    fold_psrs = []
    for train_idx, test_idx in cpcv_folds:
        if len(test_idx) < 30: continue
        test_rets = rets[test_idx]
        fold_means.append(float(test_rets.mean()))
        fold_psrs.append(qv.probabilistic_sharpe_ratio(test_rets, sr_benchmark=0.0))
    cpcv_mean_of_means = float(np.mean(fold_means)) if fold_means else None
    cpcv_min_psr = float(np.min(fold_psrs)) if fold_psrs else None
    cpcv_pct_psr_gt_80 = float(np.mean([p > 0.80 for p in fold_psrs])) if fold_psrs else None

    # Verdict per 2026 blueprint
    edge_validated = (
        psr > 0.95 and
        dsr > 0.95 and
        n >= min_trl and
        not pd_test.get("alpha_path_dependent", False) and
        (cpcv_min_psr or 0) > 0.50  # at least mediocre in worst fold
    )

    return {
        "label": label,
        "n_trades": int(n),
        "avg_hold_days": avg_hold_days,
        "sharpe_per_trade": sr,
        "sharpe_annualized_via_hold": sr_ann,
        "skew": skew_v,
        "kurt": kurt_v,
        "win_rate": win_rate,
        "psr": float(psr),
        "dsr": float(dsr),
        "min_trl_trades": float(min_trl) if math.isfinite(min_trl) else None,
        "n_required_for_winrate_95ci_5pct": n_req,
        "n_observed_vs_required": f"{n}/{n_req}",
        "path_dependency": {
            "original_max_dd": pd_test.get("original_max_dd"),
            "median_bootstrap_max_dd": pd_test.get("median_bootstrap_dd"),
            "p_better_than_random": pd_test.get("p_better_than_random"),
            "alpha_path_dependent": pd_test.get("alpha_path_dependent"),
        },
        "cpcv": {
            "n_splits": n_splits, "n_folds_used": len(fold_means),
            "mean_of_test_means": cpcv_mean_of_means,
            "min_psr_across_folds": cpcv_min_psr,
            "pct_folds_psr_gt_80": cpcv_pct_psr_gt_80,
        },
        "edge_validated_2026_blueprint": edge_validated,
    }

def main():
    print(f"=== Alpha-peak full 2026-blueprint validation ({datetime.now().isoformat()}) ===")
    tu = load_trades(OUT / "trades_unfiltered.jsonl")
    tf = load_trades(OUT / "trades_filtered.jsonl")
    print(f"  Loaded {len(tu)} unfiltered + {len(tf)} filtered trades")

    # n_trials = 2 (we tested filter ON vs OFF, no parameter sweep)
    # Conservative: use n_trials = 10 to bake in implicit hyperparameter exploration
    N_TRIALS = 10

    val_u = validate(tu, "unfiltered", n_trials_in_search=N_TRIALS)
    val_f = validate(tf, "filtered", n_trials_in_search=N_TRIALS)

    # Print comparison
    print("\n" + "="*82)
    print("FULL 2026-BLUEPRINT VALIDATION  (n_trials_in_search=10 conservative)")
    print("="*82)
    fields = [
        ("n_trades", lambda v: f"{v}"),
        ("win_rate", lambda v: f"{v*100:.2f}%"),
        ("sharpe_per_trade", lambda v: f"{v:.4f}"),
        ("sharpe_annualized_via_hold", lambda v: f"{v:.3f}"),
        ("skew", lambda v: f"{v:+.3f}"),
        ("kurt", lambda v: f"{v:+.2f}"),
        ("psr", lambda v: f"{v*100:.2f}%"),
        ("dsr", lambda v: f"{v*100:.2f}%"),
        ("min_trl_trades", lambda v: f"{v:.0f}" if v else "inf"),
        ("n_observed_vs_required", lambda v: str(v)),
        ("edge_validated_2026_blueprint", lambda v: "PASS" if v else "FAIL"),
    ]
    for key, fmt in fields:
        u = val_u.get(key); f = val_f.get(key)
        u_s = fmt(u) if u is not None else "n/a"
        f_s = fmt(f) if f is not None else "n/a"
        print(f"  {key:38s}  unfiltered={u_s:>14s}   filtered={f_s:>14s}")

    print("\n  Path-dependency (Max-DD bootstrap, 2000 iter):")
    pdu = val_u.get("path_dependency", {}); pdf = val_f.get("path_dependency", {})
    print(f"    original_max_dd               unfiltered={pdu.get('original_max_dd',0)*100:+.1f}%   filtered={pdf.get('original_max_dd',0)*100:+.1f}%")
    print(f"    median_bootstrap_max_dd       unfiltered={pdu.get('median_bootstrap_max_dd',0)*100:+.1f}%   filtered={pdf.get('median_bootstrap_max_dd',0)*100:+.1f}%")
    print(f"    p_better_than_random          unfiltered={pdu.get('p_better_than_random',0):.3f}     filtered={pdf.get('p_better_than_random',0):.3f}")
    print(f"    alpha_path_dependent          unfiltered={str(pdu.get('alpha_path_dependent')):>14s}   filtered={str(pdf.get('alpha_path_dependent')):>14s}")

    print("\n  CPCV (6 splits, 2 test-groups, purging=avg_hold, 1% embargo):")
    cu = val_u.get("cpcv", {}); cf = val_f.get("cpcv", {})
    print(f"    n_folds_used                  unfiltered={cu.get('n_folds_used','-'):>14}   filtered={cf.get('n_folds_used','-'):>14}")
    print(f"    mean_of_test_means            unfiltered={(cu.get('mean_of_test_means') or 0)*100:+.2f}%      filtered={(cf.get('mean_of_test_means') or 0)*100:+.2f}%")
    print(f"    min_psr_across_folds          unfiltered={(cu.get('min_psr_across_folds') or 0)*100:.1f}%       filtered={(cf.get('min_psr_across_folds') or 0)*100:.1f}%")
    print(f"    pct_folds_psr_gt_80           unfiltered={(cu.get('pct_folds_psr_gt_80') or 0)*100:.0f}%        filtered={(cf.get('pct_folds_psr_gt_80') or 0)*100:.0f}%")

    out_json = {"as_of": datetime.now(timezone.utc).isoformat(), "n_trials_search": N_TRIALS,
                 "unfiltered": val_u, "filtered": val_f}
    with open(OUT / "full_validation.json", "w", encoding="utf-8") as f:
        json.dump(out_json, f, ensure_ascii=False, indent=2, default=str)

    # Update tieto-portfolio with the corrected metrics
    try:
        for variant_label, val in [("unfiltered", val_u), ("filtered", val_f)]:
            if val.get("n_trades", 0) == 0: continue
            fp.record_observation(
                feature=f"alpha_peak_20_{variant_label}_blueprint_validated",
                target="cumulative_pool_pnl",
                method="event_study",
                value=val.get("sharpe_per_trade", 0),
                n_samples=val["n_trades"],
                applies_to="sp500_growth_top600",
                bot="finance",
                strategy_id=f"alpha_peak20_hold400_{variant_label}",
                target_class="portfolio_metric", domain="systematic",
                p_value=(1 - val.get("psr", 0)) if val.get("psr") else None,
                confidence_pct=(val.get("dsr", 0) * 100) if val.get("dsr") else None,
                replication_count=val.get("cpcv", {}).get("n_folds_used", 1),
                metadata={
                    "psr": val.get("psr"), "dsr": val.get("dsr"),
                    "min_trl_trades": val.get("min_trl_trades"),
                    "skew": val.get("skew"), "kurt": val.get("kurt"),
                    "max_dd": val.get("path_dependency", {}).get("original_max_dd"),
                    "path_dependent": val.get("path_dependency", {}).get("alpha_path_dependent"),
                    "cpcv_min_psr": val.get("cpcv", {}).get("min_psr_across_folds"),
                    "cpcv_pct_folds_psr_gt_80": val.get("cpcv", {}).get("pct_folds_psr_gt_80"),
                    "edge_validated_2026_blueprint": val.get("edge_validated_2026_blueprint"),
                    "source": "alpha_peak_full_validation_2026_04_28",
                }
            )
    except Exception as e:
        print(f"  [warn] portfolio record failed: {e}")

    print(f"\nSaved: {OUT / 'full_validation.json'}")

if __name__ == "__main__":
    main()
