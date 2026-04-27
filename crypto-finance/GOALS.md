# GOALS — crypto-finance

_Luotu: 2026-04-26, päivitetty 2026-04-27 (käyttäjän leverage-focus-mandate)_

## North Star (PÄIVITETTY 2026-04-27)

Rakentaa **Binance perpetual futures -leverage-strategiaportfolio** joka tuottaa
ASAP-merkittävää euromääräistä tuottoa pienellä alkupääomalla. **Vain Binance-listalla
olevat coinit**, **vain leverage-strategiat** (5-125x), hold-aika 30 min – 48 h.
High-risk-high-reward hyväksytty kunhan strategia on matemaattisesti positive-EV
validoinnin (CPCV+PBO+walk-forward+adversarial) jälkeen.

**Käyttäjän filosofia:** "Binance perp:ssä on todella halpaa käyttää leveragea →
pieni edge × korkea leverage × toistuvuus = todella nopea vaurastuminen."

## Kvartaalitavoite (90 vrk)

**Tuotto-target portfolio-tasolla**: Sharpe > 2.0 walk-forward, max DD < 15%,
70%+ kuukausista positiivinen, ennen live-rahojen sijoittamista.

## KR-tasot (auto-nostetaan saavutettaessa, 1.5-3×)

### KR1 — Leverage-strategia-katto (PÄIVITETTY 2026-04-27)
Tuottaa **5 robustia Binance perp -leverage-strategiaa** joiden walk-forward
Sharpe > 1.5 ja per-treidi nettoa (leverage huomioiden):
- Lyhyt hold (< 4 h): **≥ 1.5% per treidi** (mediaani, leverage netto)
- Pidempi hold (4-48 h): **≥ 3% per treidi** (mediaani)
- Liquidaatio-suoja: ei strategiaa joka voi liquidoida > 30% bankrollista
- Suosittu leverage-haarukka: 10-50x (suuri tuotto-potentiaali, hallittavissa oleva
  liquidaatioriski)

Aikataulu: 60 vrk MVP, jatkuva laajennus.

### KR2 — Per-treidi minimitarget paper-tradessa
Saavuta **30 vrk paper-trade** jossa:
- Lyhyet treidit ≥ 1% / treidi (mediaani)
- Pidemmät treidit ≥ 3% / treidi (mediaani)
- Hit rate > 55%

### KR3 — Cross-asset-korrelaatiosignaalit
Validoi **vähintään 3 cross-asset-paria** joista löytyy hyödynnettävä korrelaatio
tai epäkorrelaatio (BTC↔SPX, BTC↔DXY, ETH↔NDX, BTC↔Gold, ALT↔BTC dominance, jne).
Validointi: walk-forward + Monte Carlo, edge > 0.5% / kuukausi yli buy-and-hold:n.

### KR4 — Portfolion korrelaatio
Strategia-portfolio jossa per-pari **korrelaatiokerroin < 0.4** ja kokonaisportfolion
Sharpe > 2.0 (vs. yksittäisten strategioiden mediaani Sharpe).

### KR5 — Out-of-the-box -kategoria
**3+ strategiaa** jotka EIVÄT ole klassisia (ei MA-cross, ei RSI-divergenssi yksin),
vaan novel kombinaatioita esim:
- Funding-rate-paine + price-action-rejection
- BTC-dominance-shift + alt-rotation
- Cross-exchange basis arbitraasi (informaatio-edge, ei toteutus)
- VIX-spike + krypto-discount window

### KR6 — Robustness-validaatio
Jokaiselle strategialle ennen knowledge/strategies.jsonl-tallennusta:
- ✅ Walk-forward 6kk in-sample / 1kk out-of-sample, ≥3 sykliä
- ✅ Monte Carlo 1000 trade-permutaatiota, return-distribution skewness > 0
- ✅ Block bootstrap (block size = mediaani-hold), confidence interval > 0
- ✅ Survivorship + look-ahead bias tarkistettu

## Mittarit (joita orchestrator + auto_replenish + goal_elevation seuraa)

| Mittari | Lähde | Päivitystiheys |
|---|---|---|
| `strategies_validated_count` | knowledge/strategies.jsonl | per cycle |
| `mean_per_trade_short` | backtest/results/ | per validation |
| `mean_per_trade_long` | backtest/results/ | per validation |
| `portfolio_sharpe_wf` | walk-forward run | weekly |
| `cross_asset_pairs_active` | knowledge/strategies.jsonl tag | per cycle |
| `out_of_box_count` | knowledge/strategies.jsonl tag | per cycle |

## Don't / Kielletyt lähestymistavat

- ❌ Live-rahaa ennen 30 vrk paper-tradea + KR6-validaatio
- ❌ Pelkkä yksittäinen indikaattori-cross (MA, RSI yksin) — overfit-riski
- ❌ Strategia joka tuottaa < 1% / treidi (ei kelpaa hit-rate-puolustuksena)
- ❌ Strategia joka kestää vain bull-tilanteessa — testaa myös 2022-2023 dataa
- ❌ Look-ahead bias missään muodossa
- ❌ Snapshot-pohjainen "future leak" cross-asset-data:ssa
