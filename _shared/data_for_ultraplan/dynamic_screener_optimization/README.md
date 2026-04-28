# Dynamic Screener Optimization — /ultraplan -paketti

Tämä paketti sisältää kaiken mitä /ultraplan tarvitsee optimoidakseen alpha-peak-strategian regime × persona × lifecycle -luokituksen ja parametrit.

## Käynnistys
```bash
/ultraplan _shared/data_for_ultraplan/dynamic_screener_optimization
```
tai paste PROMPT.md:n sisältö suoraan ultraplaniin.

## Hakemiston rakenne

```
dynamic_screener_optimization/
├── PROMPT.md                                # ← TÄRKEIN: ultraplan-prompti, tavoite + ohjeet
├── README.md                                # tämä tiedosto
│
├── scripts/                                 # kaikki tarvittavat Python-skriptit
│   ├── dynamic_screener.py                  # nykyinen 5-modulinen screener (RegimeDetector + Personality + Lifecycle + Breaks + Router)
│   ├── quant_validation_v2.py               # 2026-blueprint validointi-kirjasto (PSR, DSR, MinTRL, CPCV, AEDL)
│   ├── feature_portfolio.py                 # knowledge-portfolio API (record_observation jne)
│   ├── adaptive_backtest_router.py          # frequency-adaptive validointi
│   ├── alpha_peak_filtered_strategy.py      # walk-forward backtest-runner
│   ├── alpha_peak_class_analysis.py         # predictor-correlation + kvintiili-analyysi
│   ├── alpha_peak_class_table.py            # luokkataulukon generaattori
│   ├── alpha_peak12_full_run.py             # alpha-peak 12% -kynnyksellä full run
│   └── alpha_peak_full_validation.py        # 2026-blueprint full validointi olemassa oleville trade-listoille
│
├── results/                                 # nykyiset ajot (älä korvaa, vertaa vs. niitä)
│   ├── trades_alpha15_unfiltered.jsonl      # 2877 kauppaa, kynnys 15%, OOS 2015-2026
│   ├── trades_alpha15_filtered.jsonl        # 907 kauppaa nykyisellä filterillä
│   ├── trades_alpha15_enriched.csv          # 2849 kauppaa + mom_60d/dist/atr/spy_60d/vol_ratio
│   ├── trades_alpha12_unfiltered.jsonl      # 3836 kauppaa, kynnys 12%
│   ├── class_table_alpha15.csv + .json      # luokkataulukko 15%
│   ├── class_table_alpha12.csv + .json      # luokkataulukko 12%
│   ├── class_analysis_alpha15.json          # pearson + spearman + quintiili
│   ├── comparison_filter_vs_unfiltered.json # vanhan filterin vs no-filterin vertailu
│   └── full_validation_2026_blueprint.json  # PSR/DSR/CPCV/path-dependency
│
└── reference/                               # taustamateriaali
    └── (täydennetään tarvittaessa)
```

## Pohjadata (alkuperäisissä paikoissa, viittaa suoraan)

| Polku | Sisältö |
|---|---|
| `C:/Users/puros/bots/finance/alpha_data/pure_alpha_capm.csv` | 840 osakkeen päivittäinen CAPM-jäännös 2000-2026 (signaalin lähde) |
| `C:/Users/puros/bots/finance/alpha_data/pure_alpha_sector.csv` | sektor-residual vaihtoehtoinen alpha |
| `C:/Users/puros/bots/finance/alpha_data/pure_alpha_multifactor.csv` | multi-factor residual |
| `C:/Users/puros/bots/finance/alpha_data/rolling_beta_60d.csv` (+120, +252) | rolling-beta per ticker |
| `C:/Users/puros/bots/finance/data_for_ultraplan/v20_full_prices_close_cleaned.parquet` | 703 osakkeen close 2014-2026 (spike-handlerilla siivottu) |
| `C:/Users/puros/bots/finance/data_for_ultraplan/v20_full_prices_volume.parquet` | volyymi |
| `C:/Users/puros/bots/finance/asset_cache/_GSPC.pkl` | SPY 2005-2026 |
| `C:/Users/puros/bots/finance/asset_cache/_VIX.pkl` | VIX 2005-2026 |

## Strategia (kiinteä — älä muuta)
- **Signaali**: alpha_peak_20 = rolling 20d sum daily-CAPM-jäännöksistä SPY:ä vasten
- **Kynnys**: 12% (saadaan 3836 kauppaa) — kynnys 15% on referenssinä (2877 kauppaa)
- **Hold**: 400 päivää
- **Universumi**: 600 osaketta (top alpha-coverage)
- **Kustannukset**: 30 bps round-trip
- **OOS-jakso**: 2015-01-01 → 2026-04-23

## Optimointikohteet — käyttäjän pyynnön mukaisesti
1. **Regime-conditional persona/lifecycle -kynnykset** (osakkeen luonne suhteellinen markkinatilaan)
2. **Score-pohjainen position-sizing** (binary skip/pass tilalle 0–3.0 skoori)
3. **Lisämittarit** (mom_60d, dist_from_252d_high, atr_pct, vol_ratio, spy_60d, ...)
4. **Hold-adaptiivisuus** (regime/persona-conditional, AEDL)
5. **Kestävyys**: walk-forward 3 segmenttiä (2015-2017 / 2018-2021 / 2022-2026)

## Ehdoton vaatimus — 2026-blueprint validointi
- PSR > 0.95
- DSR > 0.95 (n_trials = oma tutkimusgrid, raportoi rehellisesti)
- CPCV min-fold-PSR > 0.80
- Path-dependency Max-DD bootstrap: alpha_path_dependent = False
- N ≫ MinTRL

## Käyttäjän kestävyys-mandaatti (LUE PROMPT.md tarkasti)
- **EI** post-hoc-optimointia OOS-datalla
- **EI** mystisiä sääntöjä jotka eivät yleisty (esim. "if month==March")
- **EI** kynnyksiä joiden lähde on PnL-katselu (lähde tilastollinen tai teoreettinen)
- **JA** moniulotteinen — vähintään 3 mittaria päätösketjussa
