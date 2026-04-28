# -*- coding: utf-8 -*-
r"""v32_dyn_nosl_validator.py — sama kuin v32_dyn mutta ILMAN SL:ää.

Ainut exit: hold 680 trading days, tai data_end (MTM viimeisellä Closella).
Käyttäjän pyyntö 2026-04-28: "joo mutta ei stop lossia"
"""
from __future__ import annotations
import sys, time, json, warnings
warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from pathlib import Path
import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).parent

LOOKBACK = 378
BETA_WIN = 120
PEAK_WIN = 20
HOLD = 680
PEAK_THR = 15
DIST_THR = -20
MIN_ADAV = 20e6
POSITION_SIZE = 10000.0
TC_PCT = 0.5
MAX_OPEN_POSITIONS = 200

EXCLUDE_SEC = {"Utilities", "Real Estate", "Consumer Staples"}
HYPE_BLACKLIST = {"GME","AMC","BB","BBBY","CLOV","EXPR","HOOD","KOSS","NOK",
                  "PLTR","RIDE","SDC","SPCE","WISH"}


def load_data():
    print("[1/4] Lataa data...")
    close = pd.read_parquet(DATA_DIR / "v20_full_prices_close.parquet")
    volume = pd.read_parquet(DATA_DIR / "v20_full_prices_volume.parquet")
    sec = pd.read_csv(DATA_DIR / "v20_full_sector_map.csv")
    close.index = pd.to_datetime(close.index)
    volume.index = pd.to_datetime(volume.index)
    if close.index.tz: close.index = close.index.tz_localize(None)
    if volume.index.tz: volume.index = volume.index.tz_localize(None)
    sec_map = dict(zip(sec["symbol"], sec["sector"]))
    return close, volume, sec_map


def compute_signals(close, volume):
    print("[2/4] Laske signaalit...")
    ret = close.pct_change() * 100
    xli = ret["XLI"]
    cov = ret.rolling(BETA_WIN).cov(xli)
    var = xli.rolling(BETA_WIN).var()
    beta = cov.div(var, axis=0)
    alpha = ret.subtract(beta.multiply(xli, axis=0), fill_value=0)
    peak_20 = alpha.rolling(PEAK_WIN).max()
    hi_378 = close.rolling(LOOKBACK).max()
    dist_378 = (close / hi_378 - 1) * 100
    dolvol = (close * volume).rolling(20).mean()
    return peak_20, dist_378, dolvol


def select_universe(close, sec_map):
    avail = []
    for sym in close.columns:
        if sym == "XLI": continue
        if sym in HYPE_BLACKLIST: continue
        if sec_map.get(sym, "") in EXCLUDE_SEC: continue
        if close[sym].notna().sum() < 460: continue
        avail.append(sym)
    return sorted(avail)


def run_backtest(close, peak_20, dist_378, dolvol, avail,
                 start="2015-01-01", end="2026-04-26"):
    s = pd.Timestamp(start); e = pd.Timestamp(end)
    days = close.index[(close.index >= s) & (close.index <= e)]
    print(f"\n[3/4] Daily scan {s.date()}..{e.date()} ({len(days)} päivää, EI SL:ää)...")

    open_pos = []
    trade_log = []
    n_signals = 0
    n_trades = 0
    n_skipped_cap = 0
    avail_set = set(avail)

    for i, day in enumerate(days):
        if i % 500 == 0:
            print(f"  day {i}/{len(days)}: open_pos={len(open_pos)}, trades={n_trades}")

        # Exit: vain HOLD 680
        still = []
        for pos in open_pos:
            held = len(close.index[(close.index > pos["entry_date"]) &
                                    (close.index <= day)])
            if held >= HOLD:
                px = close[pos["ticker"]].get(day, np.nan)
                if pd.notna(px):
                    gross = pos["shares"] * float(px)
                    sc = gross * TC_PCT / 100
                    net = gross - sc
                    pnl_pct = (net / pos["invested"] - 1) * 100
                    trade_log.append({**pos, "exit_date": day, "exit_price": float(px),
                                      "reason": "HOLD", "pnl_pct": pnl_pct,
                                      "pnl_eur": net - pos["invested"]})
                    continue
            still.append(pos)
        open_pos = still

        # Signal scan
        open_tickers = {p["ticker"] for p in open_pos}
        if day not in peak_20.index: continue
        try:
            pk_row = peak_20.loc[day]
            d_row = dist_378.loc[day]
            adv_row = dolvol.loc[day]
            close_row = close.loc[day]
        except KeyError:
            continue

        sig_mask = (pk_row > PEAK_THR) & (d_row < DIST_THR) & (adv_row >= MIN_ADAV) & close_row.notna()
        sigs = sig_mask[sig_mask].index.tolist()
        sigs = [s for s in sigs if s in avail_set and s not in open_tickers]
        n_signals += len(sigs)

        # Avaa next-day closella
        if i + 1 >= len(days): continue
        next_day = days[i + 1]

        for sym in sigs:
            if len(open_pos) >= MAX_OPEN_POSITIONS:
                n_skipped_cap += 1
                continue
            ep = close[sym].get(next_day, np.nan)
            if pd.isna(ep) or ep <= 0: continue
            invested = POSITION_SIZE
            bc = invested * TC_PCT / 100
            shares = (invested - bc) / ep
            open_pos.append({"ticker": sym, "entry_date": next_day, "entry_price": float(ep),
                            "shares": shares, "invested": invested, "signal_date": day})
            n_trades += 1

    # MTM
    last_day = days[-1]
    for pos in open_pos:
        avail_idx = close.index[close.index <= last_day]
        if len(avail_idx) == 0: continue
        px = close[pos["ticker"]].get(avail_idx[-1], pos["entry_price"])
        if pd.isna(px): px = pos["entry_price"]
        gross = pos["shares"] * float(px)
        sc = gross * TC_PCT / 100
        net = gross - sc
        pnl_pct = (net / pos["invested"] - 1) * 100
        trade_log.append({**pos, "exit_date": last_day, "exit_price": float(px),
                          "reason": "MTM", "pnl_pct": pnl_pct,
                          "pnl_eur": net - pos["invested"]})

    return {"trades": trade_log, "n_signals": n_signals, "n_trades": n_trades,
            "n_skipped_cap": n_skipped_cap}


def report(result):
    df = pd.DataFrame(result["trades"])
    print(f"\n[4/4] TULOKSET (NO STOP-LOSS)")
    print("=" * 78)
    print(f"Signaaleja:           {result['n_signals']}")
    print(f"Treidejä toteutui:    {result['n_trades']}")
    print(f"Skippaa (cap-limit):  {result['n_skipped_cap']}")
    print(f"N ≥ 200: {'PASS' if result['n_trades'] >= 200 else 'FAIL'}")

    pnls = df["pnl_pct"].values
    wins = (pnls > 0).sum(); losses = (pnls <= 0).sum()
    print(f"\n--- Per-trade ({len(df)} trades) ---")
    print(f"  Profitable:  {wins/len(df)*100:.1f}% ({wins}/{len(df)})")
    if wins > 0:
        print(f"  Avg WIN:    +{pnls[pnls > 0].mean():.1f}%")
    if losses > 0:
        print(f"  Avg LOSS:   {pnls[pnls <= 0].mean():.1f}%")
    print(f"  Median pnl: {np.median(pnls):.1f}%")
    print(f"  Best:       +{pnls.max():.1f}% ({df.loc[df['pnl_pct'].idxmax(),'ticker']})")
    print(f"  Worst:      {pnls.min():.1f}% ({df.loc[df['pnl_pct'].idxmin(),'ticker']})")

    rc = df["reason"].value_counts()
    print(f"\n  Exit reasons: {dict(rc)}")

    total_pnl = df["pnl_eur"].sum()
    total_inv = df["invested"].sum()
    print(f"\n  Total invested: ${total_inv:,.0f}")
    print(f"  Total PnL:      ${total_pnl:,.0f}")
    print(f"  Avg PnL/trade:  ${df['pnl_eur'].mean():,.0f}")

    df["entry_year"] = pd.to_datetime(df["entry_date"]).dt.year
    yr = df.groupby("entry_year").agg(n=("ticker", "count"),
                                       avg_pnl=("pnl_pct", "mean"),
                                       total=("pnl_eur", "sum"),
                                       win_rate=("pnl_pct", lambda s: (s > 0).mean() * 100))
    print("\n  Per-year:")
    print(yr.to_string())

    if total_inv > 0:
        roi = total_pnl / total_inv * 100
        years = (pd.to_datetime(df["exit_date"]).max() -
                 pd.to_datetime(df["entry_date"]).min()).days / 365.25
        if years > 0:
            cagr = ((1 + total_pnl/total_inv) ** (1/years) - 1) * 100
            print(f"\n  Total ROI: {roi:.1f}%")
            print(f"  Annualized: {cagr:.2f}% / {years:.1f}v")

    by_ticker = df.groupby("ticker")["pnl_eur"].sum().sort_values(ascending=False)
    print("\n  Top-10 contributors:")
    print(by_ticker.head(10).to_string())
    print("\n  Worst-10 contributors:")
    print(by_ticker.tail(10).to_string())

    # Distribution
    print("\n  Pnl distribution:")
    bins = [-100, -50, -25, -10, 0, 25, 50, 100, 250, 500, 1000, 5000, 100000]
    labels = ['<-50%','-50..-25','-25..-10','-10..0','0..25','25..50',
             '50..100','100..250','250..500','500..1000','1k..5k','>5k']
    df["pnl_bin"] = pd.cut(pnls, bins=bins, labels=labels)
    print(df["pnl_bin"].value_counts().sort_index().to_string())

    return df


def main():
    t0 = time.time()
    print("="*78)
    print("v32_dyn NO-SL VALIDATOR — only HOLD 680d or data_end exit")
    print("="*78)

    close, volume, sec_map = load_data()
    peak_20, dist_378, dolvol = compute_signals(close, volume)
    avail = select_universe(close, sec_map)
    print(f"  Universumi: {len(avail)} tickers")

    result = run_backtest(close, peak_20, dist_378, dolvol, avail)
    df = report(result)
    df.to_csv(DATA_DIR / "v32_dyn_nosl_trades.csv", index=False)

    pnls = df["pnl_pct"].values
    summary = {
        "no_stop_loss": True,
        "n_trades": int(result["n_trades"]),
        "n_signals": int(result["n_signals"]),
        "profitable_rate": float((pnls > 0).mean()),
        "avg_win_pct": float(pnls[pnls > 0].mean()) if (pnls > 0).any() else 0,
        "avg_loss_pct": float(pnls[pnls <= 0].mean()) if (pnls <= 0).any() else 0,
        "median_pnl": float(np.median(pnls)),
        "total_pnl": float(df["pnl_eur"].sum()),
        "total_invested": float(df["invested"].sum()),
        "elapsed_s": time.time() - t0
    }
    (DATA_DIR / "v32_dyn_nosl_summary.json").write_text(json.dumps(summary, indent=2, default=str))

    print(f"\nKesto: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
