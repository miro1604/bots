# -*- coding: utf-8 -*-
"""init_db.py — alusta betting_brain.db SQLite-tietokanta.

Taulut:
  odds_history — kaikki havaitut kertoimet aikaleimoineen
  ev_bets      — tunnistetut +EV-vedot Kelly-panoksineen ja tuloksineen
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "betting_brain.db"


def init():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS odds_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            bookmaker_name TEXT NOT NULL,
            odds_value REAL NOT NULL,
            outcome_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            implied_prob REAL,
            sport TEXT,
            league TEXT,
            home_team TEXT,
            away_team TEXT,
            commence_time TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_oh_match ON odds_history(match_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_oh_book ON odds_history(bookmaker_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_oh_ts ON odds_history(timestamp)")

    c.execute("""
        CREATE TABLE IF NOT EXISTS ev_bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            soft_book TEXT NOT NULL,
            soft_odds REAL NOT NULL,
            pinnacle_devigged_odds REAL NOT NULL,
            edge_percentage REAL NOT NULL,
            recommended_kelly_stake REAL NOT NULL,
            result TEXT,
            timestamp TEXT NOT NULL,
            outcome_type TEXT,
            sport TEXT,
            league TEXT,
            true_prob REAL,
            devig_method TEXT,
            settled_at TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_evb_match ON ev_bets(match_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_evb_result ON ev_bets(result)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_evb_ts ON ev_bets(timestamp)")

    conn.commit()
    conn.close()
    print(f"OK init_db: {DB_PATH}")


if __name__ == "__main__":
    init()
