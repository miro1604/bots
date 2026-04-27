# -*- coding: utf-8 -*-
r"""fetch_low_data.py — lataa Low-hinnat samalle 673-ticker universumille.

Lukee v20_full_prices_close.parquet:sta ticker-listan, lataa Low (auto_adjust=True),
tallentaa v20_full_prices_low.parquet:iin. Tarvitaan SL-laskennalle v31:ssa.
"""
from __future__ import annotations
import sys, time
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pandas as pd
import yfinance as yf

OUT_DIR = Path(__file__).parent
START = "2014-01-01"
END = "2026-04-26"
BATCH_SIZE = 50

def main():
    close = pd.read_parquet(OUT_DIR / "v20_full_prices_close.parquet")
    tickers = sorted(close.columns.tolist())
    print(f"Lataa Low {len(tickers)} tickerille {START}-{END}...")

    all_low = {}
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i:i+BATCH_SIZE]
        try:
            raw = yf.download(batch, start=START, end=END, auto_adjust=True,
                              progress=False, group_by="column")
            if isinstance(raw.columns, pd.MultiIndex):
                low = raw["Low"]
            else:
                low = pd.DataFrame({batch[0]: raw["Low"]})
            for sym in batch:
                if sym in low.columns:
                    s = low[sym].dropna()
                    if len(s) > 100:
                        all_low[sym] = s
            print(f"  batch {i//BATCH_SIZE+1}/{(len(tickers)-1)//BATCH_SIZE+1} done "
                  f"({len(all_low)} cumulative)")
        except Exception as e:
            print(f"  batch {i//BATCH_SIZE} ERROR: {e}")
        time.sleep(1)

    df_low = pd.DataFrame(all_low).sort_index()
    out = OUT_DIR / "v20_full_prices_low.parquet"
    df_low.to_parquet(out, compression="snappy")
    print(f"\nTallennettu: {out.name} "
          f"({out.stat().st_size / 1024 / 1024:.1f} MB, shape {df_low.shape})")

if __name__ == "__main__":
    main()
