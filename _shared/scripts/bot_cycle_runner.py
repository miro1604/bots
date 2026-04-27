# -*- coding: utf-8 -*-
r"""bot_cycle_runner.py — yleinen cycle-runner muille boteille (videoempire/leadgen/finance).

Kullakin botilla on oma cycle-tehtävä joka aktivoituu täällä:
  videoempire: trend-scenario → script_debate (critique mode)
  leadgen:     keyword-scenario → leadgen_debate (headline_battle)
  finance:     market-scenario → debate_orchestrator (default)

Cycle-loop:
  1. Lue inbox (check_my_pending) — sovella jos löytyy päätöksiä
  2. Tarkista quota pause (skip jos paused)
  3. Generoi scenario (per-botti)
  4. Aja debate
  5. Kirjoita synthesis
  6. Lokita: heartbeat + jsonl
  7. Sleep cycle_interval
  8. Repeat

Ajo:
  python _shared/scripts/bot_cycle_runner.py --bot videoempire --interval-min 60
  python _shared/scripts/bot_cycle_runner.py --bot leadgen --interval-min 90
  python _shared/scripts/bot_cycle_runner.py --bot finance --interval-min 30

Ei kovin älykäs scenario-generaatio — käyttää staattisia template:ja MVP:hen, voi
laajentaa myöhemmin oikealla data-syötöllä (RSS, market API yms).
"""
import argparse
import json
import random
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BOTS_ROOT = Path(__file__).resolve().parents[2]
PYTHON = r"C:\Users\puros\nn_env\Scripts\python.exe"
sys.path.insert(0, str(BOTS_ROOT / "_shared" / "scripts"))

from quota_tracker import is_bot_paused
from model_router import call_claude

shutdown = False


def _sig(signum, frame):
    global shutdown
    print(f"\n[cycle-runner] Graceful shutdown.")
    shutdown = True


signal.signal(signal.SIGINT, _sig)
try:
    signal.signal(signal.SIGTERM, _sig)
except Exception:
    pass


def write_heartbeat(bot, state, detail):
    p = BOTS_ROOT / "_shared" / "memory" / "watchdog" / f"{bot}_heartbeat.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    hb = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "state": state,
        **detail,
    }
    p.write_text(json.dumps(hb, ensure_ascii=False, indent=2), encoding="utf-8")


def check_inbox(bot):
    """Tarkista onko orchestrator vastannut aiempiin kysymyksiimme."""
    try:
        r = subprocess.run(
            [PYTHON, str(BOTS_ROOT / "_shared" / "scripts" / "agent_inbox.py"),
             "check_my_pending", "--bot", bot, "--auto-mark-read"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20,
        )
        if "PENDING:" in (r.stdout or ""):
            print(f"[{bot}] inbox päätöksiä luettu:")
            print(r.stdout[:1500])
            return r.stdout
    except Exception as e:
        print(f"[{bot}] inbox check fail: {e}")
    return None


# === SCENARIO GENERATORS ===

VIDEOEMPIRE_SCENARIOS = [
    "Niche: AI tools — uutuudet 2026 (Pluginmaker, autonomous agents)",
    "Niche: Krypto-analyysi — Bitcoin halving 2026 + ETF-virrat",
    "Niche: B2B SaaS reviews — Notion vs Linear vs ClickUp",
    "Niche: Real estate investing — REITs vs DIY ostaminen 2026",
    "Niche: Tech news — Apple Vision Pro 2 vs Meta Quest 4",
]
LEADGEN_SCENARIOS = [
    "Aurinkopaneelilaskuri — Helsingin pientalot, sähkölasku >2000e/v",
    "Maalämpö-vertailija — Tampere, omakotitaloasujat 35-65v",
    "Kattoremontti-tarjouspyyntö — Espoo, lattialaatat näyttää vuotoa",
    "Julkisivu-saneeraus — Vantaa, betonijulkisivut 70-luvulta",
    "Aurinkopaneelilaskuri kerrostaloyhtiölle — taloyhtiön hallitus tilaajana",
]
FINANCE_SCENARIOS = [
    "TSLA -22% 30 päivässä, Q4-toimitukset alle odotusten, Cybertruck-myynti hidastuu, mutta robotaxi-laaja roll-out alkaa Q2.",
    "BTC consolidoinut 95-105k 4 vk. Halving 12 vk sitten. ETF-virrat jatkuvat. Lyhyet rahoituskorot deep negative.",
    "VIX 32 (3v korkein). 10Y yield 4.8%. Fed pivots dovish. Banking sektori -8%. Gold +5%. USD -3% DXY.",
    "Small-cap value (IWN) +14% YoY vs S&P 500 +25%. Insider buying 5y high. P/E 14x vs S&P 22x.",
    "AAPL ilmoittaa 200B$ buyback-ohjelman. Q1 revenue +18%. Vision Pro 2 myynnit ylittävät odotukset.",
]


def pop_research_task(bot):
    """Lue korkein-prioriteettinen avoin research-tehtava + merkitse 'in_progress'.

    Palauttaa task-dict tai None.
    """
    rq = BOTS_ROOT / bot / "research" / "research_queue.jsonl"
    if not rq.exists():
        return None
    items = []
    with open(rq, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except Exception:
                    pass
    # Filter open + sort by priority
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    open_tasks = [t for t in items if t.get("status") == "queued"]
    if not open_tasks:
        return None
    open_tasks.sort(key=lambda t: order.get((t.get("priority") or "medium").lower(), 4))
    chosen = open_tasks[0]

    # Merkitse in_progress
    for t in items:
        if t.get("id") == chosen.get("id"):
            t["status"] = "in_progress"
            t["started_at"] = datetime.now(timezone.utc).isoformat()
    with open(rq, "w", encoding="utf-8") as f:
        for t in items:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    return chosen


def mark_research_done(bot, task_id, outcome):
    """Merkitse research-tehtava completed/failed."""
    rq = BOTS_ROOT / bot / "research" / "research_queue.jsonl"
    if not rq.exists():
        return
    items = []
    with open(rq, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    for t in items:
        if t.get("id") == task_id:
            t["status"] = "completed" if outcome == "success" else "failed"
            t["completed_at"] = datetime.now(timezone.utc).isoformat()
            t["outcome"] = outcome
    with open(rq, "w", encoding="utf-8") as f:
        for t in items:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")


def task_to_scenario(task):
    """Muunna research-tehtava scenario-tekstiksi joka voidaan syottaa debate-orchestratoriin."""
    return f"""# Tutkimustehtava: {task.get('title','?')}

Type: {task.get('type','?')}
Priority: {task.get('priority','?')}
Linked goal: {task.get('linked_goal','?')}

# Method (suuntaviivat)
{task.get('method','?')}

# Notes
{task.get('notes','')}

# Tehtava
Sovella oman botin filosofiaa ja persoonien naekemyksia tahan tutkimustehtavaan.
Anna konkreettiset askeleet ja arvio toimivuudesta.
"""


def get_scenario(bot, idx):
    """ENSISIJAINEN: research_queue:sta tehtava. Toissijainen: staattinen scenario-lista."""
    task = pop_research_task(bot)
    if task:
        print(f"[{bot}] research_queue task: {task.get('id')} - {task.get('title','')[:80]}")
        return ("research", task, task_to_scenario(task))

    if bot == "videoempire":
        return ("static", None, VIDEOEMPIRE_SCENARIOS[idx % len(VIDEOEMPIRE_SCENARIOS)])
    elif bot == "leadgen":
        return ("static", None, LEADGEN_SCENARIOS[idx % len(LEADGEN_SCENARIOS)])
    elif bot == "finance":
        return ("static", None, FINANCE_SCENARIOS[idx % len(FINANCE_SCENARIOS)])
    return ("static", None, "")


# === DEBATE INVOCATION ===

def run_debate_for_bot(bot, scenario, cycle_n):
    """Aja botin debate-orchestrator scenarion kanssa."""
    debate_paths = {
        "videoempire": BOTS_ROOT / "videoempire" / "debate" / "script_debate.py",
        "leadgen": BOTS_ROOT / "leadgen" / "debate" / "leadgen_debate.py",
        "finance": BOTS_ROOT / "finance" / "debate" / "debate_orchestrator.py",
        "rd-coworker": BOTS_ROOT / "rd-coworker" / "debate" / "coworker_debate.py",
        "design": BOTS_ROOT / "design" / "debate" / "design_debate.py",
        "crypto-finance": BOTS_ROOT / "crypto-finance" / "debate" / "debate_orchestrator.py",
        "betting": BOTS_ROOT / "betting" / "debate" / "debate_orchestrator.py",
    }
    debate_modes = {
        "videoempire": "niche_score",
        "leadgen": "headline_battle",
        "finance": None,  # default scenario
        "rd-coworker": "test_plan",
        "design": "brief",
        "crypto-finance": None,
        "betting": None,
    }
    debate_path = debate_paths.get(bot)
    if not debate_path or not debate_path.exists():
        print(f"[{bot}] debate-script ei löytynyt: {debate_path}")
        return None

    args = [PYTHON, str(debate_path), "--inline", scenario]
    mode = debate_modes.get(bot)
    if mode:
        args += ["--mode", mode]

    # EI ENNALTA MÄÄRITETTYÄ AIKARAJAA — tutkimus/debate saa kestää niin
    # pitkään kuin vaatii (käyttäjän periaate 2026-04-26). Jos prosessi todella
    # jämähtää (esim. Claude CLI deadlock), käyttäjä voi käsin tappaa sen.
    # Override mahdollinen DEBATE_TIMEOUT_SEC-env-muuttujalla (esim. CI:tä varten).
    import os as _os
    _t = _os.environ.get("DEBATE_TIMEOUT_SEC")
    debate_timeout_sec = int(_t) if (_t and _t.isdigit()) else None

    try:
        kwargs = dict(capture_output=True, text=True,
                      encoding="utf-8", errors="replace")
        if debate_timeout_sec is not None:
            kwargs["timeout"] = debate_timeout_sec
        r = subprocess.run(args, **kwargs)
        return {"rc": r.returncode, "stdout_tail": (r.stdout or "")[-1500:],
                "stderr_tail": (r.stderr or "")[-300:]}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "error": f"timeout after {debate_timeout_sec}s"}


# === ORCHESTRATOR-NEXT-STEP DECISION ===

NEXT_STEP_PROMPT = """Sinä OLET vain analyysiagentti joka tuottaa TEKSTIVASTAUKSEN. ÄLÄ KÄYTÄ
työkaluja (Read, Edit, Bash, Write). ÄLÄ pyydä kirjoituslupaa.

Olet research-orchestrator botille **{bot}**. Botti on juuri saanut valmiiksi
multi-persona-debaten/tutkimuksen aiheesta. Sinun tehtäväsi on päättää: tarvitseeko
TÄMÄ aihe vielä SYVEMPÄÄ tutkimusta, vai siirrytäänkö seuraavaan?

# Tutkittu aihe (scenario / research-task)
{scenario}

# Debaten/tutkimuksen lopputulos (synthesis tail)
{result_tail}

# Päätös

Vastaa TÄSMÄLLEEN tällä rakenteella (plain text, ÄLÄ käytä code-fenceä):

DECISION: DEEPER_RESEARCH | PROCEED_NEXT
RATIONALE: 1-3 lausetta miksi

Jos DEEPER_RESEARCH — anna konkreettinen follow-up-tehtävä:
FOLLOWUP_TITLE: <60-100 merkin kuvaava otsikko>
FOLLOWUP_METHOD: <miten lisätutkitaan — datajoukot, mallit, validaatio>
FOLLOWUP_PRIORITY: high | medium | low
FOLLOWUP_NOTES: <miksi juuri tämä syvennys on arvokas — mitä uutta saadaan>

Päätä DEEPER_RESEARCH jos JOKIN seuraavista pätee:
- synteesi paljasti lupaavan signaalin/efektin jonka validointi vaatii lisäkokeita
- personat eivät päässeet konsensukseen kriittisestä kysymyksestä
- aineistossa oli aukko jonka täyttäminen muuttaisi johtopäätöstä merkittävästi
- löytyi cross-domain-yhteys (esim. uusi data-lähde, uusi malli) jota ei ehditty soveltaa

Päätä PROCEED_NEXT jos:
- aihe on käsitelty riittävän kattavasti, lisätutkimus tuottaisi marginaalista lisäarvoa
- tulokset ovat ristiriitaisia mutta johtuvat pohjimmaisista epävarmuuksista joita
  ei saa ratkaistua syventämällä (siirretään kenttätestiin / odotetaan dataa)
- queue-priorisoinnissa on tärkeämpiä avoimia tehtäviä

Aloita vastauksesi suoraan rivillä "DECISION:". ÄLÄ kirjoita preamblea.
"""


def _parse_next_step(text):
    out = {}
    keys = {"DECISION", "RATIONALE", "FOLLOWUP_TITLE", "FOLLOWUP_METHOD",
            "FOLLOWUP_PRIORITY", "FOLLOWUP_NOTES"}
    for line in text.splitlines():
        s = line.strip().lstrip("*-#").lstrip("_").replace("**", "").strip()
        if ":" not in s:
            continue
        key_part = s.split(":", 1)[0].strip().upper()
        if key_part in keys:
            _, _, val = s.partition(":")
            out[key_part.lower()] = val.strip().strip("`").strip("\"'")
    return out


def decide_next_step(bot, scenario, debate_result, current_task):
    """Aja Sonnet-päätös: tarvitaanko follow-up-tutkimus vai seuraava tehtävä?
    Jos DEEPER_RESEARCH → kirjoita follow-up research_queue:hen ja palauta True.
    """
    sys.path.insert(0, str(BOTS_ROOT / "_shared" / "scripts"))
    from model_router import call_claude

    result_tail = (debate_result or {}).get("stdout_tail", "") or ""
    if len(result_tail) > 2500:
        result_tail = result_tail[-2500:]

    prompt = NEXT_STEP_PROMPT.format(
        bot=bot,
        scenario=(scenario or "")[:1500],
        result_tail=result_tail or "(no stdout captured)",
    )
    text, err, rc = call_claude(
        prompt=prompt, task_type="deep_analysis", timeout=None,
        model="sonnet", fallback_model="haiku",
        bot=f"{bot}:next_step_decider",
    )
    if rc != 0:
        print(f"[{bot}] next-step LLM fail rc={rc}: {(err or '')[:120]}")
        return False

    parsed = _parse_next_step(text)
    decision = (parsed.get("decision") or "").upper().split()[0] if parsed.get("decision") else ""
    rationale = parsed.get("rationale", "")
    print(f"[{bot}] next-step DECISION={decision} — {rationale[:120]}")

    if decision == "DEEPER_RESEARCH":
        title = parsed.get("followup_title", "")
        if not title:
            print(f"[{bot}] DEEPER_RESEARCH ilman followup_title → skipataan")
            return False
        rq_path = BOTS_ROOT / bot / "research" / "research_queue.jsonl"
        rq_path.parent.mkdir(parents=True, exist_ok=True)
        parent_id = (current_task or {}).get("id", "")
        new_id = f"RES_{bot[:3].upper()}_FU_{int(time.time())}"
        new_task = {
            "id": new_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "priority": (parsed.get("followup_priority") or "medium").lower().split()[0],
            "type": "followup_deep_research",
            "title": title[:200],
            "method": parsed.get("followup_method", "")[:500],
            "linked_goal": (current_task or {}).get("linked_goal", ""),
            "status": "queued",
            "notes": parsed.get("followup_notes", "")[:500],
            "parent_research_id": parent_id,
            "rationale": rationale[:300],
        }
        with open(rq_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(new_task, ensure_ascii=False) + "\n")
        print(f"[{bot}] ✓ follow-up lisätty: {new_id} ({new_task['priority']}) — {title[:80]}")
        return True

    # PROCEED_NEXT — ei mitään lisättävää, seuraava cycle ottaa queuen kärjen
    return False


# === MAIN LOOP ===

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", required=True,
                     choices=["videoempire", "leadgen", "finance",
                              "rd-coworker", "design", "crypto-finance",
                              "betting"])
    ap.add_argument("--interval-min", type=int, default=60)
    ap.add_argument("--max-cycles", type=int, default=0,
                     help="0=ikuisesti, muu=lopettaa N cyclen jälkeen")
    args = ap.parse_args()

    cycle_n = 0
    print(f"=== Bot cycle-runner käynnistyy: {args.bot}, interval={args.interval_min}min ===")
    write_heartbeat(args.bot, "starting", {"cycle_runner": True})

    while not shutdown:
        cycle_n += 1
        cycle_start = time.time()
        write_heartbeat(args.bot, "cycle_start", {"cycle": cycle_n})

        # 1. Inbox-tarkistus
        check_inbox(args.bot)

        # 2. Pause-check
        paused, reason = is_bot_paused(args.bot)
        if paused:
            print(f"[{args.bot}] cycle {cycle_n} pause: {reason}")
            write_heartbeat(args.bot, "paused", {"reason": reason})
            # Self-improvement-loop kun pause
            try:
                subprocess.run([PYTHON, str(BOTS_ROOT / "_shared" / "scripts" /
                                              "self_improvement_loop.py"),
                                  "--bot", args.bot],
                                 capture_output=True, timeout=120)
            except Exception:
                pass
            time.sleep(300)
            continue

        # 3. Scenario — ensisijainen: research_queue, toissijainen: static
        source, task, scenario = get_scenario(args.bot, cycle_n)
        print(f"\n[{args.bot}] cycle {cycle_n} [{source}]: {scenario[:100]}")

        # 4. Run debate (NO TIMEOUT — tutkimus saa kestää niin pitkään kuin vaatii)
        write_heartbeat(args.bot, "debate_running",
                         {"cycle": cycle_n, "scenario_preview": scenario[:200],
                          "research_task_id": task.get("id") if task else None,
                          "source": source})
        result = run_debate_for_bot(args.bot, scenario, cycle_n)
        if result and result.get("rc") == 0:
            print(f"[{args.bot}] debate OK")
            write_heartbeat(args.bot, "cycle_done",
                             {"cycle": cycle_n, "outcome": "success",
                              "research_task_id": task.get("id") if task else None})
            if task:
                mark_research_done(args.bot, task["id"], "success")
            # 4b. Next-step decision: orchestrator katsoo tulosta ja päättää
            #     "DEEPER_RESEARCH" (luo follow-up task) vai "PROCEED_NEXT" (siirry)
            try:
                decide_next_step(args.bot, scenario, result, task)
            except Exception as e:
                print(f"[{args.bot}] next-step decide fail: {e}")
        else:
            print(f"[{args.bot}] debate FAIL rc={result.get('rc') if result else '?'}")
            write_heartbeat(args.bot, "cycle_failed",
                             {"cycle": cycle_n, "rc": result.get("rc") if result else None,
                              "research_task_id": task.get("id") if task else None})
            if task:
                mark_research_done(args.bot, task["id"], "failed")

        if args.max_cycles and cycle_n >= args.max_cycles:
            print(f"[{args.bot}] reached max_cycles, exit")
            break

        # 5. Sleep — kayta luppoaika self-improvement-meta-tyohon (paikallinen, ei quotaa)
        elapsed = time.time() - cycle_start
        sleep_left = max(0, args.interval_min * 60 - elapsed)
        slept = 0
        last_si_run = 0  # sekuntia sitten viimeisesta self-improvement-ajosta
        SI_INTERVAL = 25 * 60  # joka 25 min cycle-sleep:n aikana

        while slept < sleep_left and not shutdown:
            # Joka 25 min: aja self_improvement_loop talle botille
            if slept - last_si_run >= SI_INTERVAL:
                try:
                    print(f"[{args.bot}] luppoaika → self_improvement_loop")
                    write_heartbeat(args.bot, "self_improvement",
                                     {"cycle": cycle_n, "elapsed_in_sleep_min": int(slept / 60)})
                    si_proc = subprocess.run(
                        [PYTHON, str(BOTS_ROOT / "_shared" / "scripts" / "self_improvement_loop.py"),
                         "--bot", args.bot],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=180,
                    )
                    last_si_run = slept

                    # Tarkista myös inbox uudelleen — ehkä on tullut uusia päätöksiä
                    check_inbox(args.bot)
                except Exception as e:
                    print(f"[{args.bot}] self-improvement fail: {e}")

            time.sleep(min(30, sleep_left - slept))
            slept += 30

    write_heartbeat(args.bot, "shutdown", {"final_cycle": cycle_n})
    return 0


if __name__ == "__main__":
    sys.exit(main())
