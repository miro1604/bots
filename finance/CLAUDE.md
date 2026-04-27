# ⚠ ÄLÄ KYSY KÄYTTÄJÄLTÄ — käytä agent_inbox:ia

**Lue ENSIN**: `_shared/memory/agent_decision_authority.md`

Käyttäjä = omistaja (ei operatiivinen). Älä keskeytä häntä "voinko jatkaa?" -kysymyksillä.
Päätä itse valtuusrajoissa: koodi, debug, iteraatio, kirjaa learnings, ota seuraava todo.
Jos tarvitset päätöksen yli valtuuksien:

```bash
python _shared/scripts/agent_inbox.py ask \
  --bot <name> --type decision_request \
  --question "..." --priority medium
```

Sitten **JATKA MUULLA TYÖLLÄ** — älä jää odottamaan. Orchestrator vastaa myöhemmin.
**Pending-tarkistus jokaisen tehtävä-syklin alussa** (TÄRKEÄÄ jotta agentti jatkaa kun vastaus tullut):
```bash
python _shared/scripts/agent_inbox.py check_my_pending --bot finance --auto-mark-read
```
Jos tulos sisältää `PENDING:`, lue päätökset + sovella ne TÄMÄN syklin työhön. Jos `NO_PENDING` → jatka normaalisti.
Yksittäinen kysymys: `python _shared/scripts/agent_inbox.py check_answer --id <Qxxxxxxx>`

---

# finance — Kvantitatiivinen trading-botti

**Juuri**: `C:\Users\puros\bots\finance\`

> ## 🔗 LUE ENSIN: Shared memory
> Ennen mitään työtä, lue kaikki tiedostot: `C:\Users\puros\bots\_shared\memory\` — sisältää käyttäjän pysyvät preferenssit, ekosysteemin tilan ja cross-bot-viestit. UserPromptSubmit-hook injektoi lisäksi automaattisesti tuoreimmat muista terminaaleista tulleet viestit session alkuun.

## Projektin tarkoitus

Tutki, simuloi ja kehitä kvantitatiivisia trading-strategioita useille omaisuuslajeille (US-osakkeet, crypto, commodities, FX, bonds) käyttäen historiadataa, Monte Carlo -validointia ja neural networkejä. Tavoitteena löytää robusti edge joka kestää out-of-sample-testejä.

## Kansiorakenne

```
bots/finance/
├── CLAUDE.md                   # Tämä tiedosto
├── high_cagr_v*.py             # Strategia-versiot (v1-v79+)
├── v*_out.txt                  # Simulaation output-logit
├── v*.csv                      # Tulokset
├── market_knowledge/           # KESKITETTY TIETOKANTA (lue ensin!)
│   ├── INDEX.md                # Rakenteen kuvaus
│   ├── strategies.jsonl        # STRAT_001-007+ (strategia-määritelmät)
│   ├── universes.jsonl         # UNI_001-007+ (osakeuniversumit)
│   ├── experiments.jsonl       # v1-v79+ ajo-summary
│   ├── learnings.jsonl         # L001-L048+ kontekstissa (kaikki opit)
│   ├── todos.jsonl             # TODO_001-010+ priorisoidut tehtävät
│   ├── protocols.md            # /work-protokolla yms
│   ├── domains/                # per markkinatyyppi (crypto, equities, ...)
│   ├── strategies/             # per strategiatyyppi (momentum, ...)
│   ├── edges/                  # actionable edgit (VXX_short jne)
│   ├── lessons/                # what_works, what_doesnt, meta_lessons
│   └── market_learner.py       # CLI tietokannan ylläpitoon
├── alpha_data/                 # v72 puhdas alpha-data (CAPM/sector/MF)
│   ├── pure_alpha_capm.csv
│   ├── pure_alpha_sector.csv
│   ├── pure_alpha_multifactor.csv
│   ├── rolling_beta_{60,120,252}d.csv
│   └── alpha_metadata.json
├── nn_models/                  # NN-malli-tilat ja ennusteet
│   ├── lstm_baseline_best.pt
│   ├── patchtst_best.pt
│   ├── mamba_best.pt
│   ├── v76_feature_importance.csv
│   └── ...
├── asset_cache/                # yfinance daily-hintadata (S&P500+400)
├── crypto_cache/               # Binance intraday crypto-data
├── fx_cache/                   # yfinance FX-data
└── sec_cache/                  # SEC EDGAR insider-trading -data
```

## Python-ympäristöt

- **Python 3.14** (järjestelmä): yleiset scriptit ilman NN:ää
- **`C:\Users\puros\nn_env\`** (Python 3.12 venv): CUDA 12.1 torch 2.5.1, RTX 4070
  - Käyttö: `C:/Users/puros/nn_env/Scripts/python.exe <script>.py`
  - Sisältää: torch+CUDA, numpy, pandas, yfinance, sklearn, xgboost, lightgbm

## Keskeiset scriptit

| Versio | Aihe | Konteksti |
|---|---|---|
| v1-v14 | US-osake strategian kehitys | Deep-dist + alpha_peak |
| v32/v38 | Pitkä historia + no-dist ablation | 10v median 3.16%, alpha ilman dist=0% |
| v50-v54 | Crypto intraday | Sharp_1h_neg5 +6.7%/kauppa |
| v56 | FX alpha | **EI TOIMI** (20000 params tested) |
| v57-v59 | Multi-edge ETF portfolio | VXX decay, gap-down fades |
| v62-v64 | USA robust + matrix | QQQ bull, V bull, MA death |
| v65-v67 | Monte Carlo -metodologia | Block → regime-Markov |
| v68-v70 | DD reverse engineering | Regime-filter +1.5pp, laatufilterit tuhosi |
| v71 | Database-struktuuri | JSONL-pohjainen |
| v72 | **Alpha-isolaatio** | CAPM/sector/MF 840 osaketta |
| v73-v76 | NN vertailu SPY | LSTM/PatchTST/Mamba/XGBoost |
| v77+ | NN-optimointi ja ensemble | Tulossa |

## Protokollat

### /work -komento
Jos käyttäjän viesti alkaa `/work`-merkinnällä → sisältö lisätään AUTOMAATTISESTI `market_knowledge/todos.jsonl`:iin prompti-muotoon KESKEYTTÄMÄTTÄ nykyistä työtä.

### Oppien kirjaus
Kaikki opit `learnings.jsonl`:iin **kontekstikohjaisena** (ei ehdottomina totuuksina):
```
{"id":"LXXX","context":{"strategy":"...","universe":"...","method":"..."},
 "observation":"...","confidence":"low/medium/high","user_approved":true/false/null}
```

### Uusi versio (v-numerointi)
Kaikki muutokset (strategia, parametri, metodi, universumi) → uusi versionumero + kirjaus experiments.jsonl:iin.

## Tärkeät opit (lue lessons/what_works.md ja what_doesnt_work.md)

### Strategiat jotka toimivat (kontekstissa):
- **VXX/UNG short-decay** — rakenteellinen contango
- **QQQ bull-trend-cross 120d** — N=179, 78.8% win
- **Gap-down fade ETFeissä** — universaali (IWM, XLE, EEM jne)
- **Crypto sharp_1h_neg5 + limit-5% + 72h hold** — 65% win
- **Regime-filter** (skip Bear/Crisis/Stagflation) — paras riskinhallinta

### Strategiat jotka EIVÄT toimineet:
- FX daily-alpha (vol liian pieni)
- Price-based laatufilterit SIMPLE-strategialle (tuhosi fat-tail-winners)
- Trailing stop 30% (sama syy)
- Max 1 concurrent position (ei hajautusta)
- Kansainväliset markkinat US-strategian kanssa

## Nopea yhteenveto

**Paras paikannettu edge** (reaalidatassa bull-eralla): LongBull_only + 5× vipu crypto V3 → +237% CAGR / -47% DD (v54)

**Paras robusti edge** (MC-validoitu): Regime-filter + daily MTM → CAGR +12%, DD -19.6% (v63, OOS heikkeni)

**SPY-ennustaminen NN**: LSTM +0.032 edge @ 55.8% dir-acc, XGBoost +0.028 edge @ 60.4% dir-acc (paras tehokkuus)

## TODO-lista (`market_knowledge/todos.jsonl`)

Aktiivinen lista jota orkestroija voi lukea: TODO_001 (beta-mittaus) ... TODO_010 (WSL2-asennus). Käytä `jq` tai suoraan `jsonl`-lukuna.

## Miten lukea tätä projektia

**Ennen uutta simulaatiota**:
1. Lue `market_knowledge/INDEX.md`
2. Lue `market_knowledge/lessons/what_works.md` + `what_doesnt_work.md`
3. Tarkista `todos.jsonl` priorisoiduista tehtävistä
4. Tarkista `experiments.jsonl`: onko vastaavaa jo testattu?

**Simulaation jälkeen**:
1. Lisää rivi `experiments.jsonl`:iin
2. Lisää opit `learnings.jsonl`:iin (kontekstissa)
3. Jos uusi strategia/universumi/metodi → lisää `strategies.jsonl`/`universes.jsonl`:iin

## Yhteys muihin botteihin (orchestrator)

Tämä on yksi botti `C:\Users\puros\bots\` -juuressa. Orkestroija voi:
- Lukea `market_knowledge/todos.jsonl` tehtäväksi
- Hakea opit `learnings.jsonl`:sta muille boteille
- Tarkistaa strategia-status `experiments.jsonl`:sta
- Käyttää `alpha_data/pure_alpha_capm.csv`:tä features:iksi muihin bot-tehtäviin
