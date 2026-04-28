# -*- coding: utf-8 -*-
r"""alpha_peak_class_table.py — täyslaajuinen luokkataulukko käyttäjän tarkalla speclla.

Käyttäjän pyyntö 2026-04-28:
  Listaa KAIKKI regime × persona × lifecycle -kombinaatiot:
    - N (kauppoja)
    - mediaani yrityksen koko oston hetkellä ($ million per päivä = close × volume)
    - mediaani volyymi suhteessa kokoon (= signaali-päivän $-volume / 60d MA $-volume)
    - win-prosentti
    - avg ja mediaani tuotto
    - mediaani alpha_peak-arvo oston hetkellä

Lukee:
  artifacts/alpha_peak_filtered/trades_unfiltered.jsonl  (2877 kauppaa)
  finance/data_for_ultraplan/v20_full_prices_close_cleaned.parquet
  finance/data_for_ultraplan/v20_full_prices_volume.parquet
"""
from __future__ import annotations
import sys, json
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path("C:/Users/puros/bots")
TRADES = ROOT / "_shared/artifacts/alpha_peak_filtered/trades_unfiltered.jsonl"
V20_CLOSE = ROOT / "finance/data_for_ultraplan/v20_full_prices_close_cleaned.parquet"
V20_VOL = ROOT / "finance/data_for_ultraplan/v20_full_prices_volume.parquet"
OUT = ROOT / "_shared/artifacts/alpha_peak_filtered"

def main():
    print(f"=== Alpha-peak class table ({datetime.now().isoformat()}) ===")
    trades = [json.loads(l) for l in open(TRADES, encoding="utf-8")]
    close_df = pd.read_parquet(V20_CLOSE)
    vol_df = pd.read_parquet(V20_VOL)
    print(f"  loaded {len(trades)} trades, {close_df.shape} close, {vol_df.shape} vol")

    # Compute $-volume = close * volume (per-day per-ticker)
    common_idx = close_df.index.intersection(vol_df.index)
    common_cols = sorted(set(close_df.columns) & set(vol_df.columns))
    close_a = close_df.loc[common_idx, common_cols]
    vol_a = vol_df.loc[common_idx, common_cols]
    dvol = close_a * vol_a   # dollar volume per day

    # 60d rolling mean dollar volume = "size proxy" ($ per day average)
    dvol_60 = dvol.rolling(60, min_periods=20).mean()

    rows = []
    for t in trades:
        tk = t["ticker"]
        ed = pd.Timestamp(t["entry_date"])
        if tk not in dvol.columns: continue
        avail = dvol.index[dvol.index <= ed]
        if len(avail) < 60: continue
        d = avail[-1]
        size_dv60 = dvol_60.loc[d, tk]      # mediaani 60d MA dollar volume
        signal_dv = dvol.loc[d, tk]          # signaalipäivän dollar volume
        if pd.isna(size_dv60) or size_dv60 == 0: continue
        vol_ratio_d = signal_dv / size_dv60
        rows.append({
            "ticker": tk,
            "regime_at_entry": t.get("regime_at_entry","n/a"),
            "persona": t.get("persona","n/a"),
            "lifecycle": t.get("lifecycle","n/a"),
            "alpha_peak_at_entry": t.get("alpha_peak_at_entry"),
            "net_ret": t.get("net_ret"),
            "size_dv60_musd": size_dv60 / 1e6,    # in $ million per day
            "vol_ratio": vol_ratio_d,
        })
    df = pd.DataFrame(rows)
    print(f"  enriched {len(df)} trades with size + vol-ratio")

    # === Aggregate per (regime × persona × lifecycle) ===
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

    # Save full csv
    out_csv = OUT / "class_table_full.csv"
    agg.to_csv(out_csv)
    print(f"\nSaved full table: {out_csv}")

    # Print pretty
    print("\n" + "="*132)
    print(f"{'regime':8s} {'persona':10s} {'lifecycle':18s} {'N':>5s}  {'WR%':>5s}  {'avg%':>7s}  {'med%':>7s}  {'medAlphaPeak%':>13s}  {'medSize_M$/d':>13s}  {'medVolRatio':>11s}")
    print("="*132)
    for (reg, per, lc), row in agg.iterrows():
        print(f"{str(reg)[:8]:8s} {str(per)[:10]:10s} {str(lc)[:18]:18s} "
              f"{int(row['N']):5d}  "
              f"{row['win_rate']*100:5.1f}  "
              f"{row['avg_ret']*100:+7.1f}  "
              f"{row['median_ret']*100:+7.1f}  "
              f"{row['median_alpha_peak']*100:13.2f}  "
              f"{row['median_size_musd']:13.1f}  "
              f"{row['median_vol_ratio']:11.2f}")

    # === Print sub-breakdowns user might want ===
    print("\n" + "="*88)
    print("REGIME alone (sortted by N)")
    print("="*88)
    g1 = df.groupby("regime_at_entry").agg(
        N=("net_ret","count"),
        WR=("net_ret", lambda x: (x>0).mean()),
        avg=("net_ret","mean"), med=("net_ret","median"),
        medAlphaPeak=("alpha_peak_at_entry","median"),
        medSizeM=("size_dv60_musd","median"),
        medVolR=("vol_ratio","median"),
    ).sort_values("N", ascending=False).round(4)
    print(g1.to_string())

    print("\n" + "="*88)
    print("PERSONA alone")
    print("="*88)
    g2 = df.groupby("persona").agg(
        N=("net_ret","count"),
        WR=("net_ret", lambda x: (x>0).mean()),
        avg=("net_ret","mean"), med=("net_ret","median"),
        medAlphaPeak=("alpha_peak_at_entry","median"),
        medSizeM=("size_dv60_musd","median"),
        medVolR=("vol_ratio","median"),
    ).sort_values("N", ascending=False).round(4)
    print(g2.to_string())

    print("\n" + "="*88)
    print("LIFECYCLE alone")
    print("="*88)
    g3 = df.groupby("lifecycle").agg(
        N=("net_ret","count"),
        WR=("net_ret", lambda x: (x>0).mean()),
        avg=("net_ret","mean"), med=("net_ret","median"),
        medAlphaPeak=("alpha_peak_at_entry","median"),
        medSizeM=("size_dv60_musd","median"),
        medVolR=("vol_ratio","median"),
    ).sort_values("N", ascending=False).round(4)
    print(g3.to_string())

    # JSON dump for downstream
    out_json = OUT / "class_table_full.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "as_of": datetime.now().isoformat(),
            "n_trades": int(len(df)),
            "triple_class": agg.reset_index().to_dict(orient="records"),
            "regime_only": g1.reset_index().to_dict(orient="records"),
            "persona_only": g2.reset_index().to_dict(orient="records"),
            "lifecycle_only": g3.reset_index().to_dict(orient="records"),
            "columns_explained": {
                "N": "trade count",
                "WR": "win rate (frac)",
                "avg": "mean net return per trade (frac)",
                "med": "median net return per trade (frac)",
                "medAlphaPeak": "median rolling 20d cumulative alpha at entry (frac)",
                "medSizeM": "median 60d-MA dollar volume at entry, in $ MILLION/day (size proxy)",
                "medVolR": "median (entry-day $ volume) / (60d-MA $ volume)",
            }
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nSaved JSON: {out_json}")

if __name__ == "__main__":
    main()
