# -*- coding: utf-8 -*-
r"""alpha_peak_filtered_strategy.py — alpha_peak_20 > 15% + dynaaminen filtteri.

Käyttäjän mandaatti 2026-04-28:
  Strategia: alpha_peak_20 > 0.15 → osto, hold 400d, ei muita signaaleja.
  Universumi: laaja (840 tickeriä, S&P + growth, alpha_data/pure_alpha_capm.csv).
  Filtteri: dynamic_screener (regime + persona + lifecycle + breaks).
  Punainen lanka: filterin parametrit perustuvat A PRIORI -logiikkaan, ei
  optimointiin OOS-datalla. Tämä takaa että samoja sääntöjä voi soveltaa myös
  tulevaan dataan ilman jälkiviisauden harhaa.

A priori -säännöt (kestävällä pohjalla):
  - Persona ⊆ {balanced, reactive}     (skip herd: epävakaa, reserved: mean-revert)
  - Lifecycle ⊆ {mature, growth}       (alpha_peak on momentum-tyyppi → vaatii kasvuvaihetta)
  - Ei recent break (60d blackout)     (älä osta osaketta jonka identiteetti on muuttumassa)
  - Crisis-regiimissä position koko ½  (älä leikkaa edge:ä pois, vaan vipu pois)

Walk-forward:
  - IS-kalibraatio  2005-2014 → VIX q33/q67 + GMM(SPY-vol+DD) (vain regime, ei strategia-paramit!)
  - OOS-testi       2015-2026 → todellinen kestotesti
  - Vertailu: sama strategia ILMAN filtteriä → osoittaa filterin lisäarvon

Tulokset:
  artifacts/alpha_peak_filtered/trades_filtered.jsonl
  artifacts/alpha_peak_filtered/trades_unfiltered.jsonl
  artifacts/alpha_peak_filtered/comparison.json
  + tieto-portfolio: pooli-tason + per-ticker-tason havainnot
"""
from __future__ import annotations
import sys, os, json, math, pickle
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path("C:/Users/puros/bots")
sys.path.insert(0, str(ROOT / "_shared/scripts"))
import feature_portfolio as fp
import quant_validation_v2 as qv
from dynamic_screener import (
    RegimeDetector, PersonalityProfiler, LifecycleClassifier, BreakDetector
)

ASSET = ROOT / "finance/asset_cache"
ALPHA_PATH = ROOT / "finance/alpha_data/pure_alpha_capm.csv"
V20_CLOSE = ROOT / "finance/data_for_ultraplan/v20_full_prices_close_cleaned.parquet"
V20_VOL   = ROOT / "finance/data_for_ultraplan/v20_full_prices_volume.parquet"
OUT = ROOT / "_shared/artifacts/alpha_peak_filtered"
OUT.mkdir(parents=True, exist_ok=True)

# Strategy params (FIXED — not optimized)
ALPHA_PEAK_THR = 0.15      # cumulative 20d alpha > 15%
ALPHA_WINDOW = 20
HOLD_DAYS = 400
TC_BPS = 0.0015            # 15 bps per side (round-trip 30 bps)

# Walk-forward
IS_END = pd.Timestamp("2014-12-31")
OOS_START = pd.Timestamp("2015-01-01")

# Filter parameters (A PRIORI — based on logic, not OOS optimization)
ALLOWED_PERSONAS = {"balanced", "reactive"}
ALLOWED_LIFECYCLE = {"mature", "growth"}
RECENT_BREAK_DAYS = 60
CRISIS_SIZE_FACTOR = 0.5

# Universe size cap (for compute speed; broad enough)
UNIVERSE_CAP = 600

# ============================================================================
# Data loading
# ============================================================================

def load_alpha() -> pd.DataFrame:
    a = pd.read_csv(ALPHA_PATH, index_col=0, parse_dates=True)
    return a

def load_close_for(ticker: str) -> Optional[pd.DataFrame]:
    p = ASSET / f"{ticker}.pkl"
    if not p.exists(): return None
    try:
        with open(p, "rb") as f:
            df = pickle.load(f)
        if not isinstance(df, pd.DataFrame) or "Close" not in df.columns: return None
        return df
    except Exception:
        return None

def load_spy_vix():
    spy = load_close_for("_GSPC")
    vix = load_close_for("_VIX")
    return spy, vix

# ============================================================================
# Yearly cached profile builder (per ticker per year — saves heavy recomputation)
# ============================================================================

def build_yearly_profiles(tickers: list[str], spy_close: pd.Series,
                           close_df: pd.DataFrame, vol_df: pd.DataFrame,
                           years: list[int], min_history: int = 504) -> dict:
    """Per (ticker, year_start_date) → {persona, lifecycle, breaks_until_date}.

    Käytetään VAIN dataa siihen vuoden alkuun asti — kestävyysperiaate.
    """
    profiler = PersonalityProfiler(lookback_days=504)
    lifecycle = LifecycleClassifier()
    breakdet = BreakDetector(cusum_threshold=15.0, ph_lambda=200.0,
                              vol_jump_ratio=2.5, dedupe_days=180)
    profiles = {}
    for ti, tk in enumerate(tickers):
        if tk not in close_df.columns: continue
        s_full = close_df[tk].dropna()
        v_full = vol_df[tk].dropna() if tk in vol_df.columns else pd.Series(1.0, index=s_full.index)
        if len(s_full) < min_history: continue
        for y in years:
            asof = pd.Timestamp(f"{y}-01-01")
            close = s_full.loc[:asof]
            vol = v_full.loc[:asof]
            if len(close) < min_history: continue
            try:
                prof = profiler.profile(close, vol, spy_close.loc[:asof], end_date=close.index[-1])
                lc = lifecycle.classify(close, end_date=close.index[-1])
                br = breakdet.detect(close)
            except Exception:
                continue
            profiles[(tk, y)] = {
                "persona": prof.get("persona", "unknown"),
                "lifecycle": lc.get("stage", "unknown"),
                "breaks": br.get("breaks", []),
                "ivol": prof.get("metrics", {}).get("ivol_ann"),
                "beta": prof.get("metrics", {}).get("beta"),
            }
        if (ti + 1) % 100 == 0:
            print(f"  profiled {ti+1}/{len(tickers)} tickers, {len(profiles)} profiles so far")
    return profiles

# ============================================================================
# Backtest
# ============================================================================

def passes_filter(profile: dict, day: pd.Timestamp, regime_label: str) -> tuple[bool, float, str]:
    """Returns (passes, size_factor, reason)."""
    if profile is None:
        return False, 0.0, "no_profile"
    if profile["persona"] not in ALLOWED_PERSONAS:
        return False, 0.0, f"persona={profile['persona']}"
    if profile["lifecycle"] not in ALLOWED_LIFECYCLE:
        return False, 0.0, f"lifecycle={profile['lifecycle']}"
    # Recent break check
    breaks = profile.get("breaks", [])
    if breaks:
        last_break = pd.Timestamp(breaks[-1])
        if (day - last_break).days < RECENT_BREAK_DAYS:
            return False, 0.0, f"recent_break_{(day-last_break).days}d"
    # Size factor for crisis regime
    size_factor = CRISIS_SIZE_FACTOR if regime_label == "crisis" else 1.0
    return True, size_factor, "OK"

def backtest(alpha: pd.DataFrame, close_dict: dict, spy_close: pd.Series,
             regime_series: pd.Series, profiles: dict,
             start: pd.Timestamp, end: pd.Timestamp,
             apply_filter: bool, label: str) -> tuple[list, dict]:
    """Walk through dates; on each day check all tickers for alpha_peak_20 > 15%.
    If signal AND (filter passes if apply_filter), enter long, hold HOLD_DAYS bars.
    Returns (trades, summary).
    """
    trades = []
    # Compute alpha_peak_20 = rolling 20d sum of daily alpha (cumulative alpha)
    print(f"  computing alpha_peak_{ALPHA_WINDOW} for {alpha.shape[1]} tickers...")
    alpha_peak = alpha.rolling(ALPHA_WINDOW).sum()

    # Open positions: dict ticker -> (entry_idx, entry_date, entry_price, size_factor)
    open_positions = {}
    dates = alpha.loc[start:end].index

    # Stat tracking
    n_signals_raw = 0
    n_signals_passed = 0
    n_filter_rejects = {}
    crisis_size_count = 0

    for d in dates:
        regime_lbl = "calm"
        if d in regime_series.index and pd.notna(regime_series.loc[d]):
            regime_lbl = RegimeDetector.label(int(regime_series.loc[d]))

        # 1) Check exits — close any positions held HOLD_DAYS
        to_close = []
        for tk, pos in open_positions.items():
            entry_date = pos["entry_date"]
            held = (d - entry_date).days
            if held >= HOLD_DAYS:
                to_close.append(tk)
        for tk in to_close:
            pos = open_positions.pop(tk)
            cl = close_dict.get(tk)
            if cl is None: continue
            if d not in cl.index:
                # Use nearest available
                future = cl.index[cl.index >= d]
                if len(future) == 0: continue
                d_eff = future[0]
            else:
                d_eff = d
            exit_px = float(cl.loc[d_eff])
            if exit_px <= 0 or not np.isfinite(exit_px): continue
            gross = exit_px / pos["entry_px"] - 1
            net = gross - 2 * TC_BPS  # round-trip cost
            net_sized = net * pos["size_factor"]
            trades.append({
                "ticker": tk,
                "entry_date": str(pos["entry_date"].date()),
                "exit_date": str(d_eff.date()),
                "entry_px": pos["entry_px"],
                "exit_px": exit_px,
                "hold_days": held,
                "gross_ret": gross,
                "net_ret": net,
                "net_ret_sized": net_sized,
                "size_factor": pos["size_factor"],
                "alpha_peak_at_entry": pos["alpha_peak"],
                "regime_at_entry": pos["regime_at_entry"],
                "persona": pos.get("persona", "n/a"),
                "lifecycle": pos.get("lifecycle", "n/a"),
            })

        # 2) Check entries
        try:
            row = alpha_peak.loc[d]
        except KeyError:
            continue
        # Tickers with signal today
        signals = row[row > ALPHA_PEAK_THR].dropna()
        for tk, ap_val in signals.items():
            n_signals_raw += 1
            if tk in open_positions:
                continue  # already long
            if apply_filter:
                year = d.year
                profile = profiles.get((tk, year))
                ok, sf, reason = passes_filter(profile, d, regime_lbl)
                if not ok:
                    n_filter_rejects[reason] = n_filter_rejects.get(reason, 0) + 1
                    continue
            else:
                sf = 1.0
                profile = profiles.get((tk, d.year)) or {}

            cl = close_dict.get(tk)
            if cl is None: continue
            if d not in cl.index:
                future = cl.index[cl.index >= d]
                if len(future) == 0: continue
                d_eff = future[0]
            else:
                d_eff = d
            entry_px = float(cl.loc[d_eff])
            if entry_px <= 0 or not np.isfinite(entry_px): continue

            n_signals_passed += 1
            if regime_lbl == "crisis": crisis_size_count += 1
            open_positions[tk] = {
                "entry_date": d_eff,
                "entry_px": entry_px,
                "alpha_peak": float(ap_val),
                "size_factor": sf,
                "regime_at_entry": regime_lbl,
                "persona": (profile or {}).get("persona", "n/a"),
                "lifecycle": (profile or {}).get("lifecycle", "n/a"),
            }

    # Force-close any still-open at end (with whatever exit we can compute)
    for tk, pos in list(open_positions.items()):
        cl = close_dict.get(tk)
        if cl is None: continue
        avail = cl.index[cl.index <= end]
        if len(avail) == 0: continue
        d_eff = avail[-1]
        exit_px = float(cl.loc[d_eff])
        if exit_px <= 0: continue
        gross = exit_px / pos["entry_px"] - 1
        net = gross - 2 * TC_BPS
        net_sized = net * pos["size_factor"]
        trades.append({
            "ticker": tk,
            "entry_date": str(pos["entry_date"].date()),
            "exit_date": str(d_eff.date()),
            "entry_px": pos["entry_px"], "exit_px": exit_px,
            "hold_days": (d_eff - pos["entry_date"]).days,
            "gross_ret": gross, "net_ret": net, "net_ret_sized": net_sized,
            "size_factor": pos["size_factor"],
            "alpha_peak_at_entry": pos["alpha_peak"],
            "regime_at_entry": pos["regime_at_entry"],
            "persona": pos.get("persona", "n/a"),
            "lifecycle": pos.get("lifecycle", "n/a"),
            "force_closed_at_end": True,
        })

    if not trades:
        return [], {"n_trades": 0, "label": label}

    df = pd.DataFrame(trades)
    rets = df["net_ret_sized"].values
    # Approx CAGR — use trade-mean and HOLD_DAYS scaling (assumes constant deployment)
    mean_per_trade = float(rets.mean())
    median_per_trade = float(np.median(rets))
    win_rate = float((rets > 0).mean())
    sharpe = (mean_per_trade / rets.std()) * math.sqrt(252 / HOLD_DAYS) if rets.std() > 0 else 0.0
    psr = qv.probabilistic_sharpe_ratio(rets, sr_benchmark=0.0)
    # Per-ticker decomposition
    by_tk = df.groupby("ticker")["net_ret_sized"].agg(["count", "mean", "median", "sum"])
    by_tk = by_tk.sort_values("sum", ascending=False)
    top_tickers = by_tk.head(15).reset_index().to_dict(orient="records")

    # Regime distribution at entry
    regime_dist = df["regime_at_entry"].value_counts().to_dict()
    persona_dist = df["persona"].value_counts().to_dict()
    lifecycle_dist = df["lifecycle"].value_counts().to_dict()

    summary = {
        "label": label,
        "n_trades": int(len(df)),
        "n_unique_tickers": int(df["ticker"].nunique()),
        "n_signals_raw": n_signals_raw,
        "n_signals_passed": n_signals_passed,
        "filter_rejects": n_filter_rejects,
        "crisis_size_count": crisis_size_count,
        "mean_per_trade": mean_per_trade,
        "median_per_trade": median_per_trade,
        "win_rate": win_rate,
        "sharpe_annualized": sharpe,
        "psr": float(psr),
        "regime_dist_at_entry": regime_dist,
        "persona_dist": persona_dist,
        "lifecycle_dist": lifecycle_dist,
        "top_tickers": top_tickers,
        "period_start": str(start.date()),
        "period_end": str(end.date()),
    }
    return trades, summary

# ============================================================================
# Main
# ============================================================================

def main():
    print(f"=== Alpha-peak filtered strategy ({datetime.now().isoformat()}) ===")
    print(f"  Signal: alpha_peak_{ALPHA_WINDOW} > {ALPHA_PEAK_THR*100:.0f}%, hold {HOLD_DAYS}d")
    print(f"  Filter (A PRIORI): persona ⊆ {ALLOWED_PERSONAS}, lifecycle ⊆ {ALLOWED_LIFECYCLE}, "
          f"recent_break_blackout {RECENT_BREAK_DAYS}d, crisis_size {CRISIS_SIZE_FACTOR}x")
    print(f"  Walk-forward: IS calibration <= {IS_END.date()}, OOS test >= {OOS_START.date()}")

    spy, vix = load_spy_vix()
    if spy is None or vix is None:
        print("ERROR: missing SPY or VIX"); return

    print(f"\n[1/4] Loading alpha matrix + universe...")
    alpha = load_alpha()
    close_df = pd.read_parquet(V20_CLOSE)
    vol_df = pd.read_parquet(V20_VOL)
    print(f"  alpha_capm: {alpha.shape}, span: {alpha.index.min().date()} -> {alpha.index.max().date()}")
    print(f"  v20_full close: {close_df.shape}, span: {close_df.index.min().date()} -> {close_df.index.max().date()}")

    # Universe = intersection of alpha and v20_full, capped by alpha-coverage
    common = sorted(set(alpha.columns) & set(close_df.columns))
    coverage = alpha[common].notna().sum()
    universe = coverage.sort_values(ascending=False).head(UNIVERSE_CAP).index.tolist()
    print(f"  universe (intersection alpha ∩ v20): {len(universe)}")

    # Build close_dict from v20 parquet
    close_dict = {tk: close_df[tk].dropna() for tk in universe}
    alpha = alpha[universe]

    print(f"\n[3/4] Calibrating regime detector on IS period (<= {IS_END.date()})...")
    spy_is = spy["Close"].loc[:IS_END]
    vix_is = vix["Close"].loc[:IS_END]
    regime_det = RegimeDetector().fit(vix_is, spy_is)
    print(f"  IS calibration: VIX q33={regime_det.vix_q33:.2f}, q67={regime_det.vix_q67:.2f}")
    print(f"  GMM means (vol, dd60): {regime_det.gmm_means_}")
    # Predict on full series
    regime_series = regime_det.predict(vix["Close"], spy["Close"])
    rs_oos = regime_series.loc[OOS_START:].dropna().astype(int)
    print(f"  OOS regime distribution: calm={(rs_oos==0).mean()*100:.1f}% "
          f"normal={(rs_oos==1).mean()*100:.1f}% crisis={(rs_oos==2).mean()*100:.1f}%")

    print(f"\n[4/4] Building yearly persona/lifecycle/break profiles...")
    years = list(range(2015, 2027))
    profiles = build_yearly_profiles(universe, spy["Close"], close_df, vol_df,
                                      years, min_history=378)  # 1.5 yr min history
    print(f"  built {len(profiles)} (ticker,year) profiles")

    # ===== Run two backtests: filtered vs unfiltered =====
    print(f"\n=== BACKTEST: UNFILTERED (baseline) ===")
    trades_u, summary_u = backtest(alpha, close_dict, spy["Close"], regime_series,
                                     profiles, OOS_START, alpha.index.max(),
                                     apply_filter=False, label="unfiltered")
    print(f"\n=== BACKTEST: FILTERED (dynamic_screener) ===")
    trades_f, summary_f = backtest(alpha, close_dict, spy["Close"], regime_series,
                                     profiles, OOS_START, alpha.index.max(),
                                     apply_filter=True, label="filtered")

    # Save trades
    with open(OUT / "trades_unfiltered.jsonl", "w", encoding="utf-8") as f:
        for t in trades_u: f.write(json.dumps(t, ensure_ascii=False, default=str) + "\n")
    with open(OUT / "trades_filtered.jsonl", "w", encoding="utf-8") as f:
        for t in trades_f: f.write(json.dumps(t, ensure_ascii=False, default=str) + "\n")

    # Compare
    cmp = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "strategy": f"alpha_peak_{ALPHA_WINDOW}>{ALPHA_PEAK_THR}_hold{HOLD_DAYS}d",
        "OOS_period": f"{OOS_START.date()} -> {alpha.index.max().date()}",
        "universe_size": len(universe),
        "unfiltered": summary_u,
        "filtered": summary_f,
    }
    with open(OUT / "comparison.json", "w", encoding="utf-8") as f:
        json.dump(cmp, f, ensure_ascii=False, indent=2, default=str)

    # Print comparison
    print("\n" + "="*78)
    print("COMPARISON: filter vs no-filter")
    print("="*78)
    fmt = lambda v: f"{v*100:+.2f}%" if isinstance(v, float) else str(v)
    for k in ["n_trades", "n_unique_tickers", "win_rate", "mean_per_trade",
              "median_per_trade", "sharpe_annualized", "psr"]:
        u_val = summary_u.get(k); f_val = summary_f.get(k)
        if isinstance(u_val, float):
            print(f"  {k:25s}  unfiltered={fmt(u_val):>12s}   filtered={fmt(f_val):>12s}")
        else:
            print(f"  {k:25s}  unfiltered={u_val!s:>12s}   filtered={f_val!s:>12s}")
    print(f"\n  Filter rejected ({sum(summary_f['filter_rejects'].values())} signals):")
    for reason, n in sorted(summary_f["filter_rejects"].items(), key=lambda x: -x[1])[:8]:
        print(f"    {reason:30s}  {n}")
    print(f"\n  Filtered top 5 tickers by sum-pnl:")
    for r in summary_f.get("top_tickers", [])[:5]:
        print(f"    {r['ticker']:6s}  N={r['count']:3d}  mean={r['mean']*100:+.2f}%  sum={r['sum']*100:+.0f}%")

    # === Tieto-portfolio ===
    try:
        # Pool-level filtered observation
        if summary_f["n_trades"] > 0:
            fp.record_observation(
                feature="alpha_peak_20_filtered",
                target="cumulative_pool_pnl",
                method="event_study",
                value=summary_f["mean_per_trade"],
                n_samples=summary_f["n_trades"],
                applies_to=f"sp500_growth_top{len(universe)}",
                bot="finance",
                strategy_id=f"alpha_peak{ALPHA_WINDOW}gt{int(ALPHA_PEAK_THR*100)}_hold{HOLD_DAYS}_dynfilter",
                target_class="portfolio_metric", domain="systematic",
                p_value=(1 - summary_f["psr"]) if summary_f["psr"] else None,
                confidence_pct=summary_f["psr"] * 100 if summary_f["psr"] else None,
                replication_count=summary_f["n_unique_tickers"],
                period_start=summary_f["period_start"],
                period_end=summary_f["period_end"],
                participants=universe[:50],
                metadata={
                    "filter": "regime+persona+lifecycle+break (A PRIORI)",
                    "win_rate": summary_f["win_rate"],
                    "sharpe": summary_f["sharpe_annualized"],
                    "regime_dist": summary_f["regime_dist_at_entry"],
                    "persona_dist": summary_f["persona_dist"],
                    "lifecycle_dist": summary_f["lifecycle_dist"],
                    "filter_reject_count": sum(summary_f["filter_rejects"].values()),
                    "vs_unfiltered_lift_in_mean":
                        summary_f["mean_per_trade"] - summary_u["mean_per_trade"]
                        if summary_u["n_trades"] > 0 else None,
                    "source": "alpha_peak_filtered_2026_04_28",
                }
            )
        # Per-ticker decomposition (from filtered trades)
        if trades_f:
            decomp_input = [{"ticker": t["ticker"], "pnl_pct": t["net_ret_sized"] * 100,
                             "entry_ts": t["entry_date"], "exit_ts": t["exit_date"]}
                            for t in trades_f]
            fp.record_per_ticker_decomposition(
                trades=decomp_input,
                strategy_id=f"alpha_peak{ALPHA_WINDOW}_hold{HOLD_DAYS}_filtered",
                bot="finance",
                target_class="stock_return", domain="systematic_equity",
                min_trades_per_ticker=3,
                metadata={"source": "alpha_peak_filtered_2026_04_28",
                          "filter": "regime+persona+lifecycle+break"}
            )
    except Exception as e:
        print(f"  [warn] portfolio record failed: {e}")

    print(f"\nArtifacts: {OUT}")

if __name__ == "__main__":
    main()
