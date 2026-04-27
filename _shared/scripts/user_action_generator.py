# -*- coding: utf-8 -*-
r"""user_action_generator.py — generoi step-by-step ohjeet käyttäjälle escalated kysymyksistä.

Kun orchestrator_resolver eskaloi kysymyksen käyttäjälle JA se vaatii konkreettista
toimintaa (rekisteröidy, lataa ZIP, maksa, jne), tämä skripti generoi:

  _shared/agent_inbox/user_actions.jsonl

  {"id": Q...,  bot, question, action_title, steps: [{step_id, text, done}],
   all_steps_done: false, related_question_id, created_at}

UI:n ACTION REQUIRED -sektio lukee tämän + raksittaa stepit. Kun kaikki done →
takaisinkutsu vastaa botin alkuperäiseen kysymykseen.

Tätä ajaa orchestrator_daemon yhdessä resolverin kanssa.
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

INBOX_DIR = BOTS_ROOT / "_shared" / "agent_inbox"
ESCALATED = INBOX_DIR / "escalated_to_user.jsonl"
USER_ACTIONS = INBOX_DIR / "user_actions.jsonl"


STEP_PROMPT = """Sinä OLET vain analyysi/UX-agentti joka tuottaa TEKSTIVASTAUKSEN. ÄLÄ KÄYTÄ
työkaluja (Read, Edit, Bash, Write). ÄLÄ pyydä kirjoituslupaa. Toinen Python-skripti
tallentaa askelet — sinun pitää vain palauttaa strukturoitu teksti.

Olet UX-suunnittelija. Käyttäjä on omistaja kiireinen yrittäjä jolla ei ole aikaa
selvittää itse mitä tehdä. Hänen ainoa käyttöliittymänsä on Matrix-teemainen HTML-sivu
jossa hän raksittaa boxeja.

Sun tehtävä: muunna eskaloitu kysymys 3-7 selkeäksi step-by-step-tehtäväksi joita
käyttäjä voi raksittaa tehdyiksi.

# Kysymys / tausta
Bot: {bot}
Question: {question}
Context: {context}
Decision/reason orchestratorilta (miksi käyttäjä tarvitaan): {reason}
{previous_attempt_block}

# UX-vaatimukset
- 3-7 stepiä, EI ENEMPÄÄ
- Joka step on yksittäinen, konkreettinen toiminta (max 1 lause)
- Käyttäjä voi tehdä stepit ITSEKSEEN
- Jos vaaditaan URL → sisällytä konkreettinen URL
- Jos vaaditaan polku tai ID → sisällytä konkreettinen
- Jos vaaditaan komento → kirjoita VALMIS komento (esim. "cd C:\\\\Users\\\\puros\\\\bots && python ...")
- ÄLÄ käytä "harkitse", "mieti", "päätä" — käytä toimintaverbeja: "Avaa", "Klikkaa", "Kirjoita", "Lähetä"
- Jos previous_attempt_block annettu: ÄLÄ TOISTA samoja steppejä — anna konkreettisempaa, vaihda taktiikkaa, lisää troubleshoot-step

# Action-otsikko
1 lause, 30-60 merkkiä, käskymuoto. Esim "Tilaa ChatGPT-export ja lähetä polku".
Jos re-issue: alkuun "[UUSIKSI]" prefix.

# Output (TÄSMÄLLEEN tämä rakenne, plain text, ei markdown-koodilohkoja)

ACTION_TITLE: <yhden lauseen ydin>
STEP_1: <toiminta>
STEP_2: <toiminta>
STEP_3: <toiminta>
[... STEP_N max 7]
URGENCY: low | medium | high
ESTIMATED_TIME_MIN: <0-60>

Aloita vastauksesi suoraan rivillä "ACTION_TITLE:". ÄLÄ kirjoita preamblea.
"""


def _extract_keywords(text):
    """Pieni helppo keyword-extraction kysymyksistä — palauta isot sanat."""
    if not text:
        return set()
    words = "".join(c if c.isalnum() or c.isspace() else " " for c in text.lower()).split()
    # Suodata stop-sanat ja lyhyet
    stop = {"ja", "tai", "ei", "on", "se", "joka", "että", "kun", "jos", "tämä",
            "the", "and", "for", "are", "was", "were", "will", "can", "you",
            "olet", "olen", "ollut", "tulee", "miten", "mitä", "miksi", "mikä"}
    return {w for w in words if len(w) >= 5 and w not in stop}


def find_previous_attempts(bot, question, hours=24):
    """Etsi sama bot:n viimeaikaiset (oletus 24h) all_steps_done UA:t joilla on
    semanttinen overlap nykyisen kysymyksen kanssa. Re-issue-signaali.

    Lukee sekä user_actions.jsonl (avoimet/valmiit) että user_actions_archive.jsonl
    (verifier on arkistoinut). Näin re-issue-detektio säilyy myös siivouksen jälkeen."""
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    cur_keys = _extract_keywords(question)
    if len(cur_keys) < 2:
        return []

    archive_path = INBOX_DIR / "user_actions_archive.jsonl"

    def _scan(path, is_archive=False):
        results = []
        if not path.exists():
            return results
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                # Arkisto käärii UA:n {"ua": {...}} sisään
                ua = d.get("ua") if is_archive else d
                if not isinstance(ua, dict):
                    continue
                if ua.get("bot") != bot:
                    continue
                if not ua.get("all_steps_done"):
                    continue
                ts = ua.get("completed_at") or ua.get("ts") or ""
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except Exception:
                    continue
                if t < cutoff:
                    continue
                prev_keys = _extract_keywords(ua.get("question", ""))
                overlap = cur_keys & prev_keys
                if len(overlap) >= 2:
                    ua = dict(ua)  # kopio jotta _overlap_keys ei rikkoudu
                    ua["_overlap_keys"] = sorted(overlap)
                    results.append(ua)
        return results

    out = _scan(USER_ACTIONS) + _scan(archive_path, is_archive=True)
    # Uusin ensin
    out.sort(key=lambda x: x.get("completed_at") or x.get("ts") or "", reverse=True)
    return out


def parse_steps(text):
    title = ""
    steps = []
    urgency = "medium"
    est_time = 5
    for line in text.splitlines():
        s = line.strip().lstrip("*").lstrip("_").replace("**", "").strip()
        if not s or ":" not in s:
            continue
        key, _, val = s.partition(":")
        key = key.strip().upper()
        val = val.strip()
        if key == "ACTION_TITLE":
            title = val
        elif key.startswith("STEP_"):
            try:
                idx = int(key.split("_")[1])
                steps.append({"step_id": f"s{idx}", "text": val, "done": False})
            except Exception:
                pass
        elif key == "URGENCY":
            urgency = val.lower().split()[0] if val else "medium"
        elif key == "ESTIMATED_TIME_MIN":
            try:
                est_time = int("".join(c for c in val.split()[0] if c.isdigit()))
            except Exception:
                pass
    # sortaa step_id:n mukaan
    steps.sort(key=lambda x: int(x["step_id"][1:]))
    return title, steps, urgency, est_time


def generate_for_escalated(esc_record):
    bot = esc_record.get("bot", "?")
    question = esc_record.get("question", "?")
    previous = find_previous_attempts(bot, question)

    if previous:
        prev = previous[0]
        prev_steps_text = "\n".join(
            f"  - {s.get('text','')}" for s in prev.get("steps", [])
        )
        previous_attempt_block = (
            "\n# RE-ISSUE — edellinen yritys epäonnistui\n"
            f"Sama botti ({bot}) eskaloi samoilla avainsanoilla "
            f"(yhteiset: {', '.join(prev.get('_overlap_keys', []))}). "
            f"Käyttäjä ruksitti edellisen valmiiksi {prev.get('completed_at','?')[:19]} "
            "mutta agentti pendaa silti. Aiempi action-title:\n"
            f"  → {prev.get('action_title','?')}\n"
            "Aiemmat stepit (ÄLÄ TOISTA — anna NYT konkreettisemmat):\n"
            f"{prev_steps_text}\n"
            "Mahdolliset syyt: stepit liian abstrakteja, puutteellinen URL/polku/komento, "
            "vaaditaan eri lähestymistapa. Lisää troubleshoot-step jos epäselvää.\n"
        )
    else:
        previous_attempt_block = ""

    prompt = STEP_PROMPT.format(
        bot=bot,
        question=question,
        context=esc_record.get("context", "")[:500],
        reason=esc_record.get("reason", "")[:500],
        previous_attempt_block=previous_attempt_block,
    )
    text, err, rc = call_claude(
        prompt=prompt, task_type="structured_output", timeout=90,
        model="sonnet", fallback_model="haiku",
        bot="orchestrator:user_action_gen",
    )
    if rc != 0:
        return None
    title, steps, urgency, est_time = parse_steps(text)
    if not steps:
        return None
    rec = {
        "id": "UA" + uuid.uuid4().hex[:8].upper(),
        "ts": datetime.now(timezone.utc).isoformat(),
        "related_question_id": esc_record.get("id"),
        "bot": bot,
        "question": question,
        "action_title": title,
        "steps": steps,
        "urgency": urgency,
        "estimated_time_min": est_time,
        "all_steps_done": False,
        "completed_at": None,
    }
    if previous:
        rec["previous_attempt_failed"] = True
        rec["previous_action_id"] = previous[0].get("id")
        # Re-issue → urgency aina vähintään high (käyttäjä on jo kerran panostanut)
        rec["urgency"] = "high"
    return rec


def load_existing_user_action_qids():
    """Vain ne qid:t joilla on AVOIN UA. Suljetut sallivat re-issue:n
    (jos botti eskaloi saman qid:n uudestaan, käyttäjä saa uuden paremman
    ohjeistuksen sen sijaan että vanha UA jäisi näkymättömiin)."""
    out = set()
    if USER_ACTIONS.exists():
        with open(USER_ACTIONS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                qid = d.get("related_question_id")
                if qid and not d.get("all_steps_done"):
                    out.add(qid)
    return out


def main():
    if not ESCALATED.exists():
        print("Ei eskaloitu mitään, ei tehtävää.")
        return 0

    existing = load_existing_user_action_qids()

    new_actions = []
    with open(ESCALATED, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            qid = d.get("id")
            if not qid or qid in existing:
                continue
            # Vain ne joissa decision merkkaa eskalointi → vaatii käyttäjän toimintaa
            decision = (d.get("decision") or "").upper()
            if not (decision.startswith("ESCAL") or "USER" in decision):
                continue
            print(f"[{qid}] generoidaan user-action steppit...", end="", flush=True)
            t0 = time.time()
            ua = generate_for_escalated(d)
            if ua:
                new_actions.append(ua)
                print(f" ✓ {len(ua['steps'])} stepiä ({time.time() - t0:.1f}s)")
            else:
                print(f" FAIL")

    if new_actions:
        USER_ACTIONS.parent.mkdir(parents=True, exist_ok=True)
        with open(USER_ACTIONS, "a", encoding="utf-8") as f:
            for ua in new_actions:
                f.write(json.dumps(ua, ensure_ascii=False) + "\n")
        print(f"\nLuotu {len(new_actions)} user-action-pakettia → {USER_ACTIONS}")
    else:
        print("Ei uusia user-actioneja.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
