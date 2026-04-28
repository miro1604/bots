# -*- coding: utf-8 -*-
r"""dynamic_screener.py — markkinaregiimi- + osakekohtaisesti adaptiivinen seulonta.

Käyttäjän mandaatti 2026-04-28 (kahden tutkimusraportin synteesi):
  "Osakkeet ovat yksilöitä eivätkä saman muotin osia. Markkinaregiimi muuttaa
  parametrien optimialueet. Aivohalvaus = rakenteellinen murros pitää tunnistaa.
  Käytä maalaisjärkeä — älä yritä toteuttaa täydellistä DRL+GP+MAS-blueprintiä,
  vaan poimi käyttökelpoiset palaset olemassa olevaan B2-runkoon."

5 modulia yhdessä paketissa (riippuvuusvapaa ydinasetelma — sklearn.mixture proxynä HMM:lle):

  1. RegimeDetector   — markkinaregiimi 3-tilainen (calm / normal / crisis)
                        VIX-quantile + GaussianMixture vahvistus
                        Tutkimus 1, kohta 6 (HMM/Markov)
  2. PersonalityProfiler — osakkeen "luonne" 4-personaan
                        IVOL + jump asymmetry + Hurst + volume CV + sector corr
                        Tutkimus 1, taulukko "latentti profiili"
  3. LifecycleClassifier — yrityksen elinkaaren vaihe (proxy ilman SEC-dataa)
                        rolling 3y CAGR + DD + vol → Growth/Mature/Declining
                        Tutkimus 2, Dickinson-mallin yksinkertaistettu proxy
  4. BreakDetector    — CUSUM + Page-Hinkley + ICSS-tyylinen vol-jump
                        ("aivohalvaus" — milloin osake ei ole sama yksilö enää)
                        Tutkimus 1, kohta 4 / Tutkimus 2, kohta 4
  5. StrategyRouter   — yhdistää regime+persona+lifecycle+break → strategia-perhe
                        Päätösmatriisi (ei DRL — pragmaattinen ekspertti-sääntöpuu)

Käyttö:
  python _shared/scripts/dynamic_screener.py             # demo: 50 osaketta + SPY + VIX
  python _shared/scripts/dynamic_screener.py --tickers AAPL,MSFT,NVDA,TSLA

Tulokset:
  artifacts/dynamic_screener/screener_results.jsonl      — per ticker per päivä
  artifacts/dynamic_screener/regime_history.jsonl        — markkinaregiimi-aikasarja
  + feature_portfolio.jsonl: persona-, lifecycle-, break-, regime-conditional havainnot
"""
from __future__ import annotations
import sys, os, json, math, pickle, argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture

ROOT = Path("C:/Users/puros/bots")
sys.path.insert(0, str(ROOT / "_shared/scripts"))
import feature_portfolio as fp

ASSET_CACHE = ROOT / "finance/asset_cache"
OUT = ROOT / "_shared/artifacts/dynamic_screener"
OUT.mkdir(parents=True, exist_ok=True)

# ============================================================================
# 1. RegimeDetector — markkinaregiimi 3-tilainen
# ============================================================================

class RegimeDetector:
    """3-tilainen markkinaregiimi.

    Tila 0 = calm   (matala vol, nouseva trendi)
    Tila 1 = normal (keskitason vol)
    Tila 2 = crisis (korkea vol / drawdown)

    Kaksi täydentävää näkökulmaa, joiden enemmistö ratkaisee:
      a) VIX-quantile (kova rajataso historiallisesta jakaumasta)
      b) GaussianMixture(3) (SPY 20d realized vol + 60d max DD)
    """
    def __init__(self):
        self.gmm: Optional[GaussianMixture] = None
        self.gmm_means_: Optional[np.ndarray] = None
        self.vix_q33: Optional[float] = None
        self.vix_q67: Optional[float] = None
        self.fit_period: tuple[str, str] = ("", "")

    def fit(self, vix: pd.Series, spy_close: pd.Series):
        v = vix.dropna()
        self.vix_q33 = float(v.quantile(0.33))
        self.vix_q67 = float(v.quantile(0.67))
        ret = spy_close.pct_change().dropna()
        rv20 = ret.rolling(20).std() * math.sqrt(252)
        cum = (1 + ret).cumprod()
        dd60 = (cum / cum.rolling(60).max() - 1).rolling(60).min().abs()
        feats = pd.concat([rv20, dd60], axis=1).dropna()
        feats.columns = ["rv20", "dd60"]
        self.gmm = GaussianMixture(n_components=3, random_state=0, covariance_type="full",
                                    n_init=4, max_iter=200)
        self.gmm.fit(feats.values)
        means = self.gmm.means_
        order = np.argsort(means[:, 0])  # sort by vol ascending
        self.gmm_means_ = means[order]
        self._gmm_label_map = {old: new for new, old in enumerate(order)}
        self.fit_period = (str(feats.index.min().date()), str(feats.index.max().date()))
        return self

    def predict(self, vix: pd.Series, spy_close: pd.Series) -> pd.Series:
        idx = spy_close.index
        ret = spy_close.pct_change()
        rv20 = ret.rolling(20).std() * math.sqrt(252)
        cum = (1 + ret).cumprod()
        dd60 = (cum / cum.rolling(60).max() - 1).rolling(60).min().abs()
        feats = pd.concat([rv20, dd60], axis=1)
        feats.columns = ["rv20", "dd60"]
        # GMM prediction
        valid = feats.dropna()
        gmm_raw = self.gmm.predict(valid.values)
        gmm_state = pd.Series([self._gmm_label_map[r] for r in gmm_raw], index=valid.index)
        gmm_state = gmm_state.reindex(idx)
        # VIX bins
        vix_state = pd.Series(np.nan, index=idx)
        v = vix.reindex(idx, method="ffill")
        vix_state[v < self.vix_q33] = 0
        vix_state[(v >= self.vix_q33) & (v < self.vix_q67)] = 1
        vix_state[v >= self.vix_q67] = 2
        # Combined: average then round
        combined = (gmm_state.fillna(1).astype(float) + vix_state.fillna(1).astype(float)) / 2.0
        combined = combined.round().clip(0, 2).astype("Int64")
        return combined

    @staticmethod
    def label(state: int) -> str:
        return {0: "calm", 1: "normal", 2: "crisis"}.get(int(state) if pd.notna(state) else -1, "unknown")


# ============================================================================
# 2. PersonalityProfiler — 4-persona-luokittelija
# ============================================================================

PERSONAS = ["balanced", "reactive", "reserved", "herd"]
# balanced = matala IVOL, harvinaiset hypyt, korkea sektori-corr
# reactive = korkea positiivinen+negatiivinen jump variance, paksut hännät
# reserved = matala uutisreaktio, viivästynyt hinnanmuodostus, mean-revert
# herd     = korkea volume CV, herkkä sosiaalisille piikeille

class PersonalityProfiler:
    def __init__(self, lookback_days: int = 504):
        self.lookback = lookback_days

    def hurst(self, x: np.ndarray) -> float:
        """Yksinkertainen R/S Hurst-eksponentti. <0.5 = mean-revert, >0.5 = trend."""
        if len(x) < 100: return 0.5
        x = np.asarray(x, dtype=float)
        x = x[~np.isnan(x)]
        if len(x) < 100: return 0.5
        lags = [10, 20, 40, 80]
        tau = []
        for lag in lags:
            if lag >= len(x): continue
            d = x[lag:] - x[:-lag]
            if d.std() == 0: tau.append(1e-9)
            else: tau.append(d.std())
        if len(tau) < 3: return 0.5
        try:
            slope = np.polyfit(np.log(lags[:len(tau)]), np.log(tau), 1)[0]
            return float(slope)
        except Exception:
            return 0.5

    def profile(self, ticker_close: pd.Series, ticker_vol: pd.Series,
                spy_close: pd.Series, sector_close: Optional[pd.Series] = None,
                end_date: Optional[pd.Timestamp] = None) -> dict:
        end_date = end_date or ticker_close.index.max()
        start = end_date - pd.Timedelta(days=int(self.lookback * 1.5))
        s = ticker_close.loc[start:end_date].dropna()
        if len(s) < 100:
            return {"persona": "unknown", "n": int(len(s)), "metrics": {}}
        ret = s.pct_change().dropna()
        # CAPM-residual IVOL
        spy = spy_close.reindex(s.index).pct_change().dropna()
        common = ret.index.intersection(spy.index)
        if len(common) < 100:
            return {"persona": "unknown", "n": int(len(common)), "metrics": {}}
        r = ret.loc[common]; m = spy.loc[common]
        cov = ((r - r.mean()) * (m - m.mean())).sum()
        var_m = ((m - m.mean()) ** 2).sum()
        beta = float(cov / var_m) if var_m > 0 else 1.0
        alpha = float(r.mean() - beta * m.mean())
        resid = r - (alpha + beta * m)
        ivol_ann = float(resid.std() * math.sqrt(252))

        # Jump asymmetry — count |z|>3 in residuals
        z = (resid - resid.mean()) / (resid.std() + 1e-12)
        n_pos_jumps = int((z > 3).sum())
        n_neg_jumps = int((z < -3).sum())
        jump_total = n_pos_jumps + n_neg_jumps
        jump_asym = (n_pos_jumps - n_neg_jumps) / max(1, jump_total)
        kurtosis = float(((z ** 4).mean() - 3))

        # Volume CV (log)
        v = ticker_vol.reindex(s.index).replace(0, np.nan).dropna()
        log_v = np.log(v[v > 0])
        vol_cv = float(log_v.std() / abs(log_v.mean())) if len(log_v) > 50 and log_v.mean() != 0 else 0.0

        # Sector correlation (proxy: SPY) rolling 60d
        roll_corr = r.rolling(60).corr(m)
        sector_corr = float(roll_corr.dropna().mean()) if not roll_corr.dropna().empty else 0.0

        # Hurst
        H = self.hurst(np.log(s.values))

        metrics = {
            "ivol_ann": ivol_ann, "beta": beta, "alpha_ann": alpha * 252,
            "jump_total": jump_total, "jump_asym": jump_asym, "kurtosis": kurtosis,
            "vol_cv": vol_cv, "sector_corr": sector_corr, "hurst": H,
        }

        # Decision tree (pragmaattinen LPA-proxy)
        # Default: balanced
        persona = "balanced"
        if vol_cv > 0.20 and jump_total >= 4 and kurtosis > 5:
            persona = "herd"
        elif jump_total >= 6 and ivol_ann > 0.35:
            persona = "reactive"
        elif H < 0.45 and ivol_ann < 0.30 and sector_corr < 0.45:
            persona = "reserved"
        elif ivol_ann < 0.25 and sector_corr > 0.50 and kurtosis < 3:
            persona = "balanced"
        elif ivol_ann > 0.40 or jump_total >= 8:
            persona = "reactive"

        return {"persona": persona, "n": int(len(common)), "metrics": metrics,
                "period_start": str(common.min().date()), "period_end": str(common.max().date())}


# ============================================================================
# 3. LifecycleClassifier — Dickinson-proxy ilman SEC-dataa
# ============================================================================

class LifecycleClassifier:
    """3-vaiheinen proxy ilman kassavirtatietoja (käyttää OHLCV-johdannaisia)."""
    def classify(self, close: pd.Series, end_date: Optional[pd.Timestamp] = None) -> dict:
        end_date = end_date or close.index.max()
        s = close.loc[:end_date].dropna()
        if len(s) < 252 * 3:
            return {"stage": "insufficient_data", "n_years": len(s) / 252.0}
        s_3y = s.iloc[-252*3:]
        cagr_3y = (s_3y.iloc[-1] / s_3y.iloc[0]) ** (1/3) - 1
        ret = s_3y.pct_change().dropna()
        vol = float(ret.std() * math.sqrt(252))
        cum = (1 + ret).cumprod()
        dd_3y = float((cum / cum.cummax() - 1).min())
        # 6m trend
        s_6m = s.iloc[-126:]
        cagr_6m = (s_6m.iloc[-1] / s_6m.iloc[0]) ** (252/126) - 1

        # Decision rule (Dickinson-proxy):
        if cagr_3y > 0.25 and vol > 0.30:
            stage = "growth"          # high CAGR, high vol — early-stage / parabolic
        elif cagr_3y < -0.05 and cagr_6m < 0:
            stage = "declining"       # sustained downtrend
        elif 0.05 <= cagr_3y <= 0.25 and vol < 0.30 and dd_3y > -0.40:
            stage = "mature"          # steady compounder
        elif cagr_3y > 0.10 and dd_3y < -0.40:
            stage = "shake_out"       # high return but big DD = inflection point
        else:
            stage = "transition"      # unclear / mid-cycle

        return {"stage": stage, "cagr_3y": float(cagr_3y), "cagr_6m": float(cagr_6m),
                "vol_3y": vol, "dd_3y": dd_3y,
                "period_start": str(s_3y.index.min().date()),
                "period_end": str(s_3y.index.max().date())}


# ============================================================================
# 4. BreakDetector — CUSUM + Page-Hinkley + ICSS-vol-jump
# ============================================================================

class BreakDetector:
    """Tunnistaa rakenteelliset murrokset ("aivohalvaukset").

    3 metodia, niiden disjuntktio = break:
      - CUSUM cumsum-poikkeama
      - Page-Hinkley (mean shift detector)
      - Vol-doubling: 60d vol > 2× edellisen 60d vol
    """
    def __init__(self, cusum_threshold: float = 15.0, ph_delta: float = 0.0005,
                 ph_lambda: float = 200.0, vol_jump_ratio: float = 2.5,
                 dedupe_days: int = 180):
        self.cusum_threshold = cusum_threshold
        self.ph_delta = ph_delta
        self.ph_lambda = ph_lambda
        self.vol_jump_ratio = vol_jump_ratio
        self.dedupe_days = dedupe_days

    def detect(self, close: pd.Series) -> dict:
        s = close.dropna()
        if len(s) < 200:
            return {"breaks": [], "n_breaks": 0}
        ret = s.pct_change().dropna()

        # CUSUM on standardized returns — accept break only at confluence
        z = (ret - ret.mean()) / (ret.std() + 1e-12)
        cs_pos = np.zeros(len(z)); cs_neg = np.zeros(len(z))
        breaks_cusum = []
        for i in range(1, len(z)):
            cs_pos[i] = max(0, cs_pos[i-1] + z.iloc[i])
            cs_neg[i] = min(0, cs_neg[i-1] + z.iloc[i])
            if cs_pos[i] > self.cusum_threshold or cs_neg[i] < -self.cusum_threshold:
                breaks_cusum.append(z.index[i])
                cs_pos[i] = 0; cs_neg[i] = 0

        # Page-Hinkley
        breaks_ph = []
        m = 0.0; M = 0.0
        sd = float(ret.std())
        for i, x in enumerate(ret):
            m += x - ret.mean() - self.ph_delta
            M = min(M, m)
            if m - M > self.ph_lambda * sd:
                breaks_ph.append(ret.index[i])
                m = 0.0; M = 0.0

        # Vol-doubling: STRICT — 60d vol > 2.5× the 60d vol from one year ago
        vol60 = ret.rolling(60).std()
        vol_jumps = []
        for i in range(252, len(vol60)):
            cur = vol60.iloc[i]; prev = vol60.iloc[i-252]
            if pd.notna(cur) and pd.notna(prev) and prev > 0 and cur / prev > self.vol_jump_ratio:
                vol_jumps.append(vol60.index[i])

        # CONFLUENCE-rule: only report break if at least 2 of 3 detectors agree within 90d
        all_candidates = sorted(set(breaks_cusum + breaks_ph + vol_jumps))
        confluent = []
        for b in all_candidates:
            agree = 0
            if any(abs((b - x).days) <= 90 for x in breaks_cusum): agree += 1
            if any(abs((b - x).days) <= 90 for x in breaks_ph): agree += 1
            if any(abs((b - x).days) <= 90 for x in vol_jumps): agree += 1
            if agree >= 2:
                confluent.append(b)

        # Dedupe within 180 days
        deduped = []
        for b in sorted(confluent):
            if not deduped or (b - deduped[-1]).days > self.dedupe_days:
                deduped.append(b)
        return {
            "breaks": [str(b.date()) for b in deduped],
            "n_breaks": len(deduped),
            "n_cusum": len(breaks_cusum),
            "n_page_hinkley": len(breaks_ph),
            "n_vol_doubling": len(vol_jumps),
        }


# ============================================================================
# 5. StrategyRouter — sääntöpuu joka päättää mikä strategia mille tilanteelle
# ============================================================================

# Päätösmatriisi (regime, persona, lifecycle) → strategia + confidence
ROUTER_RULES = [
    # (regime, persona, lifecycle_stages, strategy, confidence_pct, rationale)
    ("calm",   "balanced", ["mature"],            "trend_long",         85, "vakaa nousumarkkina + tasapainoinen kompounderi → trend"),
    ("calm",   "balanced", ["growth"],            "long_growth_band",   75, "calm + growth: salli laajempi vol-band kuin staattinen 15-30%"),
    ("calm",   "reactive", ["growth"],            "momentum_breakout",  80, "calm + reactive growth: momentum ottaa kiinni jump-edge:n"),
    ("calm",   "reactive", ["mature"],            "small_cap_momentum", 65, "edes calmissa reactive vaatii tiukan SL:n"),
    ("calm",   "reserved", ["mature","transition"], "mean_reversion",   80, "reserved + mean-revert + calm = rauhallinen MR-edge"),
    ("calm",   "herd",     None,                  "fade_pump",          55, "calm-laumakiima: fade-piiki, ei chase"),

    ("normal", "balanced", ["mature"],            "trend_long_tight",   75, "normal trendi mutta tiukennetut SL:t"),
    ("normal", "reactive", None,                  "vol_targeted",       60, "normal+reactive: vol-target sizing kriittistä"),
    ("normal", "reserved", None,                  "mean_reversion",     70, "normal+reserved jatkuu MR-suosioon"),
    ("normal", "herd",     None,                  "skip",               90, "normal+herd: ei pelata"),

    ("crisis", "balanced", ["mature"],            "defensive_dca",      85, "crisis + mature compounder = DCA-kerääminen"),
    ("crisis", "reactive", None,                  "skip",               90, "crisis+reactive: stay out — likvidaatio-riski"),
    ("crisis", "reserved", None,                  "long_vix_short",     60, "crisis: VXX/UNG short-decay"),
    ("crisis", "herd",     None,                  "skip",               95, "crisis+herd: ehdoton skip"),
    ("crisis", "balanced", ["growth"],            "wait_capitulation",  70, "crisis+growth: anna kapitulaation tapahtua"),
]

class StrategyRouter:
    def __init__(self, recent_break_blackout_days: int = 60):
        self.blackout_days = recent_break_blackout_days

    def recommend(self, regime: str, persona: str, lifecycle: str,
                  break_info: dict, today: pd.Timestamp) -> dict:
        # Aivohalvaus-blackout
        breaks = break_info.get("breaks", [])
        if breaks:
            last_break = pd.Timestamp(breaks[-1])
            days_since = (today - last_break).days
            if days_since < self.blackout_days:
                return {
                    "strategy": "blackout_re_evaluate", "confidence_pct": 95,
                    "rationale": f"murros {days_since}d sitten → tunnista uusi profiili ennen kauppaa",
                    "matched_rule": None,
                }

        # Match against rules
        best = None
        for rule_regime, rule_persona, rule_lifecycle, strat, conf, rat in ROUTER_RULES:
            if rule_regime != regime: continue
            if rule_persona != persona: continue
            if rule_lifecycle is not None and lifecycle not in rule_lifecycle: continue
            best = (strat, conf, rat)
            break

        if best is None:
            return {"strategy": "skip", "confidence_pct": 50,
                    "rationale": f"ei matchia ({regime}/{persona}/{lifecycle})",
                    "matched_rule": None}
        return {"strategy": best[0], "confidence_pct": best[1], "rationale": best[2],
                "matched_rule": f"{regime}/{persona}/{lifecycle}"}


# ============================================================================
# Demo runner
# ============================================================================

def load_pkl(ticker: str) -> Optional[pd.DataFrame]:
    p = ASSET_CACHE / f"{ticker}.pkl"
    if not p.exists(): return None
    with open(p, "rb") as f:
        df = pickle.load(f)
    if not isinstance(df, pd.DataFrame): return None
    if "Close" not in df.columns: return None
    return df

def demo(tickers: Optional[list] = None):
    print(f"=== Dynamic Screener demo ({datetime.now().isoformat()}) ===")

    spy = load_pkl("_GSPC")
    if spy is None:
        print("ERROR: _GSPC.pkl missing"); return
    vix = load_pkl("_VIX")
    if vix is None:
        print("ERROR: _VIX.pkl missing"); return

    print(f"SPY: {spy.shape} {spy.index.min()} -> {spy.index.max()}")
    print(f"VIX: {vix.shape} {vix.index.min()} -> {vix.index.max()}")

    detector = RegimeDetector().fit(vix["Close"], spy["Close"])
    regime_series = detector.predict(vix["Close"], spy["Close"])
    last_regime = RegimeDetector.label(regime_series.dropna().iloc[-1])
    print(f"\nMarket regime fit: VIX q33={detector.vix_q33:.2f} q67={detector.vix_q67:.2f}")
    print(f"  GMM means (vol, dd60): {detector.gmm_means_}")
    print(f"  Latest regime ({regime_series.dropna().index[-1].date()}): {last_regime}")

    # Save regime history
    rh_path = OUT / "regime_history.jsonl"
    if rh_path.exists(): rh_path.unlink()
    with open(rh_path, "w", encoding="utf-8") as f:
        for dt, st in regime_series.dropna().items():
            f.write(json.dumps({"date": str(dt.date()), "regime_state": int(st),
                                "regime": RegimeDetector.label(st)}) + "\n")

    # Regime transitions count
    rs = regime_series.dropna().astype(int)
    transitions = (rs.diff().fillna(0) != 0).sum()
    print(f"  Transitions over period: {transitions}")
    print(f"  Time spent: calm={(rs==0).mean()*100:.1f}%  normal={(rs==1).mean()*100:.1f}%  crisis={(rs==2).mean()*100:.1f}%")

    if tickers is None:
        # Take top 50 alphabetical from cache (skip prefixed _ index symbols)
        all_tk = sorted([p.stem for p in ASSET_CACHE.glob("*.pkl") if not p.stem.startswith("_")])
        tickers = all_tk[:50]
    print(f"\n=== Per-ticker analysis (n={len(tickers)}) ===")

    profiler = PersonalityProfiler(lookback_days=504)
    lifecycle = LifecycleClassifier()
    breakdet = BreakDetector(cusum_threshold=15.0, ph_lambda=200.0, vol_jump_ratio=2.5,
                              dedupe_days=180)
    router = StrategyRouter(recent_break_blackout_days=60)

    results_path = OUT / "screener_results.jsonl"
    if results_path.exists(): results_path.unlink()

    summary_persona = {p: 0 for p in PERSONAS}
    summary_persona["unknown"] = 0
    summary_lifecycle = {}
    summary_strategy = {}

    today = spy.index.max()
    for tk in tickers:
        df = load_pkl(tk)
        if df is None or len(df) < 252*2:
            print(f"  [skip] {tk}: missing or short")
            continue
        prof = profiler.profile(df["Close"], df["Volume"], spy["Close"], end_date=today)
        lc = lifecycle.classify(df["Close"], end_date=today)
        br = breakdet.detect(df["Close"])
        rec = router.recommend(last_regime, prof["persona"], lc.get("stage","unknown"), br, today)

        rec_full = {
            "ticker": tk, "as_of": str(today.date()),
            "regime": last_regime,
            "persona": prof["persona"], "persona_metrics": prof.get("metrics", {}),
            "lifecycle": lc, "breaks": br,
            "recommendation": rec,
        }
        with open(results_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec_full, ensure_ascii=False, default=str) + "\n")

        summary_persona[prof["persona"]] = summary_persona.get(prof["persona"], 0) + 1
        summary_lifecycle[lc.get("stage","unknown")] = summary_lifecycle.get(lc.get("stage","unknown"),0)+1
        summary_strategy[rec["strategy"]] = summary_strategy.get(rec["strategy"],0)+1

        # Tieto-portfolio: kirjaa persona, lifecycle, breaks
        try:
            m = prof.get("metrics", {})
            fp.record_observation(
                feature=f"persona_{prof['persona']}",
                target=f"{tk}_classification",
                method="other",
                value=1.0,
                n_samples=prof.get("n", 0),
                applies_to=tk,
                bot="finance",
                strategy_id="dynamic_screener",
                target_class="other",
                domain="systematic",
                raw_value=m.get("ivol_ann"),
                confidence_pct=70,  # rule-based — moderate confidence
                period_start=prof.get("period_start"),
                period_end=prof.get("period_end"),
                participants=[tk, "SPY"],
                metadata={
                    "persona": prof["persona"],
                    "lifecycle_stage": lc.get("stage"),
                    "regime_at_classification": last_regime,
                    "ivol_ann": m.get("ivol_ann"),
                    "beta": m.get("beta"),
                    "kurtosis": m.get("kurtosis"),
                    "hurst": m.get("hurst"),
                    "sector_corr": m.get("sector_corr"),
                    "vol_cv": m.get("vol_cv"),
                    "n_breaks_lifetime": br["n_breaks"],
                    "last_break_date": br["breaks"][-1] if br["breaks"] else None,
                    "recommended_strategy": rec["strategy"],
                    "source": "dynamic_screener_2026_04_28",
                }
            )
        except Exception as e:
            print(f"  [warn] portfolio record failed for {tk}: {e}")

        line = (f"  {tk:6s} regime={last_regime:6s} persona={prof['persona']:9s} "
                f"life={lc.get('stage','?'):12s} breaks={br['n_breaks']} "
                f"-> {rec['strategy']:25s} conf={rec['confidence_pct']}%")
        print(line)

    print(f"\n=== Summary across {len(tickers)} tickers ===")
    print(f"  Persona:   {summary_persona}")
    print(f"  Lifecycle: {summary_lifecycle}")
    print(f"  Strategy:  {summary_strategy}")

    print(f"\nResults: {results_path}")
    print(f"Regime history: {rh_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=None, help="Comma-separated, e.g. AAPL,MSFT")
    args = ap.parse_args()
    tk = args.tickers.split(",") if args.tickers else None
    demo(tk)
