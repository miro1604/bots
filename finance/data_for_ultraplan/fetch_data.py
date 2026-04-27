# -*- coding: utf-8 -*-
r"""fetch_data.py — lataa kaikki data jonka v20-validointi tarvitsee.

Tallennusmuoto: Parquet (pakattu, GitHub-yhteensopiva).
Tavoite: ultraplan-cloud saa kaiken datan REPOSTA ilman verkkoa.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pandas as pd
import numpy as np

OUT_DIR = Path(__file__).parent

# 50 tickeriä — v20-voittajat + sektorit + benchmarkit (riittävän pieni
# tiivistetyssä Parquet:ssa < 5 MB)
TICKERS = [
    # v20 isot voittajat
    "CVNA", "APP", "APA", "TWLO", "RH", "OXY", "NTNX", "GAP", "WBD",
    "EPAM", "AAL", "NCLH",
    # Mega-cap stable core
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
    "JPM", "V", "UNH", "MA", "HD", "WMT",
    # Energia ja perinteinen
    "XOM", "CVX", "JNJ", "PG", "PEP", "KO", "MRK",
    # Tech / kasvu
    "ORCL", "ADBE", "CSCO", "AVGO", "AMD", "QCOM",
    # Sektori-ETF benchmarkit (KRIITTISET v20:lle)
    "SPY", "QQQ", "DIA", "IWM",
    "XLF", "XLI", "XLK", "XLE", "XLY", "XLV", "XLP", "XLU", "XLRE",
    # Volatiliteetti + bond
    "TLT", "GLD",
]

START = "2014-01-01"
END = "2026-04-26"


def fetch_prices():
    print(f"Lataa {len(TICKERS)} tickerin hintahistoria...")
    try:
        import yfinance as yf
    except Exception:
        print("  asenna: pip install yfinance")
        sys.exit(1)
    data = yf.download(TICKERS, start=START, end=END, progress=False,
                       auto_adjust=True)
    # Multi-level columns: (price_type, ticker)
    close = data["Close"]
    volume = data["Volume"]
    print(f"  Close shape: {close.shape}")
    return close, volume


def fetch_sp500_membership():
    """Hae historiallinen S&P500-jäsenyys (Wikipedia)."""
    print("Lataa S&P500 historical members (Wikipedia)...")
    try:
        # Wikipediassa current + historical changes
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = pd.read_html(url)
        current = tables[0][["Symbol", "Security", "GICS Sector",
                              "GICS Sub-Industry", "Date added"]].copy()
        current.columns = ["symbol", "name", "sector", "sub_industry", "date_added"]
        # Historical changes
        try:
            changes = tables[1].copy()
            # Useimmiten: Date | Added Symbol | Added Name | Removed Symbol | Removed Name
            changes.columns = [str(c).lower().replace(" ", "_") for c in changes.columns]
        except Exception:
            changes = pd.DataFrame()
        return current, changes
    except Exception as e:
        print(f"  Virhe: {e}")
        return pd.DataFrame(), pd.DataFrame()


def main():
    OUT_DIR.mkdir(exist_ok=True)
    metadata = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "start": START,
        "end": END,
        "tickers_count": len(TICKERS),
        "tickers": TICKERS,
        "purpose": "v20 PDF2 validation — offline-kelpoinen data ultraplan-cloud:lle",
    }

    # 1. Hintadata
    close, volume = fetch_prices()
    close_path = OUT_DIR / "prices_close.parquet"
    volume_path = OUT_DIR / "prices_volume.parquet"
    close.to_parquet(close_path, compression="snappy")
    volume.to_parquet(volume_path, compression="snappy")
    metadata["prices_close_size_kb"] = close_path.stat().st_size / 1024
    metadata["prices_volume_size_kb"] = volume_path.stat().st_size / 1024
    print(f"  Tallennettu: {close_path.name} ({metadata['prices_close_size_kb']:.0f} KB)")
    print(f"  Tallennettu: {volume_path.name} ({metadata['prices_volume_size_kb']:.0f} KB)")

    # 2. Lasketut päivätuotot (helpoa ultraplan-puolelle)
    rets = close.pct_change()
    rets.to_parquet(OUT_DIR / "daily_returns.parquet", compression="snappy")
    print(f"  Tallennettu: daily_returns.parquet")

    # 3. S&P500-membership (PiT-validointiin)
    current, changes = fetch_sp500_membership()
    if not current.empty:
        current.to_csv(OUT_DIR / "sp500_current_members.csv", index=False)
        print(f"  Tallennettu: sp500_current_members.csv ({len(current)} riviä)")
    if not changes.empty:
        changes.to_csv(OUT_DIR / "sp500_membership_changes.csv", index=False)
        print(f"  Tallennettu: sp500_membership_changes.csv ({len(changes)} riviä)")

    # 4. Sektori-mappingit (välttämätön v20:lle)
    sector_etfs = {
        "XLF": "Financials", "XLI": "Industrials", "XLK": "Information Technology",
        "XLE": "Energy", "XLY": "Consumer Discretionary", "XLV": "Healthcare",
        "XLP": "Consumer Staples", "XLU": "Utilities", "XLRE": "Real Estate",
        "SPY": "Total Market", "QQQ": "Tech-Growth", "DIA": "Industrial-Old",
        "IWM": "Small-Cap",
    }
    pd.DataFrame(list(sector_etfs.items()),
                 columns=["etf", "name"]).to_csv(
        OUT_DIR / "sector_etfs.csv", index=False)
    print(f"  Tallennettu: sector_etfs.csv")

    # 5. Metadata
    (OUT_DIR / "metadata.json").write_text(
        json.dumps(metadata, indent=2, default=str), encoding="utf-8")
    print(f"  Tallennettu: metadata.json")

    print(f"\nValmista! Total: {sum(p.stat().st_size for p in OUT_DIR.glob('*')) / 1024:.0f} KB")
    print("\nSeuraavaksi:")
    print("  cd /c/Users/puros/bots")
    print("  git add finance/data_for_ultraplan/")
    print("  git commit -m 'data: v20-validation dataset for ultraplan'")
    print("  git push origin master")


if __name__ == "__main__":
    main()
