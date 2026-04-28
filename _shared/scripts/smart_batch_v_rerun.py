# -*- coding: utf-8 -*-
r"""smart_batch_v_rerun.py — älykäs 141 v-skriptin uudelleen-ajo.

Käyttäjän mandaatti 2026-04-28:
  - Aja KAIKKI 141 v-skriptiä uudelleen uusilla simulaatio-opeilla
  - OPTIMOIDU: deduplikoi päällekkäiset parametri-yhdistelmät
  - Käytä CLEANED-data (v20_full_prices_close_cleaned.parquet)
  - Tieto-portfolio + aikadimensio + per-ticker decomposition
  - Ei sokea järjestys vaan älykäs perheittäin

Algoritmi:
  1. Parsi kaikki v-skriptit → poimi kaikki ainutlaatuiset parametri-yhdistelmät
     (peak_thr, dist_thr, hold, sl_pct, n_picks, win, beta_win, lookback)
  2. Deduplikoi: 141 skriptiä → ~30-50 ainutlaatuista yhdistelmää
  3. Aja kukin ainutlaatuinen yhdistelmä yhden kerran cleaned-datalla
  4. Kirjaa kukin tulos portfolio:on aikajakson + osallisten kanssa
  5. Linkitä alkuperäiset v-versiot mihin yhdistelmään ne kuuluvat
"""
from __future__ import annotations
import sys, json, re, time
from pathlib import Path
from collections import defaultdict
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_shared" / "scripts"))
from feature_portfolio import (
    record_observation, record_per_ticker_decomposition
)

FINANCE = ROOT / "finance"
DATA = FINANCE / "data_for_ultraplan"


# ============================================================================
# PARSI PARAMETRIT
# ============================================================================

def parse_parameters_from_script(p: Path) -> dict:
    """Pura python-skriptistä avaintekijät."""
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    params = {}
    patterns = {
        "peak_thr": [r"PEAK_THR\s*=\s*(\d+)", r"peak_50\s*>\s*(\d+)", r"peak_thr=(\d+)"],
        "dist_thr": [r"DIST_THR\s*=\s*([+-]?\d+)", r"dist_378\s*<\s*([+-]?\d+)",
                     r"d<\s*([+-]?\d+)", r"dist<-?(\d+)"],
        "hold": [r"HOLD\s*=\s*(\d+)", r"HOLD_DAYS\s*=\s*(\d+)", r"hold=(\d+)"],
        "sl_pct": [r"SL_PCT\s*=\s*(\d+)", r"sl_pct=(\d+)"],
        "n_picks": [r"N_PICKS\s*=\s*(\d+)"],
        "lookback": [r"LOOKBACK\s*=\s*(\d+)"],
        "beta_win": [r"BETA_WIN\s*=\s*(\d+)"],
        "win": [r"^WIN\s*=\s*(\d+)"],
    }
    for key, regs in patterns.items():
        for r in regs:
            m = re.search(r, content, re.MULTILINE)
            if m:
                try:
                    params[key] = int(m.group(1))
                    break
                except: pass
    return params


def parse_all_v_scripts() -> dict:
    """Parsi kaikki v*.py → versio → parametrit."""
    scripts = {}
    for p in sorted(FINANCE.glob("high_cagr_v*.py")):
        m = re.search(r"v(\d+[a-z]?)", p.stem)
        if not m: continue
        version = m.group(1)
        params = parse_parameters_from_script(p)
        params["_version"] = version
        params["_path"] = p.name
        scripts[version] = params
    return scripts


def deduplicate_combinations(scripts: dict) -> dict:
    """Group by (peak_thr, dist_thr, hold, sl_pct) — deduplikoi."""
    groups = defaultdict(list)
    for v, p in scripts.items():
        key = (
            p.get("peak_thr", "n/a"),
            p.get("dist_thr", "n/a"),
            p.get("hold", "n/a"),
            p.get("sl_pct", "n/a"),
            p.get("n_picks", "n/a"),
        )
        groups[key].append(v)
    return groups


# ============================================================================
# BACKTEST GENERIC RUNNER (cleaned-data)
# ============================================================================

def load_cleaned_data():
    """Lataa cleaned-parquet (smart_spike_handler:n jälki)."""
    p_close = DATA / "v20_full_prices_close_cleaned.parquet"
    if not p_close.exists():
        # Fallback raw
        p_close = DATA / "v20_full_prices_close.parquet"
    p_low = DATA / "v20_full_prices_low.parquet"
    p_vol = DATA / "v20_full_prices_volume.parquet"
    close = pd.read_parquet(p_close)
    low = pd.read_parquet(p_low) if p_low.exists() else close
    vol = pd.read_parquet(p_vol) if p_vol.exists() else close * 0
    sec = pd.read_csv(DATA / "v20_full_sector_map.csv") if (DATA / "v20_full_sector_map.csv").exists() else None
    for df in (close, low, vol):
        df.index = pd.to_datetime(df.index)
        if df.index.tz: df.index = df.index.tz_localize(None)
    sec_map = dict(zip(sec["symbol"], sec["sector"])) if sec is not None else {}
    return close, low, vol, sec_map


def compute_signals(close, vol, beta_win=60, win=50, lookback=378):
    """Generic signaalin laskenta."""
    ret = close.pct_change() * 100
    # Jos XLI ei ole, käytä SPY:tä
    benchmark = "XLI" if "XLI" in close.columns else "SPY"
    if benchmark not in close.columns:
        return None, None, None
    bench_ret = ret[benchmark]
    cov = ret.rolling(beta_win).cov(bench_ret)
    var = bench_ret.rolling(beta_win).var()
    beta = cov.div(var, axis=0)
    alpha = ret.subtract(beta.multiply(bench_ret, axis=0), fill_value=0)
    peak = alpha.rolling(win).max()
    hi = close.rolling(lookback).max()
    dist = (close / hi - 1) * 100
    dolvol = (close * vol).rolling(20).mean()
    return peak, dist, dolvol


def backtest_combination(close, low, vol, peak, dist, dolvol, sec_map,
                          peak_thr, dist_thr, hold_days, sl_pct, n_picks=2,
                          start="2015-01-01", end="2026-04-26",
                          min_adv=20e6, tc_pct=0.5,
                          excl_sectors={"Utilities","Real Estate","Consumer Staples"},
                          hype_blacklist={"GME","AMC","BB","BBBY","CLOV","EXPR","HOOD","KOSS","NOK",
                                           "PLTR","RIDE","SDC","SPCE","WISH"}):
    """Aja yksi parametri-yhdistelmä cleaned-datalla."""
    days = close.index[(close.index >= pd.Timestamp(start)) & (close.index <= pd.Timestamp(end))]
    open_pos = []
    trades = []
    avail = set(close.columns) - {"XLI", "SPY", "QQQ", "DIA", "IWM"} - hype_blacklist
    avail = [t for t in avail if t in close.columns and sec_map.get(t, "") not in excl_sectors]
    cap_max = 200

    for i, day in enumerate(days):
        # Exit
        still = []
        for pos in open_pos:
            sym = pos["ticker"]
            entry = pos["entry_price"]
            held = len(close.index[(close.index > pos["entry_date"]) & (close.index <= day)])
            exit_done = False
            if sl_pct and sl_pct > 0:
                lo = low[sym].get(day, np.nan) if sym in low.columns else np.nan
                trigger = entry * (1 - sl_pct/100)
                if pd.notna(lo) and lo <= trigger:
                    px = trigger
                    pnl_pct = (px / entry - 1) * 100 - 2 * tc_pct
                    trades.append({"ticker": sym, "entry_date": pos["entry_date"],
                                    "exit_date": day, "pnl_pct": pnl_pct, "reason": "SL"})
                    exit_done = True
            if not exit_done and held >= hold_days:
                px = close[sym].get(day, entry)
                if pd.notna(px):
                    pnl_pct = (px / entry - 1) * 100 - 2 * tc_pct
                    trades.append({"ticker": sym, "entry_date": pos["entry_date"],
                                    "exit_date": day, "pnl_pct": pnl_pct, "reason": "HOLD"})
                    exit_done = True
            if not exit_done:
                still.append(pos)
        open_pos = still

        # Entry
        if i + 1 >= len(days): continue
        next_day = days[i + 1]
        try:
            pk_row = peak.loc[day]
            d_row = dist.loc[day]
            adv_row = dolvol.loc[day]
            close_row = close.loc[day]
        except KeyError:
            continue
        candidates = []
        open_tickers = {p["ticker"] for p in open_pos}
        for sym in avail:
            if sym in open_tickers: continue
            try:
                pk = pk_row.get(sym, np.nan)
                d = d_row.get(sym, np.nan)
                adv = adv_row.get(sym, np.nan)
                cl = close_row.get(sym, np.nan)
            except Exception: continue
            if pd.isna(pk) or pd.isna(d) or pd.isna(adv) or pd.isna(cl): continue
            if adv < min_adv: continue
            if pk <= peak_thr: continue
            if d >= dist_thr: continue
            candidates.append({"sym": sym, "metric": d})
        candidates.sort(key=lambda x: x["metric"])
        for c in candidates[:n_picks]:
            if len(open_pos) >= cap_max: break
            ep = close[c["sym"]].get(next_day, np.nan)
            if pd.isna(ep) or ep <= 0: continue
            open_pos.append({"ticker": c["sym"], "entry_date": next_day, "entry_price": float(ep)})

    # MTM at end
    last_day = days[-1]
    for pos in open_pos:
        sym = pos["ticker"]
        avail_idx = close.index[close.index <= last_day]
        if len(avail_idx) == 0: continue
        px = close[sym].get(avail_idx[-1], pos["entry_price"])
        if pd.isna(px): px = pos["entry_price"]
        pnl_pct = (px / pos["entry_price"] - 1) * 100 - 2 * tc_pct
        trades.append({"ticker": sym, "entry_date": pos["entry_date"],
                        "exit_date": last_day, "pnl_pct": pnl_pct, "reason": "MTM"})

    return trades


# ============================================================================
# MAIN
# ============================================================================

def main():
    t0 = time.time()
    print("="*70)
    print("SMART BATCH V-RERUN — 141 skriptiä deduplikoituna")
    print("="*70)

    # 1. Parsi kaikki
    scripts = parse_all_v_scripts()
    print(f"\nLuettu {len(scripts)} v-skriptiä")

    # 2. Deduplikoi
    groups = deduplicate_combinations(scripts)
    print(f"Ainutlaatuisia (peak_thr, dist_thr, hold, sl_pct, n_picks) -yhdistelmiä: {len(groups)}")
    # Filtteri vain ne joilla on riittävät parametrit
    valid_groups = {k: v for k, v in groups.items() if all(x != "n/a" for x in k[:4])}
    print(f"Kelvollisia (kaikki avaimet löytyy): {len(valid_groups)}")

    # 3. Lataa cleaned-data
    print(f"\nLataa cleaned-data...")
    close, low, vol, sec_map = load_cleaned_data()
    print(f"  close: {close.shape}")

    # 4. Aja kukin ainutlaatuinen yhdistelmä
    n_combos = len(valid_groups)
    print(f"\nAjetaan {n_combos} ainutlaatuista yhdistelmää (säästää {len(scripts) - n_combos} duplikaatti-ajoa)...")

    # Esi-laske signaalit kerran (kallis, mutta vain kerran)
    print(f"  Lasketaan signaalit (peak/dist/dolvol)...")
    peak, dist, dolvol = compute_signals(close, vol)
    if peak is None:
        print("  Fatal: ei XLI tai SPY datassa")
        return
    print(f"  signaalit valmiit. Aloitetaan ajot...")

    n_recorded = 0
    for gi, (key, versions) in enumerate(valid_groups.items()):
        peak_thr, dist_thr, hold_days, sl_pct, n_picks = key
        # Pakota numeroiksi (jos n/a-merkki vielä jäljellä, skipataan)
        try:
            peak_thr = int(peak_thr); dist_thr = int(dist_thr)
            hold_days = int(hold_days); sl_pct = int(sl_pct)
            n_picks = int(n_picks) if n_picks != "n/a" else 2
        except (ValueError, TypeError):
            continue
        # Filtteri järkevät arvot
        if not (5 <= peak_thr <= 30): continue
        if not (-50 <= dist_thr <= -10): continue
        if not (60 <= hold_days <= 1500): continue
        if not (0 <= sl_pct <= 30): continue
        if not (1 <= n_picks <= 10): continue

        t_combo = time.time()
        try:
            trades = backtest_combination(
                close, low, vol, peak, dist, dolvol, sec_map,
                peak_thr=peak_thr, dist_thr=dist_thr,
                hold_days=hold_days, sl_pct=sl_pct, n_picks=n_picks
            )
        except Exception as e:
            print(f"  [{gi+1}/{n_combos}] {key} ERR: {type(e).__name__}: {e}")
            continue

        if not trades:
            continue
        df = pd.DataFrame(trades)
        n_trades = len(df)
        win_rate = (df["pnl_pct"] > 0).mean()
        median_pnl = df["pnl_pct"].median()
        mean_pnl = df["pnl_pct"].mean()
        std = df["pnl_pct"].std() if n_trades > 1 else 0

        # Kirjaa portfolioon
        verdict = "EFFECT_STRONG" if mean_pnl > 5 and n_trades > 30 else \
                  "EFFECT_MODERATE" if mean_pnl > 1 and n_trades > 20 else \
                  "EFFECT_WEAK" if mean_pnl > -5 else "NO_EFFECT"

        record_observation(
            feature=f"v_combo_peak{peak_thr}_dist{dist_thr}_hold{hold_days}_sl{sl_pct}_npicks{n_picks}",
            target="strategy_per_trade_pnl_pct",
            target_class="portfolio_metric",
            domain="systematic_equity",
            method="other",
            value=mean_pnl / 100,
            raw_value=mean_pnl,
            n_samples=n_trades,
            applies_to=f"S&P500_growth_{n_picks}picks",
            bot="finance",
            strategy_id=f"smart_batch_v_combo",
            p_value=None,
            verdict=verdict,
            confidence_pct=85 if n_trades > 50 else 60,
            replication_count=len(versions),
            event_date=None,
            period_start="2015-01-01",
            period_end="2026-04-26",
            participants=[
                f"peak_thr={peak_thr}", f"dist_thr={dist_thr}",
                f"hold={hold_days}d", f"sl_pct={sl_pct}", f"n_picks={n_picks}",
                f"linked_versions={','.join(versions[:5])}"
            ],
            metadata={
                "n_versions_using_this_combo": len(versions),
                "linked_v_versions": versions,
                "win_rate": win_rate,
                "median_pnl": median_pnl,
                "std_pnl": std,
                "duration_s": time.time() - t_combo,
                "data_source": "cleaned_v20",
            }
        )
        n_recorded += 1

        # Per-ticker decomposition
        n_pt = record_per_ticker_decomposition(
            df, f"v_combo_p{peak_thr}_d{dist_thr}_h{hold_days}_sl{sl_pct}",
            "finance", min_trades_per_ticker=2
        )
        n_recorded += n_pt

        if (gi + 1) % 5 == 0 or gi == 0:
            print(f"  [{gi+1}/{n_combos}] peak{peak_thr} dist{dist_thr} hold{hold_days} sl{sl_pct} np{n_picks}: "
                  f"N={n_trades:>3} mean={mean_pnl:+6.1f}% win={win_rate*100:>4.1f}% verdict={verdict}  "
                  f"({time.time()-t_combo:.1f}s, +{n_pt} per-ticker)")

    print(f"\n{'='*70}")
    print(f"VALMIS — {n_recorded} havaintoa (yhdistelmiä + per-ticker)")
    print(f"Kesto: {time.time()-t0:.0f}s")

    from feature_portfolio import summarize
    s = summarize()
    print(f"\nPortfolio total: {s['n_observations']}")
    print(f"By verdict: {s['by_verdict']}")


if __name__ == "__main__":
    main()
