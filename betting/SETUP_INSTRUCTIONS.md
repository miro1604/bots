# Betting-agentin asennus ja käyttöönotto

## 1. Riippuvuudet

```bash
# nn_env on jo käytössä — käytä sitä
/c/Users/puros/nn_env/Scripts/python.exe -m pip install \
    requests pandas numpy shin scipy
```

(SQLite on osa Python-standardikirjastoa — ei erillistä asennusta.)

Jos asennukset onnistuivat, varmista:
```bash
/c/Users/puros/nn_env/Scripts/python.exe -c "import shin, requests, pandas, numpy, scipy; print('OK')"
```

## 2. Tietokannan alustus

```bash
cd /c/Users/puros/bots/betting
/c/Users/puros/nn_env/Scripts/python.exe init_db.py
```

Pitää tulostaa: `OK init_db: ...\betting_brain.db`

## 3. Smoke-testi (quant_math)

```bash
/c/Users/puros/nn_env/Scripts/python.exe quant_math.py
```

Pitää tulostaa devig-tulokset Pinnacle-tyypilliselle 1X2-pelille
(noin: home=0.45, draw=0.27, away=0.28).

## 4. The-Odds-API (FREE-tier 500 req/kk)

Rekisteröidy: https://the-odds-api.com (ilmainen, 500 req/kk)

```bash
export THE_ODDS_API_KEY="<sinun-avain>"
# tai Windows: setx THE_ODDS_API_KEY "<sinun-avain>"
```

## 5. MCP-asennus (valinnainen, auttaa Claude Code -integroinnissa)

The-Odds-API MCP-palvelin (composio):
```bash
claude mcp add --transport http the_odds_api-composio "https://mcp.composio.dev/the_odds_api/<your-token>"
```

SQLite MCP (Claude Code voi lukea betting_brain.db:tä):
```bash
claude mcp add --transport stdio sqlite-betting "npx -y @modelcontextprotocol/server-sqlite C:/Users/puros/bots/betting/betting_brain.db"
```

## 6. Yksittäinen ajo

```bash
bash run_agent_cycle.sh
# tai pelkkä Python (skipataan API-fetch jos avain puuttuu)
/c/Users/puros/nn_env/Scripts/python.exe scripts/run_agent_cycle.py --bankroll 1000
```

## 7. Autonominen 15-min-loop Claude Codessa

```
/loop 15m bash /c/Users/puros/bots/betting/run_agent_cycle.sh
```

TAI orchestrator-daemonin kautta (suositeltu):
- bot_cycle_runner integrointi tehdään seuraavassa vaiheessa.

## 8. Riskirajat

- **Bankroll**: oletuksena 1000 €
- **Max edge**: 10% (yli tämän = virhekerroin → hylätään)
- **Min edge**: 0.5%
- **Kelly-fraktio**: 0.25 (varianssi-suoja)
- **Stealth-pyöristys**: 5/10€ tasalukuihin
- **Max exposure**: 25% bankrollista (ei lisävetoja kunnes settled)

## 9. Tietokannan tarkastelu

```bash
sqlite3 betting_brain.db
> .tables
> SELECT * FROM ev_bets WHERE result IS NULL ORDER BY timestamp DESC LIMIT 10;
```

## 10. Logi-rakenne

`logs/cycle_<ts>.json` — yksittäisen syklin tulos
`logs/api_fetch_<ts>.log` — odds-fetch-loki

---

**Huomautus**: Tämä on tutkimus-/demo-agentti. Ennen rahallista käyttöä:
1. 90 vrk paperitestaus
2. CLV-tracking (Pinnacle close vs sinun otto-kerroin)
3. Profit-factor > 1.05 stable yli 100+ veton
4. Gubbing-protokollan validointi
