# -*- coding: utf-8 -*-
r"""debate_orchestrator.py - Multi-Agent Debate (Du MIT 2023) finance-personille.

3-rounded MAD:
  ROUND 1 (Generate): jokainen persona tuottaa signaali-ehdotuksen
  ROUND 2 (Revise): jokainen lukee KAIKKIEN MUIDEN ehdotukset, paivittaa oman
  ROUND 3 (Consensus): vote + dissent-log

Ajo:
  python finance/debate/debate_orchestrator.py --scenario path/to/scenario.txt
  python finance/debate/debate_orchestrator.py --inline "NVDA -18% drop ..."

Output:
  betting/debate/debate_log.jsonl - kaikki kierrokset
  finance/signals/proposed_signals.jsonl - kaikki ehdotukset
  finance/signals/consensus_signals.jsonl - debate-konsensus
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_BOT_ROOT = Path(__file__).resolve().parents[1]
_BOTS_ROOT = _BOT_ROOT.parent
_SHARED_SCRIPTS = _BOTS_ROOT / "_shared" / "scripts"
sys.path.insert(0, str(_SHARED_SCRIPTS))
sys.path.insert(0, str(_BOT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from model_router import call_claude


PERSONAS_FILE = _BOT_ROOT / "agents" / "personas.jsonl"
DEBATE_LOG = _BOT_ROOT / "debate" / "debate_log.jsonl"
PROPOSED = _BOT_ROOT / "signals" / "proposed_signals.jsonl"
CONSENSUS = _BOT_ROOT / "signals" / "consensus_signals.jsonl"


def load_personas() -> list[dict]:
    out = []
    with open(PERSONAS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return [p for p in out if p.get("active")]


def load_persona_prompt(persona: dict) -> str:
    p = _BOT_ROOT / persona["prompt_file"]
    return p.read_text(encoding="utf-8")


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


import re as _re

def _strip_md(s: str) -> str:
    """Poista markdown-bold/italic ympäriltä ennen tokenisointia."""
    if s is None:
        return s
    return s.replace("**", "").replace("__", "").replace("*", "").strip()


def parse_proposal(text: str) -> dict:
    out = {"action": None, "ticker": None, "hold_period": None, "conviction": None,
            "thesis": None, "key_risk": None, "raw": text}
    for line in text.splitlines():
        s = line.strip().lstrip("*").lstrip("_").strip()
        for key, label in [
            ("action", "ACTION:"), ("ticker", "TICKER:"),
            ("hold_period", "HOLD_PERIOD:"), ("conviction", "CONVICTION:"),
            ("thesis", "THESIS:"), ("key_risk", "KEY_RISK:"),
        ]:
            if s.upper().startswith(label):
                out[key] = s[len(label):].strip()
                break
    # ACTION: poista markdown ja ota ensimmäinen sana (BUY/SELL/HOLD/NO_TRADE)
    if out["action"]:
        cleaned = _strip_md(out["action"])
        m = _re.search(r"\b(BUY|SELL|HOLD|NO_TRADE)\b", cleaned.upper())
        out["action"] = m.group(1) if m else cleaned.split()[0] if cleaned else None
    # Conviction-numeerinen — ensimmäinen 0.X tai 0,X
    if out["conviction"]:
        cleaned = _strip_md(out["conviction"])
        m = _re.search(r"(\d+\.\d+|\d+,\d+|\d+)", cleaned)
        try:
            out["conviction_num"] = float(m.group(1).replace(",", ".")) if m else 0.5
        except Exception:
            out["conviction_num"] = 0.5
    return out


# ─────────────────── ROUND 1: GENERATE ───────────────────

def round1_generate(scenario: str, personas: list[dict], debate_id: str) -> list[dict]:
    print(f"\n=== ROUND 1: GENERATE ({len(personas)} personaa) ===")
    proposals = []
    for i, persona in enumerate(personas, 1):
        ptext = load_persona_prompt(persona)
        prompt = f"""{ptext}

---

# Tilanne

{scenario}

# Tehtava

Esita SIJOITUS-EHDOTUKSESI talle tilanteelle, omasta persoonastasi kasin. Vastaa TASMALLEEN nain:

ACTION: BUY | SELL | HOLD | NO_TRADE
TICKER: <ticker tai n/a>
HOLD_PERIOD: <esim 5d, 30d, 1y, 5y, event_driven_until_X>
CONVICTION: <0.0 - 1.0>
THESIS: <2-3 lauseen perustelu omasta filosofiastasi>
KEY_RISK: <yksi tarkein riski jonka hyvaksyt ottaa>
"""
        print(f"  [{i}/{len(personas)}] {persona['slug']}...", end="", flush=True)
        t0 = time.time()
        out, err, rc = call_claude(
            prompt=prompt, task_type="deep_analysis", timeout=None,
            model="sonnet", fallback_model="haiku", bot=f"betting:{persona['slug']}"
        )
        dur = time.time() - t0
        if rc != 0:
            print(f" FAIL rc={rc}")
            continue
        parsed = parse_proposal(out)
        proposals.append({
            "persona": persona["slug"],
            "name": persona["name"],
            "round": 1,
            "duration_s": round(dur, 1),
            **parsed,
        })
        print(f" {parsed.get('action', '?')} (cv={parsed.get('conviction', '?')}) {dur:.1f}s")

        # Jokainen ehdotus → debate_log
        append_jsonl(DEBATE_LOG, {
            "debate_id": debate_id, "round": 1, "persona": persona["slug"],
            "ts": datetime.now(timezone.utc).isoformat(),
            **parsed,
        })

    return proposals


# ─────────────────── ROUND 2: REVISE ───────────────────

def format_others_proposals(my_slug: str, all_proposals: list[dict]) -> str:
    others = [p for p in all_proposals if p["persona"] != my_slug]
    lines = []
    for p in others:
        lines.append(f"## {p['persona']} ({p['name']})")
        lines.append(f"  ACTION: {p.get('action')}")
        lines.append(f"  HOLD: {p.get('hold_period')}")
        lines.append(f"  CONVICTION: {p.get('conviction')}")
        lines.append(f"  THESIS: {p.get('thesis')}")
        lines.append(f"  KEY_RISK: {p.get('key_risk')}")
        lines.append("")
    return "\n".join(lines)


def round2_revise(scenario: str, personas: list[dict], proposals: list[dict],
                    debate_id: str) -> list[dict]:
    print(f"\n=== ROUND 2: REVISE ({len(personas)} personaa lukee muiden + paivittaa) ===")
    revised = []
    for i, persona in enumerate(personas, 1):
        my_proposal = next((p for p in proposals if p["persona"] == persona["slug"]), None)
        if not my_proposal:
            continue

        ptext = load_persona_prompt(persona)
        others_text = format_others_proposals(persona["slug"], proposals)
        prompt = f"""{ptext}

---

# Tilanne

{scenario}

# Sinun alkuperainen ehdotus (ROUND 1)

ACTION: {my_proposal.get('action')}
HOLD_PERIOD: {my_proposal.get('hold_period')}
CONVICTION: {my_proposal.get('conviction')}
THESIS: {my_proposal.get('thesis')}
KEY_RISK: {my_proposal.get('key_risk')}

# Muiden persoonien ehdotukset

{others_text}

# Tehtava

Lue muiden ehdotukset. Mieti omasta filosofiastasi kasin:
1. Onko joku heistä huomannut jotain mita sina et? Jos kylla, kuinka se vaikuttaa sinun nakemykseesi?
2. Kuinka heidan filosofiansa voivat epaonnistua tassa tilanteessa?
3. Lopullinen revisoitu nakemyksesi.

Vastaa rakenteessa:
INSIGHTS_FROM_OTHERS: <1-3 spesifista oivallusta muista, oma filosofia kohti>
COUNTER_ARGUMENTS: <1-3 kritiikkiä muista persoonista>
ACTION: BUY | SELL | HOLD | NO_TRADE
TICKER: <sama tai muutettu>
HOLD_PERIOD: <sama tai muutettu>
CONVICTION: <revisoitu, mahdollisesti samama>
REVISED_THESIS: <miten muiden tarkastelu vaikutti omaan>
"""
        print(f"  [{i}/{len(personas)}] {persona['slug']}...", end="", flush=True)
        t0 = time.time()
        out, err, rc = call_claude(
            prompt=prompt, task_type="deep_analysis", timeout=None,
            model="sonnet", fallback_model="haiku", bot=f"betting:{persona['slug']}"
        )
        dur = time.time() - t0
        if rc != 0:
            print(f" FAIL rc={rc}")
            continue
        parsed = parse_proposal(out)  # parser kayttaa ACTION/etc. — sopii myos REVISED:lle
        # Parse ekstrat
        insights = ""
        counters = ""
        rev_thesis = ""
        for line in out.splitlines():
            s = line.strip()
            if s.upper().startswith("INSIGHTS_FROM_OTHERS:"):
                insights = s[len("INSIGHTS_FROM_OTHERS:"):].strip()
            elif s.upper().startswith("COUNTER_ARGUMENTS:"):
                counters = s[len("COUNTER_ARGUMENTS:"):].strip()
            elif s.upper().startswith("REVISED_THESIS:"):
                rev_thesis = s[len("REVISED_THESIS:"):].strip()

        revised.append({
            "persona": persona["slug"],
            "name": persona["name"],
            "round": 2,
            "duration_s": round(dur, 1),
            "insights_from_others": insights,
            "counter_arguments": counters,
            "revised_thesis": rev_thesis,
            "round1_action": my_proposal.get("action"),
            "round2_action": parsed.get("action"),
            "changed": my_proposal.get("action") != parsed.get("action"),
            **parsed,
        })
        change_marker = "→ CHANGED" if revised[-1]["changed"] else ""
        print(f" {parsed.get('action', '?')} (cv={parsed.get('conviction', '?')}) {dur:.1f}s {change_marker}")

        append_jsonl(DEBATE_LOG, {
            "debate_id": debate_id, "round": 2, "persona": persona["slug"],
            "ts": datetime.now(timezone.utc).isoformat(),
            **revised[-1],
        })

    return revised


# ─────────────────── CONSENSUS ───────────────────

def normalize_hold(hold_str: str) -> str:
    """Mappaa hold-periodi kategoriaan (1d, 5d, 30d, 90d, 1y, 5y+, event).

    Etsii ensin numero+yksikkö-parit; jos useampi (esim 45-60d), ottaa
    keskiarvon. Sen jälkeen kategorisoi.
    """
    s = (hold_str or "").lower()
    s = _strip_md(s) if s else ""
    if not s or s in ("n/a", "none", "null"):
        return "unknown"
    if "event" in s or "until" in s or "watch_list" in s or "watchlist" in s:
        return "event"

    # Etsi numero+yksikkö-parit (5d, 45-60d, 5y, 30d, 12 months, 6 weeks)
    matches = _re.findall(r"(\d+)\s*[-–]?\s*(\d+)?\s*(d|day|w|week|m|mo|month|y|yr|year|q|quarter)", s)
    days = []
    for m in matches:
        a = int(m[0])
        b = int(m[1]) if m[1] else a
        avg = (a + b) / 2
        unit = m[2]
        if unit.startswith("d"):
            days.append(avg)
        elif unit.startswith("w"):
            days.append(avg * 7)
        elif unit.startswith("m"):
            days.append(avg * 30)
        elif unit.startswith("y"):
            days.append(avg * 365)
        elif unit.startswith("q"):
            days.append(avg * 90)

    if days:
        d = max(days)  # konservatiivinen — pisin viittaus ratkaisee
        if d >= 1500:    # >= ~4y
            return "5y+"
        if d >= 270:     # >= ~9 mo
            return "1y"
        if d >= 60:
            return "90d"
        if d >= 14:
            return "30d"
        if d >= 3:
            return "5d"
        return "1d"

    # Sanahaku fallback
    if "intraday" in s:
        return "1d"
    if "long-term" in s or "long term" in s or "decade" in s:
        return "5y+"
    return "unknown"


def compute_consensus(round2_proposals: list[dict]) -> dict:
    """Action vote (conviction-weighted) + hold-period clustering + dissent."""
    # Action voting (conviction-weighted)
    actions = {}
    for p in round2_proposals:
        a = p.get("action") or "UNKNOWN"
        cv = p.get("conviction_num", 0.5)
        actions[a] = actions.get(a, 0.0) + cv

    # Top action
    if actions:
        top_action = max(actions, key=actions.get)
        top_weight = actions[top_action]
        total_weight = sum(actions.values())
        share = top_weight / total_weight if total_weight else 0
    else:
        top_action, share = "NO_SIGNAL", 0

    # Hold-period clustering
    hold_clusters = {}
    for p in round2_proposals:
        cat = normalize_hold(p.get("hold_period"))
        if p.get("action") == top_action:  # vain konsensus-action:n holdit
            hold_clusters[cat] = hold_clusters.get(cat, 0) + 1

    # Dissent
    dissent = [
        {"persona": p["persona"], "action": p.get("action"),
         "hold": p.get("hold_period"), "thesis": p.get("revised_thesis") or p.get("thesis")}
        for p in round2_proposals
        if p.get("action") != top_action
    ]

    # Verdict
    n = len(round2_proposals)
    n_top = sum(1 for p in round2_proposals if p.get("action") == top_action)
    if n_top >= n - 1 and share >= 0.7:  # 6+/7 OR 4+/5 + high conviction
        verdict = "STRONG_CONSENSUS"
    elif n_top >= n // 2 + 1 and share >= 0.5:
        verdict = "WEAK_CONSENSUS"
    else:
        verdict = "NO_SIGNAL"

    return {
        "verdict": verdict,
        "consensus_action": top_action if verdict != "NO_SIGNAL" else None,
        "consensus_share": round(share, 3),
        "n_voters": n,
        "n_in_consensus": n_top,
        "hold_period_clusters": hold_clusters,
        "action_breakdown": {a: round(w, 2) for a, w in actions.items()},
        "dissent": dissent,
    }


# ─────────────────── MAIN ───────────────────

def run_debate(scenario: str) -> dict:
    debate_id = f"DEB_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    print(f"\n=== DEBATE {debate_id} ===")
    print(f"Scenario length: {len(scenario)} chars")

    personas = load_personas()
    print(f"Personas: {[p['slug'] for p in personas]}")

    # ROUND 1
    proposals = round1_generate(scenario, personas, debate_id)
    if len(proposals) < 3:
        print(f"\nLIIAN VAHAN ehdotuksia ({len(proposals)}). Keskeytan.")
        return {"verdict": "ABORT_INSUFFICIENT_PROPOSALS", "debate_id": debate_id}

    # Tallenna proposals
    for p in proposals:
        append_jsonl(PROPOSED, {"debate_id": debate_id, **p})

    # ROUND 2
    revised = round2_revise(scenario, personas, proposals, debate_id)
    if len(revised) < 3:
        print(f"\nLIIAN VAHAN revisioita. Keskeytan.")
        return {"verdict": "ABORT_INSUFFICIENT_REVISIONS", "debate_id": debate_id}

    # CONSENSUS
    consensus = compute_consensus(revised)
    consensus["debate_id"] = debate_id
    consensus["ts"] = datetime.now(timezone.utc).isoformat()

    # Tallenna consensus
    append_jsonl(CONSENSUS, consensus)

    print(f"\n=== CONSENSUS ===")
    print(f"VERDICT: {consensus['verdict']}")
    print(f"  Consensus action: {consensus['consensus_action']}")
    print(f"  Share (conviction-weighted): {consensus['consensus_share']*100:.0f}%")
    print(f"  In-consensus: {consensus['n_in_consensus']}/{consensus['n_voters']}")
    print(f"  Hold-period clusters: {consensus['hold_period_clusters']}")
    print(f"  Action breakdown: {consensus['action_breakdown']}")
    print(f"  Dissenters ({len(consensus['dissent'])}): {[d['persona'] for d in consensus['dissent']]}")

    return consensus


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", help="Path to scenario text file")
    p.add_argument("--inline", help="Inline scenario string")
    args = p.parse_args()

    if args.scenario:
        scenario = Path(args.scenario).read_text(encoding="utf-8")
    elif args.inline:
        scenario = args.inline
    else:
        # Default smoke scenario (sama kuin smoke_test_personas.py:ssa)
        scenario = """
Date: 2026-04-25
Ticker: NVDA (Nvidia)
Recent context:
- NVDA stock down -18% in last 30 days from peak ($142 -> $116)
- Q3 2026 earnings beat by 8%, but guidance for Q4 revealed slowing data-center growth (+22% YoY vs +56% prior quarter)
- China export restrictions tightened in March 2026, eliminating ~12% of TAM
- Competition: AMD MI400 launched in Feb showing 1.4x perf/$ vs H100 in published benchmarks
- Q4 forward P/E now 22x (5y avg 35x). Revenue still expected +18% YoY.
- VIX has risen from 13 to 22 in same 30-day window (broad market uncertainty)
- Fed signaled possible rate cut in June; 10Y yield down 40bp
- AI infrastructure capex from hyperscalers (MSFT, GOOG, META, AMZN) revised UP 8% for 2026
"""
    result = run_debate(scenario)
    print(f"\nDebate-loki: {DEBATE_LOG}")
    print(f"Proposed: {PROPOSED}")
    print(f"Consensus: {CONSENSUS}")


if __name__ == "__main__":
    main()
