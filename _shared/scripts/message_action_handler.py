# -*- coding: utf-8 -*-
r"""message_action_handler.py — käyttäjän MESSAGE → konkreettinen muutos.

Käyttäjän viesti UI:n MESSAGE-kentästä → tämä lukee viestin, päättelee Sonnet:lla
mitä KONKREETTISTA muutosta käyttäjä haluaa, ja TEKEE sen oikeasti:

  - PRINCIPLE_UPDATE → lisää rivin leadership_principles.md
  - PERSONA_TOGGLE → enable/disable persona
  - RESEARCH_TASK → lisää research_queue.jsonl
  - GOAL_AMENDMENT → päivittää bot/GOALS.md raw + formatter
  - DECISION_AUTHORITY → muuttaa agent_decision_authority.md
  - SCOPE_CHANGE → päivittää bot/CLAUDE.md
  - INFO_ONLY → tallentaa learning:iin (ei muutosta)

Ajo:
  python message_action_handler.py [--max 10]

Daemon ajaa joka 5 min (ottaa user_general_inputs.jsonl uudet viestit).
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BOTS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BOTS_ROOT / "_shared" / "scripts"))

from model_router import call_claude

INPUTS = BOTS_ROOT / "_shared" / "memory" / "user_general_inputs.jsonl"
PROCESSED = BOTS_ROOT / "_shared" / "memory" / "user_general_inputs_processed.jsonl"
LEARNINGS = BOTS_ROOT / "_shared" / "memory" / "orchestrator_learnings.jsonl"
PRINCIPLES = BOTS_ROOT / "_shared" / "memory" / "leadership_principles.md"


PROMPT_TEMPLATE = """Sinä OLET vain analyysiagentti joka tuottaa TEKSTIVASTAUKSEN. ÄLÄ KÄYTÄ
mitään työkaluja (Read, Edit, Bash, Write). ÄLÄ pyydä kirjoituslupaa tiedostoihin.
Toinen erillinen Python-skripti tekee fyysiset tiedostomuutokset sinun
tekstivastauksesi pohjalta — sinun ei tarvitse kirjoittaa mitään tiedostoa.

Tehtäväsi: tulkitse käyttäjän viesti ja luokittele se yhdeksi kahdeksasta
toimintatyypistä, palauttaen STRUKTUROITU TEKSTIRESPONSE alla olevassa muodossa.

# Käyttäjän viesti
Target: {target_bot}
Subject: {subject}
Text: {text}

# Toimintatyypit (valitse YKSI)

- PRINCIPLE_UPDATE — uusi pysyvä toimintaperiaate (yleinen sääntö jatkoon)
- GOAL_AMENDMENT — botin tavoitteen täsmennys/lisäys
- RESEARCH_TASK — konkreettinen uusi tutkimustehtävä jollekin botille
- PERSONA_INSTRUCTION — ohje jollekin agentin sisäiselle persoonalle
- TASK_TODO — ad-hoc tehtävä orchestratorille
- CLARIFICATION — käyttäjä haluaa selityksen, ei muutosta
- INFO_ONLY — käyttäjä jakaa havainnon, ei vaadi muutosta
- PRIORITY_BOOST — jonkin olemassaolevan prioriteetin nosto

# Vastauksen rakenne (TÄSMÄLLEEN tämä, plain text, EI markdown-koodilohkoja)

ACTION_TYPE: yksi yllä olevista (vain yksi sana, ei tähtiä, ei backtickkejä)
TARGET_BOT: bot-slug tai "all" tai "orchestrator"
RATIONALE: 2-3 lauseen perustelu

NEW_PRINCIPLE_TEXT: (vain jos PRINCIPLE_UPDATE) täsmällinen rivi joka lisätään
GOAL_AMENDMENT_TEXT: (vain jos GOAL_AMENDMENT) raw-text appendoituna
RESEARCH_TASK_JSON: (vain jos RESEARCH_TASK) JSON-rivi research_queueen
PERSONA_NOTE: (vain jos PERSONA_INSTRUCTION) slug + ohje
TODO_TEXT: (vain jos TASK_TODO) kuvaus
CLARIFICATION_RESPONSE: (vain jos CLARIFICATION) vastauksesi käyttäjälle
LEARNING_NOTE: (vain jos INFO_ONLY) havainto-tiivistys

Jos epävarmuus → ACTION_TYPE: INFO_ONLY ja LEARNING_NOTE: tiivistys.
Aloita vastauksesi suoraan rivillä "ACTION_TYPE:". ÄLÄ kirjoita preamblea.
"""


KNOWN_KEYS = [
    "ACTION_TYPE", "TARGET_BOT", "RATIONALE", "NEW_PRINCIPLE_TEXT",
    "GOAL_AMENDMENT_TEXT", "RESEARCH_TASK_JSON", "PERSONA_NOTE",
    "TODO_TEXT", "CLARIFICATION_RESPONSE", "LEARNING_NOTE",
]
KNOWN_ACTIONS = [
    "PRINCIPLE_UPDATE", "GOAL_AMENDMENT", "RESEARCH_TASK",
    "PERSONA_INSTRUCTION", "TASK_TODO", "CLARIFICATION",
    "INFO_ONLY", "PRIORITY_BOOST",
]


def _strip_decor(s):
    # Strip markdown decoration (bold, italic, code-fence, list bullets, headings)
    s = s.strip()
    # Remove leading list bullets / heading markers
    while s and s[0] in "-*#>":
        s = s[1:].lstrip()
    s = s.lstrip("_").replace("**", "")
    # Strip wrapping backticks/quotes
    return s.strip("`").strip("'").strip('"').strip()


def parse_response(text):
    """Tolerant parser — handles backticks, bold, list-bullets, code fences."""
    out = {}
    current_key = None
    current_val = []
    in_fence = False
    for raw in text.splitlines():
        # Skip code fence lines
        if raw.strip().startswith("```"):
            in_fence = not in_fence
            continue
        s = _strip_decor(raw)
        if not s:
            if current_key and current_val:
                out[current_key] = "\n".join(current_val).strip()
            current_key = None
            current_val = []
            continue
        # Look for KEY: value
        matched = False
        if ":" in s:
            key_part = s.split(":", 1)[0].strip().upper()
            if key_part in KNOWN_KEYS:
                if current_key and current_val:
                    out[current_key] = "\n".join(current_val).strip()
                _, _, val = s.partition(":")
                current_key = key_part.lower()
                v = _strip_decor(val) if val else ""
                current_val = [v] if v else []
                matched = True
        if not matched and current_key:
            current_val.append(s)
    if current_key and current_val:
        out[current_key] = "\n".join(current_val).strip()

    # Fallback — if action_type is empty, scan whole text for first known action
    action = out.get("action_type", "").upper()
    action = action.strip("`").strip("*").strip()
    if not action or action not in KNOWN_ACTIONS:
        for ka in KNOWN_ACTIONS:
            if ka in text.upper():
                out["action_type"] = ka
                break
    else:
        out["action_type"] = action
    return out


def apply_principle_update(text):
    """Lisää rivin leadership_principles.md:n version-listalle."""
    if not PRINCIPLES.exists() or not text:
        return False
    content = PRINCIPLES.read_text(encoding="utf-8")
    today = datetime.now(timezone.utc).date().isoformat()
    # Etsi viimeisin v-numero
    import re
    versions = re.findall(r"v(\d+)\.(\d+)", content)
    if versions:
        major = max(int(v[0]) for v in versions)
        latest_minor = max(int(v[1]) for v in versions if int(v[0]) == major)
        next_v = f"{major}.{latest_minor + 1}"
    else:
        next_v = "1.0"
    new_line = f"\n- {today} v{next_v}: **käyttäjän viesti UI:sta** — {text}"
    content += new_line
    PRINCIPLES.write_text(content, encoding="utf-8")
    return True


def apply_goal_amendment(target_bot, text):
    raw_dir = BOTS_ROOT / "_shared" / "memory" / "goals_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{target_bot}_raw.txt"
    existing = raw_path.read_text(encoding="utf-8") if raw_path.exists() else ""
    appendix = f"\n\n--- Lisäys {datetime.now(timezone.utc).date().isoformat()} ---\n{text}"
    raw_path.write_text(existing + appendix, encoding="utf-8")
    return True


def apply_research_task(target_bot, task_json_text):
    rq_dir = BOTS_ROOT / target_bot / "research"
    rq_dir.mkdir(parents=True, exist_ok=True)
    rq_path = rq_dir / "research_queue.jsonl"
    try:
        task = json.loads(task_json_text)
    except Exception:
        # Jos ei ole valid JSON, kääri siihen
        task = {
            "id": f"RES_{target_bot.upper()[:2]}_USER_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            "ts": datetime.now(timezone.utc).isoformat(),
            "priority": "medium",
            "type": "user_message",
            "title": task_json_text[:120],
            "method": "TBD",
            "linked_goal": "user-message",
            "status": "queued",
            "notes": task_json_text[:300],
        }
    if not task.get("id"):
        task["id"] = f"RES_USER_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    if not task.get("ts"):
        task["ts"] = datetime.now(timezone.utc).isoformat()
    if not task.get("status"):
        task["status"] = "queued"
    with open(rq_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(task, ensure_ascii=False) + "\n")
    return True


def append_learning(target_bot, note):
    LEARNINGS.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "user_message",
        "target_bot": target_bot,
        "note": note[:500],
    }
    with open(LEARNINGS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def apply_todo_task(target_bot, todo_text, msg, rationale):
    """Kirjoita orchestrator_todo.jsonl:iin konkreettinen todo joka odottaa
    autonomista toteutusta. Käyttäjän mandaatti: TASK_TODO ei ole pelkkä learning,
    se on aloittamattoman työn merkintä jolla on PRIORITY ja STATUS=pending."""
    todo_path = BOTS_ROOT / "_shared" / "memory" / "orchestrator_todo.jsonl"
    todo_path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "user_message",
        "msg_thread_id": msg.get("thread_id"),
        "msg_subject": msg.get("subject", ""),
        "target_bot": target_bot,
        "title": (msg.get("subject") or todo_text[:60] or "user_task").strip(),
        "description": todo_text[:2000],
        "rationale": rationale[:400],
        "priority": "high",
        "status": "pending",
    }
    with open(todo_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def get_thread_history(thread_id):
    """Hae keskusteluhistoria thread:stä."""
    if not thread_id or not INPUTS.exists():
        return []
    history = []
    with open(INPUTS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    d = json.loads(line)
                    if d.get("thread_id") == thread_id:
                        history.append(d)
                except Exception:
                    pass
    history.sort(key=lambda x: x.get("ts", ""))
    return history


def append_orchestrator_reply(thread_id, target_bot, response_text, actions_summary):
    """Tallenna orchestrator-vastausviesti samaan thread:iin."""
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "orchestrator_reply",
        "role": "orchestrator",
        "thread_id": thread_id,
        "target_bot": target_bot,
        "subject": "",
        "text": response_text,
        "actions_summary": actions_summary,
    }
    with open(INPUTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def process_message(msg):
    target = msg.get("target_bot") or "all"
    text = msg.get("text", "")
    subject = msg.get("subject", "")
    thread_id = msg.get("thread_id", "")
    if not text:
        return None

    # Keskusteluhistoria thread:istä → konteksti orchestratorille
    history_text = ""
    if thread_id:
        history = get_thread_history(thread_id)
        if len(history) > 1:
            hist_lines = []
            for h in history[:-1]:  # ohita nykyinen
                role = h.get("role") or ("user" if h.get("type") in ("comment", None) else "orchestrator")
                hist_lines.append(f"[{role.upper()}] {h.get('text','')[:500]}")
            history_text = "\n\n# Aiempi keskustelu thread:issä\n" + "\n---\n".join(hist_lines)

    prompt = PROMPT_TEMPLATE.format(
        target_bot=target, subject=subject, text=text[:2000]) + history_text

    response_text, err, rc = call_claude(
        prompt=prompt, task_type="deep_analysis", timeout=120,
        model="sonnet", fallback_model="haiku",
        bot="orchestrator:message_handler",
    )
    if rc != 0:
        return {"error": err[:200]}

    parsed = parse_response(response_text)
    action = parsed.get("action_type", "").upper()
    target_bot = parsed.get("target_bot", target).strip().lower()
    rationale = parsed.get("rationale", "")

    applied = False
    detail = ""

    # Käyttäjän mandaatti: KAIKKI UI-viestit päätyvät TO-DO-listalle
    # riippumatta action-tyypistä. Action-spesifinen toimenpide tehdään lisäksi.
    todo_target = target_bot if target_bot and target_bot != "all" else "orchestrator"
    apply_todo_task(todo_target, text, msg, rationale)

    if action.startswith("PRINCIPLE"):
        new_text = parsed.get("new_principle_text", text)
        applied = apply_principle_update(new_text)
        detail = f"leadership_principles.md päivitetty: {new_text[:100]}"
    elif action.startswith("GOAL"):
        gt = parsed.get("goal_amendment_text", text)
        applied = apply_goal_amendment(target_bot, gt)
        detail = f"{target_bot}_raw.txt päivitetty (SMART-formatter ottaa 5 min sisällä)"
    elif action.startswith("RESEARCH"):
        rj = parsed.get("research_task_json", text)
        applied = apply_research_task(target_bot, rj)
        detail = f"{target_bot}/research/research_queue.jsonl päivitetty"
    elif action.startswith("TASK_TODO") or action.startswith("TODO"):
        # Käyttäjän mandaatti: TASK_TODO IMPLEMENTOIDAAN, ei pelkkä learning.
        # Kirjoita orchestrator_todo.jsonl:iin korkealla prioriteetilla, niin että
        # daemon/orchestrator näkee sen + saa tartuttua siihen autonomisesti.
        todo_text = parsed.get("todo_text") or text
        applied = apply_todo_task(target_bot, todo_text, msg, rationale)
        detail = f"orchestrator_todo.jsonl: uusi todo (target={target_bot}), aloitetaan autonomisesti"
    elif action.startswith("PRIORITY"):
        note = parsed.get("learning_note") or text
        applied = append_learning(target_bot, f"[PRIORITY_BOOST] {note}")
        detail = "prioriteetti-boost kirjattu (orchestrator nostaa target_bot:n työt)"
    elif action.startswith("PERSONA"):
        note = parsed.get("persona_note") or text
        applied = append_learning(target_bot, f"[PERSONA_INSTRUCTION] {note}")
        detail = f"persona-ohje kirjattu (sovelletaan seuraavissa debate-kierroksissa)"
    elif action.startswith("CLARIFICATION") or action.startswith("INFO"):
        note = parsed.get("learning_note") or parsed.get("clarification_response") or text
        applied = append_learning(target_bot, note)
        detail = "tallennettu orchestrator_learnings.jsonl"
    else:
        # Tuntematon → todo-jonoon ettei jää roikkumaan
        applied = apply_todo_task(target_bot, f"[unknown action {action}] {text}", msg, rationale)
        detail = f"action={action} tulkinta epäselvä → ohjattu orchestrator_todo.jsonl:iin manuaalista käsittelyä varten"

    # Tallenna orchestrator-vastaus thread:iin (käyttäjä näkee UI:ssa)
    if msg.get("thread_id"):
        action_label = action if action else "tulkinta epävarma"
        reply_text = (
            f"**Toimenpide: {action_label}** ({target_bot})\n\n"
            f"{detail}\n\n"
            f"Perustelu: {rationale[:400] if rationale else '(ei perustelua — paluu fallback-polkuun)'}"
        )
        append_orchestrator_reply(
            msg.get("thread_id"), target_bot, reply_text,
            {"action": action, "applied": applied, "detail": detail},
        )

    return {
        "action": action,
        "target_bot": target_bot,
        "rationale": rationale,
        "applied": applied,
        "detail": detail,
    }


def _msg_hash(msg):
    """Yksilöivä tunniste viestille — käyttää sisältö-hashin."""
    import hashlib
    payload = f"{msg.get('ts','')}|{msg.get('thread_id','')}|{msg.get('target_bot','')}|{(msg.get('text') or '')[:500]}"
    return hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()[:16]


def is_processed(msg):
    if not PROCESSED.exists():
        return False
    h = _msg_hash(msg)
    with open(PROCESSED, encoding="utf-8") as f:
        for line in f:
            if h in line:
                return True
    return False


def mark_processed(msg, result):
    rec = {
        "ts_processed": datetime.now(timezone.utc).isoformat(),
        "msg_hash": _msg_hash(msg),
        "msg_ts": msg.get("ts"),
        "msg_thread_id": msg.get("thread_id"),
        "msg_role": msg.get("role"),
        "msg_target": msg.get("target_bot"),
        "msg_subject": msg.get("subject"),
        "msg_text_preview": (msg.get("text") or "")[:200],
        "result": result,
    }
    with open(PROCESSED, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=10)
    args = ap.parse_args()

    if not INPUTS.exists():
        print("No user_general_inputs.jsonl yet")
        return 0

    items = []
    with open(INPUTS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except Exception:
                    pass

    n_processed = 0
    for msg in items:
        if n_processed >= args.max:
            break
        # Skip orchestrator-omat vastausviestit (ne eivät tarvitse uutta käsittelyä)
        if msg.get("role") == "orchestrator":
            continue
        if is_processed(msg):
            continue
        print(f"\n[{msg.get('ts','')[:19]}] target={msg.get('target_bot')} subject={msg.get('subject','')[:40]}")
        print(f"  text: {(msg.get('text') or '')[:120]}")
        result = process_message(msg)
        print(f"  result: {result}")
        mark_processed(msg, result)
        n_processed += 1

    print(f"\nProcessed {n_processed} new message(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
