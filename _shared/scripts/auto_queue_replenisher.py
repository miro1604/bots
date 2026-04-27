# -*- coding: utf-8 -*-
r"""auto_queue_replenisher.py — täydennä botin research_queue automaattisesti
kun queued+in_progress < threshold.

Käyttäjän mandaatti 2026-04-26: agentit eivät saa olla luppoajalla. Kun jono
ehtyy, orchestrator (Sonnet) generoi uusia tehtäviä kohti GOALS.md:n KR-tasoja.

Ajo (daemon):
  python auto_queue_replenisher.py [--threshold 3] [--bot all|<slug>] [--max-new 5]
"""
import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BOTS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BOTS_ROOT / "_shared" / "scripts"))

from model_router import call_claude

ALL_BOTS = ["alphahunter", "finance", "videoempire", "leadgen",
            "rd-coworker", "design", "convovault"]

ACTIONS_LOG = BOTS_ROOT / "_shared" / "memory" / "orchestrator_actions.jsonl"


PROMPT = """Sinä OLET vain analyysiagentti joka tuottaa TEKSTIVASTAUKSEN. ÄLÄ KÄYTÄ
työkaluja (Read, Edit, Bash, Write). ÄLÄ pyydä kirjoituslupaa. Toinen Python-skripti
appendoi research_queue:hen sen mitä palautat.

Olet research-arkkitehti botille **{bot}**. Botin tavoitteet ja jo tehty työ alla.
Tehtäväsi: generoida {n_new} UUTTA tutkimustehtävää jotka:
- vievät KR-tavoitteita seuraavalle portaalle (eivät toista jo tehtyä)
- ovat konkreettisia (mitä metodia, mitä dataa, mitä mittaria)
- ovat eri tyyppisiä (älä luo 5× samaa kategoriaa)
- huomioivat aiemmin opitun (ei sokeasti toistaa epäonnistuneita reittejä)

# Botin GOALS.md (KR-tasot)
{goals}

# Viim. {n_completed_shown} valmistunutta tehtävää (älä toista)
{completed}

# Viim. opit (signaalit + epäonnistumiset)
{learnings}

# Vastaus

Aloita rivillä "TASK_1:" suoraan, älä preamblea. Anna tasan {n_new} tehtävää.
Jokainen tehtävä TÄSMÄLLEEN tällä rakenteella (yksi rivi per kenttä):

TASK_1_TITLE: <60-150 merkin täsmä-otsikko>
TASK_1_TYPE: <category — esim. backtest, methodology, alt_data, deep_rl, reverse_engineer, cross_domain, never_quit_unconventional>
TASK_1_PRIORITY: high | medium | low
TASK_1_METHOD: <miten toteutetaan — datajoukot, mallit, validaatio (1-3 lausetta)>
TASK_1_LINKED_GOAL: <viittaus GOALS.md KR-numeroon, esim. "GOALS.md KR2">
TASK_1_NOTES: <miksi juuri tämä — mitä uutta tämä paljastaa (1-2 lausetta)>

[toista TASK_2..TASK_{n_new} samalla rakenteella]
"""


def read_jsonl(path):
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def queue_status(bot):
    rq = BOTS_ROOT / bot / "research" / "research_queue.jsonl"
    if not rq.exists():
        return {"queued": 0, "in_progress": 0, "completed": [], "exists": False}
    items = read_jsonl(rq)
    queued = sum(1 for d in items if d.get("status") == "queued")
    in_progress = sum(1 for d in items if d.get("status") == "in_progress")
    completed = [d for d in items if d.get("status") == "completed"]
    return {"queued": queued, "in_progress": in_progress,
            "completed": completed, "exists": True, "all": items}


def read_goals(bot):
    p = BOTS_ROOT / bot / "GOALS.md"
    if not p.exists():
        return "(GOALS.md puuttuu)"
    return p.read_text(encoding="utf-8")[:3000]


def read_learnings(bot, n=10):
    candidates = [
        BOTS_ROOT / bot / "research" / "learnings.jsonl",
        BOTS_ROOT / bot / "knowledge" / "learnings.jsonl",
    ]
    items = []
    for p in candidates:
        items.extend(read_jsonl(p))
    items.sort(key=lambda x: x.get("ts", ""), reverse=True)
    items = items[:n]
    if not items:
        return "(ei oppimerkintöjä vielä)"
    lines = []
    for it in items:
        note = (it.get("observation") or it.get("note") or "")[:200]
        ctx = it.get("context", "")
        if isinstance(ctx, dict):
            ctx = " ".join(f"{k}={v}" for k, v in ctx.items())[:80]
        lines.append(f"- [{(it.get('ts') or '')[:10]}] {note} ({ctx})")
    return "\n".join(lines)


def parse_response(text, n_new):
    tasks = []
    cur = None
    for raw in text.splitlines():
        s = raw.strip().lstrip("*-#").lstrip("_").replace("**", "").strip()
        if not s or ":" not in s:
            continue
        key, _, val = s.partition(":")
        key_u = key.strip().upper()
        val = val.strip().strip("`").strip('"').strip("'")
        if key_u.startswith("TASK_") and "_TITLE" in key_u:
            if cur:
                tasks.append(cur)
            try:
                idx = int(key_u.split("_")[1])
            except Exception:
                idx = len(tasks) + 1
            cur = {"_idx": idx, "title": val}
        elif cur is not None:
            for suffix, target in [("_TYPE", "type"), ("_PRIORITY", "priority"),
                                    ("_METHOD", "method"), ("_LINKED_GOAL", "linked_goal"),
                                    ("_NOTES", "notes")]:
                if key_u.endswith(suffix):
                    cur[target] = val
                    break
    if cur:
        tasks.append(cur)
    return tasks[:n_new]


def replenish_for_bot(bot, threshold, max_new):
    # Tarkista pause-flag
    pause_path = BOTS_ROOT / "_shared" / "memory" / "quota" / "pause_flags.json"
    if pause_path.exists():
        try:
            flags = json.loads(pause_path.read_text(encoding="utf-8"))
            bf = flags.get(bot)
            if isinstance(bf, dict) and bf.get("paused"):
                return {"bot": bot, "skipped": f"paused: {bf.get('reason','user')}"}
        except Exception:
            pass

    st = queue_status(bot)
    if not st["exists"]:
        return {"bot": bot, "skipped": "no_research_queue"}
    backlog = st["queued"] + st["in_progress"]
    if backlog >= threshold:
        return {"bot": bot, "skipped": f"backlog={backlog}>={threshold}"}

    n_new = min(max_new, max(3, threshold + 2))
    completed_titles = [c.get("title", "")[:120] for c in st["completed"][-10:]]
    completed_str = "\n".join(f"- {t}" for t in completed_titles) or "(ei vielä)"

    prompt = PROMPT.format(
        bot=bot,
        n_new=n_new,
        goals=read_goals(bot),
        n_completed_shown=len(completed_titles),
        completed=completed_str,
        learnings=read_learnings(bot),
    )
    text, err, rc = call_claude(
        prompt=prompt, task_type="creative_long_form", timeout=None,
        model="sonnet", fallback_model="haiku",
        bot=f"orchestrator:replenish:{bot}",
    )
    if rc != 0:
        return {"bot": bot, "error": (err or "")[:200], "rc": rc}

    parsed = parse_response(text, n_new)
    if not parsed:
        return {"bot": bot, "error": "parse_empty", "raw_tail": text[-300:]}

    rq_path = BOTS_ROOT / bot / "research" / "research_queue.jsonl"
    added = []
    for t in parsed:
        if not t.get("title"):
            continue
        rec = {
            "id": f"RES_{bot[:3].upper()}_AR_{int(time.time())}_{t.get('_idx', 0):02d}",
            "ts": datetime.now(timezone.utc).isoformat(),
            "priority": (t.get("priority") or "medium").lower().split()[0],
            "type": (t.get("type") or "auto_replenish").lower(),
            "title": t["title"][:200],
            "method": (t.get("method") or "")[:600],
            "linked_goal": t.get("linked_goal", ""),
            "status": "queued",
            "notes": (t.get("notes") or "")[:500],
            "source": "auto_replenish",
        }
        with open(rq_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        added.append(rec["id"])
    return {"bot": bot, "added": added, "previous_backlog": backlog}


def log_action(rec):
    ACTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    rec["ts"] = datetime.now(timezone.utc).isoformat()
    rec["action"] = "auto_replenish"
    with open(ACTIONS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=3,
                    help="Jos queued+in_progress < threshold, generoi uusia")
    ap.add_argument("--bot", default="all")
    ap.add_argument("--max-new", type=int, default=5)
    args = ap.parse_args()

    bots = ALL_BOTS if args.bot == "all" else [args.bot]
    summary = []
    for bot in bots:
        try:
            r = replenish_for_bot(bot, args.threshold, args.max_new)
        except Exception as e:
            r = {"bot": bot, "error": str(e)[:200]}
        log_action(r)
        summary.append(r)
        if "added" in r:
            print(f"[{bot}] ✓ +{len(r['added'])} new (backlog oli {r['previous_backlog']})")
        elif "skipped" in r:
            print(f"[{bot}] - skip: {r['skipped']}")
        else:
            print(f"[{bot}] ! error: {r.get('error', '?')[:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
