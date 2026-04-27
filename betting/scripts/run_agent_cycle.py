# -*- coding: utf-8 -*-
"""run_agent_cycle.py — yksi täysi betting-agentin sykli.

Vaiheet:
  1. Tarkista DB initialisoitu
  2. (skipataan jos API-key puuttuu) — fetch latest odds (api_fetch.py)
  3. scan_value: identify +EV-vedot Pinnacle-CLV vs soft-books
  4. tallenna ev_bets-tauluun
  5. settle_results: päivitä tulokset jos otteluja päättynyt
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant_math import (
    devig_shin, devig_power, calculate_fractional_kelly,
    stealth_stake_rounding, calculate_edge,
)

DB = ROOT / "betting_brain.db"


def ensure_db():
    if not DB.exists():
        print("DB ei ole olemassa — ajetaan init_db.py")
        from subprocess import run
        run([sys.executable, str(ROOT / "init_db.py")], check=False)


def scan_value(bankroll: float = 1000.0, max_edge_pct: float = 10.0,
                min_edge_pct: float = 0.5) -> dict:
    """Skannaa odds_history → identify +EV → tallenna ev_bets-tauluun."""
    if not DB.exists():
        return {"error": "DB puuttuu"}

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    # Hae kaikki uniikit (match_id, outcome_type) -parit joilla on Pinnacle JA soft
    c.execute("""
        SELECT DISTINCT match_id, outcome_type FROM odds_history
        WHERE bookmaker_name = 'pinnacle' AND timestamp > datetime('now', '-1 hour')
    """)
    candidates = c.fetchall()
    if not candidates:
        conn.close()
        return {"error": "ei pinnacle-dataa viimeisen 1h sisällä"}

    saved_count = 0
    skipped = 0
    for match_id, outcome_type in candidates:
        # Hae kaikki Pinnacle-kertoimet tähän outcome_typeen (1X2 = home/draw/away)
        c.execute("""
            SELECT outcome_type, odds_value FROM odds_history
            WHERE match_id = ? AND bookmaker_name = 'pinnacle'
            ORDER BY timestamp DESC LIMIT 10
        """, (match_id,))
        pin_rows = c.fetchall()
        if len(pin_rows) < 2:
            skipped += 1
            continue
        # Ryhmittele outcome_type → uusin odds
        pin_dict = {}
        for ot, ov in pin_rows:
            if ot not in pin_dict:
                pin_dict[ot] = float(ov)
        odds_list = [pin_dict[ot] for ot in sorted(pin_dict.keys())]
        outcomes_sorted = sorted(pin_dict.keys())
        if len(odds_list) < 2:
            skipped += 1
            continue

        # Devigaa
        try:
            true_probs_shin = devig_shin(odds_list)
            true_probs_pow = devig_power(odds_list)
        except Exception as e:
            print(f"  ! devig fail {match_id}: {e}")
            continue

        # Vertaa: jos > 2pp ero per outcome → flag
        if any(abs(s - p) > 0.02 for s, p in zip(true_probs_shin, true_probs_pow)):
            print(f"  ! {match_id} devig methods divergent — skip")
            skipped += 1
            continue

        # Per outcome: hae soft-bookmakerit
        for idx, ot_target in enumerate(outcomes_sorted):
            true_p = true_probs_shin[idx]
            c.execute("""
                SELECT bookmaker_name, odds_value FROM odds_history
                WHERE match_id = ? AND outcome_type = ?
                  AND bookmaker_name != 'pinnacle'
                  AND timestamp > datetime('now', '-30 minutes')
            """, (match_id, ot_target))
            for soft_book, soft_odds in c.fetchall():
                soft_odds = float(soft_odds)
                edge_pct = calculate_edge(true_p, soft_odds)
                if edge_pct < min_edge_pct or edge_pct > max_edge_pct:
                    continue
                raw_stake = calculate_fractional_kelly(
                    true_p, soft_odds, fraction=0.25, bankroll=bankroll)
                if raw_stake < 5.0:
                    continue
                final_stake = stealth_stake_rounding(raw_stake)
                if final_stake <= 0:
                    continue
                # Tallenna ev_bets-tauluun
                c.execute("""
                    INSERT INTO ev_bets
                    (match_id, soft_book, soft_odds, pinnacle_devigged_odds,
                     edge_percentage, recommended_kelly_stake, result, timestamp,
                     outcome_type, true_prob, devig_method)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    match_id, soft_book, soft_odds, 1.0/true_p,
                    edge_pct, final_stake, None,
                    datetime.now(timezone.utc).isoformat(),
                    ot_target, true_p, "shin",
                ))
                saved_count += 1
                print(f"  + EV-bet: {match_id} {ot_target} @ {soft_book} "
                      f"odds={soft_odds:.2f} edge={edge_pct:.2f}% stake={final_stake}€")

    conn.commit()
    conn.close()
    return {"saved": saved_count, "skipped": skipped, "candidates": len(candidates)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bankroll", type=float, default=1000.0)
    ap.add_argument("--max-edge-pct", type=float, default=10.0)
    ap.add_argument("--min-edge-pct", type=float, default=0.5)
    args = ap.parse_args()

    ensure_db()
    print(f"=== betting cycle {datetime.now(timezone.utc).isoformat()} ===")
    result = scan_value(args.bankroll, args.max_edge_pct, args.min_edge_pct)
    print(f"  result: {result}")
    # Logitus
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"cycle_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    log_file.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
