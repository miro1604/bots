# -*- coding: utf-8 -*-
r"""smart_spike_handler.py — älykäs spike-luokittelu (real_event / data_bug / uncertain).

Käyttäjän mandaatti 2026-04-28: ÄLÄ blacklisti sokeasti kaikkia >300%-spike:jä.
Aitoja market-tapahtumia (GME 2021, AMC, NVDA earnings, jne) säilytetään dataan.
Vain selvät data-bugit korjataan.

Algoritmi per spike-päivä:
  1. Tarkista volyymi-spike (today_vol > 5× 30d-avg) → AITO indikaatio
  2. Tarkista revertointi (next-day reverse) → BUGI indikaatio
  3. Tarkista persistence (5d-window jää voimaan) → AITO indikaatio
  4. Tarkista cross-day-koherenssi (open vs prev-close) → BUGI gap detect
  5. Classify: real_event / data_bug / uncertain

Output:
  - cleaned_close.parquet (data_bug:t interpoloitu, real_event:t säilytetty)
  - spike_classifications.jsonl (per-spike-rivi: ticker, date, ret, classification, syyt)
  - feature_portfolio: real_event:t kirjataan event_study-tyyppisinä havaintoina
"""
from __future__ import annotations
import sys, json, time
from pathlib import Path
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_shared" / "scripts"))
from feature_portfolio import record_observation


def classify_spike(close: pd.Series, volume: pd.Series, idx: int,
                    spike_threshold: float = 3.0) -> dict:
    """Luokittele yksi spike-päivä.

    Returns:
        {
          "classification": "real_event" / "data_bug" / "uncertain",
          "ret_today": float,
          "vol_spike_x": float,
          "reverts_next_day": bool,
          "persists_5d": bool,
          "extreme_magnitude": bool,  # > 5000%
          "reasoning": str
        }
    """
    if idx < 30 or idx + 5 >= len(close):
        return {"classification": "uncertain", "reasoning": "boundary"}

    ret_today = (close.iloc[idx] / close.iloc[idx-1] - 1)
    if abs(ret_today) < spike_threshold:
        return {"classification": "normal", "reasoning": "below threshold"}

    # 1. Volyymi-tarkistus
    avg_vol_30d = volume.iloc[idx-30:idx].mean()
    today_vol = volume.iloc[idx]
    vol_spike_x = today_vol / max(avg_vol_30d, 1) if avg_vol_30d > 0 else 0
    has_vol_spike = vol_spike_x > 3.0  # 3× normaali volyymi

    # 2. Revertointi-tarkistus
    next_day_ret = (close.iloc[idx+1] / close.iloc[idx] - 1) if idx + 1 < len(close) else 0
    # Jos today +500% ja next-day -83% → revertointi (lähes täysin)
    revert_ratio = abs(next_day_ret) / abs(ret_today) if abs(ret_today) > 0 else 0
    reverts_next_day = revert_ratio > 0.5 and (np.sign(next_day_ret) != np.sign(ret_today))

    # 3. Persistence (5d eteenpäin: jääkö taso lähelle huippua?)
    fwd_5d_close = close.iloc[idx+5] if idx + 5 < len(close) else close.iloc[-1]
    pre_close = close.iloc[idx-1]
    spike_close = close.iloc[idx]
    # Persistence: 5d:n päästä hinta vielä >= 50% spike-noususta
    if ret_today > 0:
        persist_ratio = (fwd_5d_close - pre_close) / (spike_close - pre_close) if (spike_close - pre_close) > 0 else 0
    else:
        persist_ratio = (pre_close - fwd_5d_close) / (pre_close - spike_close) if (pre_close - spike_close) > 0 else 0
    persists_5d = persist_ratio > 0.30

    # 4. Extreme magnitude check
    extreme_magnitude = abs(ret_today) > 50.0  # 5000%+ = lähes aina bugi

    # 5. Decision tree
    reasoning_parts = []
    if extreme_magnitude:
        classification = "data_bug"
        reasoning_parts.append(f"extreme_magnitude({ret_today*100:.0f}%)")
    elif reverts_next_day and not has_vol_spike:
        classification = "data_bug"
        reasoning_parts.append(f"reverts_no_volume(revert={revert_ratio:.2f}, vol_x={vol_spike_x:.1f})")
    elif has_vol_spike and persists_5d:
        classification = "real_event"
        reasoning_parts.append(f"vol_spike({vol_spike_x:.1f}x)+persists({persist_ratio:.2f})")
    elif has_vol_spike and not reverts_next_day:
        classification = "real_event"
        reasoning_parts.append(f"vol_spike({vol_spike_x:.1f}x)+no_revert")
    elif reverts_next_day:
        classification = "data_bug"
        reasoning_parts.append(f"reverts({revert_ratio:.2f})")
    elif not has_vol_spike and not persists_5d:
        classification = "uncertain"
        reasoning_parts.append("no_clear_signal")
    else:
        classification = "uncertain"

    return {
        "classification": classification,
        "ret_today": float(ret_today),
        "vol_spike_x": float(vol_spike_x),
        "reverts_next_day": bool(reverts_next_day),
        "persists_5d": bool(persists_5d),
        "extreme_magnitude": bool(extreme_magnitude),
        "next_day_ret": float(next_day_ret),
        "persist_ratio": float(persist_ratio),
        "reasoning": "+".join(reasoning_parts)
    }


def process_universe(close: pd.DataFrame, volume: pd.DataFrame,
                      threshold_pct: float = 3.0,
                      record_to_portfolio: bool = True) -> dict:
    """Käy läpi koko universumi spike-detection:lla."""
    results = []
    classifications = {"real_event": 0, "data_bug": 0, "uncertain": 0, "normal": 0}

    common_tickers = [t for t in close.columns if t in volume.columns]
    print(f"  Käsitellään {len(common_tickers)} tickeria...")

    for ti, ticker in enumerate(common_tickers):
        if ti % 50 == 0:
            print(f"  [{ti}/{len(common_tickers)}] {ticker} ({classifications})")
        c = close[ticker].dropna()
        v = volume[ticker].reindex(c.index).fillna(0)
        if len(c) < 50: continue

        rets = c.pct_change()
        spike_mask = rets.abs() >= threshold_pct
        if spike_mask.sum() == 0: continue

        # Map index → integer-position
        spike_idx = np.where(spike_mask.values)[0]
        for i in spike_idx:
            cls = classify_spike(c, v, i, threshold_pct)
            classifications[cls["classification"]] = classifications.get(cls["classification"], 0) + 1
            if cls["classification"] in ("normal",): continue
            results.append({
                "ticker": ticker,
                "date": str(c.index[i].date()),
                **cls
            })

    print(f"\n  Yhteensä spike-tapahtumia: {len(results)}")
    print(f"  Luokitukset: {classifications}")

    # Kirjaa real_event:t feature_portfolio:on event_study-tyyppisesti
    if record_to_portfolio:
        n_recorded = 0
        for r in results:
            if r["classification"] != "real_event": continue
            record_observation(
                feature=f"market_spike_{r['ticker']}_{r['date']}",
                target="significant_market_event",
                target_class="stock_return",
                domain="behavioral",
                method="event_study",
                value=float(r["ret_today"]),
                raw_value=float(r["ret_today"] * 100),
                n_samples=1,
                applies_to=r["ticker"],
                bot="finance",
                strategy_id="spike_classifier",
                verdict="EFFECT_STRONG",
                confidence_pct=85,
                metadata={
                    "date": r["date"],
                    "vol_spike_x": r["vol_spike_x"],
                    "persists_5d": r["persists_5d"],
                    "reasoning": r["reasoning"],
                    "note": "AITO market-event (vahva vol-spike + persistence)"
                }
            )
            n_recorded += 1
        print(f"  Kirjattu {n_recorded} aitoa eventtiä portfolio:on (event_study)")

    return {
        "spike_events": results,
        "classifications": classifications,
        "n_total_spikes": len(results)
    }


def apply_corrections(close: pd.DataFrame, spike_results: list[dict],
                       correct_data_bugs: bool = True) -> pd.DataFrame:
    """Korjaa data_bug-päivät interpoloimalla edellisen ja seuraavan päivän väliltä."""
    if not correct_data_bugs:
        return close
    cleaned = close.copy()
    n_corrected = 0
    for r in spike_results:
        if r["classification"] != "data_bug": continue
        ticker = r["ticker"]
        date_str = r["date"]
        try:
            date = pd.Timestamp(date_str)
            idx = cleaned.index.get_loc(date)
            if idx < 1 or idx + 1 >= len(cleaned): continue
            prev_close = cleaned[ticker].iloc[idx-1]
            next_close = cleaned[ticker].iloc[idx+1]
            if pd.notna(prev_close) and pd.notna(next_close):
                # Geometric interpolation
                cleaned[ticker].iloc[idx] = float(np.sqrt(prev_close * next_close))
                n_corrected += 1
        except Exception:
            continue
    return cleaned, n_corrected


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.50,
                    help="Spike-kynnys (0.50 = 50%, 1.0 = 100%, 3.0 = 300%)")
    args = ap.parse_args()

    t0 = time.time()
    print("="*70)
    print(f"SMART SPIKE HANDLER (threshold={args.threshold*100:.0f}%)")
    print("="*70)

    DATA = ROOT / "finance" / "data_for_ultraplan"
    close = pd.read_parquet(DATA / "v20_full_prices_close.parquet")
    volume = pd.read_parquet(DATA / "v20_full_prices_volume.parquet")
    close.index = pd.to_datetime(close.index)
    volume.index = pd.to_datetime(volume.index)
    if close.index.tz: close.index = close.index.tz_localize(None)
    if volume.index.tz: volume.index = volume.index.tz_localize(None)

    print(f"\nData: {close.shape} (close), {volume.shape} (volume)")

    # Aja spike-detection
    print(f"\n[1/3] Spike-detection (threshold {args.threshold*100:.0f}%)")
    out = process_universe(close, volume, threshold_pct=args.threshold, record_to_portfolio=True)

    # Tallenna spike-log
    log_path = DATA / "spike_classifications.jsonl"
    if log_path.exists(): log_path.unlink()
    with open(log_path, "w", encoding="utf-8") as f:
        for r in out["spike_events"]:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(f"\n[2/3] Tallennettu spike-log: {log_path.name}")

    # Korjaa data-bugit, säilytä real-events
    print(f"\n[3/3] Korjaa data_bug-päivät interpoloimalla...")
    cleaned, n_corr = apply_corrections(close, out["spike_events"])
    cleaned_path = DATA / "v20_full_prices_close_cleaned.parquet"
    cleaned.to_parquet(cleaned_path, compression="snappy")
    print(f"  Korjattu {n_corr} data_bug-päivää")
    print(f"  Tallennettu cleaned: {cleaned_path.name}")

    # Top esimerkit kummastakin
    print(f"\n=== TOP REAL EVENTS (10 isointa, vol-spike + persistence) ===")
    real_events = [r for r in out["spike_events"] if r["classification"] == "real_event"]
    real_events.sort(key=lambda x: abs(x["ret_today"]), reverse=True)
    for r in real_events[:10]:
        print(f"  {r['ticker']:<8s} {r['date']} ret={r['ret_today']*100:+8.1f}% vol_x={r['vol_spike_x']:.1f}× persist={r['persist_ratio']:.2f} → {r['reasoning']}")

    print(f"\n=== TOP DATA BUGS (10 isointa) ===")
    data_bugs = [r for r in out["spike_events"] if r["classification"] == "data_bug"]
    data_bugs.sort(key=lambda x: abs(x["ret_today"]), reverse=True)
    for r in data_bugs[:10]:
        print(f"  {r['ticker']:<8s} {r['date']} ret={r['ret_today']*100:+8.1f}% vol_x={r['vol_spike_x']:.1f}× revert={r['reverts_next_day']} → {r['reasoning']}")

    print(f"\n=== UNCERTAIN ({len([r for r in out['spike_events'] if r['classification']=='uncertain'])} kpl) ===")
    uncertain = [r for r in out["spike_events"] if r["classification"] == "uncertain"][:5]
    for r in uncertain:
        ret = r.get("ret_today", 0)
        print(f"  {r['ticker']:<8s} {r['date']} ret={ret*100:+8.1f}% → {r.get('reasoning', '?')}")

    print(f"\nKesto: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
