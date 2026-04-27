# -*- coding: utf-8 -*-
r"""user_action_comment_handler.py — käyttäjä-kommentti UI:n action-korttiin → välitön orchestrator-vastaus.

UI:n add_user_action_comment() launches this. Pipeline:
  1. Lue user_action + comment-historia
  2. Sonnet-LLM analysoi: mitä käyttäjä tarvitsee?
  3. Päätä: lisätäänkö stepit / muutetaanko / annetaanko lisäohje
  4. Päivitä user_actions.jsonl
  5. Merkitse needs_orchestrator_review=False

Tavoite: vastaus 30-90s, ei 5 min daemon-cycle.

Ajo:
  python user_action_comment_handler.py --action-id UAxxxxxxxx
"""
import argparse
import json
import sys
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

USER_ACTIONS = BOTS_ROOT / "_shared" / "agent_inbox" / "user_actions.jsonl"


PROMPT_TEMPLATE = """Sinä OLET vain analyysiagentti joka tuottaa TEKSTIVASTAUKSEN. ÄLÄ KÄYTÄ
työkaluja (Read, Edit, Bash, Write). ÄLÄ pyydä kirjoituslupaa. Toinen Python-skripti
tallentaa muutokset — sinä palautat strukturoidun tekstin alla olevassa muodossa.

Olet orchestrator joka auttaa käyttäjää (kiireinen omistaja) suorittamaan UI:n
ACTION REQUIRED -tehtävän. Käyttäjä on jättänyt kommentin joka kertoo että hänellä
on jokin ongelma.

# Tehtävä
{action_title}

# Alkuperäinen kysymys
{question}

# Nykyiset stepit
{steps_json}
{step_focus_block}
# Käyttäjän kommentit (uusin viimeisenä)
{comments_text}

# Tehtäväsi

Päätä mitä tehdä:

1. **CLARIFY** — käyttäjä ei ymmärrä jotain steppia → lisää lisäohje selittävä-kenttäksi (steps[i].help)
2. **REWRITE** — joku step on epäselvä tai virheellinen → kirjoita uudet stepit kokonaan
3. **SKIP_STEP** — joku step on jo tehty / ei tarvita → merkitse skip
4. **ABORT** — koko tehtävä ei toteuta → merkitse done + raportoi miksi
5. **ESCALATE** — tarvitaan käyttäjältä lisää tietoa, ei pysty päättää → kysy yksi tarkka kysymys

Kirjoita vastaus näin (TÄSMÄLLEEN):

DECISION: CLARIFY | REWRITE | SKIP_STEP | ABORT | ESCALATE
SUMMARY: <1-2 lausetta mitä päätit ja miksi>

Sitten jos REWRITE → kirjoita kokonainen uusi steps-lista JSON:na:
NEW_STEPS_JSON:
[{{"step_id":"s1","text":"...","done":false}}, ...]

Jos CLARIFY → kerro mille step:lle lisäohje + lisäohjeen sisältö:
CLARIFY_STEP_ID: s2
CLARIFY_HELP: <yksi lause selventää käyttäjälle>

Jos SKIP_STEP → step_id:
SKIP_STEP_ID: s3

Jos ABORT → reason:
ABORT_REASON: <miksi tehtävä peruutetaan>

Jos ESCALATE → kysymys käyttäjälle:
ASK_USER: <yksi tarkka kysymys>
"""


def parse_response(text):
    out = {"decision": None}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip().lstrip("*").lstrip("_").replace("**", "").strip()
        if s.upper().startswith("DECISION:"):
            out["decision"] = s[9:].strip().split()[0].upper()
        elif s.upper().startswith("SUMMARY:"):
            out["summary"] = s[8:].strip()
        elif s.upper().startswith("NEW_STEPS_JSON:"):
            # Read until next non-JSON line
            json_lines = []
            i += 1
            while i < len(lines):
                ln = lines[i].rstrip()
                if ln.strip().startswith(("DECISION:", "SUMMARY:", "CLARIFY_STEP_ID:",
                                              "CLARIFY_HELP:", "SKIP_STEP_ID:",
                                              "ABORT_REASON:", "ASK_USER:")):
                    break
                json_lines.append(ln)
                i += 1
            try:
                out["new_steps"] = json.loads("\n".join(json_lines).strip())
            except Exception:
                pass
            continue
        elif s.upper().startswith("CLARIFY_STEP_ID:"):
            out["clarify_step_id"] = s[16:].strip()
        elif s.upper().startswith("CLARIFY_HELP:"):
            out["clarify_help"] = s[13:].strip()
        elif s.upper().startswith("SKIP_STEP_ID:"):
            out["skip_step_id"] = s[13:].strip()
        elif s.upper().startswith("ABORT_REASON:"):
            out["abort_reason"] = s[13:].strip()
        elif s.upper().startswith("ASK_USER:"):
            out["ask_user"] = s[9:].strip()
        i += 1
    return out


def handle(action_id, step_id=None):
    if not USER_ACTIONS.exists():
        print("USER_ACTIONS missing")
        return 1
    items = []
    with open(USER_ACTIONS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    target = None
    for it in items:
        if it["id"] == action_id:
            target = it
            break
    if not target:
        print(f"action_id {action_id} not found")
        return 1

    comments = target.get("user_comments", [])
    if not comments:
        print("no comments to process")
        return 0

    # Erottele step-kohtaiset vs yleiset kommentit (näytä suluissa step_id jos löytyy)
    comments_text_lines = []
    for c in comments:
        sid = c.get("step_id")
        prefix = f"[{c.get('ts','')[:19]}]"
        if sid:
            prefix += f" [STEP {sid}]"
        comments_text_lines.append(f"{prefix} {c.get('text','')}")
    comments_text = "\n".join(comments_text_lines)

    # Step-focus block jos kommentti kohdistettiin tiettyyn stepiin
    step_focus_block = ""
    if step_id:
        focused = next((s for s in target.get("steps", []) if s.get("step_id") == step_id), None)
        if focused:
            step_focus_block = (
                f"\n# KOMMENTIN FOKUS — käyttäjä kommentoi tätä yhtä stepiä ({step_id})\n"
                f"  Step-teksti: {focused.get('text','')}\n"
                f"  Step done: {focused.get('done', False)}\n"
                "Tulkitse kommentti TÄMÄN stepin kontekstissa ensisijaisesti.\n"
            )

    prompt = PROMPT_TEMPLATE.format(
        action_title=target.get("action_title", ""),
        question=target.get("question", ""),
        steps_json=json.dumps(target.get("steps", []), ensure_ascii=False, indent=2),
        step_focus_block=step_focus_block,
        comments_text=comments_text,
    )

    text, err, rc = call_claude(
        prompt=prompt, task_type="deep_analysis", timeout=120,
        model="sonnet", fallback_model="haiku",
        bot="orchestrator:user_action_comment",
    )
    if rc != 0:
        print(f"call_claude fail rc={rc}: {err[:200]}")
        return 1

    parsed = parse_response(text)
    decision = parsed.get("decision")
    summary = parsed.get("summary", "")

    print(f"[{action_id}] DECISION: {decision}")
    print(f"  SUMMARY: {summary[:200]}")

    # Sovella muutokset
    target.setdefault("orchestrator_responses", []).append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "summary": summary,
        "raw": text[:1500],
    })

    if decision == "REWRITE" and parsed.get("new_steps"):
        target["steps"] = parsed["new_steps"]
        target["orchestrator_note"] = summary
    elif decision == "CLARIFY":
        for step in target.get("steps", []):
            if step.get("step_id") == parsed.get("clarify_step_id"):
                step["help"] = parsed.get("clarify_help", "")
        target["orchestrator_note"] = summary
    elif decision == "SKIP_STEP":
        sid = parsed.get("skip_step_id")
        for step in target.get("steps", []):
            if step.get("step_id") == sid:
                step["done"] = True
                step["skipped_by_orchestrator"] = True
        target["orchestrator_note"] = summary
    elif decision == "ABORT":
        target["all_steps_done"] = True
        target["completed_at"] = datetime.now(timezone.utc).isoformat()
        target["aborted_by_orchestrator"] = True
        target["abort_reason"] = parsed.get("abort_reason", summary)
    elif decision == "ESCALATE":
        target.setdefault("orchestrator_questions", []).append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "ask": parsed.get("ask_user", ""),
        })
        target["orchestrator_note"] = "ESCALATED: " + parsed.get("ask_user", summary)

    target["needs_orchestrator_review"] = False

    with open(USER_ACTIONS, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

    print(f"  applied → user_actions.jsonl")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--action-id", required=True)
    ap.add_argument("--step-id", default=None,
                    help="(optional) kommentti kohdistuu yksittäiseen stepiin")
    args = ap.parse_args()
    sys.exit(handle(args.action_id, step_id=args.step_id))


if __name__ == "__main__":
    main()
