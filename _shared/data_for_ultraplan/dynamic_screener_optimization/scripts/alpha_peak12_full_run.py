# -*- coding: utf-8 -*-
r"""alpha_peak12_full_run.py — sama kun aiemmat ajot mutta kynnys 12% (isompi N).

Käyttäjä 2026-04-28: "voitko sätä samaa niin että alpha 20 peak > 12? Saadaan
isompi otoskoko. Tuota samanlaiset taulukot regime × persona × lifecycle".

Tekee yhdellä ajolla:
  1) trade-listan (alpha_peak_20 > 0.12, hold 400d, OOS 2015-2026, ei filteriä)
  2) tagaa per-trade regime + persona + lifecycle
  3) tuottaa täsmälleen saman class-taulukon kuin alpha_peak_class_table.py
"""
from __future__ import annotations
import sys, json, math, pickle
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path("C:/Users/puros/bots")
sys.path.insert(0, str(ROOT / "_shared/scripts"))
from dynamic_screener import RegimeDetector, PersonalityProfiler, LifecycleClassifier, BreakDetector

ASSET = ROOT / "finance/asset_cache"
ALPHA_PATH = ROOT / "finance/alpha_data/pure_alpha_capm.csv"
V20_CLOSE = ROOT / "finance/data_for_ultraplan/v20_full_prices_close_cleaned.parquet"
V20_VOL = ROOT / "finance/data_for_ultraplan/v20_full_prices_volume.parquet"
OUT = ROOT / "_shared/artifacts/alpha_peak12_filtered"
OUT.mkdir(parents=True, exist_ok=True)

# Strategy params
ALPHA_PEAK_THR = 0.12
ALPHA_WINDOW = 20
HOLD_DAYS = 400
TC_BPS = 0.0015
IS_END = pd.Timestamp("2014-12-31")
OOS_START = pd.Timestamp("2015-01-01")
UNIVERSE_CAP = 600

def load_pkl(name):
    p = ASSET / f"{name}.pkl"
    if not p.exists(): return None
    with open(p, "rb") as f: return pickle.load(f)

def main():
    print(f"=== alpha_peak_20 > 12% full run ({datetime.now().isoformat()}) ===")

    spy = load_pkl("_GSPC")
    vix = load_pkl("_VIX")
    print(f"  SPY/VIX loaded")

    print("[1/4] Loading alpha matrix + universe...")
    alpha = pd.read_csv(ALPHA_PATH, index_col=0, parse_dates=True)
    close_df = pd.read_parquet(V20_CLOSE)
    vol_df = pd.read_parquet(V20_VOL)
    common = sorted(set(alpha.columns) & set(close_df.columns))
    coverage = alpha[common].notna().sum()
    universe = coverage.sort_values(ascending=False).head(UNIVERSE_CAP).index.tolist()
    alpha = alpha[universe]
    close_dict = {tk: close_df[tk].dropna() for tk in universe}
    print(f"  universe: {len(universe)} tickers")

    print("[2/4] Calibrating regime detector on IS (≤ 2014)...")
    spy_is = spy["Close"].loc[:IS_END]
    vix_is = vix["Close"].loc[:IS_END]
    regime_det = RegimeDetector().fit(vix_is, spy_is)
    regime_series = regime_det.predict(vix["Close"], spy["Close"])
    print(f"  VIX q33={regime_det.vix_q33:.2f} q67={regime_det.vix_q67:.2f}")

    print("[3/4] Building yearly persona/lifecycle/break profiles...")
    profiler = PersonalityProfiler(lookback_days=504)
    lifecycle_clf = LifecycleClassifier()
    breakdet = BreakDetector(cusum_threshold=15.0, ph_lambda=200.0,
                              vol_jump_ratio=2.5, dedupe_days=180)
    profiles = {}
    years = list(range(2015, 2027))
    for ti, tk in enumerate(universe):
        if tk not in close_df.columns: continue
        s_full = close_df[tk].dropna()
        v_full = vol_df[tk].dropna() if tk in vol_df.columns else pd.Series(1.0, index=s_full.index)
        if len(s_full) < 378: continue
        for y in years:
            asof = pd.Timestamp(f"{y}-01-01")
            close = s_full.loc[:asof]
            vol = v_full.loc[:asof]
            if len(close) < 378: continue
            try:
                prof = profiler.profile(close, vol, spy["Close"].loc[:asof], end_date=close.index[-1])
                lc = lifecycle_clf.classify(close, end_date=close.index[-1])
                br = breakdet.detect(close)
            except Exception:
                continue
            profiles[(tk, y)] = {
                "persona": prof.get("persona", "unknown"),
                "lifecycle": lc.get("stage", "unknown"),
                "breaks": br.get("breaks", []),
            }
        if (ti + 1) % 100 == 0:
            print(f"  profiled {ti+1}/{len(universe)}")
    print(f"  built {len(profiles)} (ticker,year) profiles")

    print("[4/4] Backtest with alpha_peak_20 > 0.12, hold 400d...")
    alpha_peak = alpha.rolling(ALPHA_WINDOW).sum()
    open_pos = {}
    trades = []
    end = alpha.index.max()
    dates = alpha.loc[OOS_START:end].index

    for d in dates:
        regime_lbl = "calm"
        if d in regime_series.index and pd.notna(regime_series.loc[d]):
            regime_lbl = RegimeDetector.label(int(regime_series.loc[d]))
        # Exits
        to_close = []
        for tk, pos in open_pos.items():
            if (d - pos["entry_date"]).days >= HOLD_DAYS:
                to_close.append(tk)
        for tk in to_close:
            pos = open_pos.pop(tk)
            cl = close_dict.get(tk)
            if cl is None: continue
            future = cl.index[cl.index >= d]
            if len(future) == 0: continue
            d_eff = future[0]
            exit_px = float(cl.loc[d_eff])
            if exit_px <= 0: continue
            gross = exit_px / pos["entry_px"] - 1
            net = gross - 2 * TC_BPS
            trades.append({
                "ticker": tk,
                "entry_date": str(pos["entry_date"].date()),
                "exit_date": str(d_eff.date()),
                "entry_px": pos["entry_px"], "exit_px": exit_px,
                "hold_days": (d_eff - pos["entry_date"]).days,
                "gross_ret": gross, "net_ret": net,
                "alpha_peak_at_entry": pos["alpha_peak"],
                "regime_at_entry": pos["regime_at_entry"],
                "persona": pos["persona"], "lifecycle": pos["lifecycle"],
            })
        # Entries
        try:
            row = alpha_peak.loc[d]
        except KeyError:
            continue
        signals = row[row > ALPHA_PEAK_THR].dropna()
        for tk, ap_val in signals.items():
            if tk in open_pos: continue
            cl = close_dict.get(tk)
            if cl is None: continue
            future = cl.index[cl.index >= d]
            if len(future) == 0: continue
            d_eff = future[0]
            entry_px = float(cl.loc[d_eff])
            if entry_px <= 0: continue
            prof = profiles.get((tk, d.year), {})
            open_pos[tk] = {
                "entry_date": d_eff, "entry_px": entry_px,
                "alpha_peak": float(ap_val),
                "regime_at_entry": regime_lbl,
                "persona": prof.get("persona", "n/a"),
                "lifecycle": prof.get("lifecycle", "n/a"),
            }

    # Force-close end
    for tk, pos in list(open_pos.items()):
        cl = close_dict.get(tk)
        if cl is None: continue
        avail = cl.index[cl.index <= end]
        if len(avail) == 0: continue
        d_eff = avail[-1]
        exit_px = float(cl.loc[d_eff])
        if exit_px <= 0: continue
        gross = exit_px / pos["entry_px"] - 1
        net = gross - 2 * TC_BPS
        trades.append({
            "ticker": tk,
            "entry_date": str(pos["entry_date"].date()),
            "exit_date": str(d_eff.date()),
            "entry_px": pos["entry_px"], "exit_px": exit_px,
            "hold_days": (d_eff - pos["entry_date"]).days,
            "gross_ret": gross, "net_ret": net,
            "alpha_peak_at_entry": pos["alpha_peak"],
            "regime_at_entry": pos["regime_at_entry"],
            "persona": pos["persona"], "lifecycle": pos["lifecycle"],
            "force_closed_at_end": True,
        })

    print(f"  total trades: {len(trades)}")

    # Save trades
    with open(OUT / "trades_unfiltered.jsonl", "w", encoding="utf-8") as f:
        for t in trades: f.write(json.dumps(t, ensure_ascii=False, default=str) + "\n")

    # === Class table with size + vol_ratio + alpha_peak medians ===
    common_idx = close_df.index.intersection(vol_df.index)
    common_cols = sorted(set(close_df.columns) & set(vol_df.columns))
    dvol = (close_df.loc[common_idx, common_cols] * vol_df.loc[common_idx, common_cols])
    dvol_60 = dvol.rolling(60, min_periods=20).mean()

    rows = []
    for t in trades:
        tk = t["ticker"]
        ed = pd.Timestamp(t["entry_date"])
        if tk not in dvol.columns: continue
        avail = dvol.index[dvol.index <= ed]
        if len(avail) < 60: continue
        d = avail[-1]
        size_dv60 = dvol_60.loc[d, tk]
        if pd.isna(size_dv60) or size_dv60 == 0: continue
        signal_dv = dvol.loc[d, tk]
        rows.append({
            "ticker": tk,
            "regime_at_entry": t["regime_at_entry"],
            "persona": t["persona"], "lifecycle": t["lifecycle"],
            "alpha_peak_at_entry": t["alpha_peak_at_entry"],
            "net_ret": t["net_ret"],
            "size_dv60_musd": size_dv60 / 1e6,
            "vol_ratio": signal_dv / size_dv60,
        })
    df = pd.DataFrame(rows)
    print(f"  enriched {len(df)} for class table")

    grp = df.groupby(["regime_at_entry","persona","lifecycle"], dropna=False)
    agg = grp.agg(
        N=("net_ret","count"),
        win_rate=("net_ret", lambda x: (x>0).mean()),
        avg_ret=("net_ret","mean"),
        median_ret=("net_ret","median"),
        median_alpha_peak=("alpha_peak_at_entry","median"),
        median_size_musd=("size_dv60_musd","median"),
        median_vol_ratio=("vol_ratio","median"),
    )
    agg = agg.sort_values("N", ascending=False).round(4)

    # CSV + JSON
    agg.to_csv(OUT / "class_table_full.csv")
    with open(OUT / "class_table_full.json", "w", encoding="utf-8") as f:
        json.dump({"as_of": datetime.now().isoformat(),
                    "alpha_peak_threshold": ALPHA_PEAK_THR,
                    "n_trades": int(len(df)),
                    "triple_class": agg.reset_index().to_dict(orient="records")},
                   f, ensure_ascii=False, indent=2, default=str)

    # Print sub-aggregates
    print(f"\nTotal trades: {len(df)}")
    print("\n=== REGIME alone ===")
    g1 = df.groupby("regime_at_entry").agg(
        N=("net_ret","count"),
        WR=("net_ret", lambda x: (x>0).mean()),
        avg=("net_ret","mean"), med=("net_ret","median"),
        medAlphaPeak=("alpha_peak_at_entry","median"),
        medSizeM=("size_dv60_musd","median"),
        medVolR=("vol_ratio","median"),
    ).sort_values("N", ascending=False).round(4)
    print(g1.to_string())

    print("\n=== PERSONA alone ===")
    g2 = df.groupby("persona").agg(
        N=("net_ret","count"),
        WR=("net_ret", lambda x: (x>0).mean()),
        avg=("net_ret","mean"), med=("net_ret","median"),
        medAlphaPeak=("alpha_peak_at_entry","median"),
        medSizeM=("size_dv60_musd","median"),
        medVolR=("vol_ratio","median"),
    ).sort_values("N", ascending=False).round(4)
    print(g2.to_string())

    print("\n=== LIFECYCLE alone ===")
    g3 = df.groupby("lifecycle").agg(
        N=("net_ret","count"),
        WR=("net_ret", lambda x: (x>0).mean()),
        avg=("net_ret","mean"), med=("net_ret","median"),
        medAlphaPeak=("alpha_peak_at_entry","median"),
        medSizeM=("size_dv60_musd","median"),
        medVolR=("vol_ratio","median"),
    ).sort_values("N", ascending=False).round(4)
    print(g3.to_string())

    # Print full table sorted by regime then N
    print(f"\n=== TRIPLE class table (alpha_peak > 12%, sorted regime, N) ===")
    full = agg.reset_index().sort_values(["regime_at_entry","N"], ascending=[True, False])
    for reg in ["calm","crisis","normal"]:
        sub = full[full["regime_at_entry"]==reg]
        if len(sub) == 0: continue
        print(f"\n--- {reg.upper()} (N total {sub.N.sum()}) ---")
        for _, r in sub.iterrows():
            print(f"  {str(r.persona)[:9]:9s} {str(r.lifecycle)[:18]:18s} N={int(r.N):4d}  "
                  f"WR={r.win_rate*100:5.1f}%  avg={r.avg_ret*100:+6.1f}%  med={r.median_ret*100:+6.1f}%  "
                  f"αPeak_med={r.median_alpha_peak*100:5.2f}%  size_M$/d={r.median_size_musd:6.1f}  "
                  f"volR={r.median_vol_ratio:.2f}")

    print(f"\nSaved: {OUT}/class_table_full.csv  + .json + trades_unfiltered.jsonl")

if __name__ == "__main__":
    main()
