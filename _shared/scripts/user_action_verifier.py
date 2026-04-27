# -*- coding: utf-8 -*-
r"""user_action_verifier.py — varmista että käyttäjän tekemä ACTION REQUIRED ratkaisi
botin alkuperäisen ongelman, ja siivoa onnistuneet pois ettei lista täyty.

Pipeline (ajetaan daemon-cycleltä):

  1. Lue user_actions.jsonl
  2. Etsi UA:t joilla all_steps_done=True JA archived ei ole vielä asetettu
  3. Per UA, tarkista verify_grace_min (oletus 5 min) — anna botille aikaa reagoida
  4. Verify-ehdot:
       a) Onko botti eskaloi saman topic:n (>=2 yhteistä keyword:iä) viime
          15 min sisällä? → "re_issued" — user_action_generator generoi uuden
          paremmilla ohjeilla → arkistoi vanha
       b) Onko answered.jsonl saanut USER_ACTION_COMPLETED-merkinnän tästä
          UA:sta + agentti on jatkanut (heartbeat tuore)? → "verified_done"
       c) Muuten: jätä avoimeksi (verify_pending), tarkista seuraavalla cyclellä
  5. Arkistoidut UA:t kirjoitetaan user_actions_archive.jsonl:iin (audit-trail)
     ja poistetaan user_actions.jsonl:stä → UI:n lista pysyy puhtaana

Ajo: python user_action_verifier.py [--grace-min 5]
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
INBOX = BOTS_ROOT / "_shared" / "agent_inbox"
USER_ACTIONS = INBOX / "user_actions.jsonl"
ARCHIVE = INBOX / "user_actions_archive.jsonl"
QUEUE = INBOX / "queue.jsonl"
ESCALATED = INBOX / "escalated_to_user.jsonl"
ANSWERED = INBOX / "answered.jsonl"
HEARTBEAT_DIR = BOTS_ROOT / "_shared" / "memory" / "heartbeats"


STOP = {"ja", "tai", "ei", "on", "se", "joka", "että", "kun", "jos", "tämä",
        "the", "and", "for", "are", "was", "were", "will", "can", "you",
        "olet", "olen", "ollut", "tulee", "miten", "mitä", "miksi", "mikä"}


def _keys(text):
    if not text:
        return set()
    words = "".join(c if c.isalnum() or c.isspace() else " " for c in text.lower()).split()
    return {w for w in words if len(w) >= 5 and w not in STOP}


def _parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _read_jsonl(path):
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


def _bot_recent_escalations(bot, cutoff_ts):
    """Lue queue + escalated, suodata bot + ts >= cutoff."""
    out = []
    for p in (QUEUE, ESCALATED):
        for d in _read_jsonl(p):
            if d.get("bot") != bot:
                continue
            if d.get("status") in ("answered", "resolved", "closed"):
                continue
            t = _parse_ts(d.get("ts"))
            if t and t >= cutoff_ts:
                out.append(d)
    return out


def _answered_for_qid(qid):
    """Etsi answered.jsonl:stä merkintä joka liittyy UA:n related_question_id:hen."""
    for d in _read_jsonl(ANSWERED):
        if d.get("id") == qid:
            return d
    return None


def _bot_heartbeat_age_min(bot):
    """Palaa botin viimeisen heartbeatin ikä minuutteina, tai None."""
    p = HEARTBEAT_DIR / f"{bot}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        ts = _parse_ts(d.get("ts"))
        if not ts:
            return None
        age_s = (datetime.now(timezone.utc) - ts).total_seconds()
        return age_s / 60.0
    except Exception:
        return None


def verify_one(ua, grace_min=5, reissue_window_min=15):
    """Palauta (status, reason): status ∈ {verify_pending, verified_done, re_issued}."""
    completed_at = _parse_ts(ua.get("completed_at"))
    if not completed_at:
        return ("verify_pending", "no completed_at")
    age_min = (datetime.now(timezone.utc) - completed_at).total_seconds() / 60.0
    if age_min < grace_min:
        return ("verify_pending", f"grace ({age_min:.1f}/{grace_min} min)")

    bot = ua.get("bot", "")
    cur_keys = _keys(ua.get("question", ""))

    # Re-issue check: onko botti eskaloi sama topic re-issue-window sisällä?
    cutoff = datetime.now(timezone.utc).replace(microsecond=0)
    cutoff = cutoff.fromtimestamp(
        cutoff.timestamp() - reissue_window_min * 60, tz=timezone.utc
    )
    # Use only escalations AFTER the UA was completed (botti reagoi)
    effective_cutoff = max(cutoff, completed_at)

    recent_esc = _bot_recent_escalations(bot, effective_cutoff)
    for esc in recent_esc:
        if esc.get("id") == ua.get("related_question_id"):
            # Sama qid yhä avoinna → action ei riittänyt → re-issue
            return ("re_issued", f"sama qid {esc.get('id')} yhä escalated")
        ek = _keys(esc.get("question", ""))
        if len(cur_keys & ek) >= 2:
            return ("re_issued",
                    f"botti eskaloi {esc.get('id','?')} samasta topic:sta "
                    f"({len(cur_keys & ek)} yhteistä keyword:iä)")

    # Answered-check: onko USER_ACTION_COMPLETED kirjautunut alkuperäiseen kysymykseen?
    qid = ua.get("related_question_id")
    if qid:
        ans = _answered_for_qid(qid)
        if ans and "USER_ACTION_COMPLETED" in (ans.get("decision", "") or "").upper():
            return ("verified_done",
                    f"answered.jsonl on USER_ACTION_COMPLETED + ei re-eskalaatiota "
                    f"({age_min:.1f} min sitten)")

    # Heartbeat-check: jos botti on tuore (<10 min) eikä eskaloi → toimii ja jatkaa
    hb_age = _bot_heartbeat_age_min(bot)
    if hb_age is not None and hb_age < 10 and not recent_esc:
        return ("verified_done",
                f"heartbeat tuore ({hb_age:.1f}min), ei re-eskalaatiota")

    # Muuten: jätä pending — daemon kokeilee uudestaan
    return ("verify_pending", f"odottaa botin signaalia ({age_min:.1f} min)")


def archive_record(ua, status, reason):
    rec = {
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "verify_status": status,
        "verify_reason": reason,
        "ua": ua,
    }
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    with open(ARCHIVE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grace-min", type=int, default=5,
                    help="Anna botille N min reagoida ennen verify:ä")
    ap.add_argument("--reissue-window-min", type=int, default=15,
                    help="Re-issue-detektion ikkunakoko")
    args = ap.parse_args()

    if not USER_ACTIONS.exists():
        print("user_actions.jsonl puuttuu — ei mitään verifyä")
        return 0

    items = _read_jsonl(USER_ACTIONS)
    keep = []
    archived_count = 0
    re_issued_count = 0
    verified_count = 0

    for ua in items:
        if not ua.get("all_steps_done"):
            keep.append(ua)
            continue
        status, reason = verify_one(ua, grace_min=args.grace_min,
                                    reissue_window_min=args.reissue_window_min)
        if status == "verified_done":
            ua["verify_status"] = "verified_done"
            ua["verify_reason"] = reason
            archive_record(ua, status, reason)
            verified_count += 1
            archived_count += 1
            print(f"[{ua.get('id')}] ✓ VERIFIED — {reason}")
            # ei keep — siivoutuu
        elif status == "re_issued":
            ua["verify_status"] = "re_issued"
            ua["verify_reason"] = reason
            archive_record(ua, status, reason)
            re_issued_count += 1
            archived_count += 1
            print(f"[{ua.get('id')}] ⟳ RE-ISSUED — {reason}")
            # user_action_generator havaitsee re-eskalaation ja generoi uuden parempaa
            # ei keep — vanha siivoutuu
        else:
            keep.append(ua)
            # Ei tulosteta verbose pending:istä joka cyclellä

    if archived_count > 0:
        # Rewrite user_actions.jsonl ilman arkistoituja
        with open(USER_ACTIONS, "w", encoding="utf-8") as f:
            for ua in keep:
                f.write(json.dumps(ua, ensure_ascii=False) + "\n")

    print(f"Verified+archived={verified_count}, re-issued={re_issued_count}, "
          f"jäljellä avoimina={len(keep)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
