# -*- coding: utf-8 -*-
r"""crypto_hyper_strategies_rerun.py — 32-tutkimuksesta 15 testattavaa hyper-yield -strategiaa.

Käyttäjän mandaatti 2026-04-28 (research dump → orchestrator/crypto_hyper_strategies_research.txt):
  - Aja kaikki implementoitavat strategiat kaikilla 14 cache-coineilla
  - Realistic-optimistic costs (TC 4 bps, slippage 5 bps × lev, funding 2 bps/8h × lev, lev cap 25×)
  - 2026-blueprint validation (PSR/DSR/CPCV)
  - Per-coin decomposition (yksittäisten coinien edge säilytetään vaikka pooli flat)
  - Tieto-portfolio: aikadimensio, osalliset, raw_value, confidence_pct
  - EI pessimistinen: 100-3000% target on tutkimuksen lupaus, ei rajata sitä alas

Strategiat (abstraktoitu 32-listasta, vain ne joita voi laskea OHLCV+volume:sta):
  M07  4-week red rule mean reversion (long)
  M08  Inverted fractal: -X% ATH:sta -> long
  M09  Post-impulse accumulation: pumppi+konsolidaatio -> long
  T10  Falling wedge proxy = Bollinger squeeze + breakout (long)
  T12  Cup&Handle proxy = Stoch RSI oversold cross (long)
  T13  RSI bullish divergence (long)
  T14  Fibonacci 0.618 retracement -> 0.236 trail (long)
  T17  Fib 0.236 trail-only variant
  T18  50% scale-out variant
  L19  Micro-margin 1/99 (lev 5x BB squeeze)
  L23  3x leveraged falling wedge
  A27  15m T3 MA + ADX momentum (käytetään 1h:lla approxina)
  S29  4-sigma RSI 95 short
  S30  Parabolic + RSI divergence short
  S31  RSI > 85 fakeout short

Tulosteet:
  artifacts/crypto_hyper_strategies/results.jsonl
  artifacts/crypto_hyper_strategies/per_coin_decomp.jsonl
  artifacts/crypto_hyper_strategies/strategy_summary.json
  + tieto-portfolio: feature_portfolio.jsonl (record_observation + per_ticker_decomposition)
"""
from __future__ import annotations
import sys, os, json, math, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path("C:/Users/puros/bots")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_shared" / "scripts"))

import feature_portfolio as fp
import quant_validation_v2 as qv

CACHE = ROOT / "crypto-finance/assets/ohlcv_cache/binance"
OUT = ROOT / "_shared/artifacts/crypto_hyper_strategies"
OUT.mkdir(parents=True, exist_ok=True)

COINS = ["BTC_USDT","ETH_USDT","BNB_USDT","SOL_USDT","ADA_USDT","DOGE_USDT",
         "LINK_USDT","OP_USDT","LTC_USDT","XRP_USDT","ARB_USDT","AVAX_USDT",
         "DOT_USDT","TRX_USDT"]

# Realistic-optimistic costs (per 1-side, in fraction)
TC_PER_SIDE = 0.0004     # 4 bps spot/perp taker
SLIPPAGE_PER_SIDE = 0.0005  # 5 bps base, multiplied by leverage
FUNDING_8H = 0.0002      # 2 bps per 8h base, multiplied by leverage
LEV_CAP = 25             # max leverage
DEFAULT_LEV_LONG = 1     # spot/perp 1x
DEFAULT_LEV_SHORT = 1
LEV_19 = 5
LEV_23 = 3

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_coin(coin: str, tf: str = "1h") -> Optional[pd.DataFrame]:
    p = CACHE / coin / f"{tf}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p).copy()
    df = df[~df.index.duplicated(keep="first")].sort_index()
    df["ret"] = df["close"].pct_change()
    return df

# ---------------------------------------------------------------------------
# Indicators (vectorized)
# ---------------------------------------------------------------------------

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.where(d > 0, 0.0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.where(d < 0, 0.0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def stoch_rsi(close: pd.Series, n: int = 14, k: int = 3, d: int = 3) -> tuple[pd.Series, pd.Series]:
    r = rsi(close, n)
    lo = r.rolling(n).min()
    hi = r.rolling(n).max()
    sr = (r - lo) / (hi - lo).replace(0, np.nan)
    K = sr.rolling(k).mean() * 100
    D = K.rolling(d).mean()
    return K, D

def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    ma = close.rolling(n).mean()
    sd = close.rolling(n).std()
    return ma, ma + k*sd, ma - k*sd

def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    plus_dm = (high.diff()).clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm <= plus_dm.shift(1).fillna(0)] = 0
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/n, adjust=False).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1/n, adjust=False).mean() / atr.replace(0, np.nan))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean()

# ---------------------------------------------------------------------------
# Trade simulator: long/short with leverage, hold N bars or trail/SL
# ---------------------------------------------------------------------------

def apply_costs(gross_ret: float, leverage: int, hold_hours: int, side: str = "long") -> float:
    """Realistic-optimistic: TC 4bps × 2 (in+out), slippage 5bps × lev × 2, funding 2bps/8h × lev."""
    sign = 1 if side == "long" else -1
    leveraged = sign * gross_ret * leverage
    tc = 2 * TC_PER_SIDE
    slip = 2 * SLIPPAGE_PER_SIDE * leverage
    funding_periods = max(1, hold_hours / 8.0)
    fund = FUNDING_8H * leverage * funding_periods
    return leveraged - tc - slip - fund

def simulate_trades(entries: pd.Series, df: pd.DataFrame, hold_bars: int,
                    side: str = "long", leverage: int = 1,
                    sl_pct: Optional[float] = None,
                    trail_pct: Optional[float] = None,
                    tp_pct: Optional[float] = None,
                    scale_out_pct: Optional[float] = None) -> list[dict]:
    """entries: bool/int Series indexed like df. Returns list of trade dicts."""
    idx = df.index
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    n = len(closes)
    trades = []
    entry_idx_arr = np.where(entries.fillna(False).values)[0]
    last_exit = -1
    for i in entry_idx_arr:
        if i <= last_exit:
            continue  # no overlapping positions per coin
        if i >= n - 1:
            break
        entry_price = closes[i]
        if entry_price <= 0 or not np.isfinite(entry_price):
            continue
        max_exit = min(i + hold_bars, n - 1)
        peak = entry_price
        exit_idx = max_exit
        exit_price = closes[max_exit]
        for j in range(i + 1, max_exit + 1):
            cur_high = highs[j]; cur_low = lows[j]; cur_close = closes[j]
            if side == "long":
                peak = max(peak, cur_high)
                if sl_pct is not None and cur_low <= entry_price * (1 - sl_pct):
                    exit_idx = j; exit_price = entry_price * (1 - sl_pct); break
                if tp_pct is not None and cur_high >= entry_price * (1 + tp_pct):
                    exit_idx = j; exit_price = entry_price * (1 + tp_pct); break
                if trail_pct is not None and cur_low <= peak * (1 - trail_pct):
                    exit_idx = j; exit_price = peak * (1 - trail_pct); break
            else:  # short
                peak = min(peak, cur_low)  # for short, peak is low
                if sl_pct is not None and cur_high >= entry_price * (1 + sl_pct):
                    exit_idx = j; exit_price = entry_price * (1 + sl_pct); break
                if tp_pct is not None and cur_low <= entry_price * (1 - tp_pct):
                    exit_idx = j; exit_price = entry_price * (1 - tp_pct); break
                if trail_pct is not None and cur_high >= peak * (1 + trail_pct):
                    exit_idx = j; exit_price = peak * (1 + trail_pct); break
        gross = (exit_price - entry_price) / entry_price
        hold_hours = exit_idx - i
        net = apply_costs(gross, leverage, hold_hours, side=side)
        # Optional scale-out: take half at +scale_out_pct, leave rest to exit
        if scale_out_pct and side == "long":
            target = entry_price * (1 + scale_out_pct)
            scaled_hit = (highs[i+1:exit_idx+1] >= target).any() if exit_idx > i else False
            if scaled_hit:
                # half of position closed at +scale_out_pct
                net = 0.5 * apply_costs(scale_out_pct, leverage, max(1, hold_hours//2), side="long") + \
                      0.5 * net
        trades.append({
            "entry_ts": idx[i].isoformat(),
            "exit_ts": idx[exit_idx].isoformat(),
            "entry_px": float(entry_price),
            "exit_px": float(exit_price),
            "gross_ret": float(gross),
            "net_ret": float(net),
            "leverage": leverage,
            "side": side,
            "hold_bars": int(hold_hours),
        })
        last_exit = exit_idx
    return trades

# ---------------------------------------------------------------------------
# Strategy generators (return entry-Series)
# ---------------------------------------------------------------------------

def strat_M07_4week_red(df: pd.DataFrame) -> pd.Series:
    """4 consecutive weekly red closes -> buy. Approx with 4×168h rolling sum<0."""
    weekly = df["close"].resample("1W").last().dropna()
    weekly_ret = weekly.pct_change()
    # 4 consecutive negative weeks
    cond = (weekly_ret < 0)
    streak = cond.astype(int).groupby((~cond).cumsum()).cumsum()
    sig_weekly = (streak >= 4)
    # propagate to hourly index: signal at the next hour after week ends
    sig = sig_weekly.reindex(df.index, method="ffill").shift(1).fillna(False)
    sig = sig & ~sig.shift(1).fillna(False)  # only first bar after signal becomes true
    return sig

def strat_M08_inverted_fractal(df: pd.DataFrame, dd_threshold: float = 0.50) -> pd.Series:
    """Deep DD from rolling ATH (90d). Buy when DD <= -50%."""
    win = 24 * 90
    rolling_ath = df["close"].rolling(win, min_periods=24*30).max()
    dd = df["close"] / rolling_ath - 1
    sig = (dd <= -dd_threshold) & (dd.shift(1) > -dd_threshold)
    return sig.fillna(False)

def strat_M09_post_impulse_accum(df: pd.DataFrame) -> pd.Series:
    """After +30% in 7d, then 14d sideways (range < 8%), buy."""
    ret_7d = df["close"].pct_change(24*7)
    high_14 = df["close"].rolling(24*14).max()
    low_14 = df["close"].rolling(24*14).min()
    range_14 = (high_14 - low_14) / df["close"]
    impulse_done = ret_7d.shift(24*14) > 0.30
    consolidating = range_14 < 0.08
    sig = impulse_done & consolidating
    sig = sig & ~sig.shift(1).fillna(False)
    return sig.fillna(False)

def strat_T10_bb_squeeze_breakout(df: pd.DataFrame) -> pd.Series:
    """Bollinger band squeeze (low width) + breakout above upper band."""
    ma, hi, lo = bollinger(df["close"], 20, 2.0)
    width = (hi - lo) / ma
    width_ma = width.rolling(50).mean()
    squeeze = width < 0.6 * width_ma
    breakout = (df["close"] > hi) & squeeze.shift(1).fillna(False)
    return breakout.fillna(False)

def strat_T12_stochrsi_oversold_cross(df: pd.DataFrame) -> pd.Series:
    """Stoch RSI K crosses above D from below 20 (oversold)."""
    K, D = stoch_rsi(df["close"], 14, 3, 3)
    cross = (K > D) & (K.shift(1) <= D.shift(1)) & (K.shift(1) < 20)
    return cross.fillna(False)

def strat_T13_rsi_bull_div(df: pd.DataFrame) -> pd.Series:
    """Price makes new 50-bar low but RSI does not (bullish divergence)."""
    r = rsi(df["close"], 14)
    px_low = df["close"] == df["close"].rolling(50).min()
    rsi_low_50 = r.rolling(50).min()
    div = px_low & (r > rsi_low_50.shift(1))
    return div.fillna(False)

def strat_T14_fib_618(df: pd.DataFrame) -> pd.Series:
    """Buy at retracement to 0.618 of last 50-bar swing."""
    swing_high = df["close"].rolling(50).max()
    swing_low = df["close"].rolling(50).min()
    fib_618 = swing_high - 0.618 * (swing_high - swing_low)
    near = (df["low"] <= fib_618) & (df["close"] > fib_618 * 0.99) & (df["close"] < fib_618 * 1.01)
    sig = near & ~near.shift(1).fillna(False)
    return sig.fillna(False)

def strat_S29_rsi95_short(df: pd.DataFrame) -> pd.Series:
    """RSI > 95 (extreme overbought) -> short."""
    r = rsi(df["close"], 14)
    sig = (r > 95) & (r.shift(1) <= 95)
    return sig.fillna(False)

def strat_S30_parabolic_div_short(df: pd.DataFrame) -> pd.Series:
    """Parabolic move (+30% in 3d) + RSI < prior peak (bearish div) -> short."""
    ret_3d = df["close"].pct_change(24*3)
    parabolic = ret_3d > 0.30
    r = rsi(df["close"], 14)
    px_high = df["close"] == df["close"].rolling(50).max()
    rsi_high_50 = r.rolling(50).max()
    div = px_high & (r < rsi_high_50.shift(1))
    sig = parabolic & div
    return sig.fillna(False)

def strat_S31_rsi85_fakeout_short(df: pd.DataFrame) -> pd.Series:
    """RSI crosses above 85 then back below 85 -> short."""
    r = rsi(df["close"], 14)
    cross_down = (r < 85) & (r.shift(1) >= 85)
    return cross_down.fillna(False)

def strat_A27_t3_adx_momentum(df: pd.DataFrame) -> pd.Series:
    """T3-MA proxy (EMA-of-EMA) + ADX > 25 momentum long."""
    ema1 = df["close"].ewm(span=8, adjust=False).mean()
    ema2 = ema1.ewm(span=8, adjust=False).mean()
    t3 = ema2.ewm(span=8, adjust=False).mean()
    adx_v = adx(df["high"], df["low"], df["close"], 14)
    cross_up = (df["close"] > t3) & (df["close"].shift(1) <= t3.shift(1)) & (adx_v > 25)
    return cross_up.fillna(False)

# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

STRATEGIES = [
    # (id, fn, side, hold_bars, sl, trail, tp, leverage, scale_out, family)
    ("M07_4week_red",        strat_M07_4week_red,       "long",  24*30, 0.20, None, None, 1,        None, "MEAN_REVERSION"),
    ("M08_inverted_fractal", strat_M08_inverted_fractal,"long",  24*90, 0.30, None, 1.50, 1,        None, "DEEP_DD_REVERSAL"),
    ("M09_post_impulse",     strat_M09_post_impulse_accum,"long",24*60,0.20, None, 0.80, 1,        None, "ACCUMULATION"),
    ("T10_bb_squeeze",       strat_T10_bb_squeeze_breakout,"long",24*14,0.10, 0.08, None, 1,        None, "BREAKOUT"),
    ("T12_stochrsi_cross",   strat_T12_stochrsi_oversold_cross,"long",24*5,0.05,None,0.10, 1,       None, "MOMENTUM_OS"),
    ("T13_rsi_bull_div",     strat_T13_rsi_bull_div,    "long",  24*10, 0.08, None, 0.20, 1,        None, "DIVERGENCE"),
    ("T14_fib_618",          strat_T14_fib_618,         "long",  24*7,  0.06, 0.04, None, 1,        None, "FIB"),
    ("T17_fib_236_trail",    strat_T14_fib_618,         "long",  24*14, 0.08, 0.024,None, 1,        None, "FIB"),  # 0.236 trail
    ("T18_fib_50_scaleout",  strat_T14_fib_618,         "long",  24*10, 0.06, 0.05, None, 1,        0.50, "FIB"),
    ("L19_micro_bb",         strat_T10_bb_squeeze_breakout,"long",24*14,0.04, 0.05, None, LEV_19,   None, "LEV_BREAKOUT"),
    ("L23_3x_bb",            strat_T10_bb_squeeze_breakout,"long",24*14,0.04, 0.05, None, LEV_23,   None, "LEV_BREAKOUT"),
    ("A27_t3_adx_mom",       strat_A27_t3_adx_momentum, "long",  24*5,  0.04, 0.03, None, 1,        None, "MOMENTUM"),
    ("S29_rsi95_short",      strat_S29_rsi95_short,     "short", 24*3,  0.06, None, 0.10, 1,        None, "OVERBOUGHT_SHORT"),
    ("S30_parabolic_div_short",strat_S30_parabolic_div_short,"short",24*5,0.10,None,0.15, 1,        None, "PARABOLIC_SHORT"),
    ("S31_rsi85_fakeout_short",strat_S31_rsi85_fakeout_short,"short",24*4,0.05,None,0.08, 1,        None, "FAKEOUT_SHORT"),
]

# ---------------------------------------------------------------------------
# Per-coin runner
# ---------------------------------------------------------------------------

def run_strategy_on_coin(strat_meta, coin: str, df: pd.DataFrame) -> dict:
    sid, fn, side, hold, sl, trail, tp, lev, scale_out, family = strat_meta
    try:
        entries = fn(df)
    except Exception as e:
        return {"strategy_id": sid, "coin": coin, "error": str(e), "n_trades": 0}
    if entries.sum() == 0:
        return {"strategy_id": sid, "coin": coin, "n_trades": 0, "mean_ret": 0.0, "median_ret": 0.0,
                "win_rate": 0.0, "total_pnl": 0.0, "sharpe": 0.0, "max_dd": 0.0, "leverage": lev,
                "side": side, "family": family, "trades": []}
    trades = simulate_trades(entries, df, hold_bars=hold, side=side, leverage=lev,
                              sl_pct=sl, trail_pct=trail, tp_pct=tp, scale_out_pct=scale_out)
    if not trades:
        return {"strategy_id": sid, "coin": coin, "n_trades": 0, "mean_ret": 0.0, "median_ret": 0.0,
                "win_rate": 0.0, "total_pnl": 0.0, "sharpe": 0.0, "max_dd": 0.0, "leverage": lev,
                "side": side, "family": family, "trades": []}
    rets = np.array([t["net_ret"] for t in trades])
    sharpe = (rets.mean() / rets.std()) * math.sqrt(252) if rets.std() > 0 else 0.0
    cumret = (1 + rets).cumprod()
    running_max = np.maximum.accumulate(cumret)
    max_dd = float((cumret / running_max - 1).min()) if len(cumret) else 0.0
    total_pnl = float(np.prod(1 + rets) - 1)
    return {
        "strategy_id": sid, "coin": coin, "n_trades": int(len(trades)),
        "mean_ret": float(rets.mean()), "median_ret": float(np.median(rets)),
        "win_rate": float((rets > 0).mean()), "total_pnl": total_pnl,
        "sharpe": float(sharpe), "max_dd": max_dd, "leverage": lev,
        "side": side, "family": family, "trades": trades,
        "first_entry": trades[0]["entry_ts"], "last_exit": trades[-1]["exit_ts"],
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"=== Crypto hyper-yield strategies rerun ({datetime.now().isoformat()}) ===")
    print(f"Strategies: {len(STRATEGIES)}, Coins: {len(COINS)}")
    print(f"Out: {OUT}")

    coin_data = {}
    for c in COINS:
        df = load_coin(c, "1h")
        if df is None or len(df) < 24*60:
            print(f"  [skip] {c}: missing or short")
            continue
        coin_data[c] = df
        print(f"  [load] {c}: {len(df)} rows {df.index.min()} -> {df.index.max()}")
    print()

    results_path = OUT / "results.jsonl"
    decomp_path = OUT / "per_coin_decomp.jsonl"
    if results_path.exists(): results_path.unlink()
    if decomp_path.exists(): decomp_path.unlink()

    all_results = []
    summary = {}

    for strat_meta in STRATEGIES:
        sid, _, side, hold, sl, trail, tp, lev, scale_out, family = strat_meta
        print(f"[strat] {sid}  side={side} hold={hold}h lev={lev}x family={family}")
        per_coin_results = {}
        all_trades = []
        for coin, df in coin_data.items():
            r = run_strategy_on_coin(strat_meta, coin, df)
            per_coin_results[coin] = r
            all_trades.extend([t["net_ret"] for t in r.get("trades", [])])
            with open(results_path, "a", encoding="utf-8") as f:
                # don't dump full trades list -> too big; only summary + first 5 trades
                rec = {k: v for k, v in r.items() if k != "trades"}
                rec["sample_trades"] = r.get("trades", [])[:5]
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

        if not all_trades:
            print(f"  no trades across all coins, skipping validation\n")
            summary[sid] = {"family": family, "n_trades_total": 0}
            continue

        rets = np.array(all_trades)
        n = len(rets)
        mean = float(rets.mean())
        std = float(rets.std()) if n > 1 else 0.0
        sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0.0
        winrate = float((rets > 0).mean())
        psr = qv.probabilistic_sharpe_ratio(rets, sr_benchmark=0.0)
        # DSR with n_trials = 15 strategies × 14 coins = 210 trials in our grid
        try:
            dsr = qv.deflated_sharpe_ratio(rets, n_trials=210, sr_benchmark=0.0)
        except Exception:
            dsr = 0.0
        try:
            min_trl = qv.minimum_track_record_length(sharpe / math.sqrt(252) if sharpe else 0.0,
                                                      sr_benchmark=0.0)
        except Exception:
            min_trl = None
        # PER-COIN compound (more honest than pooling thousands of trades sequentially)
        per_coin_compound = [r.get("total_pnl", 0.0) for r in per_coin_results.values() if r.get("n_trades", 0) > 0]
        median_coin_pnl = float(np.median(per_coin_compound)) if per_coin_compound else 0.0
        best_coin_pnl = float(np.max(per_coin_compound)) if per_coin_compound else 0.0
        # Pool-level compound (kept for reference, but interpret with caution)
        total_ret = float(np.prod(1 + rets) - 1) if n > 0 else 0.0
        # Span
        per_coin_spans = [r.get("first_entry") for r in per_coin_results.values() if r.get("first_entry")]
        per_coin_spans_end = [r.get("last_exit") for r in per_coin_results.values() if r.get("last_exit")]
        period_start = min(per_coin_spans) if per_coin_spans else None
        period_end = max(per_coin_spans_end) if per_coin_spans_end else None
        # Years approx
        years = None
        if period_start and period_end:
            try:
                ds = pd.Timestamp(period_start)
                de = pd.Timestamp(period_end)
                years = max(0.1, (de - ds).total_seconds() / (365.25*24*3600))
            except Exception:
                pass
        cagr = ((1 + total_ret) ** (1/years) - 1) if years and total_ret > -1 else None

        verdict = "EFFECT_STRONG" if (psr > 0.95 and mean > 0.005) else \
                  "EFFECT_MODERATE" if (psr > 0.80 and mean > 0.002) else \
                  "EFFECT_WEAK" if (sharpe > 0.5 and mean > 0) else "NO_EFFECT"

        # Top per-coin contributors (strong individual edges even if pool is flat)
        coin_summaries = []
        for c, r in per_coin_results.items():
            if r.get("n_trades", 0) < 3:
                continue
            coin_summaries.append({
                "coin": c, "n_trades": r["n_trades"], "mean_ret": r["mean_ret"],
                "win_rate": r["win_rate"], "total_pnl": r["total_pnl"], "sharpe": r["sharpe"],
            })
        coin_summaries.sort(key=lambda x: x["total_pnl"], reverse=True)

        s = {
            "strategy_id": sid, "family": family, "side": side, "leverage": lev,
            "n_trades_total": n, "mean_ret": mean, "std_ret": std, "win_rate": winrate,
            "sharpe": sharpe, "psr": float(psr), "dsr": float(dsr),
            "total_pnl_pool": total_ret, "cagr_approx": cagr,
            "median_coin_pnl": median_coin_pnl, "best_coin_pnl": best_coin_pnl,
            "period_start": period_start, "period_end": period_end, "years": years,
            "verdict": verdict, "top_coins": coin_summaries[:5],
            "min_trl_years": min_trl,
        }
        summary[sid] = s
        all_results.append(s)

        print(f"  N={n}  mean={mean*100:+.2f}%  WR={winrate*100:.1f}%  SR={sharpe:.2f}  "
              f"PSR={psr:.2f}  DSR={dsr:.2f}  totalPnl={total_ret*100:+.1f}%  "
              f"CAGR~{(cagr or 0)*100:+.1f}%  -> {verdict}")
        if coin_summaries:
            top = coin_summaries[0]
            print(f"    top coin: {top['coin']} N={top['n_trades']} mean={top['mean_ret']*100:+.2f}% pnl={top['total_pnl']*100:+.1f}%")
        print()

        # === Tieto-portfolio: pooli-tason havainto ===
        try:
            fp.record_observation(
                feature=f"strategy_{sid}",
                target="crypto_pool_pnl",
                method="event_study",
                value=mean,
                n_samples=n,
                applies_to=f"crypto_pool_{len(per_coin_results)}_coins",
                bot="crypto-finance",
                strategy_id=sid,
                p_value=(1 - psr) if psr else None,
                target_class="portfolio_metric",
                domain="alt",
                replication_count=len([r for r in per_coin_results.values() if r.get("n_trades", 0) > 0]),
                raw_value=total_ret,
                confidence_pct=psr * 100 if psr else None,
                period_start=period_start,
                period_end=period_end,
                participants=list(per_coin_results.keys()),
                metadata={
                    "family": family, "side": side, "leverage": lev,
                    "win_rate": winrate, "sharpe": sharpe, "dsr": dsr,
                    "verdict": verdict, "cagr_approx": cagr,
                    "source": "crypto_hyper_strategies_research_2026_04_27",
                }
            )
        except Exception as e:
            print(f"  [warn] portfolio record failed: {e}")

        # === Per-coin decomposition (KRIITTINEN: säilyttää yksittäiset coinedge:t) ===
        try:
            trades_for_decomp = []
            for c, r in per_coin_results.items():
                for t in r.get("trades", []):
                    trades_for_decomp.append({
                        "ticker": c, "pnl_pct": t["net_ret"] * 100,
                        "entry_ts": t["entry_ts"], "exit_ts": t["exit_ts"],
                    })
            if trades_for_decomp:
                fp.record_per_ticker_decomposition(
                    trades=trades_for_decomp,
                    strategy_id=sid,
                    bot="crypto-finance",
                    target_class="crypto_price",
                    domain="alt",
                    min_trades_per_ticker=3,
                    metadata={"family": family, "side": side, "leverage": lev,
                              "source": "crypto_hyper_strategies_2026_04_27"}
                )
        except Exception as e:
            print(f"  [warn] per-ticker decomp failed: {e}")

        # Per-coin decomp file
        for c, r in per_coin_results.items():
            with open(decomp_path, "a", encoding="utf-8") as f:
                rec = {k: v for k, v in r.items() if k != "trades"}
                rec["strategy_id"] = sid; rec["family"] = family
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    # === Summary ===
    summary_path = OUT / "strategy_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    # Sort and print final ranking — by best_coin_pnl (per-coin edge survives pool flat)
    print("\n=== FINAL RANKING (by best_coin_pnl, then PSR) ===")
    ranked = sorted(all_results, key=lambda x: (x.get("best_coin_pnl", -1), x.get("psr", 0)), reverse=True)
    for s in ranked:
        print(f"  {s['strategy_id']:30s} {s['family']:18s} N={s['n_trades_total']:5d}  "
              f"meanRet={s['mean_ret']*100:+5.2f}%  WR={s['win_rate']*100:4.1f}%  "
              f"medCoin={s['median_coin_pnl']*100:+7.1f}%  bestCoin={s['best_coin_pnl']*100:+7.1f}%  "
              f"PSR={s['psr']:.2f}  -> {s['verdict']}")
        if s.get("top_coins"):
            top3 = s["top_coins"][:3]
            top_str = ", ".join([f"{tc['coin']}:N{tc['n_trades']}/{tc['mean_ret']*100:+.2f}%/pnl{tc['total_pnl']*100:+.0f}%" for tc in top3])
            print(f"    top3: {top_str}")

    print(f"\nResults: {results_path}")
    print(f"Per-coin: {decomp_path}")
    print(f"Summary: {summary_path}")
    print(f"Portfolio: {fp.PORTFOLIO_PATH}")

if __name__ == "__main__":
    main()
