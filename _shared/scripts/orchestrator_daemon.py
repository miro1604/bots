# -*- coding: utf-8 -*-
r"""orchestrator_daemon.py — taustalla pyörivä orchestrator-resolver-looppi.

Joka 15 min:
  1. Aja `agent_inbox_resolver.py --use-llm` (rule + Haiku quick-pass)
  2. Aja `orchestrator_resolver.py` (Sonnet-pohjainen pää-orchestrator)
  3. Lokeeraa run-yhteenveto

Ajo:
  python _shared/scripts/orchestrator_daemon.py [--interval-min 15]

Lopetus: Ctrl+C tai signaaliloista (graceful shutdown).
"""
import argparse
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
SCRIPTS = BOTS_ROOT / "_shared" / "scripts"
LOG = BOTS_ROOT / "_shared" / "agent_inbox" / "daemon.log"
PYTHON = r"C:\Users\puros\nn_env\Scripts\python.exe"

shutdown = False


def _sig_handler(signum, frame):
    global shutdown
    print(f"\n[daemon] Graceful shutdown pyydetty (signal={signum}).")
    shutdown = True


signal.signal(signal.SIGINT, _sig_handler)
try:
    signal.signal(signal.SIGTERM, _sig_handler)
except Exception:
    pass


def run_step(name, args_list):
    cmd = [PYTHON, str(SCRIPTS / name)] + args_list
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=600)
        return {
            "step": name, "rc": r.returncode,
            "duration_s": round(time.time() - t0, 1),
            "stdout_tail": r.stdout[-500:] if r.stdout else "",
            "stderr_tail": r.stderr[-300:] if r.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"step": name, "rc": -1, "error": "timeout"}
    except Exception as e:
        return {"step": name, "rc": -2, "error": str(e)}


def log_line(msg):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}\n"
    print(line, end="")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line)


def main():
    ap = argparse.ArgumentParser()
    # Token-budget B-leikkaus 2026-04-28: default 15→180min (oli liian usein)
    ap.add_argument("--interval-min", type=int, default=180)
    ap.add_argument("--once", action="store_true",
                     help="Aja yksi kierros + exit (cron-tilaan)")
    args = ap.parse_args()

    log_line(f"=== Orchestrator daemon käynnistyy ===")
    log_line(f"Interval: {args.interval_min} min")
    log_line(f"PID: {Path(__file__).stem} (this process)")

    while not shutdown:
        cycle_start = time.time()
        log_line("--- Cycle start ---")

        # 1. Quick auto-resolve (rule + Haiku)
        r1 = run_step("agent_inbox_resolver.py", ["--use-llm"])
        log_line(f"agent_inbox_resolver.py rc={r1.get('rc')} dur={r1.get('duration_s')}s")

        # 2. Sonnet-orchestrator pää-päätöksentekijä
        r2 = run_step("orchestrator_resolver.py", ["--max-per-run", "5"])
        log_line(f"orchestrator_resolver.py rc={r2.get('rc')} dur={r2.get('duration_s')}s")

        # 3. User-action-step-generaattori (escalated → UI:lle)
        r3 = run_step("user_action_generator.py", [])
        log_line(f"user_action_generator.py rc={r3.get('rc')} dur={r3.get('duration_s')}s")

        # 4. Quota-tracker — päivittää pause_flags + global_pause @ 90%
        r4 = run_step("quota_tracker.py", [])
        log_line(f"quota_tracker.py rc={r4.get('rc')} dur={r4.get('duration_s')}s")

        # 4b. DORA-collector — päivittää output-frequency / rejection-rate
        r4b = run_step("dora_collector.py", ["--days", "7"])
        log_line(f"dora_collector.py rc={r4b.get('rc')} dur={r4b.get('duration_s')}s")

        # 4c. Goal formatter — käyttäjän raw → SMART → GOALS.md (5 min reagointi)
        r4c = run_step("goal_formatter.py", ["--bot", "all"])
        log_line(f"goal_formatter.py rc={r4c.get('rc')} dur={r4c.get('duration_s')}s")

        # 4d. Message-action-handler — käyttäjän MESSAGE-syöttö → konkreettiset muutokset
        r4d = run_step("message_action_handler.py", ["--max", "10"])
        log_line(f"message_action_handler.py rc={r4d.get('rc')} dur={r4d.get('duration_s')}s")

        # 4e. User-action-verifier — siivoaa ruksitut UA:t kun botin re-eskaloi ei tule
        r4e = run_step("user_action_verifier.py", ["--grace-min", "5"])
        log_line(f"user_action_verifier.py rc={r4e.get('rc')} dur={r4e.get('duration_s')}s")

        # 4f. Auto-replenisher — kun jono on alle threshold (3), generoi uusia tehtäviä
        # Käyttäjän mandaatti: agentit eivät saa olla luppoajalla
        r4f = run_step("auto_queue_replenisher.py", ["--threshold", "3", "--max-new", "5"])
        log_line(f"auto_queue_replenisher.py rc={r4f.get('rc')} dur={r4f.get('duration_s')}s")

        # 5. Self-improvement-loop — paikallinen meta-tyo kun botit eivat voi LLM-kuormittaa
        # Aja kerran tunnissa (joka 12. cycle 5min-intervalilla = ~tunti)
        if not hasattr(main, '_si_counter'):
            main._si_counter = 0
        main._si_counter += 1
        if main._si_counter % 12 == 1:
            r5 = run_step("self_improvement_loop.py", ["--bot", "all"])
            log_line(f"self_improvement_loop.py rc={r5.get('rc')} dur={r5.get('duration_s')}s")

        # 6. Source quality analyzer (kerran tunnissa)
        if main._si_counter % 12 == 2:
            r6 = run_step("source_quality_analyzer.py", ["--bot", "all"])
            log_line(f"source_quality_analyzer.py rc={r6.get('rc')} dur={r6.get('duration_s')}s")

        # 7. Persona learning updater (kerran tunnissa)
        if main._si_counter % 12 == 3:
            r7 = run_step("persona_learning_updater.py", ["--bot", "all", "--n", "5"])
            log_line(f"persona_learning_updater.py rc={r7.get('rc')} dur={r7.get('duration_s')}s")

        # 8. Goal-elevation-check — PAUSED 2026-04-28 (token-budget B-leikkaus)
        # Aktivoi takaisin asettamalla ENV ENABLE_GOAL_ELEVATION=1
        import os as _os_step8
        if _os_step8.environ.get("ENABLE_GOAL_ELEVATION") == "1" and main._si_counter % 12 == 4:
            r8 = run_step("goal_elevation_check.py", ["--bot", "all"])
            log_line(f"goal_elevation_check.py rc={r8.get('rc')} dur={r8.get('duration_s')}s")

        # 9. Orchestrator-self-research — PAUSED 2026-04-28 (token-budget B-leikkaus)
        # Aktivoi takaisin asettamalla ENV ENABLE_SELF_RESEARCH=1
        if _os_step8.environ.get("ENABLE_SELF_RESEARCH") == "1" and main._si_counter % 12 == 5:
            r9 = run_step("orchestrator_self_research.py", ["--cooldown-min", "55"])
            log_line(f"orchestrator_self_research.py rc={r9.get('rc')} dur={r9.get('duration_s')}s")

        # 10. AgencyBench-light per-bot (joka cyclellä, kevyt)
        r10 = run_step("agency_metrics.py", [])
        log_line(f"agency_metrics.py rc={r10.get('rc')} dur={r10.get('duration_s')}s")

        # 11. Alphahunter idea router (cycle%3 → finance / crypto-finance)
        # Korvaa aiemman alphahunter_finance_bridge.py:n (käyttäjän triple-focus)
        r11 = run_step("alphahunter_idea_router.py", [])
        log_line(f"alphahunter_idea_router.py rc={r11.get('rc')} dur={r11.get('duration_s')}s")

        log_line(f"--- Cycle end ({round(time.time() - cycle_start, 1)}s) ---")

        if args.once:
            log_line("Once-mode → exit")
            break

        # Sleep — tarkista shutdown joka sekunti
        sleep_until = cycle_start + args.interval_min * 60
        while time.time() < sleep_until and not shutdown:
            time.sleep(1)

    log_line("=== Daemon sammutettu ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
