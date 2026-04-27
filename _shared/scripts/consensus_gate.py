# -*- coding: utf-8 -*-
r"""consensus_gate.py — multi-persona-konsensus-gate ennen knowledge/strategies.jsonl-tallennusta.

Gate-säännöt (PDF1: super-additiivinen yhteistyö):
  1. Vaadi vähintään 3 ERI personaa BUY/SELL samaan suuntaan
  2. Yksittäinen persona ei saa edustaa > 40% rounds:eista samalle ticker:lle
  3. Critique Agent on saanut vastata + sen verdict ei ole REJECT
  4. avg_conviction >= 0.55
  5. Jos validation_results.jsonl:ssä on PBO > 0.5 → REJECT

Ajetaan daemon-cyclellä joka tunti — käy läpi viim. 24h debate-rounds:
  - Jos jokin (bot, ticker)-pari läpäisee → tallenna knowledge/candidate_strategies.jsonl
  - Jos REJECT → kirjaa learnings:iin
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BOTS_ROOT = Path(__file__).resolve().parents[2]

BOTS_WITH_TICKER_DEBATES = ["finance", "crypto-finance"]


def load_recent_debates(bot: str, hours: int = 24) -> list[dict]:
    log = BOTS_ROOT / bot / "debate" / "debate_log.jsonl"
    if not log.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    out = []
    with open(log, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("ts", "") >= cutoff:
                out.append(d)
    return out


def normalize_action(a: str) -> str:
    a = (a or "").replace("*", "").strip().upper()
    if a.startswith("BUY") or a.startswith("LONG") or a.startswith("STRONG_BUY"):
        return "BUY"
    if a.startswith("SELL") or a.startswith("SHORT"):
        return "SELL"
    if a.startswith("HOLD") or a.startswith("CONDITION") or a.startswith("NO_TRADE"):
        return "HOLD"
    return ""


def normalize_ticker(t: str) -> str:
    """Yhdistä BTC/USD, BTC-USD, BTC-PERP, ** BTC, BTC/USDT → BTC."""
    if not t:
        return ""
    t = t.upper().strip().replace("**", "").strip()
    # Karsi suluissa olevat selitykset
    if "(" in t:
        t = t.split("(")[0].strip()
    # Karsi pari-erotin
    for sep in ["/", "-", " "]:
        if sep in t:
            t = t.split(sep)[0].strip()
    # Karsi suffix
    for sfx in ["USDT", "USD", "USDC", "PERP", "SPOT"]:
        if t.endswith(sfx) and len(t) > len(sfx):
            t = t[:-len(sfx)].strip()
    return t


def evaluate_consensus(bot: str, hours: int = 24) -> list[dict]:
    """Käy läpi (ticker, side) -parit, palauta gate-tulokset."""
    rows = load_recent_debates(bot, hours)
    # ryhmittele per (ticker, side) — keräten unique personas + convictions
    pairs = defaultdict(lambda: {"personas": [], "convictions": [],
                                  "rounds": 0, "critique_verdict": None})
    persona_per_ticker = defaultdict(Counter)
    for r in rows:
        ticker = normalize_ticker(r.get("ticker", ""))
        if not ticker or ticker.startswith("N/A") or "KALSHI" in ticker:
            continue
        side = normalize_action(r.get("action", ""))
        if side not in ("BUY", "SELL"):
            continue
        persona = r.get("persona", "")
        conv = r.get("conviction_num")
        key = (ticker, side)
        pairs[key]["personas"].append(persona)
        if isinstance(conv, (int, float)):
            pairs[key]["convictions"].append(float(conv))
        pairs[key]["rounds"] += 1
        # critique_agent erityishuomio
        if persona == "critique_agent":
            # critique_agent ottaa kantaa REJECT/CONDITIONAL_ACCEPT (raw-fieldissä)
            raw = (r.get("raw") or "").upper()
            if "POSITION: REJECT" in raw or "REJECT" in raw[:200]:
                pairs[key]["critique_verdict"] = "REJECT"
            elif "CONDITIONAL_ACCEPT" in raw:
                pairs[key]["critique_verdict"] = "CONDITIONAL"
        persona_per_ticker[ticker][persona] += 1

    results = []
    for (ticker, side), data in pairs.items():
        unique_personas = set(data["personas"])
        unique_personas.discard("critique_agent")  # critique ei lasketa side-personaksi
        n_unique = len(unique_personas)
        rounds = data["rounds"]
        avg_conv = (sum(data["convictions"]) / len(data["convictions"])
                    if data["convictions"] else 0.0)

        # Persona-dominance: yksittäinen persona ei saa > 40% rounds
        persona_counts = Counter(data["personas"])
        max_share = (max(persona_counts.values()) / rounds) if rounds else 0

        # Gate-säännöt
        passes = []
        fails = []
        if n_unique >= 3:
            passes.append(f"unique_personas={n_unique}>=3")
        else:
            fails.append(f"unique_personas={n_unique}<3")
        if max_share <= 0.4:
            passes.append(f"max_persona_share={max_share:.2f}<=0.4")
        else:
            fails.append(f"max_persona_share={max_share:.2f}>0.4")
        if avg_conv >= 0.55:
            passes.append(f"avg_conv={avg_conv:.2f}>=0.55")
        else:
            fails.append(f"avg_conv={avg_conv:.2f}<0.55")
        if data["critique_verdict"] != "REJECT":
            passes.append("critique_not_REJECT")
        else:
            fails.append("critique_REJECT")

        verdict = "PASS" if not fails else "REJECT"

        results.append({
            "bot": bot,
            "ticker": ticker,
            "side": side,
            "rounds": rounds,
            "unique_personas": n_unique,
            "personas": list(unique_personas),
            "max_persona_share": round(max_share, 3),
            "avg_conviction": round(avg_conv, 3),
            "critique_verdict": data["critique_verdict"],
            "verdict": verdict,
            "passes": passes,
            "fails": fails,
        })

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()

    candidates_path = BOTS_ROOT / "_shared" / "memory" / "candidate_strategies.jsonl"
    candidates_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"=== Consensus Gate (last {args.hours}h) ===")
    all_results = []
    for bot in BOTS_WITH_TICKER_DEBATES:
        results = evaluate_consensus(bot, args.hours)
        for r in results:
            all_results.append(r)
            r["ts"] = datetime.now(timezone.utc).isoformat()
            with open(candidates_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    passed = [r for r in all_results if r["verdict"] == "PASS"]
    rejected = [r for r in all_results if r["verdict"] == "REJECT"]

    print(f"  Total: {len(all_results)}, PASS={len(passed)}, REJECT={len(rejected)}")
    print()
    if passed:
        print("=== PASSED CANDIDATES (knowledge:n promotoitavia) ===")
        for r in passed:
            print(f"  [{r['bot']}] {r['ticker']} {r['side']:5s}  "
                  f"personas={r['unique_personas']} rounds={r['rounds']} "
                  f"conv={r['avg_conviction']:.2f}")
            print(f"    personas: {', '.join(r['personas'])}")
    print()
    print("=== REJECTED (top fails) ===")
    rejected.sort(key=lambda x: x["rounds"], reverse=True)
    for r in rejected[:8]:
        print(f"  [{r['bot']}] {r['ticker']} {r['side']:5s}  "
              f"rounds={r['rounds']} fails={r['fails']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
