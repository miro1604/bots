# -*- coding: utf-8 -*-
"""api_fetch.py — hae kerroindatat The-Odds-API:lta ja tallenna odds_history-tauluun.

Käyttäjän pitää asettaa env-muuttuja THE_ODDS_API_KEY tai antaa --api-key.
Free-tier: 500 req/kk. Käytä säästeliäästi (1 cycle ~ 5-10 req).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import requests
except Exception as e:
    print(f"requests-kirjasto puuttuu: pip install requests")
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "betting_brain.db"

# Lataa .env-tiedosto jos olemassa (vältetään globaalin env-muuttujan tarve)
_env_path = ROOT / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except Exception:
        # Yksinkertainen fallback ilman python-dotenv:iä
        for line in _env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
BASE_URL = "https://api.the-odds-api.com/v4"

# Pääsarjat (likviditeetti riittää)
TIER1_SPORTS = [
    "americanfootball_nfl",
    "basketball_nba",
    "icehockey_nhl",
    "baseball_mlb",
    "soccer_epl",
    "soccer_uefa_champs_league",
    "tennis_atp_singles",
]


def fetch_odds(api_key: str, sport: str, regions: str = "us,uk,eu",
                markets: str = "h2h,spreads,totals") -> list:
    url = f"{BASE_URL}/sports/{sport}/odds"
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": "decimal",
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code != 200:
            print(f"  ! {sport}: HTTP {r.status_code}: {r.text[:200]}")
            return []
        # Tallenna API-rate-limit-tieto vastauksen otsikoista
        remaining = r.headers.get("x-requests-remaining", "?")
        used = r.headers.get("x-requests-used", "?")
        print(f"  {sport}: requests remaining={remaining}, used={used}")
        return r.json()
    except Exception as e:
        print(f"  ! {sport}: error {e}")
        return []


def save_to_db(rows: list, sport: str):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    n = 0
    for ev in rows:
        match_id = ev.get("id")
        commence = ev.get("commence_time")
        home = ev.get("home_team")
        away = ev.get("away_team")
        for bk in ev.get("bookmakers", []):
            book_name = bk.get("key")
            for mkt in bk.get("markets", []):
                mkey = mkt.get("key", "")
                for outcome in mkt.get("outcomes", []):
                    name = outcome.get("name", "")
                    price = outcome.get("price")
                    if price is None:
                        continue
                    outcome_type = f"{mkey}:{name}"
                    implied = 1.0 / float(price) if price > 0 else None
                    c.execute("""
                        INSERT INTO odds_history
                        (match_id, bookmaker_name, odds_value, outcome_type,
                         timestamp, implied_prob, sport, league, home_team,
                         away_team, commence_time)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        match_id, book_name, float(price), outcome_type,
                        datetime.now(timezone.utc).isoformat(), implied,
                        sport, sport, home, away, commence,
                    ))
                    n += 1
    conn.commit()
    conn.close()
    print(f"  {sport}: tallennettu {n} riviä")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", default=os.environ.get("THE_ODDS_API_KEY", ""))
    ap.add_argument("--sports", nargs="*", default=TIER1_SPORTS)
    args = ap.parse_args()

    if not args.api_key:
        print("ERROR: THE_ODDS_API_KEY puuttuu. Aseta env-muuttuja tai käytä --api-key")
        return 1

    if not DB.exists():
        from subprocess import run
        run([sys.executable, str(ROOT / "init_db.py")], check=False)

    total = 0
    for sport in args.sports:
        rows = fetch_odds(args.api_key, sport)
        if rows:
            n = save_to_db(rows, sport)
            total += n
        time.sleep(0.5)  # rate limit-suoja
    print(f"DONE: yhteensä {total} riviä tallennettu")
    return 0


if __name__ == "__main__":
    sys.exit(main())
