# ⚠ ÄLÄ KYSY KÄYTTÄJÄLTÄ — käytä agent_inbox:ia

**Lue ENSIN**: `_shared/memory/agent_decision_authority.md`

---

# betting — Autonominen vedonlyöntiagentti

**Juuri**: `C:\Users\puros\bots\betting\`

## Tehtävä

Rakentaa autonominen vedonlyöntijärjestelmä joka löytää **markkinoiden ylikertoimet
(+EV)** kahdella rinnakkaisella linjalla:

1. **Linja A — Pinnacle CLV -devigging** (klassinen): vertaa Pinnacle-puhdistettuja
   todennäköisyyksiä soft-kertoimiin. (alkuperäinen toteutus jo tehty —
   `quant_math.py` + `scripts/run_agent_cycle.py`).

2. **Linja B — Syvädata + AutoML + symbolic regression** (uusi, käyttäjän brief
   2026-04-27): louhi piiloitettuja korrelaatioita event-tason datasta jonka
   markkinat eivät ole vielä hinnoitelleet.

## Ehdoton rajoite — NOLLA BUDJETTI

> Tämä projekti toteutetaan ehdottomalla nollabudjetilla. Ainoa käytössä oleva
> resurssi on tämä Claude-tilaus ja siihen kuuluva laskentakapasiteetti
> (`/ultraplan`). Et saa käyttää maksullisia data-rajapintoja (kuten Opta tai
> kaupallinen Wyscout) etkä maksullisia pilvipalveluita. Kaikki käytettävä data,
> kirjastot ja työkalut on oltava avointa lähdekoodia ja täysin ilmaisia.

Sallittu:
- The-Odds-API free-tier (500 req/kk)
- StatsBomb Open Data (statsbombpy)
- soccerdata (FBref, Understat — open scrapes)
- Avoimet MCP-palvelimet
- nn_env-pohjaiset ilmaiset Python-kirjastot

## Linja B — Syvädata-pipeline (käyttäjän brief 2026-04-27)

### Vaihe 1 — Datalake-rakennus

**Datalähteet:**
- `statsbombpy` — StatsBomb Open Data (event-tason data, xG, pressing, frame-data)
- `soccerdata` — FBref + Understat scrape-pohjainen
- Avoimet MCP-palvelimet (jos saatavilla)

**Kohde-sarjat (5 vähemmän kilpailtua eurosarjaa, EI Valioliiga):**
- Ranskan Ligue 1
- Saksan Bundesliga
- Italian Serie A
- Kaksi muuta (esim. Espanjan La Liga, Hollannin Eredivisie tai Portugalin Primeira Liga
  — agentti valitsee analyysin perusteella)

### Vaihe 1.5 — Syvyysvaatimus pelaaja- ja valmentaja-analyysissä

**Pelaajan psykologinen resilienssi** — älä mittaa pelkkiä yleisarvosanoja:
- Onnistumisprosentti edistävissä syötöissä **paineen alla**
- Käyttäytyminen **tappioputkessa** (korkea yritysmäärä heikolla onnistumisella =
  turhautuminen; onnistunut riskinotto = itsevarmuus)
- Henkilökohtainen flow-tila vs. ottelutila-flow

**Valmentajan psykologinen vaikutus:**
- Kyky kääntää negatiivinen momentum ottelun sisällä
- Erätauko-vaikutukset (mitä muuttuu 2. puoliajalla)
- Pelaaja-flown ylläpito ilman turhautumista

### Vaihe 2 — Pattern Discovery (autonominen)

**ÄLÄ tyydy logistiseen regressioon tai random forestiin.**

- `featuretools` — automaattinen feature engineering aikasarja- + relaatiodatalle.
  Generoi piirteitä joita ihmisanalyytikko ei keksisi.
- `FLAML` (tai vastaava) — AutoML, etsi parhaat malliarkkitehtuurit autonomisesti.
- `PySR` (symbolic regression) — louhi datasta **selitettäviä matemaattisia
  yhtälöitä**, joita vedonvälittäjät eivät vielä heijasta.

### Vaihe 3 — Arkkitehtuuri (vapaus + LangGraph)

Vapaus valita parhaat menetelmät:
- **GNN (Graafiset neuroverkot)** joukkuekemian mallintamiseen
- **Transformer**-arkkitehtuurit pelivirran (event sequence) analyysiin
- **Reinforcement Learning** kertoimien siirtymien hyödyntämiseen

**Orkestrointi:** `LangGraph` (tai vastaava) → moniagenttijärjestelmä joka reitittää
dataa asiantuntija-agenteille ja tekee päätökset matemaattisten mallien pohjalta.

## Työnkulku ennen koodia: ULTRAPLAN BLUEPRINT

> Koska käytämme `/ultraplan`-ominaisuutta, käyttäjä vaatii ensin **erittäin
> kattavan ja syvällisen suunnitelman (Blueprint)** arkkitehtuurista, ilmaisista
> tietolähteistä ja datan mallinnusstrategiasta. Suunnitelma kootaan **selaimen
> puolelle tarkistettavaksi** käyttäjän kommentointia varten. **Vasta kun käyttäjä
> hyväksyy suunnitelman, aloitamme syvän datan lataamisen ja koodin
> implementoinnin.**

Älä siis vielä koodaa Linja B:n datalake-pipelineä — odota Blueprint-hyväksyntä.

## Linja A — nykyinen toteutus (jatkaa rinnalla)

`scripts/run_agent_cycle.py` + `quant_math.py` jatkaa Pinnacle-CLV-pohjaista
+EV-skannausta The-Odds-API:lla kun sinulla on API-key (ks. `SETUP_INSTRUCTIONS.md`).

## Riskirajat (molemmat linjat)

- **Bankroll**: 1000 € (oletus)
- **Max edge**: 10% (yli = virhekerroin → hylätään)
- **Min edge**: 0.5% (Linja A) / TBD Linja B (Blueprintissä määriteltävä)
- **Kelly-fraktio**: 0.25 (varianssi-suoja)
- **Stealth-pyöristys**: 5/10€ tasalukuihin
- **Max exposure**: 25% bankrollista
- **Live-rahaa vasta**: 90 vrk paperitestaus + CLV-tracking + profit-factor > 1.05

## Personat (debate-orchestrator käyttää näitä)

7 personaa: pinnacle_devigger, soft_book_hunter, risk_skeptic, stealth_pattern,
liquidity_filter, edge_validator, critique_agent (ks. `agents/personas.jsonl`).

**Linja B vaatii uusia personeja** (Blueprintissä määriteltävä) — esim.
event_data_analyst, psych_resilience_modeler, flow_state_quant, gnn_team_chemist.
