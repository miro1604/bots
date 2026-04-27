---
name: Analyze +EV Bets and Save
version: "1.0"
description: Skannaa kerroindatat, devigaa Pinnacle-kertoimet (Shin) ja tunnista +EV-vedot suhteessa soft-bookmakerien kertoimiin. Tallenna ehdotukset SQLite-tietokantaan stealth-pyöristyksin.
---

# Find Value Bets — toimintalogiikka

## Tavoite

Löytää **markkinoiden ylikertoimet** (+EV-vedot) vertaamalla puhdistettuja
Pinnacle CLV -todennäköisyyksiä soft-bookmakerien kertoimiin ja tallentaa
suositukset `betting_brain.db`-tietokantaan.

## Progressive Disclosure -työnkulku

Kun agentti kutsutaan etsimään ylikertoimia:

### Vaihe 1 — Datan keruu

1. Tarkista `betting_brain.db` viimeisin `odds_history`-aikaleima.
2. Jos > 5 min vanha → kutsu `scripts/api_fetch.py` joka:
   - Hakee The-Odds-API:lta kaikki aktiiviset ottelut pääsarjoissa
     (NFL, NBA, NHL, MLB, EPL, NBA, ATP).
   - Tallentaa Pinnacle, Bet365, William Hill, Unibet, jne.
     `odds_history`-tauluun.
3. Suodata vain ottelut, joiden `commence_time` on 5 min – 36 h tulevaisuudessa
   (vältä in-play ja liian myöhäiset).

### Vaihe 2 — Pinnacle-devigging (CLV-puhdistus)

4. Per ottelu, per markkina (1X2, O/U, AH):
   - Lue Pinnacle-kertoimet `odds_history`:sta.
   - Aja `quant_math.devig_shin([odds_list])` → puhdistetut todelliset todennäköisyydet.
   - Vertailubaseline: aja myös `devig_power()` ja varmista että erot < 2pp.
   - Jos eri metodit antavat radikaalisti eri tulokset → flag, älä pelaa.

### Vaihe 3 — Soft-bookmakereiden vertailu

5. Hae kaikki muut soft-vedonvälittäjien kertoimet samaan markkinaan.
6. Per (soft_book, soft_odds, outcome):
   - Laske `edge_pct = (true_prob * soft_odds - 1) * 100`.
   - Jos `edge_pct < 0.5` → ohita (ei riittävä).
   - Jos `edge_pct > 10.0` → **ehdoton hylkäys** (virhekertoimet / limitoitumisriski).
   - Jos `0.5 ≤ edge_pct ≤ 10.0` → jatka vaiheeseen 4.

### Vaihe 4 — Kelly-laskenta + Stealth-pyöristys

7. `raw_stake = quant_math.calculate_fractional_kelly(true_prob, soft_odds, fraction=0.25, bankroll=BANKROLL)`.
8. Jos `raw_stake < 5.0` → ohita (liian pieni jotta kannattaa pyöristää).
9. `final_stake = quant_math.stealth_stake_rounding(raw_stake)`.
10. Tallenna `ev_bets`-tauluun:
    ```
    {
        "match_id": ..., "soft_book": ..., "soft_odds": ...,
        "pinnacle_devigged_odds": 1/true_prob,
        "edge_percentage": edge_pct,
        "recommended_kelly_stake": final_stake,
        "true_prob": true_prob,
        "devig_method": "shin",
        "result": null,  // täytetään myöhemmin
        "timestamp": now()
    }
    ```

### Vaihe 5 — Lokitus + raportointi

11. Kirjoita `logs/cycle_<ts>.json`:
    - Skannattuja otteluita
    - Kandidaatti-vetoja (ennen filteröintiä)
    - Tallennettuja +EV-vetoja (jälkeen filteröintiä)
    - Bankroll-state, tot. exposure
12. Lähetä yhteenveto orchestratorille jos tunnistettiin > 0 +EV-vetoja.

## Edge Cases — ehdottomat säännöt

| Ehto | Toiminta |
|---|---|
| `edge_pct > 10%` | EI KOSKAAN PELATA (kerroin todennäköisesti virhe / triggers limitointi) |
| `Pinnacle puuttuu` | Skip — ei luotettavaa CLV-baselinea |
| `soft_book on banlist:ssa` | Skip — ei luotettava (esim. Bovada manuaali) |
| `match_id duplicate ev_bets` | Päivitä, älä lisää duplikaattia |
| `bankroll < 100€` | Halt — ei riittävä portfolio-divisification |
| `total_exposure > 25% bankroll` | Halt — ei lisää vetoja kunnes settled |
| `liiga ei-pääliiga` | Skip — likviditeetti liian matala |
| `commence_time < 5min` | Skip — luistoriski |
| `commence_time > 36h` | Skip — kertoimet eivät vielä konvergoi CLV:hen |

## Tietokantaintegrointi

Skill kytkeytyy `betting_brain.db`-SQLiteen joko suoraan (Python `sqlite3`)
tai MCP-palvelimen kautta jos käyttäjä on asentanut SQLite MCP:n.

## Cron / Loop

Käynnistys:
```
python scripts/run_agent_cycle.py --bankroll 1000
```

Tai 15min cron:
```
/loop 15m python scripts/run_agent_cycle.py --bankroll 1000
```
