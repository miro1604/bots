#!/bin/bash
# run_agent_cycle.sh — yhden täyden betting-cycle wrapper.
# Käytä sekä Linux/macOS että Windows Git Bash:ssa.

set -e
cd "$(dirname "$0")"

PYTHON="${PYTHON:-/c/Users/puros/nn_env/Scripts/python.exe}"

# 1. Initialise DB jos puuttuu
if [ ! -f betting_brain.db ]; then
  echo "[$(date)] DB puuttuu — alustetaan..."
  "$PYTHON" init_db.py
fi

# 2. Hae odds API:lta (jos API-key annettu)
if [ -n "$THE_ODDS_API_KEY" ]; then
  echo "[$(date)] Haetaan odds-data..."
  "$PYTHON" scripts/api_fetch.py
else
  echo "[$(date)] THE_ODDS_API_KEY puuttuu — skipataan API-fetch"
fi

# 3. Skannaa +EV-vedot
echo "[$(date)] Skannataan +EV-vetoja..."
"$PYTHON" scripts/run_agent_cycle.py --bankroll "${BANKROLL:-1000}"

echo "[$(date)] DONE"
