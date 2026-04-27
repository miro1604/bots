# GOALS — betting

_Päivitetty 2026-04-27 (käyttäjän brief: nollabudjetti + syvädata + ultraplan blueprint)_

## North Star

Rakentaa **autonominen kvantitatiivinen vedonlyöntijärjestelmä** joka tuottaa
positiivista riskikorjattua tuottoa yli 12 kk:n aikajaksolla, **nollabudjetilla**
(vain Claude-tilaus + ilmaiset OSS-työkalut + free-tier API:t).

## Kvartaalitavoite (90 vrk)

Profit-factor > 1.05, Sharpe > 1.0, **paper-trade-validointi** ennen live-rahojen
sijoittamista. Kaksi rinnakkaista tutkimuslinjaa.

## KR-tasot

### Linja A — Pinnacle CLV -pohjainen +EV-skanneri

**KR1**: 30 vrk paper-trade jossa per-veto-edge mediaani >= 1% Pinnacle-CLV-pohjaisessa
ranking:ssa. Hit rate > 55%, ROI > 0%.

**KR2**: CLV-tracking — vähintään 60% otto-kertoimista parempia kuin Pinnacle-close.
Hodges/Buchdahl: CLV+ ennakoi long-term ROI:ta.

**KR3**: Stealth-protokolla validoitu — ei limitointia 30 vrk:n aikana yhdellä
soft-bookmakerilla.

### Linja B — Syvädata + AutoML + Symbolic Regression (käyttäjän brief 2026-04-27)

**KR4 — Blueprint-vaihe**: Rakenna ultraplan:n kautta **kattava Blueprint** joka
sisältää:
- Datalähteet (statsbombpy, soccerdata, avoimet MCP) + datalake-arkkitehtuuri
- 5 valittua sarjaa (Ligue 1, Bundesliga, Serie A + 2) perusteluineen
- Pelaajan resilienssi-mallinnusstrategia (paine, tappioputket, riskinotto)
- Valmentajan psykologisen vaikutuksen mallinnus (momentum, erätauot, flow)
- featuretools-feature-engineering-suunnitelma
- FLAML AutoML-vaiheet
- PySR symbolic regression -kohteet
- LangGraph multi-agent-arkkitehtuuri (uudet expert-personat)
- Arkkitehtuuri: GNN / Transformer / RL — perustelut datan pohjalta
- **Hyväksyttävyyskriteerit**: minkä Blueprint:n vaatimusten on toteuduttava ennen koodausta

**KR5 — Pattern Discovery**: löydä **vähintään 3 selitettävää matemaattista
yhtälöä** (PySR-output) joita Pinnacle-kerroin ei vielä heijasta. Jokaisen
edge > 1.5% paper-tradessa 30 vrk.

**KR6 — Robust validation**: jokainen löydetty signaali läpäisee:
- CPCV 5-fold + PBO < 0.5
- Walk-forward 3+ syklia
- Adversarial: 5 bps slippage + 3% reject + signal-inversion-placebo
- 90 vrk paper-trade ennen live

**KR7 — Out-of-the-box -kategoria**: vähintään 2 syvädata-pohjaista signaalia
joita perinteisten urheiluvedonlyöntimallien (xG-pohjaiset) ei ole ottaneet
huomioon.

## Mittarit (UI:hin näkyväksi)

| Mittari | Lähde | Päivitystiheys |
|---|---|---|
| `paper_bets_settled` | `betting_brain.db.ev_bets` | per cycle |
| `clv_plus_rate` | live vs Pinnacle-close diff | per veto |
| `total_exposure_pct` | bankroll vs avoin position | per cycle |
| `linja_b_signals_validated` | Blueprint:n hyväksynnän jälkeen | weekly |
| `pysr_equations_discovered` | symbolic regression output | weekly |

## Don't / Kielletyt lähestymistavat

- ❌ Maksulliset data-rajapinnat (Opta, kaupallinen Wyscout)
- ❌ Maksulliset pilvipalvelut (paitsi Claude-tilaus)
- ❌ Live-rahaa ennen 30 vrk paper-tradea + KR6-validointi
- ❌ Logistinen regressio tai random forest YKSIN syvädata-mallinnuksessa
- ❌ Pinnalliset pelaaja-arvosanat jotka korreloivat joukkuemenestykseen
- ❌ Edge > 10% (kerroin-virhe / limitointi-riski)
- ❌ Valioliiga-kohdistus (liian tehokas markkina)

## Nykyinen tila

| Komponentti | Status |
|---|---|
| Linja A: `quant_math.py` (Shin/Power/Kelly/Stealth) | ✅ valmis, smoke-testattu |
| Linja A: `init_db.py` + SQLite | ✅ valmis |
| Linja A: `run_agent_cycle.py` skanneri | ✅ valmis |
| Linja A: API-key + odotetaan ensimmäistä cycle:ä | ⏳ käyttäjä asentaa |
| Linja A: 7 personaa + debate-orchestrator | ✅ valmis |
| **Linja B Blueprint** | ⏳ ULTRAPLAN-pyyntö menossa |
| Linja B: koodi/datalake | 🔒 ei aloiteta ennen Blueprint-hyväksyntää |
