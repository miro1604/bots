# -*- coding: utf-8 -*-
r"""alphahunter_idea_router.py — reititä alphahunterin ideat kohdebotteihin.

Käyttäjän mandaatti 2026-04-27 (ops_alphahunter_triple_focus.md):
  cycle % 3 == 0 → agenttiverkoston kehitys (oma orchestrator_research-pipeline,
                                              ei reititystä)
  cycle % 3 == 1 → finance/research_queue.jsonl (perinteiset alpha-ideat)
  cycle % 3 == 2 → crypto-finance/research_queue.jsonl (krypto-strategiat,
                                                         leverage-friendly preferred)

Korvaa aiemman alphahunter_finance_bridge.py:n (joka teki vain bipolaarisen jaon).
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
CRY_QUEUE = BOTS_ROOT / "crypto-finance" / "research" / "research_queue.jsonl"
STATE = BOTS_ROOT / "_shared" / "memory" / "ah_router_state.json"


FINANCE_KEYWORDS = [
    "alpha", "stock", "equit", "etf", "option", "factor",
    "momentum", "value", "macro", "yield", "dividend",
    "sharpe", "earnings", "10-k", "fundamental",
]
CRYPTO_KEYWORDS = [
    "crypto", "bitcoin", "btc", "ethereum", "eth", "altcoin",
    "perp", "perpetual", "futures", "binance", "leverage",
    "defi", "stablecoin", "amm", "uniswap", "funding rate",
    "on-chain", "wallet", "mev",
]
LEVERAGE_KEYWORDS = ["leverage", "perp", "perpetual", "futures", "margin",
                     "10x", "20x", "50x", "100x", "125x"]


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"transferred_finance": [], "transferred_crypto": [], "last_run": None}


def save_state(state: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                     encoding="utf-8")


def categorize_idea(idea: dict) -> str:
    """Heuristiikka: mihin kategoriaan idea kuuluu cycle-mod-rajan jälkeen."""
    blob = json.dumps(idea, ensure_ascii=False).lower()
    crypto_hits = sum(1 for kw in CRYPTO_KEYWORDS if kw in blob)
    finance_hits = sum(1 for kw in FINANCE_KEYWORDS if kw in blob)
    if crypto_hits > finance_hits and crypto_hits >= 2:
        return "crypto"
    if finance_hits >= 2:
        return "finance"
    return "ambiguous"


def is_leverage_friendly(idea: dict) -> bool:
    blob = json.dumps(idea, ensure_ascii=False).lower()
    return any(kw in blob for kw in LEVERAGE_KEYWORDS)


def build_record(idea: dict, target_bot: str) -> dict:
    idea_id = idea.get("id", "?")
    cycle = idea.get("cycle", 0)
    seed = idea.get("seed", {})
    phases = idea.get("phases", {})

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

    if target_bot == "finance":
        prefix = "RES_FIN_AHR"
        linked_goal = "finance_alpha_discovery"
        notes_extra = ""
    else:  # crypto-finance
        prefix = "RES_CFI_AHR"
        linked_goal = "crypto_alpha_discovery"
        leverage_note = (
            " | LEVERAGE-FRIENDLY (Binance perp / margin)"
            if is_leverage_friendly(idea) else ""
        )
        notes_extra = (
            " ASAP-suuri-tuotto-fokus: korkea volatiliteetti + liquidaatioriski. "
            "Vaadi CPCV+PBO+walk-forward + adversarial ennen knowledge:hen. "
            "robustness_skeptic gate.")
        notes_extra += leverage_note

    rec = {
        "id": f"{prefix}_{idea_id}",
        "ts": datetime.now(timezone.utc).isoformat(),
        "priority": "high" if (target_bot == "crypto-finance"
                                and is_leverage_friendly(idea)) else "medium",
        "type": "alphahunter_referral",
        "title": f"[AH-referral] {str(name)[:140]}",
        "method": method[:600],
        "linked_goal": linked_goal,
        "status": "queued",
        "notes": (f"Alphahunter cycle {cycle}, idea_id={idea_id}.{notes_extra}"),
        "source_alphahunter_idea_id": idea_id,
        "source": "alphahunter_router",
        "leverage_friendly": is_leverage_friendly(idea) if target_bot == "crypto-finance" else False,
    }
    return rec


def transfer_to(queue_path: Path, rec: dict):
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with open(queue_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    if not AH_IDEAS.exists():
        print("alphahunter/knowledge/ideas.jsonl puuttuu — skip")
        return 0

    state = load_state()
    fin_done = set(state.get("transferred_finance", []))
    cry_done = set(state.get("transferred_crypto", []))

    candidates = []
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
            if not idea_id:
                continue
            cycle = d.get("cycle", 0)
            if cycle == 0:
                continue
            mod = cycle % 3
            # cycle % 3 == 0 → agenttiverkosto, ei reititetä
            if mod == 0:
                continue
            # Cycle % 3 == 1 → finance, cycle % 3 == 2 → crypto
            target = "finance" if mod == 1 else "crypto-finance"
            already = (idea_id in fin_done) if target == "finance" else (idea_id in cry_done)
            if already:
                continue
            # Tarkista että idea EI ole rejected
            status = (d.get("final_status") or "").lower()
            if status in ("rejected", "rejected_by_validator", "validator_failed"):
                continue
            # Tarkista kategorian relevanssi (sanity check — voi olla ettei cycle %
            # ole identtinen idean sisällön kanssa)
            cat = categorize_idea(d)
            if target == "crypto-finance" and cat == "finance":
                # Cycle ohjasi crypto:lle mutta sisältö on finance — pakota finance
                target = "finance"
                if idea_id in fin_done:
                    continue
            elif target == "finance" and cat == "crypto":
                # Vastaava: pakota crypto
                target = "crypto-finance"
                if idea_id in cry_done:
                    continue
            candidates.append((d, target))

    # Rate-limit per ajo: max 2 per kohde
    fin_added = 0
    cry_added = 0
    for idea, target in candidates:
        if target == "finance" and fin_added >= 2:
            continue
        if target == "crypto-finance" and cry_added >= 2:
            continue
        rec = build_record(idea, target)
        if target == "finance":
            transfer_to(FIN_QUEUE, rec)
            fin_done.add(idea["id"])
            fin_added += 1
            print(f"  + finance: {rec['id']}")
        else:
            transfer_to(CRY_QUEUE, rec)
            cry_done.add(idea["id"])
            cry_added += 1
            lev = " [LEVERAGE]" if rec.get("leverage_friendly") else ""
            print(f"  + crypto-finance: {rec['id']}{lev}")

    state["transferred_finance"] = list(fin_done)
    state["transferred_crypto"] = list(cry_done)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    print(f"  Transferred this run: finance={fin_added}, crypto={cry_added}")
    print(f"  Total ever: finance={len(fin_done)}, crypto={len(cry_done)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
