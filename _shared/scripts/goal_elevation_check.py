# -*- coding: utf-8 -*-
r"""goal_elevation_check.py — kun bot:in KR/tavoite saavutetaan, nosta tasoa
1.5-3× automaattisesti. Käyttäjän mandaatti 2026-04-26: ei kattoa kasvulle.

Pipeline:
  1. Lue <bot>/GOALS.md → KR-rivit ja niiden numerolliset targetit
  2. Lue DORA / per-bot success-metrics (output_freq, conversion, jne)
  3. Per KR: jos toteuma >= target → mark achieved + Sonnet generoi seuraavan tason
  4. Kirjoita uusi GOALS.md, vanha → GOALS_archive_<ts>.md
  5. Triggers auto_queue_replenisher → uudet tehtävät uutta tavoitetta kohti

Ajo: python goal_elevation_check.py [--bot all|<slug>]
"""
import argparse
import json
import re
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

ALL_BOTS = ["alphahunter", "finance", "videoempire", "leadgen",
            "rd-coworker", "design", "convovault"]

DORA = BOTS_ROOT / "_shared" / "memory" / "dora"
ACTIONS_LOG = BOTS_ROOT / "_shared" / "memory" / "orchestrator_actions.jsonl"


PROMPT = """Sinä OLET vain analyysiagentti joka tuottaa TEKSTIVASTAUKSEN. ÄLÄ KÄYTÄ
työkaluja (Read, Edit, Bash, Write). ÄLÄ pyydä kirjoituslupaa.

Botti **{bot}** on saavuttanut KR-tason. Sinun pitää generoida UUSI haastavampi taso.

# Saavutettu KR
{achieved_kr}

# Toteuma vs. target
{actual_vs_target}

# Vaatimukset uudelle tasolle (käyttäjän mandaatti — Trump/Musk-tason kunnianhimo)
- Nosta target 1.5-3× nykyisestä (älä +10% — kasvun pitää olla merkittävä)
- Säilytä mittari sama (esim. jos KR on "output_freq", uusi taso edelleen output_freq)
- Realistinen 30-90 vrk:n haaste (saavutettavissa intensiivisellä työllä, ei mahdoton)
- Kirjoita SAMASSA SMART-formaatissa kuin alkuperäinen KR (Specific, Measurable, Achievable, Relevant, Time-bound)
- Linkki strategiseen KR-numeroon säilyy

# Vastauksen rakenne (TÄSMÄLLEEN, plain text, ei markdown-koodilohkoja)

Aloita rivillä "NEW_KR_LINE:" suoraan, älä preamblea.

NEW_KR_LINE: <yksi rivi joka korvaa vanhan KR:n GOALS.md:ssä — pidä formaatti samana>
RATIONALE: <2-3 lausetta miksi juuri tämä uusi taso — mitä saavutetaan kun täytetään>
FOLLOWUP_FOCUS: <mille alueille auto-replenish keskittyy uutta tasoa kohti>
"""


def read_goals(bot):
    p = BOTS_ROOT / bot / "GOALS.md"
    if not p.exists():
        return None, None
    text = p.read_text(encoding="utf-8")
    return p, text


def archive_goals(bot, text):
    arch = BOTS_ROOT / bot / "GOALS_archive_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + ".md"
    arch.write_text(text, encoding="utf-8")
    return arch


def latest_dora_for_bot(bot):
    """Poimi viimeisin DORA-rivi tälle botille."""
    p = DORA / "dora_metrics.jsonl"
    if not p.exists():
        return {}
    last = None
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("bot") == bot:
                last = d
    return last or {}


def parse_kr_lines(goals_text):
    """Etsi KR-rivit (sekä numerollinen target että ACHIEVED-status jos merkitty)."""
    kr_re = re.compile(
        r"^[\s\-\*]*\**KR\s*(\d+)\**\s*[:\-]?\s*(.+)$",
        re.MULTILINE | re.IGNORECASE,
    )
    result = []
    for m in kr_re.finditer(goals_text):
        num = m.group(1)
        body = m.group(2).strip()
        achieved = "ACHIEVED" in body.upper() or "✅" in body
        # Yritä napata numero target:istä — esim "100k€/kk", "5/day", "50%"
        result.append({
            "num": num,
            "raw_line": m.group(0),
            "body": body,
            "already_achieved": achieved,
        })
    return result


def kr_target_met(kr, dora_row):
    """Heuristinen: vertaa KR-rivin numeroa DORA-mittariin. Erittäin yksinkertainen
    ensimmäinen versio — myöhemmin tarkennetaan parserilla."""
    # MVP: jos DORA:ssa on output_freq tieto + KR mainitsee output/per day/per week
    body_l = kr["body"].lower()
    of = dora_row.get("output_freq")
    if of is None:
        return False, "no_dora_signal"

    # Etsi numero-haarukoita KR:stä
    nums = re.findall(r"(\d+(?:\.\d+)?)", body_l)
    if not nums:
        return False, "no_target_numbers"

    if "day" in body_l or "päiv" in body_l or "per day" in body_l:
        try:
            target = float(nums[0])
        except Exception:
            return False, "parse_fail"
        return (of >= target, f"output_freq={of:.2f} vs target={target}")

    return False, "rule_not_matched"


def elevate_kr(bot, kr, actual_vs_target_str):
    achieved_kr_text = kr["raw_line"]
    prompt = PROMPT.format(
        bot=bot,
        achieved_kr=achieved_kr_text,
        actual_vs_target=actual_vs_target_str,
    )
    text, err, rc = call_claude(
        prompt=prompt, task_type="deep_analysis", timeout=None,
        model="sonnet", fallback_model="haiku",
        bot=f"orchestrator:elevate:{bot}",
    )
    if rc != 0:
        return None, f"sonnet rc={rc}: {err[:120]}"

    new_line = None
    rationale = ""
    focus = ""
    for raw in text.splitlines():
        s = raw.strip().lstrip("*-#").lstrip("_").replace("**", "").strip()
        if s.upper().startswith("NEW_KR_LINE:"):
            new_line = s.partition(":")[2].strip()
        elif s.upper().startswith("RATIONALE:"):
            rationale = s.partition(":")[2].strip()
        elif s.upper().startswith("FOLLOWUP_FOCUS:"):
            focus = s.partition(":")[2].strip()
    if not new_line:
        return None, "no_new_kr_line"
    return {"new_line": new_line, "rationale": rationale, "focus": focus}, None


def process_bot(bot):
    p, text = read_goals(bot)
    if not text:
        return {"bot": bot, "skipped": "no_GOALS"}

    krs = parse_kr_lines(text)
    if not krs:
        return {"bot": bot, "skipped": "no_KR_lines"}

    dora = latest_dora_for_bot(bot)
    if not dora:
        return {"bot": bot, "skipped": "no_dora"}

    elevations = []
    new_text = text
    for kr in krs:
        if kr["already_achieved"]:
            continue
        met, reason = kr_target_met(kr, dora)
        if not met:
            continue

        elevation, err = elevate_kr(bot, kr, reason)
        if not elevation:
            elevations.append({"kr_num": kr["num"], "error": err})
            continue

        # Korvaa vanha KR-rivi GOALS.md:ssä
        marker = " [ACHIEVED " + datetime.now(timezone.utc).date().isoformat() + "]"
        new_text = new_text.replace(
            kr["raw_line"],
            elevation["new_line"] + marker + "  <!-- prev: " + kr["raw_line"][:120] + " -->",
        )
        elevations.append({
            "kr_num": kr["num"],
            "previous": kr["raw_line"],
            "new_line": elevation["new_line"],
            "rationale": elevation["rationale"],
            "focus": elevation["focus"],
        })

    if elevations and any("new_line" in e for e in elevations):
        # Arkistoi vanha + kirjoita uusi
        archive_goals(bot, text)
        p.write_text(new_text, encoding="utf-8")

    return {"bot": bot, "elevations": elevations, "dora": dora.get("output_freq")}


def log_action(rec):
    ACTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    rec["ts"] = datetime.now(timezone.utc).isoformat()
    rec["action"] = "goal_elevation_check"
    with open(ACTIONS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", default="all")
    args = ap.parse_args()

    bots = ALL_BOTS if args.bot == "all" else [args.bot]
    for bot in bots:
        try:
            r = process_bot(bot)
        except Exception as e:
            r = {"bot": bot, "error": str(e)[:200]}
        log_action(r)
        if r.get("elevations") and any("new_line" in e for e in r["elevations"]):
            n = sum(1 for e in r["elevations"] if "new_line" in e)
            print(f"[{bot}] ✓ {n} KR nostettu uudelle tasolle")
        elif "skipped" in r:
            pass  # hiljaa skip — yleensä ei DORA-dataa
        else:
            print(f"[{bot}] ei nostoja tällä cyclellä")
    return 0


if __name__ == "__main__":
    sys.exit(main())
