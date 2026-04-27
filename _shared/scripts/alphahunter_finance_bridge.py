# -*- coding: utf-8 -*-
r"""alphahunter_finance_bridge.py — siirrä alphahunterin parilliset-cycle-ideat
finance:n research_queue:hen "alphahunter_referral"-tyypillä.

Käyttäjän mandaatti 2026-04-27 (ops_alphahunter_dual_focus.md):
  Parittomat cyclet → agenttiverkoston kehitys (oma idea-pipeline, ei toimenpidettä)
  Parilliset cyclet → finance-ylituotto-idea → siirretään finance:n queueen

Logiikka:
  1. Lue alphahunter/knowledge/ideas.jsonl
  2. Suodata: cycle % 2 == 0 ja final_status sopiva (esim. 'approved')
  3. Vertaa _shared/memory/ah_bridge_state.json:iin → mitkä on jo siirretty
  4. Lisää uudet finance/research/research_queue.jsonl:iin tyypillä alphahunter_referral

Ajetaan daemon-cyclellä (5 min). Idempotentti — sama idea siirretään vain kerran.
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

BOTS_ROOT = Path(__file__).resolve().parents[2]
AH_IDEAS = BOTS_ROOT / "alphahunter" / "knowledge" / "ideas.jsonl"
FIN_QUEUE = BOTS_ROOT / "finance" / "research" / "research_queue.jsonl"
STATE = BOTS_ROOT / "_shared" / "memory" / "ah_bridge_state.json"


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"transferred_ids": [], "last_run": None}


def save_state(state: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                     encoding="utf-8")


def is_finance_relevant(idea: dict) -> bool:
    """Heuristiikka: onko idea ylituotto-relevantti finance:lle?

    Tarkistaa idea:n seed/phases-tekstit avainsanoista jotka viittaavat
    sijoittamiseen, tradingiin, alfaan, marketiin tai vastaavaan."""
    blob = json.dumps(idea, ensure_ascii=False).lower()
    finance_keywords = [
        "alpha", "trading", "stock", "equit", "portfolio",
        "yield", "return", "sharpe", "backtest", "signal",
        "market", "asset", "macro", "factor", "momentum",
        "mean-revers", "arbitr", "hedge", "options", "crypto",
        "sijoitt", "kauppa", "tuotto",
    ]
    return any(kw in blob for kw in finance_keywords)


def transfer_idea(idea: dict) -> bool:
    """Lisää idea finance:n queueen alphahunter_referral-tyypillä."""
    idea_id = idea.get("id", "?")
    cycle = idea.get("cycle", 0)
    seed = idea.get("seed", {})
    phases = idea.get("phases", {})

    # Yritä koota kuvaava title + method
    name = (phases.get("visionary", {}).get("idea_name")
            or seed.get("title")
            or idea.get("idea_name")
            or f"Alphahunter idea {idea_id}")
    mechanism = (phases.get("visionary", {}).get("mechanism")
                 or phases.get("idea", {}).get("mechanism")
                 or "")
    revenue = (phases.get("visionary", {}).get("revenue")
               or "")

    method = f"{mechanism[:400]}\n\nMonetization: {revenue[:200]}".strip()

    rec = {
        "id": f"RES_FIN_AHR_{idea_id}",
        "ts": datetime.now(timezone.utc).isoformat(),
        "priority": "medium",
        "type": "alphahunter_referral",
        "title": f"[AH-referral] {str(name)[:140]}",
        "method": method[:600],
        "linked_goal": "finance_alpha_discovery",
        "status": "queued",
        "notes": (f"Alphahunter cycle {cycle}, idea_id={idea_id}. "
                  "Validoi finance:n personalattalla. CPCV+PBO ennen knowledge:hen."),
        "source_alphahunter_idea_id": idea_id,
        "source": "alphahunter_bridge",
    }
    FIN_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with open(FIN_QUEUE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def main():
    if not AH_IDEAS.exists():
        print("alphahunter/knowledge/ideas.jsonl puuttuu — skip")
        return 0

    state = load_state()
    transferred = set(state.get("transferred_ids", []))

    new_ideas = []
    with open(AH_IDEAS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            idea_id = d.get("id")
            if not idea_id or idea_id in transferred:
                continue
            cycle = d.get("cycle", 0)
            # Vain parilliset cyclet → finance
            if cycle == 0 or cycle % 2 != 0:
                continue
            # Tarkista onko finance-relevantti
            if not is_finance_relevant(d):
                continue
            # Tarkista että final_status ei ole "rejected"
            status = (d.get("final_status") or "").lower()
            if status in ("rejected", "rejected_by_validator", "validator_failed"):
                continue
            new_ideas.append(d)

    # Rate limit: max 1 per cycle-pari = max 2-3 per ajo
    new_ideas = new_ideas[:3]

    transferred_count = 0
    for idea in new_ideas:
        try:
            if transfer_idea(idea):
                transferred.add(idea["id"])
                transferred_count += 1
                print(f"  + {idea['id']} → finance/research_queue.jsonl")
        except Exception as e:
            print(f"  ! transfer fail {idea.get('id')}: {e}")

    state["transferred_ids"] = list(transferred)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    print(f"  Transferred {transferred_count}/{len(new_ideas)} new ideas. "
          f"Total ever transferred: {len(transferred)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
