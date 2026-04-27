# v98 — DESIGN-RATIONALE

## Mistä v98 syntyi

Yön työ paljasti **kaksi rinnakkaista löytöä**:

1. **v93**: Meidän alpha-strategia (v86-v90) kärsii **9.5-18.5pp survivorship-biaksesta** → aito CAGR vain +5-6%, ei +29%
2. **v97c**: Käyttäjän TV-strategia tuottaa **+175% per kauppa** mutta universum on 100% hindsight-biased (18/40 stockia ei ollut olemassa 2006)

→ **Kysymys**: Voidaanko TV:n hyvät elementit yhdistää meidän point-in-time-disipliiniin?

## v98:n kolme avainmuutosta

### 1. Z-SCORE NORMALISOITU ALPHA (universaali signaali)

**Ongelma TV:ssä**: alpha > 12% absolute kynnys → vain high-beta stockit saavat signaaleja
- TSLA (β=2): 12% alpha-spike yleinen
- MSFT (β=1): alpha harvoin >12% → 0 signaalia
- → strategia vain volatiileille

**Ratkaisu**: Z-score = `alpha / rolling_std(alpha, 252d)`
- "Onko tämä alpha epätavallisen iso TÄMÄN STOCKIN omaan historiaan nähden?"
- 2-sigma alpha (Z>2) on yhtä harvinainen sekä TSLA:ssa että MSFT:ssä
- → MSFT/ETN/SPGI alkaa saamaan signaaleja **laadukkailla** tilanteilla

### 2. HIGH-BETA FILTER (β ≥ 1.3, 252-pv rolling)

**Ongelma TV:ssä**: 40 osaketta käsin valittu "kasvavia voittajia" → hindsight bias

**Ratkaisu**: Algoritminen high-beta filter
- β ≥ 1.3 (laskettu rolling 252pv) = automaattisesti kasvuosakkeet
- Karsii defensiiviset (utilities, staples) jotka eivät tuota TV-tyylistä signaalia muutoinkaan
- Beta on POINT-IN-TIME laskettu — emme käytä future-leakage

### 3. POINT-IN-TIME UNIVERSUM (v93-tyyli)

**Ongelma**: TV:n 40 stock + meidän 255 stock = molemmat olivat survivorship-biased

**Ratkaisu**: Wikipedia historiallinen SP500-jäsenyys
- Jokaiselle päivälle: mitkä stockit OLIVAT SP500:ssa SINÄ päivänä
- Signaali hyväksytään vain jos `sym ∈ members_by_date[signal_date]`
- → Strategy "tunnistaa" voittajat reaaliajassa, ei jälkikäteen

## Muut TV-elementit jotka säilytetään

✓ 1-pv alpha (ei smoothing) - nopea reagointi
✓ 400 päivän hold - pitkä trend-following
✓ Dippi -10% (300-bar high) - milder kuin meidän -20%
✓ Entry next-day open - nopea
✓ Ei VIX-filteriä - säilytetään entry-discipline (testissä)
✓ Ei stop-lossia - säilytetään fat-tail-tuotot
✓ 2.5% sizing per position, max 40 concurrent

## Testattava

| Komponentti | Validointi |
|---|---|
| Base FULL (2005-2026) | Kokonaistuotto, vertailu SPY |
| TRAIN/TEST (2005-2018/2018-2026) | OOS sanity check |
| Rolling WF (12 × 5y/3y) | Aika-robustius |
| Stress (GFC, COVID, 2022, Recovery) | Worst-case |
| Sensitivity (4 params × 4-5 values) | Parametrirobustius |
| LOO TOP-10 | Konsentraatio-riski |
| Block bootstrap MC (1000 simu) | Trade-jakauman robustius |
| SPY HODL vertailu | Onko aito edge? |

## Onnistumiskriteerit

🟢 **Vahva edge**: CAGR ≥ +15%, DD ≤ -25%, Sharpe ≥ 1.0, WF positiivinen ≥10/12, MC p5 > 0
🟡 **Marginal edge**: CAGR +10-15%, voittaa SPY mutta ei riittävän selvästi
🔴 **Ei edgeä**: CAGR < +10% PIT-universumilla → TV-strategia oli pelkkä hindsight

## Ennustaminen

Mun arvio: **+12-18% CAGR PIT-universumilla**, koska:
- v97c tuotti +175% per kauppa hindsight 40-stockilla, mutta survivorship oli ~50-60% siitä
- Ilman survivorshipiä per-trade tuotto ehkä +60-80%
- 2.5% sizing × ~150 tradea / 20v × 70% avg → +8-15% CAGR
- Z-score normalisointi tuo lisäsignaaleja → enemmän tradeja
- High-beta filter parantaa per-trade-tuottoa
- → Bull-case +15-20%, base-case +12%, bear-case +8%
