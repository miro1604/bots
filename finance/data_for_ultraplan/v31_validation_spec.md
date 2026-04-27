# v31 Validation Spec — for /ultraplan cloud sandbox

## Goal
Validate `finance/high_cagr_v31.py` claim that strategy delivers
**CAGR ≥ 30% over 7-10y rolling windows with N ≥ 100 trades** using PDF2-grade
quantitative protocol (CPCV + PBO + walk-forward + adversarial).

Local in-sample results to verify (from `finance/high_cagr_v31_results.txt`):
- 10y window 2015-01 → 2024-12: **CAGR 42.24%, N=130, win 11.5%**
- 10y window 2016-01 → 2025-12: **CAGR 48.32%, N=131, win 12.1%**
- 7y windows median: **CAGR 33.76%, N=107**
- 8y windows median: **CAGR 39.79%, N=118**

## Strategy parameters (frozen — do not change)
- Signal: `peak_50 > 15%` AND `dist_378pv < -20%` on alpha-residual vs sector-ETF
- Beta-window: 120 days vs sector-ETF (default XLI; per-ticker sector mapping
  available in `v20_full_sector_map.csv`)
- Hold: 680 trading days (or until SL hit)
- Stop-loss: -12% from entry (intraday, uses Low)
- Picks per signal: 2 highest-ranked
- Transaction cost: 1% per side
- Universe: 673 tickers (S&P 500 + S&P 400 growth sectors + MEGA_POOL)
- Date range: 2015-01-01 → 2026-04-26
- Excluded sectors: Utilities, Real Estate, Consumer Staples
- Hype blacklist: GME, AMC, BB, BBBY, CLOV, EXPR, HOOD, KOSS, NOK, PLTR, RIDE, SDC, SPCE, WISH

## Data files (offline — do NOT call yfinance/Wikipedia)
All in `finance/data_for_ultraplan/`:
- `v20_full_prices_close.parquet` — 673 × 3096, auto-adjusted Close
- `v20_full_prices_low.parquet` — 673 × 3096, auto-adjusted Low (for SL)
- `v20_full_prices_volume.parquet` — Volume (for participation-rate sanity check)
- `v20_full_daily_returns.parquet` — pre-computed pct_change
- `v20_full_sector_map.csv` — symbol → GICS Sector → sector_etf
- `v20_full_metadata.json` — universe spec

## Validation protocol (PDF2-grade)

### 1. Bug audit (CRITICAL)
Read `finance/high_cagr_v31.py` end-to-end. List any:
- Compound bugs (does portfolio compound across overlapping holds correctly?)
- Look-ahead bias (peak_50 / dist_378 must use only data ≤ signal date)
- Survivorship bias (universe is *current* SP500/SP400 → ignores delisted tickers).
  Quantify the bias: how many tickers in the dataset were already delisted by 2020?
- SL-recovery bug (does SL exit cleanly without paying full hold return?)

Fix all bugs before backtesting. Report each fix in the verdict.

### 2. CPCV (Combinatorial Purged Cross-Validation, López de Prado 2018)
- N = 10 splits, k = 2 test groups
- Embargo = 21 days between train/test
- Purge overlapping holds (since hold=680d, this is significant)
- Report: mean OOS Sharpe, std, 95% CI; median OOS CAGR

### 3. PBO (Probability of Backtest Overfitting, Bailey et al 2017)
- Compute on the trial space of {peak_thr ∈ [10,15,20], dist_thr ∈ [-15,-20,-25,-30]}
  → 12 trials
- Report PBO. **Pass criterion: PBO < 0.5**

### 4. Walk-forward
- 5-year in-sample → 1-year out-of-sample, sliding 1 year
- ≥ 5 cycles (2015-19→20, 2016-20→21, 2017-21→22, 2018-22→23, 2019-23→24)
- Per-cycle: OOS CAGR, N, max DD, SR
- Report mean ± std and how many cycles are positive

### 5. Adversarial
- (a) Signal inversion (long ↔ short) — alpha must collapse, otherwise look-ahead
- (b) +50% transaction cost shock (TC=1.5% per side)
- (c) 30% randomly-rejected entries (slippage proxy)
- (d) Survivorship-stripped universe (only tickers alive throughout 2015-2026)
- Report degradation per perturbation

### 6. Benchmark
- SPY buy-and-hold same period, same monthly investment schedule (3000 €/month)
- QQQ buy-and-hold ditto
- Report v31 vs SPY vs QQQ on CAGR, max DD, SR

## Deliverable format
```
VERDICT: VALIDATED | REJECTED | INCONCLUSIVE
True 10y CAGR (mean of 2 windows): ____%  (claim: 45.28%)
CPCV mean OOS Sharpe:            ____  (95% CI [_, _])
PBO:                             ____  (must be < 0.5)
Walk-forward win rate:           ___ / 5 cycles positive
Bug fixes applied:               <list>
Survivorship bias estimate:      ___% of universe was delisted by 2020
Adversarial robustness:          <table per perturbation>
SPY benchmark CAGR (same period):____%
QQQ benchmark CAGR (same period):____%
v31 vs SPY excess CAGR:          ____pp
Top-3 risks user must know:      <list>
Recommendation:                  proceed_to_paper_trade | recalibrate | reject
```

## Constraints
- NO network calls (yfinance, requests, urllib). All data is in Parquet.
- Use pyarrow for Parquet read.
- If a ticker is missing from a parquet, log it as survivorship case, don't skip silently.
- Compute time budget: aim < 30 min on a t4-medium-equivalent.
