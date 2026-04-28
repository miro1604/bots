# Context Notes — käyttäjän mandaatit ja oppimishistoria

## Käyttäjän mandaatit (pysyvät)

### A) Optimistinen-realistinen knowledge portfolio (2026-04-28)
JOKAINEN simulaatio kirjaa havaintoja `_shared/knowledge/feature_portfolio.jsonl`:iin (mitattavat, korreloivat, epäkorreloivat, ei-vaikuttavat). EI vain rahapelillä todistetut ilmiöt — KAIKKI mitattavat (volatiliteetti, volume, fundamentit, alt-data). Sisältää matrix-korrelaatiot, ei-lineaariset suhteet, multi-feature-interaktiot, regime-conditional, sequence patterns. Lähde: `_shared/memory/ops_optimistic_realistic_knowledge_portfolio.md`.

### B) EI pessimistisiä CAGR-yläraja-oletuksia (2026-04-28)
Renaissance 39%, Buffett 20%, Lynch 29%, Druckenmiller 30%+, Greenblatt 50% — 30%+ CAGR todistettu. Älä hylkää korkea-CAGR-strategiaa "L090 ceiling" -argumentilla. Käytä PSR/DSR/CPCV-validointia älykkäästi.

### C) 2026 validation blueprint (kriittinen)
PSR/DSR/MinTRL/FDR pakollinen, CPCV+purging+embargo, AEDL korvaa TBM, Sobol-parametrisalkku, CPA portfolio, VIX-suodatin, feature harvesting.

### D) Sinnikäs exploration
EI luovu yhdellä ajolla — 12+ variaation gate (param × pool × regime × hold) ennen hylkäystä. Korrelaatio/epäkorrelaatio krypto-indeksin kanssa dipeissä yksi vinkki, ei ainoa.

### E) Persona Lattice Rotation Protocol
Dynaaminen PPS-pohjainen persona-rotaatio kaikille agenteille. Bench/retire/new_hire autonomisesti.

### F) Aikadimensio + osalliset
Jokaisen havainnon on sisällettävä: event_date (yksittäinen päivä) tai period_start+period_end (timespan), participants list (tickers/parametrit/markkinat).

### G) Per-ticker decomposition
Kun portfolio-tason CAGR on heikko mutta yksittäiset tickerit (esim. NVDA 60%) toimivat — yksittäisten edge:t MUST olla kirjattu erikseen `record_per_ticker_decomposition()`:lla.

### H) Smart spike handling
Älä blacklistaa 300%+ moveja sokeasti. Erottele real_events (GME, Volmageddon, COVID) vs data_bugs (decimal-virheet). 

### I) Ei aikarajoja tutkimuksille
Debate/tutkimus saa kestää niin pitkään kuin vaatii.

### J) Itsenäinen TODO-aloitus
Älä kysy lupaa, aloita aina itse.

## Aikaisempien iterointien opit (alpha-peak)

### Iter 1: Naiivi filteri (kerros 1-5: regime, persona, lifecycle, breaks, position-size)
- Sääntö: persona ⊆ {balanced, reactive}, lifecycle ⊆ {mature, growth}, ei recent break, crisis × 0.5 size
- Tulos: 907 kauppaa (vs 2877 unfiltered), WR +2.3pp, mean -9.2pp, Sharpe sama
- **Viallinen**: leikkasi pois 5295 declining-tickeriä joista alpha-peak nimenomaan saa edge:nsä (turnaround)
- **Viallinen**: crisis × 0.5 size oli päinvastoin oikealle suunnalle (crisisissä tuotto 3×)

### Iter 2: Class-analysis (KAIKKI luokat hyväksytty, vain mitattu tulokset)
- 2877 kauppaa luokiteltu 62 luokkaan
- **Crisis on PARAS regiimi** (mean +47.8% vs calm +17.8%)
- **Crisis × balanced × mature** N=102 WR 97.1% — kapitulaatio-osto klassikko
- **Calm × reactive × growth** N=116 WR 47.4% med -2.3% — bull-trap
- **Mom_60d Q1** (osake jo laskenut 60d) → mean +51.4% WR 78.2% — reversal-luonne dominoi

### Iter 3: 12%-kynnys (suurempi otos)
- 3836 kauppaa (+33%), pääryhmissä WR sama 67%, mean -3.7pp
- Crisis × balanced × declining N=15 mean **+103.7%** med +84.8% (uusi top-1)

## Tärkeät predictor-korrelaatiot (Pearson, n=2849)
| Mittari | Pearson | Tulkinta |
|---|---:|---|
| atr_pct (20d realisoitu vol) | **+0.321** | korkeampi vol → parempi tuotto (vastoin "low-vol-anomaliaa") |
| dist_from_252d_high | **-0.225** | mitä KAUEMPANA 1y-huipusta → parempi |
| spy_60d | -0.184 | jos SPY on jo noussut → odota |
| mom_60d | -0.179 | osakkeen 60d-tuotto NEGATIIVISESTI korreloitu |
| vol_ratio | -0.049 | volyymi-piikki signaalipäivänä ei ennusta |
| alpha_peak_at_entry | +0.066 | itse signaalin vahvuus EI eroa luokkien välillä |

## Datan rajoitukset
- `pure_alpha_capm.csv`: 840 osaketta, mutta close-data vain 703 osakkeelle (2014-2026)
- IS-jakso 2005-2014 sisältää SPY+VIX, ei strategy-trade-dataa (universumi alkaa 2014)
- Missing values: 250 trades:lle persona/lifecycle = "n/a" (kun ticker:n historia <378 päivää signaalipäivän kohdalla)

## Sopimukset Pythonin kanssa
- Käytä `py -3` (Python 3.14, sis. pyarrow + sklearn + scipy)
- Encoding-fix scriptin alkuun: `try: sys.stdout.reconfigure(encoding="utf-8", errors="replace") except: pass`
- Älä sisennä alaluettelua tutkimuksia/portfolioita ilman aikaleimaa
