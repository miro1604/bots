# -*- coding: utf-8 -*-
r"""fetch_full_v20_universe.py — lataa kaikki 632 v20-tickeriä parquet:iin.

Replikoi v20:n universumi-build:n (S&P 500 + S&P 400 -kasvusektorit + MEGA_POOL),
lataa OHLCV 2014-2026 yfinance:lta, tallentaa snappy-parquet:iin.

Lopputulos: ~10-15 MB tiedosto joka committable GitHub:iin → ultraplan saa
täydellisen offline-datan.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pandas as pd
import numpy as np

OUT_DIR = Path(__file__).parent

# v20:n MEGA_POOL ja HYPE_BLACKLIST kopioituina
MEGA_POOL = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "AMD",
    "AVGO", "ORCL",
]
HYPE_BLACKLIST = [
    "GME", "AMC", "BBBY", "BB", "NOK",  # meme-stockit, low-quality
]
EXCLUDE_SEC = {"Real Estate", "Utilities"}  # v20-tyylinen kasvu-fokus
G400_GROWTH = {"Information Technology", "Health Care",
                "Consumer Discretionary", "Communication Services", "Industrials"}

# Sektori-ETF v20-tyylinen
SECTOR_ETF = {
    "Information Technology": "XLK", "Health Care": "XLV",
    "Consumer Discretionary": "XLY", "Communication Services": "XLC",
    "Industrials": "XLI", "Financials": "XLF", "Energy": "XLE",
    "Consumer Staples": "XLP", "Real Estate": "XLRE", "Utilities": "XLU",
    "Materials": "XLB",
}

START = "2014-01-01"
END = "2026-04-26"
BATCH_SIZE = 50


def fetch_sp500_sp400():
    """Hae Wikipediasta S&P 500 ja S&P 400 jäsenlistat + sektorit."""
    print("Lataa S&P 500 + S&P 400 Wikipediasta...")
    try:
        # S&P 500
        sp500 = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            storage_options={"User-Agent": "Mozilla/5.0"},
        )[0]
        sp500["Symbol"] = sp500["Symbol"].astype(str).str.strip().str.replace(".", "-")
        sec_map = dict(zip(sp500["Symbol"], sp500["GICS Sector"]))
        sp500g = [s for s in sp500["Symbol"] if sec_map.get(s, "") not in EXCLUDE_SEC]
        print(f"  S&P 500: {len(sp500)} kpl → {len(sp500g)} suodatuksen jälkeen")
    except Exception as e:
        print(f"  S&P 500 fail: {e}")
        return [], {}

    sp400g = []
    try:
        sp400 = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
            storage_options={"User-Agent": "Mozilla/5.0"},
        )[0]
        sp400["Symbol"] = sp400["Symbol"].astype(str).str.strip().str.replace(".", "-")
        if "GICS Sector" in sp400.columns:
            sp400g = sp400[sp400["GICS Sector"].isin(G400_GROWTH)]["Symbol"].tolist()
            for s, sec in zip(sp400["Symbol"], sp400["GICS Sector"]):
                sec_map[s] = sec
        print(f"  S&P 400: {len(sp400)} kpl → {len(sp400g)} kasvusektoreilta")
    except Exception as e:
        print(f"  S&P 400 fail: {e}")

    return sorted(set(sp500g + sp400g + MEGA_POOL)), sec_map


def fetch_prices(tickers):
    """Lataa OHLCV batch:eittain."""
    import yfinance as yf
    benchmarks = ["SPY", "QQQ", "DIA", "IWM"] + list(set(SECTOR_ETF.values()))
    all_dl = sorted(set(tickers + benchmarks))
    # Suodata vain sallitut symbolit
    all_dl = [t for t in all_dl
              if 1 < len(t) <= 6 and t.replace("-", "").isalpha()
              and t not in HYPE_BLACKLIST]
    print(f"\nLataa {len(all_dl)} symbolin OHLCV {START}-{END} (batches of {BATCH_SIZE})...")

    all_close = {}
    all_volume = {}
    failed = []

    for i in range(0, len(all_dl), BATCH_SIZE):
        batch = all_dl[i:i + BATCH_SIZE]
        try:
            raw = yf.download(batch, start=START, end=END, auto_adjust=True,
                              progress=False, group_by="column")
            if isinstance(raw.columns, pd.MultiIndex):
                close = raw["Close"]
                volume = raw["Volume"]
            else:
                close = pd.DataFrame({batch[0]: raw["Close"]})
                volume = pd.DataFrame({batch[0]: raw["Volume"]})
            for sym in batch:
                if sym in close.columns:
                    s = close[sym].dropna()
                    if len(s) > 100:
                        all_close[sym] = s
                        all_volume[sym] = volume[sym].reindex(s.index).fillna(0)
                    else:
                        failed.append(sym)
                else:
                    failed.append(sym)
        except Exception as e:
            print(f"  batch {i//BATCH_SIZE}: ERROR {e}")
            failed.extend(batch)
        print(f"  batch {i//BATCH_SIZE+1}/{(len(all_dl)-1)//BATCH_SIZE+1} done "
              f"({len(all_close)}/{len(all_dl)} cumulative)")
        time.sleep(1)  # rate-limit

    df_close = pd.DataFrame(all_close).sort_index()
    df_volume = pd.DataFrame(all_volume).sort_index()
    print(f"\n  Onnistui: {len(all_close)}, epäonnistui: {len(failed)}")
    return df_close, df_volume, failed


def main():
    OUT_DIR.mkdir(exist_ok=True)
    t0 = time.time()

    print("=" * 70)
    print("v20 FULL UNIVERSE FETCH — 632 ticker → parquet")
    print("=" * 70)

    tickers, sec_map = fetch_sp500_sp400()
    if not tickers:
        print("\nFatal: tickerit eivät latautuneet. Avaa Wikipedia selaimessa varmuudeksi.")
        return 1

    print(f"\nYhteensä universumissa: {len(tickers)} kpl")

    df_close, df_volume, failed = fetch_prices(tickers)

    if df_close.empty:
        print("\nFatal: hintaa ei tullut.")
        return 1

    # Tallenna parquet:eihin
    close_path = OUT_DIR / "v20_full_prices_close.parquet"
    volume_path = OUT_DIR / "v20_full_prices_volume.parquet"
    df_close.to_parquet(close_path, compression="snappy")
    df_volume.to_parquet(volume_path, compression="snappy")
    print(f"\n  Tallennettu: {close_path.name} "
          f"({close_path.stat().st_size / 1024 / 1024:.1f} MB, "
          f"shape {df_close.shape})")
    print(f"  Tallennettu: {volume_path.name} "
          f"({volume_path.stat().st_size / 1024 / 1024:.1f} MB)")

    # Sektori-mappi
    sec_df = pd.DataFrame([(k, v) for k, v in sec_map.items()],
                           columns=["symbol", "sector"])
    # Lisää sektorin ETF-ticker
    sec_df["sector_etf"] = sec_df["sector"].map(SECTOR_ETF)
    sec_df.to_csv(OUT_DIR / "v20_full_sector_map.csv", index=False)
    print(f"  Tallennettu: v20_full_sector_map.csv ({len(sec_df)} riviä)")

    # Daily returns esi-laskettuna
    rets = df_close.pct_change()
    rets_path = OUT_DIR / "v20_full_daily_returns.parquet"
    rets.to_parquet(rets_path, compression="snappy")
    print(f"  Tallennettu: {rets_path.name} "
          f"({rets_path.stat().st_size / 1024 / 1024:.1f} MB)")

    # Metadata
    metadata = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "start": START,
        "end": END,
        "tickers_total_universe": len(tickers),
        "tickers_with_data": len(df_close.columns),
        "tickers_failed": failed,
        "shape": list(df_close.shape),
        "sector_etfs": SECTOR_ETF,
        "purpose": "v20 full-universe validation — offline dataset",
    }
    (OUT_DIR / "v20_full_metadata.json").write_text(
        json.dumps(metadata, indent=2, default=str), encoding="utf-8")
    print(f"  Tallennettu: v20_full_metadata.json")

    print(f"\nKesto: {time.time()-t0:.0f}s")
    print(f"Total dataset koko: {sum(p.stat().st_size for p in OUT_DIR.glob('v20_full_*')) / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
