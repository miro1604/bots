# -*- coding: utf-8 -*-
"""
v20 — "EI-OMA-SEKTORI" -BENCHMARK: osake vs kaikki-muut-sektorit.

Tavoite: alphan puhtaus. Kun osake verrataan benchmarkkiin johon se EI kuulu,
sen alpha ei laimennu itsensa kanssa.

Variantit:
  I: Exclude-own-sector (kaikki 10 muuta sektori-ETF equal-weight)
  J: Exclude-own 50% + SPY 50%
  K: Ei-oma-sektori vain growth-sektorit (XLK+XLY+XLC jos ei oma)
  L: Ei-oma-sektori vain defensive (XLP+XLU+XLV)
  M: Ei-oma-sektori pois ylin + alin: vain 5-8 "keski" sektoria

Strategia: B3-hierarkia (peak>15 d<-60, peak>14 d<-55, peak>13 d<-50),
hold=680, SL=12, n_picks=2, tc=1.
"""
import warnings; warnings.filterwarnings("ignore")
import yfinance as yf, requests as _req, time as _time
import pandas as pd, numpy as np
from io import StringIO as _SIO
from datetime import datetime

LOOKBACK=378
START_DATE="2015-01-01"; END_DATE=datetime.today().strftime("%Y-%m-%d")
BATCH_SIZE=200; MIN_ADAV=50e6; MAX_HOLD=1200; MONTHLY_INV=3000.0
EXCLUDE_SEC={"Utilities","Real Estate","Consumer Staples"}
HYPE_BLACKLIST={"GME","AMC","BB","BBBY","CLOV","EXPR","HOOD","KOSS","NOK",
                "PLTR","RIDE","SDC","SPCE","WISH"}
MEGA_POOL=["AAPL","MSFT","GOOGL","AMZN","META","NVDA","TSLA","NFLX","AVGO","ORCL"]
BUY_COST_PCT=2.0; SELL_COST_PCT=2.0; TAX_RATE=0.20
BETA_WIN=60; WIN=50
HOLD=680; SL_PCT=12; N_PICKS=2; TC=1

SECTOR_ETF = {
    "Information Technology": "XLK",
    "Financials":              "XLF",
    "Health Care":             "XLV",
    "Consumer Discretionary":  "XLY",
    "Communication Services":  "XLC",
    "Industrials":             "XLI",
    "Energy":                  "XLE",
    "Utilities":               "XLU",
    "Real Estate":             "XLRE",
    "Materials":               "XLB",
    "Consumer Staples":        "XLP",
}

GROWTH_SECTORS={"Information Technology","Consumer Discretionary","Communication Services"}
DEFENSIVE_SECTORS={"Consumer Staples","Utilities","Health Care"}

print("="*78)
print("v20  -  'Ei-oma-sektori' -benchmark variantit")
print("="*78)

hdrs={"User-Agent":"Mozilla/5.0"}
def ghtml(u, retries=4):
    for i in range(retries):
        try:
            r=_req.get(u,headers=hdrs,timeout=30)
            if r.status_code==200 and len(r.text)>1000: return r.text
        except: pass
        _time.sleep(3)
    return ""
def safe_read(url, retries=3):
    for i in range(retries):
        html=ghtml(url)
        if html:
            try: return pd.read_html(_SIO(html),flavor="lxml")
            except: pass
        _time.sleep(4)
    return None

tbl500=safe_read("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
tbl500["Symbol"]=tbl500["Symbol"].astype(str).str.strip().str.replace(".","-")
sec_map=dict(zip(tbl500["Symbol"],tbl500["GICS Sector"]))
sp500g=[s for s in tbl500["Symbol"] if sec_map.get(s,"") not in EXCLUDE_SEC]
tbl400_raw=safe_read("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies")
sp400g=[]
if tbl400_raw is not None:
    tbl400=tbl400_raw[0]
    tbl400["Symbol"]=tbl400["Symbol"].astype(str).str.strip().str.replace(".","-")
    if "GICS Sector" in tbl400.columns:
        G400={"Information Technology","Health Care","Consumer Discretionary","Communication Services","Industrials"}
        sp400g=tbl400[tbl400["GICS Sector"].isin(G400)]["Symbol"].tolist()
        for s,sec in zip(tbl400["Symbol"],tbl400["GICS Sector"]): sec_map[s]=sec
for s in MEGA_POOL:
    if s not in sec_map: sec_map[s]="Information Technology"
tickers=sorted(set(sp500g+sp400g+MEGA_POOL))
tickers=[t for t in tickers if 1<len(t)<=6 and t.replace("-","").isalpha()]
tickers=[t for t in tickers if t not in HYPE_BLACKLIST]

BENCHMARK_SYMS=["SPY"]+list(SECTOR_ETF.values())
all_dl=sorted(set(tickers+BENCHMARK_SYMS))
print(f"\n[1/5] Data ({len(all_dl)} symbolille)...")
all_close,all_vol,all_low={},{},{}
for i in range(0,len(all_dl),BATCH_SIZE):
    batch=all_dl[i:i+BATCH_SIZE]
    raw=yf.download(batch,start=START_DATE,end=END_DATE,auto_adjust=True,progress=False,group_by="column")
    if isinstance(raw.columns,pd.MultiIndex):
        for sym in batch:
            try: all_close[sym]=raw["Close"][sym]; all_vol[sym]=raw["Volume"][sym]; all_low[sym]=raw["Low"][sym]
            except: continue

prices=pd.DataFrame(all_close); prices.dropna(how="all",inplace=True)
prices.index=prices.index.tz_localize(None) if prices.index.tz else prices.index
volume=pd.DataFrame(all_vol).reindex(prices.index).fillna(0)
lows=pd.DataFrame(all_low).reindex(prices.index).ffill()
avail=[s for s in tickers if s in prices.columns and prices[s].notna().sum()>460]
dolvol=pd.DataFrame(index=prices.index)
for s in avail:
    if s in volume.columns: dolvol[s]=(prices[s]*volume[s]).rolling(20).mean()

ret=prices.pct_change()*100
dist_df=pd.DataFrame(index=prices.index)
for s in avail:
    hi=prices[s].rolling(LOOKBACK).max()
    dist_df[s]=(prices[s]/hi-1)*100

month_ends=prices.groupby(prices.index.to_period("M")).apply(lambda g:g.index[-1])

def check_exit(pos, last_day, month_start, sl_pct, hold_days):
    trigger=pos["entry_price"]*(1-sl_pct/100); sym=pos["ticker"]
    days=prices.index[(prices.index>=month_start)&(prices.index<=last_day)]
    for day in days:
        if day<=pos["entry_date"]: continue
        if sym not in lows.columns: continue
        low_today=lows[sym].get(day,np.nan)
        if pd.notna(low_today) and low_today<=trigger:
            close_today=prices[sym].get(day,np.nan)
            if pd.notna(close_today) and close_today<trigger*0.95:
                return day, max(low_today,(low_today+close_today)/2), "SL"
            return day, trigger, "SL"
    mask=pos["fwd_dates"]<=last_day; day_idx=int(mask.sum())-1
    if day_idx>=0:
        if day_idx==400:
            pa=pos["fwd_prices"]; end=min(day_idx+1,len(pa)); start=max(0,end-252)
            w=pa[start:end]
            if len(w)>0:
                hi=w.max(); cur=pa[day_idx]
                if (cur/hi-1)*100<-10: return last_day, cur, "400"
        if day_idx>=hold_days:
            return last_day, pos["fwd_prices"][min(day_idx,len(pos["fwd_prices"])-1)], "HOLD"
        elif day_idx>=len(pos["fwd_prices"])-1:
            return last_day, pos["fwd_prices"][-1], "DATA_END"
    return None, None, None

PEAK_THR_MAP={1:15,2:14,3:13}
DIST_MAP={1:-60,2:-55,3:-50}

def compute_peak_per_stock(stock_bench_ret_map):
    alpha=pd.DataFrame(index=prices.index)
    for s in avail:
        if s not in stock_bench_ret_map: continue
        bench_ret=stock_bench_ret_map[s]
        r=ret[s]
        b=r.rolling(BETA_WIN).cov(bench_ret)/bench_ret.rolling(BETA_WIN).var()
        alpha[s]=r-b*bench_ret
    return alpha.rolling(WIN).max()

def run_sim_B3(peak_df, start_m=None, end_m=None, track=False):
    me=month_ends.copy()
    if start_m is not None: me=me[me.index>=pd.Period(start_m,"M")]
    if end_m is not None: me=me[me.index<=pd.Period(end_m,"M")]

    cash=0.0; open_pos=[]; annual_realized={}; carry_fwd=0.0
    cur_year=None; n_trades=0; trades=[]

    for mi,(month,last_day) in enumerate(me.items()):
        this_year=last_day.year
        if cur_year is not None and this_year>cur_year:
            yr_net=annual_realized.get(cur_year,0)
            if yr_net>0:
                taxable=max(yr_net-carry_fwd,0); carry_fwd=max(carry_fwd-yr_net,0)
                if taxable>0: cash-=taxable*TAX_RATE
            else: carry_fwd+=-yr_net
        cur_year=this_year
        cash+=MONTHLY_INV
        month_start=month.to_timestamp()

        still=[]
        for pos in open_pos:
            ed, ep_exit, reason=check_exit(pos, last_day, month_start, SL_PCT, HOLD)
            if ed is not None:
                gross=pos["shares"]*ep_exit; sc=gross*SELL_COST_PCT/100
                np_=gross-sc; cash+=np_
                realized=np_-pos["invested"]
                yr=ed.year; annual_realized[yr]=annual_realized.get(yr,0)+realized
                if track:
                    trades.append({"ticker":pos["ticker"],"entry_date":pos["entry_date"],
                                  "exit_date":ed,"entry_px":pos["entry_price"],"exit_px":ep_exit,
                                  "invested":pos["invested"],"proceeds":np_,
                                  "ret_pct":(ep_exit/pos["entry_price"]-1)*100,"reason":reason,
                                  "sector":sec_map.get(pos["ticker"],"?")})
            else: still.append(pos)
        open_pos=still

        open_per_ticker={}
        for pos in open_pos: open_per_ticker[pos["ticker"]]=open_per_ticker.get(pos["ticker"],0)+1

        chosen_list=[]; seen=set()
        for tier_idx in range(1, 4):
            if len(chosen_list)>=N_PICKS: break
            pthr=PEAK_THR_MAP[tier_idx]; dthr=DIST_MAP[tier_idx]
            cands=[]
            for sym in avail:
                if sym in seen: continue
                if open_per_ticker.get(sym,0)>=TC: continue
                adav=dolvol[sym].get(last_day,np.nan) if sym in dolvol.columns else np.nan
                if pd.isna(adav) or adav<MIN_ADAV: continue
                ep=prices[sym].get(last_day,np.nan)
                if pd.isna(ep) or ep<=0: continue
                pk=peak_df[sym].get(last_day,np.nan) if sym in peak_df.columns else np.nan
                if pd.isna(pk) or pk<=pthr: continue
                d=dist_df[sym].get(last_day,np.nan)
                if pd.isna(d) or d>=dthr: continue
                cands.append({"sym":sym,"ep":float(ep),"metric":float(d)})
            cands.sort(key=lambda x:x["metric"])
            for c in cands:
                if len(chosen_list)>=N_PICKS: break
                if c["sym"] in seen: continue
                chosen_list.append(c); seen.add(c["sym"])

        if not chosen_list or cash<=0: continue

        valid=[]
        for c in chosen_list:
            fs=prices[c["sym"]][prices.index>last_day].dropna()
            if len(fs)<400: continue
            c["fs"]=fs; valid.append(c)
        if not valid: continue

        per_alloc=cash/len(valid)
        for c in valid:
            sym=c["sym"]; ep=c["ep"]; fs=c["fs"]
            fwd_len=min(MAX_HOLD,len(fs))
            invested=per_alloc; bc=invested*BUY_COST_PCT/100; nb=invested-bc
            shares=nb/ep
            open_pos.append({"ticker":sym,"entry_date":last_day,"entry_price":ep,"shares":shares,
                             "invested":invested,"fwd_prices":fs.iloc[:fwd_len].values,"fwd_dates":fs.index[:fwd_len]})
            n_trades+=1
        cash=0.0

    if cur_year is not None:
        yr_net=annual_realized.get(cur_year,0)
        if yr_net>0:
            taxable=max(yr_net-carry_fwd,0)
            if taxable>0: cash-=taxable*TAX_RATE

    final=cash; last_date=me.iloc[-1]; fr=0
    for pos in open_pos:
        mask=pos["fwd_dates"]<=last_date; day_idx=int(mask.sum())-1
        px=pos["fwd_prices"][min(day_idx,len(pos["fwd_prices"])-1)] if day_idx>=0 else pos["entry_price"]
        gross=pos["shares"]*px; sc=gross*SELL_COST_PCT/100
        net=gross-sc; final+=net; fr+=net-pos["invested"]
    if fr>0:
        taxable=max(fr-carry_fwd,0)
        if taxable>0: final-=taxable*TAX_RATE

    months_count=len(me); total_saved=months_count*MONTHLY_INV
    n_yr_loc=months_count/12 if months_count>0 else 1
    cagr=(final/total_saved)**(1/n_yr_loc)-1 if total_saved>0 else 0
    return final, cagr, n_trades, trades

print(f"[2/5] Rakenna benchmark-kartat...")

# I: Exclude-own-sector, equal-weight loput 10
def build_excl_own(own_weight_sectors=None, include_spy_fraction=0.0):
    """
    own_weight_sectors: None = kaikki ei-oma. Tai set: kayttaa vain sektorit tasta joukosta.
    include_spy_fraction: lisaa SPY:n tassa osuudessa, muut saavat (1-fraction)/N painon.
    """
    stock_bench={}
    for s in avail:
        own_sec=sec_map.get(s,"?")
        # Hyvaksyttavat sektorit benchmarkkiin
        if own_weight_sectors is None:
            accept=[sector for sector, etf in SECTOR_ETF.items() if sector!=own_sec and etf in ret.columns]
        else:
            accept=[sector for sector in own_weight_sectors if sector!=own_sec and SECTOR_ETF.get(sector) in ret.columns]
        accept_etfs=[SECTOR_ETF[sec] for sec in accept]
        if not accept_etfs: continue
        etf_ret=ret[accept_etfs].mean(axis=1)
        if include_spy_fraction>0 and "SPY" in ret.columns:
            bench=(1-include_spy_fraction)*etf_ret + include_spy_fraction*ret["SPY"]
        else:
            bench=etf_ret
        stock_bench[s]=bench
    return stock_bench

peak_configs={}
print(f"  I: Exclude-own-sector (kaikki 10 muuta)...")
peak_configs["I_EXCL10"]=compute_peak_per_stock(build_excl_own())

print(f"  J: Exclude-own 50% + SPY 50%...")
peak_configs["J_EXCL+SPY"]=compute_peak_per_stock(build_excl_own(include_spy_fraction=0.5))

print(f"  K: Exclude-own, vain growth-sektorit (IT/ConDisc/Comm)...")
peak_configs["K_GROWTH"]=compute_peak_per_stock(build_excl_own(own_weight_sectors=GROWTH_SECTORS))

print(f"  L: Exclude-own, vain defensive (Staples/Utilities/HC)...")
peak_configs["L_DEFENSIVE"]=compute_peak_per_stock(build_excl_own(own_weight_sectors=DEFENSIVE_SECTORS))

print(f"  Ref: A_SPY baseline...")
def compute_peak_vs_index(idx):
    ir=ret[idx]
    alpha=pd.DataFrame(index=prices.index)
    for s in avail:
        r=ret[s]
        b=r.rolling(BETA_WIN).cov(ir)/ir.rolling(BETA_WIN).var()
        alpha[s]=r-b*ir
    return alpha.rolling(WIN).max()
peak_configs["A_SPY"]=compute_peak_vs_index("SPY")
peak_configs["D_XLI"]=compute_peak_vs_index("XLI")

print(f"\n[3/5] Simuloi B3-hierarkia kullekin...\n")
print(f"  {'Config':<13} {'Full CAGR':>10} {'Train':>8} {'Test':>8} {'N':>4} {'k/v':>5} {'min(T,t)':>9}")
results=[]
for name, peak_df in peak_configs.items():
    _,c_fu,nt,_=run_sim_B3(peak_df)
    _,c_tr,_,_=run_sim_B3(peak_df, start_m="2015-01", end_m="2020-12")
    _,c_te,_,_=run_sim_B3(peak_df, start_m="2021-01", end_m="2026-04")
    mn=min(c_tr,c_te)
    kv=nt/(len(month_ends)/12)
    print(f"  {name:<13} {c_fu*100:>9.2f}% {c_tr*100:>7.2f}% {c_te*100:>7.2f}% {nt:>4} {kv:>5.1f} {mn*100:>8.2f}%")
    results.append({"config":name,"full":c_fu,"train":c_tr,"test":c_te,"n":nt,"kv":kv,"min_tt":mn})

results.sort(key=lambda x:-x["min_tt"])
print(f"\n--- TOP by min(Train, Test) ---")
print(f"  {'Config':<13} {'Full':>7} {'Train':>7} {'Test':>7} {'N':>4} {'k/v':>5} {'min(T,t)':>9}")
for r in results:
    print(f"  {r['config']:<13} {r['full']*100:>6.2f}% {r['train']*100:>6.2f}% {r['test']*100:>6.2f}% {r['n']:>4} {r['kv']:>5.1f} {r['min_tt']*100:>8.2f}%")

results.sort(key=lambda x:-x["full"])
print(f"\n--- TOP by Full CAGR ---")
print(f"  {'Config':<13} {'Full':>7} {'Train':>7} {'Test':>7} {'N':>4} {'k/v':>5}")
for r in results:
    print(f"  {r['config']:<13} {r['full']*100:>6.2f}% {r['train']*100:>6.2f}% {r['test']*100:>6.2f}% {r['n']:>4} {r['kv']:>5.1f}")

pd.DataFrame(results).to_csv("C:\\Users\\puros\\v20_summary.csv",index=False)
print(f"\n[4/5] Parhaan kauppatilastot + nyky-signaalit...")
BEST=max(results, key=lambda x:x["min_tt"])
print(f"  Paras config: {BEST['config']}")
f,c,nt,trades=run_sim_B3(peak_configs[BEST["config"]], track=True)
tdf=pd.DataFrame(trades)
if len(tdf)>0:
    tdf["entry_date"]=pd.to_datetime(tdf["entry_date"])
    tdf["exit_date"]=pd.to_datetime(tdf["exit_date"])
    tdf["exit_year"]=tdf["exit_date"].dt.year
    print(f"  Final: {f:,.0f}e  CAGR: {c*100:.2f}%  N={nt}  k/v: {nt/(len(month_ends)/12):.1f}")
    print(f"\n  Top-10 voittaja-kauppaa:")
    tsort=tdf.sort_values("ret_pct",ascending=False).head(10)
    for _,t in tsort.iterrows():
        print(f"    {t['ticker']:<7} {t['sector']:<22} {t['entry_date'].strftime('%Y-%m-%d')} -> {t['exit_date'].strftime('%Y-%m-%d')}  {t['ret_pct']:+.1f}%")
    tdf.to_csv("C:\\Users\\puros\\v20_best_trades.csv",index=False)

print(f"\n[5/5] v20 valmis.")
