# /ultraplan: Alpha-peak strategy + dynamic screener -optimointi (moniulotteinen, kestävä)

## TL;DR — mitä haluan
Optimoi **regime × persona × lifecycle** -luokituksen ja sen parametrit alpha-peak-momentum-strategiassa
siten, että **EI ylioptimoida historian dataa**. Saat käyttää uusia mittareita, säätää luokituskynnyksiä,
ehdottaa regime-conditional kynnysmatriisit, ja suunnitella position-sizing-logiikan — kunhan
jokainen valinta perustuu **a priori -logiikkaan** (jota voi soveltaa myös tulevaisuuden dataan)
eikä in-sample post-hoc -optimointiin.

## Strategia-tausta
- **Signaali**: alpha_peak_20 = rolling 20d sum daily-CAPM-jäännöksistä (SPY:ä vasten); osto kun > 12% (tai 15%)
- **Hold**: 400 päivää (kiinteä, ei poistumista signaaleilla, vain ajan kuluttua myynti)
- **Universumi**: 600 osaketta (S&P 500 + growth, top alpha-coverage)
- **OOS-jakso**: 2015-01-01 → 2026-04-23 (~11.3 vuotta)
- **Kustannukset**: 30 bps round-trip
- **Pohjadata**: alpha = `pure_alpha_capm.csv` (840×6616, päivittäin), close = `v20_full_prices_close_cleaned.parquet`, vol = `v20_full_prices_volume.parquet`, regime-input = SPY/VIX picklet

## Mitä jo todennettu (älä toista — laajenna)

### 1. Crisis-regiimi tuottaa kolminkertaisesti
Kynnys 12%, OOS 2015-2026:
| regiimi | N | WR | mean | median | medAlphaPeak |
|---|---:|---:|---:|---:|---:|
| calm | 2137 | 59.8% | +15.2% | +7.4% | 13.17% |
| **crisis** | **972** | **79.8%** | **+45.2%** | **+31.7%** | 13.57% |
| normal | 727 | 63.8% | +18.5% | +10.8% | 13.26% |

→ Aiempi "crisis × 0.5 size" -hypoteesi oli VÄÄRIN. Crisis on PARAS ja kannattaa allokoida **enemmän**, ei vähemmän.

### 2. Triple-class top-luokat (kynnys 12%)
- **crisis × balanced × declining** N=15 WR **93.3%** mean **+103.7%** med **+84.8%** (kapitulaatio-osto klassikko)
- **crisis × balanced × mature** N=125 WR **90.4%** mean **+48.8%** med **+44.3%**
- **crisis × balanced × transition** N=138 WR 84.8% mean **+62.4%** med **+51.0%**
- **crisis × reactive × mature** N=59 WR **88.1%** mean +40.4% med +35.0%
- **crisis × reactive × declining** N=28 WR **85.7%** mean **+71.0%** med +43.0% (huomaa: aiempi "skip declining" -sääntö hylkäsi nämä!)

### 3. Heikoimmat luokat
- **calm × reactive × growth** N=149 WR **49.7%** med **-1.4%** (klassinen NVDA/TSLA bull-trap)
- **calm × balanced × declining** N=50 WR 48.0% med -1.6%
- **calm × reserved × declining** N=27 WR 48.1% med -10.1%
- **normal × reserved × mature** N=26 med -1.5%

### 4. Aineksia jotka aidosti ennustavat 400d-tuottoa (Pearson 2877 trades, alpha 15%)
| Mittari | Pearson | Tulkinta |
|---|---:|---|
| **atr_pct** (20d realisoitu vol) | **+0.321** | korkeampi vol → parempi tuotto (vastoin "low-vol-anomaliaa") |
| **dist_from_252d_high** | **-0.225** | mitä KAUEMPANA 1y-huipusta → parempi (kapitulaatio-edge) |
| **spy_60d** | -0.184 | jos SPY on jo noussut paljon 60d → odota |
| **mom_60d** | -0.179 | osakkeen 60d-tuotto NEGATIIVISESTI korreloitu (reversal-luonne) |
| alpha_peak_at_entry | +0.066 | itse signaalin vahvuus EI eroa luokkien välillä — kynnys 12-15% riittää |

### 5. Mom_60d-kvintiili: oston ajoituksen tärkein mittari
| 60d-momentum kvintiili | N | mean tuotto | WR |
|---|---:|---:|---:|
| Q1 (alle ~-10%) | 570 | **+51.4%** | **78.2%** |
| Q2 | 570 | +23.9% | 68.9% |
| Q3 | 569 | +11.7% | 59.4% |
| Q4 | 570 | +18.2% | 63.2% |
| Q5 (yli ~+30%) | 570 | +24.1% | 66.0% |

→ Alpha-peak toimii parhaiten **kun osake on ehtinyt laskea 60d ENNEN signaalia** = reversal/contrarian-luonne, ei jatkuva-momentum.

### 6. 2026-blueprint validointi (ALPHA 15% OOS 2015-2026, sama strategia)
- PSR = 1.00, DSR (n_trials=10) = 1.00
- N=2877 ≫ MinTRL=15
- CPCV 15 fold: kaikilla PSR > 80%
- Path-dependency Max-DD bootstrap: alpha_path_dependent = False
- Skew = +4.42, kurt = +39.7 (paksut OIKEA-hännät: NVDA/SMCI/STRL +500% kaupat)
- Annualisoitu Sharpe (avg_hold=400d): **1.37**
- **Edge VALIDOITU** ilman filteriä; filteri ei lisää tilastollista arvoa

## TAVOITE — mitä optimoitavaksi

### A) Regime-conditional persona/lifecycle -kynnykset
**Hypoteesi**: osakkeen "luonne" on suhteellinen markkinatilaan — IVOL 0.40 calmissa = "reactive", mutta crisisissä se voi olla "balanced" koska crisis-vol on yleisesti korkea. Persona-kynnykset (IVOL, jump-asym, Hurst, vol_CV, sektor-corr) ja lifecycle-kynnykset (CAGR, vol, DD) PITÄISI olla regime-spesifisiä. Suosittelen taulukkoa:

| persona-mittari | calm-kynnys | normal-kynnys | crisis-kynnys |
|---|---|---|---|
| IVOL (max balanced) | 0.30 | 0.40 | 0.55 |
| Vol_CV (max non-herd) | 0.20 | 0.27 | 0.35 |
| Hurst (min non-reserved) | 0.45 | 0.43 | 0.40 |

| lifecycle-mittari | calm | normal | crisis |
|---|---|---|---|
| CAGR_3y "growth"-rajaksi | 0.25 | 0.20 | 0.10 (crisis sisältää -40% pohjat) |
| vol_3y "growth"-rajaksi | 0.30 | 0.35 | 0.45 |

→ Aja walk-forward, vahvista että regime-conditional kynnykset PARANTAVAT mean tuottoa & WR vs kiinteät kynnykset.

### B) Score-pohjainen position-sizing (per signaali)
Sen sijaan että filteri sanoo binary "salli/skip", anna jokaiselle signaalille 0–3.0 skoori. Esimerkki a-priori-painotuksia tutkittavaksi:

```
score =
  + 1.5  jos regime = crisis
  + 1.0  jos lifecycle ∈ {mature, transition} JA persona ∈ {balanced, reactive}
  + 1.0  jos mom_60d kvintiili = Q1 (osake jo laskenut)
  + 0.7  jos atr_pct > universumin mediaani
  + 0.5  jos dist_from_252d_high < -25%
  - 0.7  jos calm × reactive × growth (bull-trap-luokka)
  - 0.5  jos persona = herd (paitsi pieni N)
  - 0.5  jos viime murros < 60d
position_size = clip(score, 0.5, 3.0) × base_size
```

→ Optimoi näiden painojen jakauma SITEN ETTÄ:
1. CPCV-fold:ssa min PSR > 0.80
2. **Worst-decile-tuotto** (10% pahimpien fold-osuus) > 0
3. Mean-tuotto suurempi kuin nykyisellä baseline-strategialla
4. Säännöt itsessään ovat YLEISTETTÄVISSÄ tulevaan dataan (ei "if year == 2020 add 0.5")

### C) Lisämittarit testattavaksi (a priori-järkevyys)
- **VIX_term_structure** (VIX/VXV) — backwardation = stress-signaali
- **sektorin co-movement** — onko osake noussut SEKTORIN kanssa vai sektorista huolimatta
- **kalpa_drawdown_lähihistoria** — onko osake juuri palautumassa pohjasta
- **earnings-päivän etäisyys** — onko 400d hold:n aikana 4-6 earnings-tapahtumaa (varianssi-injektio)
- **insider buying** (sec_cache:sta jos saatavilla)
- **alpha_peak_persistence** — onko alpha-peak tullut heti vai onko osake ollut alpha-positiivinen 5-10 päivää ennen huippua
- **rolling 60d-skewness** osakkeen tuotoissa (positiivinen → upside-fat-tail)

### D) Hold-pituuden adaptiivisuus
Kiinteä 400d on yksinkertainen mutta voiko olla parempi:
- AEDL (`aedl_dynamic_horizon` quant_validation_v2:ssa): hold = 400 × (σ_max/σ_t) — korkea vol → lyhyempi
- Regime-conditional hold: crisisissä 250d (nopea käännös), calmissa 400d
- Per-persona: reactive → 200d (nopea momentum), balanced → 500d (compounder)

## RAJAT — mitä EI saa tehdä

1. **Ei post-hoc-optimointia OOS-datalla**. Esimerkki: ÄLÄ etsi parametrejä jotka antavat parhaan PSR:n koko 2015-2026 jaksolla. Sen sijaan käytä walk-forward-rakennetta (esim. 3 OOS-segmenttiä: 2015-2017, 2018-2021, 2022-2026) ja vaadi että parametrit ovat _järkeviä_ jokaisessa segmentissä.

2. **Ei "mystisiä" sääntöjä jotka eivät yleisty**. Esim. "if VIX > 30 AND month == March" on red flag — se on saattanut sopia 2020 mutta ei 2025.

3. **Ei luokituksen kynnyksiä joiden lähde on PnL-katselu**. Sen sijaan: lähde on tilastollinen ominaisuus (esim. "85% percentile of IVOL within regime") tai teoreettinen logiikka (esim. "Hurst < 0.5 = mean-reverting → ei sovi momentum-strategiaan").

4. **2026-blueprint validointi PAKOLLINEN**: PSR > 0.95, DSR > 0.95 (n_trials = oma tutkimusgrid), CPCV min-fold-PSR > 0.80, path-dependency = False, MinTRL ≪ N. Käytä `quant_validation_v2.py`.

5. **Älä rakennu 1 mittarin varaan**. Kustomointi pitää olla moniulotteinen — vähintään 3 mittaria päätösketjussa.

## DATAN POLUT (VIITTAUS — älä siirrä)

### Pohjadata (alkuperäisissä paikoissa, lue suoraan)
- `C:/Users/puros/bots/finance/alpha_data/pure_alpha_capm.csv` — 840 osakkeen päivittäinen CAPM-jäännös 2000-2026 (signaalin lähde)
- `C:/Users/puros/bots/finance/alpha_data/pure_alpha_sector.csv` — sektor-residual vaihtoehtoinen alpha
- `C:/Users/puros/bots/finance/alpha_data/pure_alpha_multifactor.csv` — multi-factor residual
- `C:/Users/puros/bots/finance/alpha_data/rolling_beta_{60,120,252}d.csv` — rolling-beta per ticker
- `C:/Users/puros/bots/finance/data_for_ultraplan/v20_full_prices_close_cleaned.parquet` — 703 osakkeen close 2014-2026 (Spike-handlerilla siivottu)
- `C:/Users/puros/bots/finance/data_for_ultraplan/v20_full_prices_volume.parquet` — volyymi
- `C:/Users/puros/bots/finance/data_for_ultraplan/v20_full_prices_low.parquet` — low (intraday)
- `C:/Users/puros/bots/finance/asset_cache/_GSPC.pkl` — SPY 2005-2026
- `C:/Users/puros/bots/finance/asset_cache/_VIX.pkl` — VIX 2005-2026
- `C:/Users/puros/bots/finance/asset_cache/<ticker>.pkl` — 60 muuta ETF/futuuria

### Tämän ajojen tulokset (results/-kansiossa)
- `trades_alpha15_unfiltered.jsonl` — 2877 kaupan rivit kynnyksellä 15%, OOS 2015-2026
- `trades_alpha15_filtered.jsonl` — 907 (vanhalla a-priori-filterillä, vertailu)
- `trades_alpha15_enriched.csv` — 2849 kauppaa lisätyillä mittareilla (mom_60d, mom_252d, dist_from_252d_high, vol_ratio, atr_pct, spy_60d, alpha_peak_at_entry)
- `trades_alpha12_unfiltered.jsonl` — 3836 kaupan rivit kynnyksellä 12% (isompi otos)
- `class_table_alpha15.csv` + `.json` — luokkataulukko 62 luokkaa kynnys 15
- `class_table_alpha12.csv` + `.json` — luokkataulukko kynnys 12
- `class_analysis_alpha15.json` — pearson + spearman korrelaatiot, kvintiili-analyysit
- `comparison_filter_vs_unfiltered.json` — vanhan filterin vs no-filterin vertailu
- `full_validation_2026_blueprint.json` — PSR/DSR/CPCV/path-dependency

### Käytettävissä olevat skriptit (scripts/-kansiossa)
- `dynamic_screener.py` — RegimeDetector + PersonalityProfiler + LifecycleClassifier + BreakDetector + StrategyRouter (5 modulia, riippuvuusvapaa, sklearn.mixture HMM:n proxynä)
- `quant_validation_v2.py` — 2026-blueprint-kirjasto: PSR, DSR, MinTRL, CPCV, bootstrap_path_dependency, AEDL, vix_filter, morris_screening, validate_strategy
- `feature_portfolio.py` — knowledge-portfolio: record_observation, record_per_ticker_decomposition, record_correlation_matrix, record_pair_relationship, record_regime_conditional, record_multi_feature_interaction, record_nonlinear_threshold, record_sequence_pattern (kasvava tieto-DB)
- `adaptive_backtest_router.py` — frequency-adaptive validointi (per-tier mandatory methods)
- `alpha_peak_filtered_strategy.py` — backtest-runner walk-forward + optionaalinen filteri
- `alpha_peak_class_analysis.py` — predictor-correlation + kvintiili-analyysi
- `alpha_peak_class_table.py` — täysi luokkataulukko per (regime × persona × lifecycle)
- `alpha_peak12_full_run.py` — sama 12%-kynnyksellä
- `alpha_peak_full_validation.py` — 2026-blueprint-validointi olemassa olevalle trade-listalle

## VAADITUT TULOSTEET (mitä haluan ultraplanilta takaisin)

### 1. Optimoitu screener-konfiguraatio (kestävyysperiaatetta noudattava)
- Regime-conditional persona/lifecycle -kynnysmatriisi (selkeät rivit ja sarakkeet, perustelut a priori)
- Score-pohjainen position-sizing-formula (lopullinen, validoitu)
- Mahdolliset uudet mittarit jotka osoittautuivat hyödyllisiksi (perustelut + Pearson/Spearman + kvintiilianalyysi)

### 2. Vertailu nykyisiin tuloksiin
| Variantti | N | Mean | WR | Sharpe ann. | PSR | DSR | CPCV min PSR | Worst-fold-mean |
|---|---|---|---|---|---|---|---|---|
| Baseline unfiltered (15%) | 2877 | +26.2% | 67.3% | 1.37 | 1.00 | 1.00 | 1.00 | ? |
| Baseline unfiltered (12%) | 3836 | +22.5% | 67.0% | ? | ? | ? | ? | ? |
| **Sinun optimoitu** | ? | ? | ? | ? | ? | ? | ? | ? |

### 3. Walk-forward 3-segmentti
2015-2017 / 2018-2021 / 2022-2026 — vaatimus: jokaisessa segmentissä optimoitu variantti voittaa baseline.

### 4. 2026-blueprint -kelpoisuus
- PSR > 0.95
- DSR > 0.95 (n_trials = sinun tutkimusgridin koko, raportoi se rehellisesti)
- CPCV: min-fold-PSR > 0.80
- Path-dependency Max-DD bootstrap: alpha_path_dependent = False

### 5. Implementoitava skripti
- `dynamic_screener_v2.py` joka päivittää nykyiset luokituslogikkat (vertaa vs nykyinen `dynamic_screener.py`)
- Yksikkötestit per komponentti (mock OHLCV-datalla — ei pelkkä hyvä OOS-tulos riitä)

### 6. Top-luokkien tutkimus (mitä yrityksiä tuottavat parhaiten?)
Tutki ne 5-10 yritystä per parhaasta luokasta jotka ajautuivat alpha-peak-signaaliin. Onko niissä yhteistä:
- toimialaa?
- pääoman rakennetta?
- kasvuvaiheen erityispiirrettä?
- ajankohdan markkinakontekstia?

Tämä auttaa ymmärtämään MIKSI luokka tuottaa hyvin, ei pelkästään ETTÄ se tuottaa.

## VINKKEJÄ (eivät käskyjä)
- `feature_portfolio.jsonl` (`C:/Users/puros/bots/_shared/knowledge/feature_portfolio.jsonl`) sisältää 5 961 aiempaa havaintoa kvant-tutkimuksesta — saatat löytää sieltä vihjeitä mihin suuntaan painot kannattaa asettaa
- `_shared/memory/` sisältää mandaatti-muistiot (mitä käyttäjä on opettanut). Erityisesti `ops_optimistic_realistic_knowledge_portfolio.md` (jokaisesta simulaatiosta kirjattava knowledge), `ops_no_pessimistic_ceiling.md` (älä leikkaa 30%+ CAGR pois ylärajalla)
- Aikadimensio on tärkeä: jokaisesta optimoidusta säännöstä — milloin se aktivoituu, mille jaksolle pätee, mitkä osakkeet osallistuivat
- Käytä paljon `record_observation()` ja `record_per_ticker_decomposition()` — ne kasaavat knowledge-portfoliota tulevaa varten

## PRIORITEETTI
1. **Regime-conditional luokitus** — tärkein, suurin lift-potentiaali
2. **Score-based sizing** — parempi kuin binary skip/pass
3. **Mom_60d Q1-flag** — pelkkä tämän lisäys nykyiseen filteriin lifteinaa potentiaalisesti +30 bps per kauppa
4. **Lisämittarit** — kokeile vain jos perus-3 ovat tehty
5. **Hold-adaptiivisuus** — alimman prioriteetin, kokeellinen

Onnea — palauta moniulotteinen kestävä raportti, ei pelkkää korkeaa Sharpe-lukua.
