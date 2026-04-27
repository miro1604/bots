# finance — Goals (SMART)

> Lähde: käyttäjän raakateksti 2026-04-25 23:47 + orchestrator-muotoilu 2026-04-26.
> **Käyttäjän eksplisiittinen kunnianhimo**: ≥ 50% vuosituotto, ilman leveragea, ilman suurta riskiä. SPY-baseline 8%/v → tämä on **6× SPY**.

## North Star

Löytää ja validoida sijoitusstrategioita joiden **risk-adjusted ylituotto vs SPY on +42pp/v** (50% vs 8%). Ilman leveragea. Strategiat voivat olla pitkän tai lyhyen aikavälin, kunhan kokonaisuudessaan portfoliotuotto tähtää 50%/v keskimäärin.

Strategioiden pitää **läpäistä robust-validointi** (out-of-sample, walk-forward, regime-tests) — ei in-sample-overfittausta.

## SMART Key Results — Q2 2026

### KR1 — Backtested ylituotto
- **Specific**: Kehitä strategia joka tuottaa **CAGR ≥ 30%** out-of-sample (2015-2024) US-osakeuniversumissa (top 500), ilman leveragea.
- **Measurable**: Sharpe ≥ 1.5, Max DD ≤ 30%, walk-forward 12+ ikkunaa, CSCV PBO < 0.5.
- **Stretch**: CAGR ≥ 50% out-of-sample.
- **Time-bound**: 2026-06-30 mennessä validi backtest + 90 vrk paper-trade aloitettu.

### KR2 — Multi-timeframe-edge-skannaus
- **Specific**: Multi-finance-debate (7 personaa) skannaa scenarioita timeframe-tasoisesti: 1d, 5d, 30d, 90d, 1y, 5y+, event.
- **Measurable**: Per-cycle synthesis tuottaa joko **STRONG_CONSENSUS** tai dissent-loki jossa vähintään 2 personaa tunnistaa edge:n vähintään 1 timeframessa.
- **Hylkäysperuste (käyttäjä vahvistanut)**: ÄLÄ hylkää jos edge löytyy missään timeframessa — drill down sen sijaan.
- **Time-bound**: rolling 30 vrk:n hit-rate.

### KR3 — Strategia-portfolio
- **Specific**: 3-5 toisistaan erilaista strategiaa joiden korrelaatio < 0.5 → diversifioitu portfolio.
- **Measurable**: Yhteenlaskettu CAGR ≥ 30% (ei pelkästään yhden strategian).
- **Time-bound**: 2026-06-30.

### KR4 — Methodology rigor (käyttäjä L081 lukittanut)
- **Specific**: Jokainen "validoitu" -merkitty strategia läpäisee 7-kohtaisen tarkistuslistan (N≥100 trades, walk-forward, CSCV PBO, stress-tests, MTM-DD, slippage 0.1-0.2%, sensitivity).
- **Measurable**: 100% läpäisy ennen "production-ready" -merkintää.
- **Time-bound**: jatkuva.

### KR5 — Self-evolution (käyttäjän mandaatti "tutki tapoja kehittää itseään")
- **Specific**: Joka 2 vk: tunnista 1 omaa toiminta-aluetta jossa metodit tai data-lähteet voi parantua.
- **Measurable**: Self-improvement-raportti viikoittain (paikallinen, ei quota-kuormaa).
- **Time-bound**: jatkuva.

### KR6 — Reverse-engineering-mindset (käyttäjän mandaatti 2026-04-26)
- **Specific**: Jokainen tutkimus alkaa target:sta (CAGR/DD/Sharpe) → reverse-engineering-pipeline (5 vaihetta `research/reverse_engineer.py`):
  1. TARGET asetus
  2. OPTIMAL PATH (perfect-foresight, mitä piti tehdä matkan varrella)
  3. WHAT MATTERED (mitkä signaalit erottivat winners-losers)
  4. FORWARD-COMPATIBLE (mitkä signaalit olivat ENNAKOITAVISSA, ei look-ahead)
  5. ROBUSTNESS (walk-forward, regime-stratified, sensitivity)
- **Measurable**: 4+ reverse-engineer-ajoa /kk → research_queue:sta otetut tehtävät
- **Käytetyt metodit**: deep learning (LSTM/PatchTST/Mamba), DSPy hypoteesi-gen, NetworkX strategy-graph, HMM regime-detection, CSCV PBO
- **Älä toista**: ei "vanha strategia uudessa scenariossa" tai "uusi strategia vanhassa scenariossa" — syvennä strategiapool luovasti
- **Time-bound**: rolling, ensimmäinen pilot RES_F_001 (target 30%/30%) Q2 alkupuolella

### KR7 — Future-proof-tarkistus (laajennettu L081 + 3 lisäkohtaa)
- **Specific**: Yksikään strategia ei merkitä production-ready ennen koko tarkistuslistan läpäisyä:
  - L081 7 kohtaa (N≥100, walk-forward, CSCV PBO, stress-tests, MTM-DD, sensitivity, LOO)
  - PLUS: reverse-engineer-target saavutettavissa proxy-signaaleilla
  - PLUS: per-regime tested separately (bull/bear/crisis/stagflation/sideways)
  - PLUS: synthetic-data adversarial scenarios survival
- **Measurable**: 100% läpäisy
- **Time-bound**: jatkuva

## Älä-tavoitteet

- **ÄLÄ** käytä leveragea (käyttäjän eksplisiittinen kielto). Cash-only.
- **ÄLÄ** lupaa "varmoja" tuottoja — kaikki ennusteet konfidenssikytkimellä
- **ÄLÄ** tyydy tulokseen joka voittaa SPY:n vain marginaalisti (< +10pp/v) — käyttäjä haluaa **6× SPY**, ei 1.2× SPY
- **ÄLÄ** in-sample-overfittaa — pakollinen out-of-sample + walk-forward
- **ÄLÄ** käynnistä live-trade-pipea ennen 90 vrk paper-trade-validointia

## Self-improvement-toimet (luppoaikana)

1. Lue `market_knowledge/learnings.jsonl` (71 oppia) → tunnista untested-hypothesis-rivit (5 kpl) ja suunnittele backtest
2. Indeksoi voittavat strategiat → analysoi yhteiset piirteet (regime, vol, sektor)
3. Tutki source_quality_protocol → onko jokin lähde tuottanut "winning_strategy" → trigger deep-dive sivustolle (käyttäjä-action)
4. Multi-asset-skannaus: missä omaisuusluokassa edge-history on vahvin?

## Mittaus & raportointi

- Pairwise multi-vs-single (`pairwise_judge.py`) viikoittain — onko 7-personan debate aidosti parempi?
- Persona-utility-heatmap kuukausittain — mikä persona painaa millekin scenario-tyyppille
- Backtest-tulokset `experiments.jsonl`:iin
- Edge-detection-kriteeri (käyttäjä L081): excess return positive AND t-stat > 2.0 AND after-cost (5 bps/side) AND max DD ≤ benchmark + 50%

## Versiot
- 2026-04-26 v1.0: SMART-muotoilu käyttäjän raakatekstistä — 50%/v tavoite lukittu, leverage kielletty.
