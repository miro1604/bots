# ⚠ ÄLÄ KYSY KÄYTTÄJÄLTÄ — käytä agent_inbox:ia

**Lue ENSIN**: `_shared/memory/agent_decision_authority.md`

Käyttäjä = omistaja (ei operatiivinen). Älä keskeytä häntä "voinko jatkaa?" -kysymyksillä.
Päätä itse valtuusrajoissa: koodi, debug, iteraatio, kirjaa learnings, ota seuraava todo.

```bash
python _shared/scripts/agent_inbox.py check_my_pending --bot crypto-finance --auto-mark-read
```

---

# crypto-finance — Kvantitatiivinen krypto-strategiabotti

**Juuri**: `C:\Users\puros\bots\crypto-finance\`

## Projektin tarkoitus

Tutki, simuloi ja kehitä **lyhyen-keskipitkän aikavälin krypto-strategioita**
(intraday → 48h hold) tavoitteena rakentaa robusti **strategia-portfolio**.

Inspiraatio: oppia ottaminen perinteiseltä `bots/finance/`-botilta, mutta sovellettuna
kryptojen erityispiirteisiin (24/7 markkina, korkea volatiliteetti, BTC-dominance,
korrelaatio risk-on/risk-off makroon).

## Käyttäjän speksit (lähde: UI MESSAGE 2026-04-26 16:16 UTC)

- **Hold-aika**: 30 min – 48 h
- **Kynttilä-resoluutiot kokeiluissa**: 1m, 10m, 30m, 45m, 60m, 90m, 120m, 240m, 4h, 8h
- **Tuottotavoitteet per treidi**:
  - Lyhyt hold (< 4 h): **vähintään 1%**
  - Pidempi hold (≥ 4 h): **vähintään 3%** (tavoite enemmänkin)
- **Strategiaperhe** (pääpaino):
  1. **Price action** — kynttilät, S/R, breakout, fakeout, range
  2. **Indeksi-priceaction-korrelaatio / epäkorrelaatio** — BTC vs alts, BTC vs ETH,
     stable-coin flows, futures basis
  3. **Cross-asset korrelaatio/epäkorrelaatio** — kryptot vs SPX/NDX/DXY/Gold/VIX
- **Mindset**: out-of-the-box, robusti walk-forward + Monte Carlo + bootstrap
- **Käyttöönotto**: cycle-runner samaan rinnalle muiden agenttien kanssa

## Persoona-lattice (alkuperäinen valikoima — rotaatio aktiivinen)

| ID | Slug | Filosofia | Hold-haarukka | Painopiste |
|---|---|---|---|---|
| C001 | `price_action_veteran` | Klassinen PA, ei indikaattoreita | 30m–8h | Kynttilämuodot, S/R, fakeoutit, kerrostumat |
| C002 | `cross_asset_correlator` | Korrelaatio/epäkorrelaatio | 1h–48h | BTC↔alts, kryptot↔SPX/DXY/Gold |
| C003 | `microstructure_specialist` | Order flow + likviditeetti | 30m–4h | Volume profile, footprint, sweep & rejection |
| C004 | `regime_detection_quant` | HMM + change-point | 2h–48h | Regime-switching, volatiilisuusrejiimit |
| C005 | `crypto_native_trader` | On-chain + sentiment | 4h–48h | BTC-dominance, alts-seasonality, funding rate |
| C006 | `robustness_skeptic` | Walk-forward, MC, bootstrap | (ei trade — quality gate) | Hylkää overfit, vaatii out-of-sample |
| C007 | `adaptive_velocity` | Momentum-burst | 30m–3h | 1-min/10-min liikkeiden jatkuvuus, RSI-divergenssi |

Persona-rotaatio: `_shared/memory/ops_persona_lattice_rotation.md`-protokollan mukainen
PPS-pohjainen bench/retire/hire.

## Strategia-pipeline

```
1. data_fetch.py        → ccxt: BTC, ETH, top-20 alts, kynttilä-OHLCV (1m..1d)
2. cross_asset_fetch.py → SPX, NDX, DXY, Gold, VIX (yfinance) + funding/OI (deribit)
3. signal_generator.py  → per-persona: lue raakadata, tuota long/short signaali
4. backtester.py        → vectorbt + walk-forward 6kk in-sample / 1kk out-of-sample
5. monte_carlo.py       → 1000 random subsample, return-distribution
6. bootstrap_validator.py → block bootstrap, varmistaa ettei strategia katoa
7. robustness_scorer.py → Sharpe, Sortino, MAR, Ulcer, profit-factor
8. portfolio_builder.py → korreloimattomien strategioiden allokaatio
9. paper_trader.py      → paper trade ennen live, vrt. backtest-ennusteita
10. live_executor.py    → (vasta validoinnin jälkeen) ccxt-API
```

## Tekninen stack

- Python 3.12 (`C:\Users\puros\nn_env\Scripts\python.exe`)
- `ccxt` (krypto-pörssidata), `vectorbt` (backtest), `hmmlearn` (regime)
- `scikit-learn`, `pandas`, `numpy`, `arch` (GARCH)
- `deap` (GA strategy-evoluutio)
- `stable-baselines3` (RL — myöhemmin kun vakaa baseline)
- Multi-agent debate Sonnet-pohjainen (sama kuin finance)

## Kansiorakenne

```
crypto-finance/
├── CLAUDE.md
├── STATE.md
├── GOALS.md
├── agents/                  # persona-prompit
│   ├── personas.jsonl
│   ├── price_action_veteran.md
│   ├── cross_asset_correlator.md
│   ├── microstructure_specialist.md
│   ├── regime_detection_quant.md
│   ├── crypto_native_trader.md
│   ├── robustness_skeptic.md
│   └── adaptive_velocity.md
├── debate/
│   ├── debate_orchestrator.py  # Sonnet-debaten ajaja, sama API kuin finance
│   └── debate_log.jsonl
├── research/
│   ├── research_queue.jsonl    # tutkittavat strategiat
│   └── learnings.jsonl
├── knowledge/
│   └── strategies.jsonl        # validoidut strategiat
├── backtest/
│   ├── results/                # csv per strategia
│   └── walk_forward/
├── assets/
│   ├── ohlcv_cache/            # ccxt-cache
│   └── cross_asset_cache/      # SPX/NDX/DXY
└── signals/
    └── live/                   # nykyiset paper/live-signaalit
```

## GOALS.md (alku — auto-elevation nostaa kun saavutetaan)

Ks. erillinen `GOALS.md` tiedosto. Lyhyesti:
- KR1: 5 robustia strategiaa joiden Sharpe > 1.5 walk-forward 60 vrk:n sisällä
- KR2: 1% / 3% per-treidi tavoite saavutettu paper-tradessa 30 vrk
- KR3: Cross-asset-korrelaatiosignaalit löytyy ja validoitu (≥3 toimivaa paria)
- KR4: Strategia-portfolio jonka korrelaatiokerroin per pari < 0.4

## Riskirajat (tärkeää!)

- **Ei live-rahaa** ennen kuin walk-forward + MC + bootstrap-validointi ok 60+ vrk
- Paper-trade ensimmäiset 30 vrk
- Per-strategia max 5% portfolio-allokaatio
- Stop-loss aina (riski = 0.5-1% per treidi)
- Käyttäjän vahvistus ennen kuin live-API:n credentials lisätään

---

Luotu: 2026-04-26 (orchestrator user-MESSAGE THR1A0292BE perusteella)
