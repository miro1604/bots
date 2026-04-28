# -*- coding: utf-8 -*-
r"""alpha_peak_class_analysis.py — luokittele KAIKKI 2877 alpha-peak-signaalia,
katso tulokset per luokka, etsi oikeat kustomointi-mittarit.

Käyttäjän mandaatti 2026-04-28:
  "Ei hylätä mitään luokkia tässä kokeilussa. Katsotaan kun peak_20 > 15 mihin
  luokkaan ajautuu ja mikä tulos. Tutki yritystä kun signaali tulee. Kokeile
  myös eri parametreja kuin nykyiset, käytä maalaisjärkeä ja opittuja asioita."

Vaiheet:
  1) Lue trades_unfiltered.jsonl (KAIKKI 2877 kauppaa, jokainen tagattu jo
     regime/persona/lifecycle-luokalla)
  2) Per-luokka-statistiikat (regime × persona × lifecycle)
  3) "Tutki yritystä": laske jokaiselle kaupalle MUUT mittarit signaalipäivänä:
       - momentum_60d (60d return ennen signaalia)
       - momentum_252d (1y return ennen signaalia)
       - dist_from_252d_high (% nykyhinnasta vuoden huipusta)
       - vol_ratio (volyymi signaalipäivänä vs 20d MA)
       - spy_60d (markkinan tila)
       - atr_pct (volatiliteetti)
  4) Regressio + ryhmäerot → mikä ENNUSTAA 400d tuottoa
  5) Suositukset per luokka — KÄYTÄNNÖN parametri-ehdotukset
"""
from __future__ import annotations
import sys, json, math
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from collections import defaultdict
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path("C:/Users/puros/bots")
sys.path.insert(0, str(ROOT / "_shared/scripts"))
import quant_validation_v2 as qv

TRADES_PATH = ROOT / "_shared/artifacts/alpha_peak_filtered/trades_unfiltered.jsonl"
V20_CLOSE = ROOT / "finance/data_for_ultraplan/v20_full_prices_close_cleaned.parquet"
V20_VOL   = ROOT / "finance/data_for_ultraplan/v20_full_prices_volume.parquet"
ASSET     = ROOT / "finance/asset_cache"
OUT       = ROOT / "_shared/artifacts/alpha_peak_filtered"

def load_spy_close():
    import pickle
    p = ASSET / "_GSPC.pkl"
    with open(p, "rb") as f: df = pickle.load(f)
    return df["Close"]

def enrich_trades(trades: list[dict], close_df: pd.DataFrame, vol_df: pd.DataFrame,
                  spy: pd.Series) -> pd.DataFrame:
    """Per-trade lisämittarit signaalipäivänä."""
    out = []
    for t in trades:
        tk = t["ticker"]
        entry = pd.Timestamp(t["entry_date"])
        if tk not in close_df.columns: continue
        s = close_df[tk].dropna()
        avail = s.index[s.index <= entry]
        if len(avail) < 252: continue
        d = avail[-1]
        px = float(s.loc[d])
        # Momentum lookbacks
        mom_60d = float(s.loc[d] / s.loc[avail[-60]] - 1) if len(avail) > 60 else None
        mom_252d = float(s.loc[d] / s.loc[avail[-252]] - 1) if len(avail) > 252 else None
        # Distance from 252d high
        win252 = s.loc[avail[-252]:d]
        d52h = float(px / win252.max() - 1) if len(win252) > 0 else None
        # Volume ratio
        if tk in vol_df.columns:
            v = vol_df[tk].dropna().reindex(avail)
            v_ma20 = v.rolling(20).mean()
            v_ratio = float(v.loc[d] / v_ma20.loc[d]) if pd.notna(v_ma20.loc[d]) and v_ma20.loc[d] > 0 else None
        else:
            v_ratio = None
        # ATR-pct (proxy: 20d std of daily returns)
        ret20 = s.loc[avail[-20]:d].pct_change()
        atr_pct = float(ret20.std()) if len(ret20.dropna()) > 5 else None
        # SPY 60d
        spy_avail = spy.index[spy.index <= entry]
        spy_60d = None
        if len(spy_avail) > 60:
            spy_60d = float(spy.loc[spy_avail[-1]] / spy.loc[spy_avail[-60]] - 1)
        out.append({
            **t,
            "mom_60d": mom_60d, "mom_252d": mom_252d, "dist_from_252d_high": d52h,
            "vol_ratio": v_ratio, "atr_pct": atr_pct, "spy_60d": spy_60d,
        })
    return pd.DataFrame(out)

def class_stats(df: pd.DataFrame, group_cols: list, min_n: int = 20) -> pd.DataFrame:
    g = df.groupby(group_cols)
    stats = g["net_ret"].agg(["count", "mean", "median", "std", "min", "max"]).round(4)
    stats["win_rate"] = g["net_ret"].apply(lambda x: (x > 0).mean()).round(3)
    stats["pct_of_total"] = (stats["count"] / len(df) * 100).round(1)
    # PSR per group (skip if too few)
    psrs = []
    for keys, sub in g:
        if len(sub) >= 30:
            psrs.append((keys, qv.probabilistic_sharpe_ratio(sub["net_ret"].values, 0.0)))
        else:
            psrs.append((keys, None))
    psr_dict = {k: v for k, v in psrs}
    stats["psr"] = [psr_dict.get(idx) for idx in stats.index]
    stats["psr"] = stats["psr"].apply(lambda x: round(x, 3) if x is not None else None)
    stats = stats.sort_values("count", ascending=False)
    if min_n:
        stats = stats[stats["count"] >= min_n]
    return stats

def correlate_predictors(df: pd.DataFrame) -> pd.DataFrame:
    """Lineaariset korrelaatiot 'enrich' -mittareiden ja 400d-tuoton välillä."""
    cols = ["alpha_peak_at_entry", "mom_60d", "mom_252d", "dist_from_252d_high",
            "vol_ratio", "atr_pct", "spy_60d"]
    rows = []
    for c in cols:
        if c not in df.columns: continue
        sub = df[[c, "net_ret"]].dropna()
        if len(sub) < 50:
            rows.append({"feature": c, "n": len(sub), "pearson": None, "spearman": None}); continue
        from scipy.stats import pearsonr, spearmanr
        pr, pp = pearsonr(sub[c], sub["net_ret"])
        sr, sp = spearmanr(sub[c], sub["net_ret"])
        rows.append({"feature": c, "n": int(len(sub)),
                     "pearson": round(pr, 4), "p_pearson": round(pp, 4),
                     "spearman": round(sr, 4), "p_spearman": round(sp, 4)})
    return pd.DataFrame(rows)

def quintile_analysis(df: pd.DataFrame, feature: str) -> pd.DataFrame:
    """Kvintiili-analyysi: jaa 5 ryhmään feature-arvon mukaan, katso net_ret."""
    sub = df[[feature, "net_ret"]].dropna()
    if len(sub) < 100: return None
    try:
        sub["q"] = pd.qcut(sub[feature], 5, duplicates="drop")
    except Exception:
        return None
    agg = sub.groupby("q", observed=True)["net_ret"].agg(["count","mean","median"]).round(4)
    agg["win_rate"] = sub.groupby("q", observed=True)["net_ret"].apply(lambda x: (x > 0).mean()).round(3)
    return agg

def main():
    print(f"=== Alpha-peak class analysis ({datetime.now().isoformat()}) ===")
    trades = [json.loads(l) for l in open(TRADES_PATH, encoding="utf-8")]
    print(f"  loaded {len(trades)} unfiltered trades")

    print("  loading prices + volumes...")
    close_df = pd.read_parquet(V20_CLOSE)
    vol_df = pd.read_parquet(V20_VOL)
    spy = load_spy_close()

    print("  enriching trades with signal-day metrics...")
    df = enrich_trades(trades, close_df, vol_df, spy)
    print(f"  enriched {len(df)} trades")

    # ============================================================
    # 1) PER-CLASS STATS
    # ============================================================
    print("\n" + "="*88)
    print("1) PER-LUOKKA TUOTOT (regime × persona × lifecycle)")
    print("="*88)

    print("\n--- Regime alone ---")
    s_reg = class_stats(df, ["regime_at_entry"], min_n=10)
    print(s_reg.to_string())

    print("\n--- Persona alone ---")
    s_per = class_stats(df, ["persona"], min_n=10)
    print(s_per.to_string())

    print("\n--- Lifecycle alone ---")
    s_lc = class_stats(df, ["lifecycle"], min_n=10)
    print(s_lc.to_string())

    print("\n--- Regime × persona ---")
    s_rp = class_stats(df, ["regime_at_entry","persona"], min_n=20)
    print(s_rp.to_string())

    print("\n--- Regime × lifecycle ---")
    s_rl = class_stats(df, ["regime_at_entry","lifecycle"], min_n=20)
    print(s_rl.to_string())

    print("\n--- Persona × lifecycle ---")
    s_pl = class_stats(df, ["persona","lifecycle"], min_n=20)
    print(s_pl.to_string())

    print("\n--- TRIPLE: regime × persona × lifecycle (only N>=20) ---")
    s_full = class_stats(df, ["regime_at_entry","persona","lifecycle"], min_n=20)
    print(s_full.to_string())

    # ============================================================
    # 2) PREDICTOR CORRELATIONS
    # ============================================================
    print("\n" + "="*88)
    print("2) MITKÄ MITTARIT ENNUSTAVAT 400d TUOTTOA?")
    print("="*88)
    corr = correlate_predictors(df)
    print(corr.to_string(index=False))

    # ============================================================
    # 3) QUINTILE ANALYSIS — for each predictor
    # ============================================================
    print("\n" + "="*88)
    print("3) KVINTIILI-ANALYYSI — Q1 (matala) → Q5 (korkea)")
    print("="*88)
    for f in ["alpha_peak_at_entry","mom_60d","mom_252d","dist_from_252d_high","vol_ratio","atr_pct","spy_60d"]:
        if f not in df.columns: continue
        q = quintile_analysis(df, f)
        if q is None: continue
        print(f"\n--- {f} ---")
        print(q.to_string())

    # ============================================================
    # 4) Per-class quintile of alpha_peak (signal strength)
    # ============================================================
    print("\n" + "="*88)
    print("4) ALPHA_PEAK-VOIMAKKUUS per regime (onko korkeampi peak parempi tuotto?)")
    print("="*88)
    for reg in ["calm","normal","crisis"]:
        sub = df[df["regime_at_entry"] == reg]
        if len(sub) < 100: continue
        try:
            sub_c = sub.copy()
            sub_c["q"] = pd.qcut(sub_c["alpha_peak_at_entry"], 4,
                                  duplicates="drop", labels=["Q1","Q2","Q3","Q4"])
            agg = sub_c.groupby("q", observed=True)["net_ret"].agg(["count","mean","median"]).round(4)
            agg["win_rate"] = sub_c.groupby("q", observed=True)["net_ret"].apply(lambda x: (x > 0).mean()).round(3)
            print(f"\n--- regime={reg} ---")
            print(agg.to_string())
        except Exception as e:
            print(f"  regime={reg} err: {e}")

    # ============================================================
    # 5) SAVE FOR FUTURE USE
    # ============================================================
    out_data = {
        "as_of": datetime.now().isoformat(),
        "n_trades": int(len(df)),
        "regime_stats": s_reg.reset_index().to_dict(orient="records"),
        "persona_stats": s_per.reset_index().to_dict(orient="records"),
        "lifecycle_stats": s_lc.reset_index().to_dict(orient="records"),
        "regime_x_persona": s_rp.reset_index().to_dict(orient="records"),
        "regime_x_lifecycle": s_rl.reset_index().to_dict(orient="records"),
        "persona_x_lifecycle": s_pl.reset_index().to_dict(orient="records"),
        "triple_class": s_full.reset_index().to_dict(orient="records"),
        "predictor_correlations": corr.to_dict(orient="records"),
    }
    out_path = OUT / "class_analysis.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2, default=str)
    df.to_csv(OUT / "trades_enriched.csv", index=False)
    print(f"\nSaved: {out_path}")
    print(f"Saved: {OUT / 'trades_enriched.csv'}")

if __name__ == "__main__":
    main()
