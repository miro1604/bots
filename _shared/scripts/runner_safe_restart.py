# -*- coding: utf-8 -*-
r"""runner_safe_restart.py — odota että cycle-runneri ei ole debate_running-tilassa,
sitten tapa ja restartaa se. Käytetään kun on tehty perustasoinen koodimuutos jota
ei haluta hoputtaa katkaisemalla kesken olevia tutkimuksia.

Ajo:
  python runner_safe_restart.py --bot finance --interval-min 25
  python runner_safe_restart.py --bot leadgen --interval-min 30
  python runner_safe_restart.py --bot videoempire --interval-min 30
"""
import argparse
import json
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
HEARTBEAT = BOTS_ROOT / "_shared" / "memory" / "watchdog"
PYTHON = r"C:\Users\puros\nn_env\Scripts\python.exe"

SAFE_STATES = {"cycle_done", "cycle_failed", "starting", "paused"}


def read_state(bot):
    p = HEARTBEAT / f"{bot}_heartbeat.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def find_pids(bot):
    """wmic-haku kaikille bot_cycle_runner.py-prosesseille jotka ajavat tätä bot:ia."""
    try:
        r = subprocess.run(
            ["wmic", "process", "where",
             f"name='python.exe' and CommandLine like '%bot_cycle_runner%--bot {bot}%'",
             "get", "processid", "/format:csv"],
            capture_output=True, text=True, timeout=15,
        )
        pids = []
        for line in (r.stdout or "").splitlines():
            parts = line.strip().split(",")
            if len(parts) >= 2 and parts[1].isdigit():
                pids.append(parts[1])
        return pids
    except Exception:
        return []


def kill_pids(pids):
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/PID", pid],
                           capture_output=True, timeout=10)
        except Exception:
            pass


def spawn_runner(bot, interval_min):
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    p = subprocess.Popen(
        [PYTHON, r"_shared/scripts/bot_cycle_runner.py",
         "--bot", bot, "--interval-min", str(interval_min)],
        cwd=str(BOTS_ROOT),
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        close_fds=True,
    )
    return p.pid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", required=True)
    ap.add_argument("--interval-min", type=int, default=30)
    ap.add_argument("--max-wait-min", type=int, default=120,
                    help="Max-aika odottaa nykyisen debaten valmistumista")
    args = ap.parse_args()

    bot = args.bot
    print(f"=== safe-restart: {bot} ===")
    deadline = time.time() + args.max_wait_min * 60

    while time.time() < deadline:
        st = read_state(bot)
        state = (st or {}).get("state", "?")
        print(f"  [{datetime.now(timezone.utc).isoformat()[:19]}Z] {bot} state={state}")
        if state in SAFE_STATES or st is None:
            print(f"  → state={state} on safe — restart NYT")
            break
        # debate_running tai self_improvement → odota
        time.sleep(20)
    else:
        print(f"  ! deadline ({args.max_wait_min} min) — pakkorestart")

    pids = find_pids(bot)
    print(f"  PIDs: {pids}")
    if pids:
        kill_pids(pids)
        time.sleep(2)
    new_pid = spawn_runner(bot, args.interval_min)
    print(f"  ✓ käynnistettiin uusi cycle-runner PID={new_pid} (interval={args.interval_min} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
