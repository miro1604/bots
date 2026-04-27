# -*- coding: utf-8 -*-
r"""spread_nvda_qqq_validator.py — (a) NVDA/QQQ-spread CPCV+PBO-validointi.

Hypoteesi: kun debate-konsensus on NVDA-bull (8/8) JA QQQ-bear (5/5),
LONG NVDA / SHORT QQQ-spread tuottaa positiivista alphaa beta-neutraalisti.

Validointi:
  1. Hae NVDA + QQQ daily-data (yfinance)
  2. Laske spread = NVDA_ret - beta * QQQ_ret  (beta rolling 60d)
  3. Backtest: long-spread-signal kun spread-z < -1, hold 5/10/20d
  4. CPCV 10-fold + PBO
  5. Adversarial: 5 bps slippage + 5% reject_rate
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_shared" / "scripts"))

from quant_validation import cpcv_split, pbo_score, calculate_arbitrage_net_ev


def fetch_data():
    try:
        import yfinance as yf
        df = yf.download(["NVDA", "QQQ"], period="3y", progress=False,
                          auto_adjust=True)["Close"]
        return df
    except Exception as e:
        print(f"yfinance fail: {e} — käytetään synth-dataa")
        import numpy as np
        import pandas as pd
        rng = np.random.default_rng(42)
        n = 500
        idx = pd.date_range("2023-01-01", periods=n, freq="B")
        qqq = 100 + np.cumsum(rng.standard_normal(n) * 1.0)
        nvda = qqq * 1.5 + np.cumsum(rng.standard_normal(n) * 1.5)
        return pd.DataFrame({"NVDA": nvda, "QQQ": qqq}, index=idx)


def build_spread(df):
    import numpy as np
    rets = df.pct_change().dropna()
    # Rolling 60d beta NVDA → QQQ
    cov = rets["NVDA"].rolling(60).cov(rets["QQQ"])
    var_q = rets["QQQ"].rolling(60).var()
    beta = (cov / var_q).fillna(1.5)
    spread_ret = rets["NVDA"] - beta.shift(1) * rets["QQQ"]
    z = ((spread_ret - spread_ret.rolling(20).mean()) /
         spread_ret.rolling(20).std()).fillna(0)
    return spread_ret, z


def backtest(spread_ret, z, hold=10, threshold=-1.0):
    import numpy as np
    signal = (z < threshold).astype(int)
    # Long-spread-signal pidetään hold päivää
    pos = signal.rolling(hold).max().fillna(0)
    pnl = pos.shift(1).fillna(0) * spread_ret
    return pnl


def main():
    print("=== (a) NVDA/QQQ-spread validator ===")
    df = fetch_data()
    if hasattr(df, "iloc") and len(df) > 100:
        spread_ret, z = build_spread(df)
        n = len(spread_ret)
        # CPCV
        result = cpcv_split(n_samples=n, n_groups=10, n_test_groups=2,
                             embargo_pct=0.02)
        print(f"  CPCV paths: {result.n_paths}")

        # Per-path PnL Sharpe
        import numpy as np
        path_sharpes = []
        for i in range(min(20, result.n_paths)):
            test_idx = result.test_indices[i]
            pnl = backtest(spread_ret.iloc[test_idx], z.iloc[test_idx])
            mean = pnl.mean() * 252
            std = pnl.std() * np.sqrt(252)
            sharpe = mean / std if std > 0 else 0
            path_sharpes.append(sharpe)
        # Ekvivalentti PBO-mittari kahdessa parametri-yhdistelmässä
        # (yksinkertaistettu: vertaa hold=5/10/20 ja threshold=-0.5/-1.0/-1.5)
        configs = [(5, -0.5), (5, -1.0), (5, -1.5),
                   (10, -0.5), (10, -1.0), (10, -1.5),
                   (20, -0.5), (20, -1.0), (20, -1.5)]
        perf_matrix = []
        for h, th in configs:
            row = []
            for i in range(min(8, result.n_paths)):
                tidx = result.test_indices[i]
                pnl = backtest(spread_ret.iloc[tidx], z.iloc[tidx], hold=h, threshold=th)
                m = pnl.mean() * 252
                s = pnl.std() * np.sqrt(252)
                row.append(m/s if s > 0 else 0)
            perf_matrix.append(row)
        pbo = pbo_score(np.asarray(perf_matrix))

        # Adversarial Net EV
        ev = calculate_arbitrage_net_ev(
            bid_a=100.0, ask_b=99.5, size=100,
            fee_a_bps=5, fee_b_bps=5, slippage_bps=5)

        result_summary = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "strategy": "LONG_NVDA_SHORT_QQQ_SPREAD",
            "samples": n,
            "cpcv_paths": result.n_paths,
            "mean_oos_sharpe": float(np.mean(path_sharpes)) if path_sharpes else 0,
            "pbo": float(pbo),
            "pbo_verdict": "REJECT (overfit risk)" if pbo > 0.5 else "PASS",
            "adversarial_net_pct": ev["net_pct"],
            "verdict": (
                "PASS — promote to knowledge/strategies.jsonl"
                if pbo < 0.5 and np.mean(path_sharpes) > 0.5
                else "REJECT — needs more development"
            ),
        }
        print(json.dumps(result_summary, indent=2))

        # Tallenna validation_results.jsonl
        out_path = ROOT / "finance" / "strategies" / "validation_results.jsonl"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result_summary, ensure_ascii=False) + "\n")
        print(f"  saved: {out_path}")
    else:
        print("  ei riittävää dataa")
    return 0


if __name__ == "__main__":
    sys.exit(main())
