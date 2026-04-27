# v32_dyn Validation Spec — for /ultraplan cloud sandbox

## Goal
Backtest käyttäjän määrittelemää **dynaamista osakepoolia + immediate-execution -strategiaa**
puhtaasti historiallisella datalla. **Ei walk-forward, ei CPCV, ei adversarial-testejä** —
vain raaka backtest joka näyttää kuinka strategia olisi pelannut 2015-01-01 → 2026-04-26.

Käyttäjän tavoite: **N ≥ 200** (paljon treidejä, dataa runsaasti). Tarkoituksena nähdä
toimiiko strategia tällä spec:llä ennen kuin tehdään raskasta validointia.

## Strategia-spec

### Dynaaminen osakepooli (suodattimet)
Per ostohetki, suodata 673-ticker-universumista vain ne jotka täyttävät:

1. **ADV-filteri** (sopivasti löysä jotta N ≥ 200): 20-päivän mediaani
   `dollar_volume = close × volume` ≥ **20M USD** (v31:n 50M oli liian tiukka).

2. **Sektori-filteri**: jätä pois `Utilities, Real Estate, Consumer Staples`.
   Käytä `v20_full_sector_map.csv`.

3. **Hype-blacklist**: poista `GME, AMC, BB, BBBY, CLOV, EXPR, HOOD, KOSS, NOK,
   PLTR, RIDE, SDC, SPCE, WISH`.

4. **Datan saatavuus**: vähintään **460 päivää** historiaa kullekin ticker:ille
   (riittää alpha-laskentaan).

5. **(VAPAAEHTOINEN)**: jos N < 200 muillakin parametreilla, kokeile lisäksi:
   - Volatility-band: 50pv hist-vol 15-80% (stikkaa pois liian rauhalliset ja liian
     hullut)
   - Min price: close > $5 (no penny stocks)

### Signaali (entry-ehto)
Per kaupankäyntipäivä, jokaiselle ticker:ille:

```
alpha[t] = return[t] - beta * xli_return[t]   # beta = rolling 120pv cov/var vs XLI
peak_20  = alpha.rolling(20).max()             # KÄYTTÄJÄN MUUTOS: peak_20, EI peak_50!
dist_378 = (close / close.rolling(378).max() - 1) * 100

ENTRY if:
  peak_20[t]  > 15
  AND dist_378[t] < -20
  AND ADV[t]  >= 20M
  AND not in hype_blacklist
  AND sector not in {Utilities, RE, Staples}
```

### Ostotoimeksianto (KRIITTINEN MUUTOS v31:stä)
**Heti, samalla päivällä** kun signaali laukeaa (ei odota kuukauden lopukkeen pakettia).

- Entry-hinta: **next-day open** (käytä open jos saatavilla, muuten close[t+1])
- Position size: **kiinteä $10,000 per signaali** (ei DCA-allokointi, ei kuukauden cash split)
- Multiple signals same day: ostetaan kaikki (yksi positio per ticker per signaali)
- Re-entry-rajoite: jos ticker on jo avoimessa positiossa, EI uusia ostoja siihen ennen exit:iä

### Exit-säännöt (sama kuin v31, ulrtaplan voi testata muunnoksia)
- **Hold**: 680 trading days
- **Stop-loss**: -12% from entry, intraday (käyttää Low-hintaa)
- **MTM at end of period**: avoimet positiot mark-to-market viimeisellä saatavilla olevalla closella

### Transaction costs
- Buy: 0.5% (lower than v31's 2% — ei kuukausi-DCA-friction:ia)
- Sell: 0.5%
- No tax modeling (yksinkertainen historiallinen backtest)

## Data files (offline — REPO-relative, ei network-kutsuja)
All in `finance/data_for_ultraplan/`:
- `v20_full_prices_close.parquet` — 673 × 3096, auto-adjusted Close
- `v20_full_prices_low.parquet` — 673 × 3096, auto-adjusted Low (SL:ää varten)
- `v20_full_prices_volume.parquet` — Volume (ADV-filterille)
- `v20_full_daily_returns.parquet` — pre-computed pct_change
- `v20_full_sector_map.csv` — symbol → GICS Sector → sector_etf
- `v20_full_metadata.json` — universe spec

XLI on universumissa mukana (sector ETF), käytä sitä beta-laskentaan.

## Vaiheet (ultraplan-skripti)

### 1. Data-load (offline)
- Lue Parquet-tiedostot pyarrow:lla
- Älä kutsu yfinance/Wikipedia/requests

### 2. Signaalien laskenta
```python
ret = close.pct_change() * 100
xli_ret = ret["XLI"]
beta = ret.rolling(120).cov(xli_ret) / xli_ret.rolling(120).var()
alpha = ret.subtract(beta.multiply(xli_ret, axis=0), fill_value=0)
peak_20 = alpha.rolling(20).max()
hi_378 = close.rolling(378).max()
dist_378 = (close / hi_378 - 1) * 100
adv_20 = (close * volume).rolling(20).mean()
```

### 3. Entry-loop (per päivä, 2015-01-01 → 2026-04-26)
- Päivittäinen pyyhkäisy
- Kerää signaalit jotka laukeavat sinä päivänä
- Avaa position seuraavan kaupankäyntipäivän openilla (tai closella jos open puuttuu)
- Tallenna kauppa: entry_date, ticker, entry_price, sector, signal_strength

### 4. Exit-loop
- Per avoin positio, jokainen seuraava päivä:
  - Tarkista intraday low ≤ entry × 0.88 → SL exit
  - Muuten kun hold ≥ 680 päivää → HOLD exit close:lla
- Tallenna: exit_date, exit_price, reason (SL/HOLD/MTM), pnl_pct

### 5. Aggregointi
- Per kauppa: pnl_pct, pnl_eur (asuming 10000 per kauppa)
- Per vuosi: net pnl, n_trades
- Yhteensä: total_pnl, n_total, profitable_trade_rate

## Deliverable format

### KRIITTINEN: Raportti on TÄYDELLINEN VAIN historialliselle ajolle.
**Ei walk-forward, ei CPCV, ei adversarial.** Käyttäjä haluaa nähdä että
strategia tuottaa tarpeeksi paljon treidejä (N ≥ 200) ja onko bruttotulos järkevä.

```
=== v32_dyn HISTORICAL BACKTEST 2015-01..2026-04 ===
Universumin koko (mediaani per kuukausi): ___
Total signals: ___
Total trades executed: ___
N ≥ 200: PASS / FAIL

Per-trade statistics:
- Profitable: ___% (___ / total)
- Avg WIN: +___%
- Avg LOSS: ___%
- Median pnl: ___%
- Best trade: +___% (ticker, date)
- Worst trade: ___% (ticker, date)
- Hold reason breakdown: HOLD ___, SL ___, MTM ___

Per-year breakdown:
| Year | N trades | Net pnl ($) | Avg pnl% |

Concentration check:
- Top 10 contributing tickers (by total pnl)
- Top 10 worst contributors

Total return assuming $10k per trade, all reinvested into next signal:
- Final equity vs starting capital
- Realized CAGR

vs SPY DCA same period (same total invested capital): ___% CAGR

VERDICT (käyttäjälle): jatketaanko validointiin (CPCV/walk-forward) vai onko
strategia jo prima-facie heikko?
```

## Constraints
- Vain Parquet-data, ei network-kutsuja
- Käytä pyarrow:ta lukuun, pandas:ia laskentaan
- Skripti aja end-to-end yhdellä kertaa (ei interaktiivista)
- Tulosta lopullinen raportti stdout:iin
- Tallenna `finance/data_for_ultraplan/v32_dyn_results.md` ja `v32_dyn_trades.csv`
