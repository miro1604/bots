# -*- coding: utf-8 -*-
r"""orchestrator_ui.py — Matrix-teemainen orkestrointi-UI.

Standalone Python http.server (ei ulkoisia riippuvuuksia).

Static frontend: _shared/ui/{index.html, style.css, app.js}
REST API:
  GET  /api/bots          — lista botteja + per-bot tila
  GET  /api/personas      — kaikki personat
  GET  /api/dora          — viimeisimmät DORA-metriikat
  GET  /api/syntheses     — viimeisimmät debatesynthesisit
  GET  /api/heartbeats    — heartbeat-tila
  GET  /api/decisions     — käyttäjän päätökset
  POST /api/decisions     — tallenna uusi päätös
  GET  /api/quota         — quota-pressure

Ajo:
  python _shared/scripts/orchestrator_ui.py [--port 8888]

Avaa selaimella: http://localhost:8888
"""
import argparse
import base64
import json
import os
import secrets
import sys
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BOTS_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = BOTS_ROOT / "_shared" / "ui"
DECISIONS_FILE = BOTS_ROOT / "_shared" / "memory" / "user_decisions.jsonl"
DECISIONS_FILE.parent.mkdir(parents=True, exist_ok=True)

# === HTTP Basic Auth ===
AUTH_FILE = BOTS_ROOT / "_shared" / "ui" / ".auth.json"


def _load_or_create_auth():
    """Lue tai luo basic-auth credentials. Generoi vahvan salasanan jos ei ole."""
    if AUTH_FILE.exists():
        try:
            return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    user = "puros"
    pw = secrets.token_urlsafe(18)  # vahva 24-merkkinen
    creds = {"user": user, "password": pw}
    AUTH_FILE.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    print("=" * 60)
    print("  UUDET BASIC AUTH -tunnukset luotu:")
    print(f"  Käyttäjä: {user}")
    print(f"  Salasana: {pw}")
    print(f"  Tallennettu: {AUTH_FILE}")
    print(f"  (lue myöhemmin: cat {AUTH_FILE})")
    print("=" * 60)
    return creds


def check_auth(header_value, expected_user, expected_pw):
    if not header_value or not header_value.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header_value[6:]).decode("utf-8")
        u, _, p = decoded.partition(":")
        return u == expected_user and p == expected_pw
    except Exception:
        return False


BOTS = ["alphahunter", "finance", "videoempire", "leadgen", "rd-coworker", "design", "convovault"]


def safe_read_json(path: Path):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"err reading {path}: {e}")
    return None


def safe_read_jsonl(path: Path, limit: int = 50):
    out = []
    if not path.exists():
        return out
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        # take last N
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except Exception as e:
        print(f"err reading {path}: {e}")
    return out


def get_bot_status():
    """Lue kaikki bottien tila + current research + all-time stats."""
    out = []
    wd_dir = BOTS_ROOT / "_shared" / "memory" / "watchdog"
    for bot in BOTS:
        info = {"slug": bot, "online": False, "personas": 0,
                "last_heartbeat": None, "stats": {}}

        # Heartbeat
        hb = wd_dir / f"{bot}_heartbeat.json"
        if hb.exists():
            d = safe_read_json(hb)
            if d:
                info["last_heartbeat"] = d.get("ts")
                info["state"] = d.get("state")
                info["stats"] = d.get("stats", {})
                info["cycle"] = d.get("cycle")
                ts_str = d.get("ts", "")
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    age_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60
                    info["heartbeat_age_min"] = round(age_min, 1)
                    info["online"] = age_min < 60
                except Exception:
                    info["heartbeat_age_min"] = None

                # Current research topic
                if d.get("scenario_preview"):
                    info["current_topic"] = d["scenario_preview"][:200]
                elif d.get("idea_id"):
                    info["current_topic"] = f"Generating idea {d['idea_id']}"
                elif d.get("target"):
                    info["current_topic"] = f"Evolution research: {d['target']}"

        # Personas (kpl ja active-määrä)
        pjs = BOTS_ROOT / bot / "agents" / "personas.jsonl"
        if pjs.exists():
            try:
                personas = []
                with open(pjs, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            personas.append(json.loads(line))
                info["personas"] = len(personas)
                info["personas_active"] = sum(1 for p in personas if p.get("active", True))
            except Exception:
                pass

        # All-time tilastot
        info["all_time"] = {}
        # Syntheses
        syn = BOTS_ROOT / bot / "debate" / "syntheses.jsonl"
        if syn.exists():
            try:
                with open(syn, encoding="utf-8") as f:
                    info["all_time"]["syntheses"] = sum(1 for l in f if l.strip())
            except Exception:
                pass
        # Ideas (alphahunter)
        ideas = BOTS_ROOT / bot / "knowledge" / "ideas.jsonl"
        if ideas.exists():
            try:
                with open(ideas, encoding="utf-8") as f:
                    info["all_time"]["ideas"] = sum(1 for l in f if l.strip())
            except Exception:
                pass
        # Evolution proposals (alphahunter)
        prop = BOTS_ROOT / bot / "proposals"
        if prop.exists() and prop.is_dir():
            info["all_time"]["evolution_proposals"] = len(list(prop.glob("*.md")))
        # Research-runs (finance)
        rr = BOTS_ROOT / bot / "research" / "reverse_engineer_runs.jsonl"
        if rr.exists():
            try:
                with open(rr, encoding="utf-8") as f:
                    info["all_time"]["research_runs"] = sum(1 for l in f if l.strip())
            except Exception:
                pass
        # GOALS.md status
        goals = BOTS_ROOT / bot / "GOALS.md"
        info["has_goals"] = goals.exists()

        # Has CLAUDE.md
        claude_md = BOTS_ROOT / bot / "CLAUDE.md"
        info["has_spec"] = claude_md.exists()
        out.append(info)
    return out


def toggle_persona_active(bot, slug, active):
    """Päivitä personas.jsonl-rivin active-kenttä."""
    if not bot or not slug:
        return False
    pjs = BOTS_ROOT / bot / "agents" / "personas.jsonl"
    if not pjs.exists():
        return False
    items = []
    with open(pjs, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    found = False
    for it in items:
        if it.get("slug") == slug:
            it["active"] = bool(active)
            found = True
    if not found:
        return False
    with open(pjs, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    return True


def decide_pending(question_id, decision_text):
    """Käyttäjä antaa päätöksen orchestratorin escalation-kysymykseen → tallenna + kytke loop."""
    if not question_id or not decision_text:
        return False
    esc = BOTS_ROOT / "_shared" / "agent_inbox" / "escalated_to_user.jsonl"
    ans = BOTS_ROOT / "_shared" / "agent_inbox" / "answered.jsonl"
    if not esc.exists():
        return False
    items = []
    with open(esc, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    found_record = None
    for it in items:
        if it.get("id") == question_id:
            found_record = it
            break
    if not found_record:
        return False

    # Tallenna käyttäjän päätös answered:iin
    user_decided = dict(found_record)
    user_decided["status"] = "answered"
    user_decided["decision"] = decision_text
    user_decided["resolved_by"] = "user_via_decisions_panel"
    user_decided["resolved_at"] = datetime.now(timezone.utc).isoformat()
    user_decided["original_escalation_decision"] = found_record.get("decision")
    with open(ans, "a", encoding="utf-8") as f:
        f.write(json.dumps(user_decided, ensure_ascii=False) + "\n")

    # Lisää myös user_decisions.jsonl:iin (UI:n decisions-log)
    append_decision({
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "user_decided",
        "target": found_record.get("bot", ""),
        "text": f"[{question_id}] {decision_text}",
    })
    return True


def get_decisions_pending():
    """Orchestrator-eskaloimat päätökset (escalated_to_user.jsonl) + käyttäjän omat päätökset."""
    pending = []
    esc = BOTS_ROOT / "_shared" / "agent_inbox" / "escalated_to_user.jsonl"
    if esc.exists():
        with open(esc, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        d = json.loads(line)
                        # Vain ne joista ei ole vielä käyttäjän vastausta
                        if d.get("decision") in ("ESCALATED_TO_USER", "ESCALATE_TO_USER"):
                            # Tarkista onko user_actionsissa kytketty + valmistunut
                            qid = d.get("id")
                            already_addressed = False
                            ua_path = BOTS_ROOT / "_shared" / "agent_inbox" / "user_actions.jsonl"
                            if ua_path.exists():
                                with open(ua_path, encoding="utf-8") as uaf:
                                    for ul in uaf:
                                        ul = ul.strip()
                                        if ul:
                                            try:
                                                ua = json.loads(ul)
                                                if ua.get("related_question_id") == qid and ua.get("all_steps_done"):
                                                    already_addressed = True
                                                    break
                                            except Exception:
                                                pass
                            if not already_addressed:
                                pending.append({
                                    "id": d.get("id"),
                                    "ts": d.get("ts"),
                                    "bot": d.get("bot"),
                                    "type": d.get("type"),
                                    "question": d.get("question", "")[:200],
                                    "options": d.get("options", []),
                                    "reason": d.get("reason", "")[:200],
                                    "priority": d.get("priority", "medium"),
                                })
                    except Exception:
                        pass
    return pending


def get_personas():
    out = []
    for bot in BOTS:
        pjs = BOTS_ROOT / bot / "agents" / "personas.jsonl"
        if not pjs.exists():
            continue
        try:
            with open(pjs, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        d = json.loads(line)
                        d["bot"] = bot
                        out.append(d)
        except Exception:
            pass
    return out


def get_recent_syntheses():
    out = []
    for bot in BOTS:
        syn = BOTS_ROOT / bot / "debate" / "syntheses.jsonl"
        if syn.exists():
            recent = safe_read_jsonl(syn, limit=10)
            for r in recent:
                r["bot"] = bot
                # truncate synthesis-text
                if "synthesis" in r and isinstance(r["synthesis"], str):
                    r["synthesis"] = r["synthesis"][:500] + "..."
                out.append(r)
    out.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return out[:30]


def get_dora_summary():
    dora_dir = BOTS_ROOT / "_shared" / "memory" / "dora"
    out = {"latest_report": None, "per_bot": {}}
    if dora_dir.exists():
        reports = sorted(dora_dir.glob("dora_report_*.md"), reverse=True)
        if reports:
            try:
                out["latest_report"] = reports[0].read_text(encoding="utf-8")[:5000]
            except Exception:
                pass
        # Per-bot CSV last row
        for bot in BOTS:
            csv = dora_dir / f"{bot}_dora.csv"
            if csv.exists():
                try:
                    lines = csv.read_text(encoding="utf-8").strip().split("\n")
                    if len(lines) >= 2:
                        headers = lines[0].split(",")
                        last = lines[-1].split(",")
                        out["per_bot"][bot] = dict(zip(headers, last))
                except Exception:
                    pass
    return out


def get_quota():
    qd = BOTS_ROOT / "_shared" / "memory" / "quota" / "pressure_latest.json"
    return safe_read_json(qd) or {}


def get_agency_metrics():
    """Lue viim. agency_metrics.jsonl-rivi per botti."""
    path = BOTS_ROOT / "_shared" / "memory" / "agency_metrics.jsonl"
    if not path.exists():
        return []
    latest_per_bot = {}
    rows = safe_read_jsonl(path, limit=10000)
    for r in rows:
        bot = r.get("bot")
        if bot:
            latest_per_bot[bot] = r
    return list(latest_per_bot.values())


def get_orchestrator_todo():
    """Lue orchestrator_todo.jsonl + ryhmittele status mukaan."""
    path = BOTS_ROOT / "_shared" / "memory" / "orchestrator_todo.jsonl"
    if not path.exists():
        return {"pending": [], "in_progress": [], "completed": []}
    rows = safe_read_jsonl(path, limit=1000)
    out = {"pending": [], "in_progress": [], "completed": []}
    for r in rows:
        status = (r.get("status") or "pending").lower()
        if status in out:
            out[status].append(r)
        else:
            out["pending"].append(r)
    # Sortaa pending: prio + ts
    prio_order = {"high": 0, "medium": 1, "low": 2}
    out["pending"].sort(
        key=lambda x: (prio_order.get(x.get("priority", "medium"), 1),
                       x.get("ts", "")))
    return out


def get_decisions(limit: int = 100):
    return safe_read_jsonl(DECISIONS_FILE, limit=limit)


INBOX_QUEUE = BOTS_ROOT / "_shared" / "agent_inbox" / "queue.jsonl"
INBOX_ANSWERED = BOTS_ROOT / "_shared" / "agent_inbox" / "answered.jsonl"
INBOX_ESCALATED = BOTS_ROOT / "_shared" / "agent_inbox" / "escalated_to_user.jsonl"


USER_ACTIONS = BOTS_ROOT / "_shared" / "agent_inbox" / "user_actions.jsonl"
ANSWERED_LOG = BOTS_ROOT / "_shared" / "agent_inbox" / "answered.jsonl"


def _count_pending_per_bot():
    """Lue queue.jsonl + escalated_to_user.jsonl ja laske montako pending-kysymystä
    per botti — botit joilla paljon pendingiä ovat blokkaantuneita ja niiden
    user-actionit pitää priorisoida."""
    counts = {}
    inbox_dir = BOTS_ROOT / "_shared" / "agent_inbox"
    for fname in ("queue.jsonl", "escalated_to_user.jsonl"):
        p = inbox_dir / fname
        if not p.exists():
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("status") in ("answered", "resolved", "closed"):
                        continue
                    bot = d.get("bot", "")
                    if bot:
                        counts[bot] = counts.get(bot, 0) + 1
        except Exception:
            pass
    return counts


def get_user_actions():
    """Palauta käyttäjälle eskaloidut toiminta-paketit, priorisoitu vaikuttavuuden mukaan.

    Ranking-järjestys (ylin ensin):
      1. Re-issue (previous_attempt_failed) — käyttäjä on jo panostanut, korjaus kriittinen
      2. Urgency (high → medium → low)
      3. Botin blokkausaste (montako muuta pending-kysymystä samalla botilla → kun
         tämä ratkeaa, vapautuu eniten työtä)
      4. Vanhin ensin (FIFO inside tier — älä jätä mitään roikkumaan)
      5. Pienempi estimoitu aika ensin (quick wins jos muu on tasan)
    """
    if not USER_ACTIONS.exists():
        return []
    items = []
    with open(USER_ACTIONS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    d = json.loads(line)
                    if not d.get("all_steps_done"):
                        items.append(d)
                except Exception:
                    pass

    bot_pending = _count_pending_per_bot()
    urgency_order = {"high": 0, "medium": 1, "low": 2}

    def rank_key(it):
        is_reissue = 1 if it.get("previous_attempt_failed") else 0
        urgency = urgency_order.get(it.get("urgency", "medium"), 1)
        bot_block = bot_pending.get(it.get("bot", ""), 0)
        ts_str = it.get("ts") or ""
        try:
            age_neg = -datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
        except Exception:
            age_neg = 0
        est_time = it.get("estimated_time_min") or 30
        return (-is_reissue, urgency, -bot_block, age_neg, est_time)

    items.sort(key=rank_key)
    # Lisää näkyvä impact-info per item (UI voi näyttää)
    for it in items:
        it["_bot_pending"] = bot_pending.get(it.get("bot", ""), 0)
    return items


def add_user_action_comment(action_id, comment_text, step_id=None):
    """Käyttäjä lisää kommentin user-actioniin → triggers välitön orchestrator-resolver.

    step_id (optional) — jos annettu, kommentti kohdistuu yksittäiseen stepiin
    (käyttäjän ei tarvitse erikseen kertoa mihin viittaa)."""
    if not USER_ACTIONS.exists():
        return False
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
        return False

    comment_rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "text": comment_text,
    }
    if step_id:
        comment_rec["step_id"] = step_id
        # Liitä myös step-objektiin oma comments-lista
        for s in target.get("steps", []):
            if s.get("step_id") == step_id:
                s.setdefault("comments", []).append({
                    "ts": comment_rec["ts"],
                    "text": comment_text,
                })
                break

    target.setdefault("user_comments", []).append(comment_rec)
    target["needs_orchestrator_review"] = True

    with open(USER_ACTIONS, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

    # Trigger: ei odoteta daemon-cycle:ä — käynnistä orchestrator-resolver heti taustaprosessina
    import subprocess as _subp
    try:
        scripts_dir = BOTS_ROOT / "_shared" / "scripts"
        cmd = [r"C:\Users\puros\nn_env\Scripts\python.exe",
               str(scripts_dir / "user_action_comment_handler.py"),
               "--action-id", action_id]
        if step_id:
            cmd += ["--step-id", step_id]
        _subp.Popen(
            cmd,
            cwd=str(BOTS_ROOT),
            creationflags=getattr(_subp, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        print(f"comment handler launch fail: {e}")
    return True


def update_user_action(action_id, step_id=None, all_done=False):
    """Päivitä step done tai merkitse kaikki done."""
    if not USER_ACTIONS.exists():
        return False
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
        return False

    if step_id:
        for s in target.get("steps", []):
            if s["step_id"] == step_id:
                s["done"] = True
                break
    if all_done or all(s.get("done") for s in target.get("steps", [])):
        target["all_steps_done"] = True
        target["completed_at"] = datetime.now(timezone.utc).isoformat()
        # Kuittaa botin alkuperäinen kysymys answered:iin
        related_qid = target.get("related_question_id")
        if related_qid:
            answered_record = {
                "id": related_qid,
                "ts": datetime.now(timezone.utc).isoformat(),
                "bot": target.get("bot"),
                "type": "user_completed",
                "question": target.get("question"),
                "status": "answered",
                "decision": "USER_ACTION_COMPLETED",
                "reason": f"Käyttäjä on suorittanut {len(target.get('steps', []))} steppiä",
                "action": target.get("action_title"),
                "resolved_at": datetime.now(timezone.utc).isoformat(),
                "resolved_by": "user_via_ui",
            }
            with open(ANSWERED_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(answered_record, ensure_ascii=False) + "\n")

    # Rewrite
    with open(USER_ACTIONS, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    return True


GOALS_DIR = BOTS_ROOT / "_shared" / "memory" / "goals_raw"
GOALS_DIR.mkdir(parents=True, exist_ok=True)
GENERAL_INPUTS = BOTS_ROOT / "_shared" / "memory" / "user_general_inputs.jsonl"

ALL_BOTS_FOR_GOALS = ["alphahunter", "finance", "videoempire", "leadgen",
                          "rd-coworker", "design", "convovault"]


def get_goals_state():
    out = {}
    for bot in ALL_BOTS_FOR_GOALS:
        raw_path = GOALS_DIR / f"{bot}_raw.txt"
        smart_path = BOTS_ROOT / bot / "GOALS.md"
        out[bot] = {
            "raw_text": raw_path.read_text(encoding="utf-8") if raw_path.exists() else "",
            "smart_exists": smart_path.exists(),
            "smart_path": str(smart_path) if smart_path.exists() else None,
            "last_raw_update": (raw_path.stat().st_mtime if raw_path.exists() else None),
        }
    return out


def save_raw_goal(bot, text):
    if bot not in ALL_BOTS_FOR_GOALS:
        return False
    raw_path = GOALS_DIR / f"{bot}_raw.txt"
    raw_path.write_text(text, encoding="utf-8")
    # Lähetä orchestratorille tieto että pitää muotoilla SMART
    inbox_q = BOTS_ROOT / "_shared" / "agent_inbox" / "queue.jsonl"
    inbox_q.parent.mkdir(parents=True, exist_ok=True)
    import uuid as _u
    record = {
        "id": "Q" + _u.uuid4().hex[:8].upper(),
        "ts": datetime.now(timezone.utc).isoformat(),
        "bot": "orchestrator-system",
        "type": "decision_request",
        "question": f"Käyttäjä syötti raakatekstin tavoitteista bot:lle {bot}. Muotoile SMART → tallenna {bot}/GOALS.md.",
        "context": f"Raw-text-polku: {raw_path}\n\nRaakateksti:\n{text[:2000]}",
        "options": [],
        "priority": "medium",
        "status": "open",
    }
    with open(inbox_q, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return True


def save_general_input(input_data):
    import uuid as _u
    thread_id = input_data.get("thread_id") or ("THR" + _u.uuid4().hex[:8].upper())
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": input_data.get("type", "comment"),
        "role": "user",
        "thread_id": thread_id,
        "target_bot": input_data.get("target_bot", ""),
        "subject": input_data.get("subject", ""),
        "text": input_data.get("text", ""),
    }
    with open(GENERAL_INPUTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Trigger välitön käsittely (taustaprosessina, ei blockaa UI:ta)
    try:
        import subprocess as _subp
        scripts_dir = BOTS_ROOT / "_shared" / "scripts"
        _subp.Popen(
            [r"C:\Users\puros\nn_env\Scripts\python.exe",
             str(scripts_dir / "message_action_handler.py"), "--max", "3"],
            cwd=str(BOTS_ROOT),
            creationflags=getattr(_subp, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        print(f"message handler launch fail: {e}")

    return {"ok": True, "thread_id": thread_id}


def get_general_inputs(limit=200):
    """Palauta viestit thread:eittäin organisoituna — uusin thread ensin."""
    if not GENERAL_INPUTS.exists():
        return []
    items = []
    with open(GENERAL_INPUTS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except Exception:
                    pass

    threads = {}
    for it in items:
        tid = it.get("thread_id") or ("legacy_" + (it.get("ts", "")[:19]))
        threads.setdefault(tid, []).append(it)

    for tid in threads:
        threads[tid].sort(key=lambda x: x.get("ts", ""))

    thread_list = []
    for tid, msgs in threads.items():
        latest_ts = msgs[-1].get("ts", "") if msgs else ""
        thread_list.append({
            "thread_id": tid,
            "latest_ts": latest_ts,
            "messages": msgs,
            "n_messages": len(msgs),
            "subject": next((m.get("subject") for m in msgs if m.get("subject")), ""),
            "target_bot": next((m.get("target_bot") for m in msgs if m.get("target_bot")), ""),
            "needs_user_reply": (msgs[-1].get("role") == "orchestrator") if msgs else False,
        })
    thread_list.sort(key=lambda t: t["latest_ts"], reverse=True)
    return thread_list[:limit]


def get_inbox():
    open_q = []
    if INBOX_QUEUE.exists():
        with open(INBOX_QUEUE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        d = json.loads(line)
                        if d.get("status") == "open":
                            open_q.append(d)
                    except Exception:
                        pass
    return {
        "open": open_q,
        "n_answered": sum(1 for _ in open(INBOX_ANSWERED, encoding="utf-8")) if INBOX_ANSWERED.exists() else 0,
        "n_escalated": sum(1 for _ in open(INBOX_ESCALATED, encoding="utf-8")) if INBOX_ESCALATED.exists() else 0,
    }


def append_decision(decision: dict):
    decision["ts"] = datetime.now(timezone.utc).isoformat()
    with open(DECISIONS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(decision, ensure_ascii=False) + "\n")


_AUTH = _load_or_create_auth()


class UIHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def log_message(self, format, *args):
        # silence default logging
        pass

    def _require_auth(self):
        """Tarkista Authorization-header. Palauttaa True jos OK, muuten lähettää 401."""
        auth_h = self.headers.get("Authorization", "")
        if check_auth(auth_h, _AUTH["user"], _AUTH["password"]):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Orchestrator UI"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Authentication required.\n")
        return False

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._require_auth():
            return
        url = urlparse(self.path)
        path = url.path
        if path == "/api/bots":
            return self._json(get_bot_status())
        if path == "/api/personas":
            return self._json(get_personas())
        if path == "/api/syntheses":
            return self._json(get_recent_syntheses())
        if path == "/api/dora":
            return self._json(get_dora_summary())
        if path == "/api/quota":
            return self._json(get_quota())
        if path == "/api/decisions":
            return self._json(get_decisions())
        if path == "/api/inbox":
            return self._json(get_inbox())
        if path == "/api/user_actions":
            return self._json(get_user_actions())
        if path == "/api/goals":
            return self._json(get_goals_state())
        if path == "/api/general_inputs":
            return self._json(get_general_inputs())
        if path == "/api/decisions_pending":
            return self._json(get_decisions_pending())
        if path == "/api/agency_metrics":
            return self._json(get_agency_metrics())
        if path == "/api/orchestrator_todo":
            return self._json(get_orchestrator_todo())
        if path == "/api/goals/smart":
            from urllib.parse import parse_qs
            qs = parse_qs(url.query)
            bot = (qs.get("bot") or [""])[0]
            goals_path = BOTS_ROOT / bot / "GOALS.md"
            if goals_path.exists():
                return self._json({"bot": bot, "content": goals_path.read_text(encoding="utf-8")})
            return self._json({"bot": bot, "content": ""})
        return super().do_GET()

    def do_POST(self):
        if not self._require_auth():
            return
        url = urlparse(self.path)
        path = url.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else ""
        try:
            data = json.loads(body) if body else {}
        except Exception:
            return self._json({"error": "invalid json"}, status=400)

        if path == "/api/decisions":
            if not data.get("type") or not data.get("text"):
                return self._json({"error": "type and text required"}, status=400)
            append_decision(data)
            return self._json({"ok": True})
        if path == "/api/user_actions/step_done":
            ok = update_user_action(data.get("action_id"),
                                       step_id=data.get("step_id"))
            return self._json({"ok": ok})
        if path == "/api/user_actions/complete":
            ok = update_user_action(data.get("action_id"), all_done=True)
            return self._json({"ok": ok})
        if path == "/api/user_actions/comment":
            ok = add_user_action_comment(
                data.get("action_id"), data.get("text", ""),
                step_id=data.get("step_id"),
            )
            return self._json({"ok": ok, "note": "orchestrator vastaa 30-90s sisällä"})
        if path == "/api/goals/save":
            ok = save_raw_goal(data.get("bot"), data.get("text", ""))
            return self._json({"ok": ok})
        if path == "/api/general_inputs":
            r = save_general_input(data)
            return self._json(r)
        if path == "/api/personas/toggle":
            ok = toggle_persona_active(data.get("bot"), data.get("slug"), data.get("active"))
            return self._json({"ok": ok})
        if path == "/api/decisions_pending/decide":
            ok = decide_pending(data.get("question_id"), data.get("decision", ""))
            return self._json({"ok": ok})
        return self._json({"error": "unknown endpoint"}, status=404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8888)
    ap.add_argument("--bind-all", action="store_true",
                       help="Bind 0.0.0.0 (mahdollistaa Cloudflare Tunnel + LAN-pääsyn)")
    args = ap.parse_args()
    if not UI_DIR.exists() or not (UI_DIR / "index.html").exists():
        print(f"!!! UI ei ole vielä rakennettu: {UI_DIR}/index.html puuttuu")
        sys.exit(1)
    bind_host = "0.0.0.0" if args.bind_all else "127.0.0.1"
    server = HTTPServer((bind_host, args.port), UIHandler)
    print(f"=" * 60)
    print(f"  ORCHESTRATOR UI — Matrix Mode")
    print(f"=" * 60)
    print(f"  Selaimessa: http://localhost:{args.port}")
    print(f"  Lopetus: Ctrl+C")
    print(f"=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nSammutetaan.")
        server.shutdown()


if __name__ == "__main__":
    main()
