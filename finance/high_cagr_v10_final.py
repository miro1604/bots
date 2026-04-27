# -*- coding: utf-8 -*-
"""
HIGH-CAGR v10 FINAL — ROBUSTISUUSTESTI parhaalle ROBUSTILLE versiolle:
  Config: hold=680, SL=15, alpha>=15, dist<-50 (ei -70!)
  Tama antaa CAGR 81.4% ja N=17 (vs -70 N=9).
"""
import warnings; warnings.filterwarnings("ignore")
import yfinance as yf, requests as _req, time as _time
import pandas as pd, numpy as np
from io import StringIO as _SIO
from datetime import datetime

ALPHA_WIN=10; LOOKBACK=378
START_DATE="2015-01-01"; END_DATE=datetime.today().strftime("%Y-%m-%d")
BATCH_SIZE=200; MIN_ADAV=50e6; MAX_HOLD=1200; MONTHLY_INV=3000.0
EXCLUDE_SEC={"Utilities","Real Estate","Consumer Staples"}
HYPE_BLACKLIST={"GME","AMC","BB","BBBY","CLOV","EXPR","HOOD","KOSS","NOK",
                "PLTR","RIDE","SDC","SPCE","WISH"}
MEGA_POOL=["AAPL","MSFT","GOOGL","AMZN","META","NVDA","TSLA","NFLX","AVGO","ORCL"]
BUY_COST_PCT=2.0; SELL_COST_PCT=2.0; TAX_RATE=0.20

CFG_ROBUST = {"hold_days":680, "sl_pct":15, "alpha_min":15, "dist_min":-50, "open_cap":4}

print("="*78)
print("HIGH-CAGR v10 FINAL  —  robustisuustesti d=-50 versiolle")
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
tickers=sorted(set(sp500g+sp400g+MEGA_POOL))
tickers=[t for t in tickers if 1<len(t)<=6 and t.replace("-","").isalpha()]
tickers=[t for t in tickers if t not in HYPE_BLACKLIST]

print(f"\n[1/3] Data...")
all_close,all_vol,all_low={},{},{}
for i in range(0,len(tickers+["SPY"]),BATCH_SIZE):
    batch=(tickers+["SPY"])[i:i+BATCH_SIZE]
    raw=yf.download(batch,start=START_DATE,end=END_DATE,auto_adjust=True,progress=False,group_by="column")
    if isinstance(raw.columns,pd.MultiIndex):
        for sym in batch:
            try: all_close[sym]=raw["Close"][sym]; all_vol[sym]=raw["Volume"][sym]; all_low[sym]=raw["Low"][sym]
            except: continue

prices=pd.DataFrame(all_close); prices.dropna(how="all",inplace=True)
prices.index=prices.index.tz_localize(None) if prices.index.tz else prices.index
volume=pd.DataFrame(all_vol).reindex(prices.index).fillna(0)
lows=pd.DataFrame(all_low).reindex(prices.index).ffill()
spy=prices["SPY"].dropna()
avail=[s for s in tickers if s in prices.columns and prices[s].notna().sum()>460]
dolvol=pd.DataFrame(index=prices.index)
for s in avail:
    if s in volume.columns: dolvol[s]=(prices[s]*volume[s]).rolling(20).mean()

print(f"\n[2/3] Alpha + dist...")
ret=prices.pct_change()*100; ir=ret["SPY"]
alpha_df=pd.DataFrame(index=prices.index); dist_df=pd.DataFrame(index=prices.index)
for s in avail:
    r=ret[s]; b=r.rolling(ALPHA_WIN).cov(ir)/ir.rolling(ALPHA_WIN).var()
    alpha_df[s]=r-b*ir
    hi=prices[s].rolling(LOOKBACK).max()
    dist_df[s]=(prices[s]/hi-1)*100

month_ends_all=prices.groupby(prices.index.to_period("M")).apply(lambda g:g.index[-1])

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

def run_sim(cfg, start_m=None, end_m=None, exclude_tickers=None, track=False):
    sl_pct=cfg["sl_pct"]; cap=cfg["open_cap"]; hold_days=cfg["hold_days"]
    dist_min=cfg["dist_min"]; alpha_min=cfg["alpha_min"]
    exclude_tickers=exclude_tickers or set()

    me=month_ends_all.copy()
    if start_m is not None: me=me[me.index>=pd.Period(start_m,"M")]
    if end_m is not None: me=me[me.index<=pd.Period(end_m,"M")]

    cash=0.0; open_pos=[]
    annual_realized={}; carry_fwd=0.0; total_costs=0.0; total_taxes=0.0
    cur_year=None; n_trades=0; trades=[]

    for mi,(month,last_day) in enumerate(me.items()):
        this_year=last_day.year
        if cur_year is not None and this_year>cur_year:
            yr_net=annual_realized.get(cur_year,0)
            if yr_net>0:
                taxable=max(yr_net-carry_fwd,0); carry_fwd=max(carry_fwd-yr_net,0)
                if taxable>0: tax=taxable*TAX_RATE; cash-=tax; total_taxes+=tax
            else: carry_fwd+=-yr_net
        cur_year=this_year
        cash+=MONTHLY_INV
        month_start=month.to_timestamp()

        still=[]
        for pos in open_pos:
            ed, ep_exit, reason=check_exit(pos, last_day, month_start, sl_pct, hold_days)
            if ed is not None:
                gross=pos["shares"]*ep_exit; sc=gross*SELL_COST_PCT/100
                np_=gross-sc; cash+=np_; total_costs+=sc
                realized=np_-pos["invested"]
                yr=ed.year; annual_realized[yr]=annual_realized.get(yr,0)+realized
                if track:
                    trades.append({"ticker":pos["ticker"],"entry_date":pos["entry_date"],
                                  "exit_date":ed,"entry_px":pos["entry_price"],"exit_px":ep_exit,
                                  "invested":pos["invested"],"proceeds":np_,
                                  "ret_pct":(ep_exit/pos["entry_price"]-1)*100,"reason":reason})
            else: still.append(pos)
        open_pos=still

        open_per={}
        for pos in open_pos: open_per[pos["ticker"]]=open_per.get(pos["ticker"],0)+1

        ms=month.to_timestamp()
        mask2=(alpha_df.index>=ms)&(alpha_df.index<=last_day)
        ma=alpha_df.loc[mask2,avail]
        if ma.empty: continue
        had=(ma>alpha_min).any(axis=0); stage=had[had].index.tolist()
        cands=[]
        for sym in stage:
            if sym in exclude_tickers: continue
            adav=dolvol[sym].get(last_day,np.nan) if sym in dolvol.columns else np.nan
            if pd.isna(adav) or adav<MIN_ADAV: continue
            ep=prices[sym].get(last_day,np.nan)
            if pd.isna(ep) or ep<=0: continue
            m=dist_df[sym].get(last_day,np.nan)
            if pd.isna(m) or m>dist_min: continue
            cands.append({"sym":sym,"ep":float(ep),"metric":float(m)})
        cands.sort(key=lambda x:x["metric"])
        if not cands: continue

        chosen=None
        for c in cands:
            if open_per.get(c["sym"],0)<cap: chosen=c; break
        if chosen is None: chosen=cands[0]

        sym=chosen["sym"]; ep=chosen["ep"]
        fs=prices[sym][prices.index>last_day].dropna()
        if len(fs)<400: continue
        fwd_len=min(MAX_HOLD,len(fs))
        if cash>0:
            invested=cash; bc=invested*BUY_COST_PCT/100; nb=invested-bc
            shares=nb/ep; total_costs+=bc
            open_pos.append({"ticker":sym,"entry_date":last_day,"entry_price":ep,"shares":shares,
                             "invested":invested,"fwd_prices":fs.iloc[:fwd_len].values,"fwd_dates":fs.index[:fwd_len]})
            cash=0.0; n_trades+=1

    if cur_year is not None:
        yr_net=annual_realized.get(cur_year,0)
        if yr_net>0:
            taxable=max(yr_net-carry_fwd,0)
            if taxable>0: tax=taxable*TAX_RATE; cash-=tax; total_taxes+=tax

    final=cash; last_date=me.iloc[-1]; fr=0
    for pos in open_pos:
        mask=pos["fwd_dates"]<=last_date; day_idx=int(mask.sum())-1
        px=pos["fwd_prices"][min(day_idx,len(pos["fwd_prices"])-1)] if day_idx>=0 else pos["entry_price"]
        gross=pos["shares"]*px; sc=gross*SELL_COST_PCT/100
        net=gross-sc; total_costs+=sc; final+=net; fr+=net-pos["invested"]
        if track:
            trades.append({"ticker":pos["ticker"],"entry_date":pos["entry_date"],"exit_date":last_date,
                          "entry_px":pos["entry_price"],"exit_px":px,"invested":pos["invested"],
                          "proceeds":net,"ret_pct":(px/pos["entry_price"]-1)*100,"reason":"OPEN"})
    if fr>0:
        taxable=max(fr-carry_fwd,0)
        if taxable>0: tax=taxable*TAX_RATE; final-=tax; total_taxes+=tax

    months_count=len(me)
    total_saved=months_count*MONTHLY_INV
    n_yr_loc=months_count/12 if months_count>0 else 1
    cagr=(final/total_saved)**(1/n_yr_loc)-1 if total_saved>0 else 0
    return final, cagr, n_trades, trades

print(f"\n[3/3] Analyysi...\n")
total_saved=len(month_ends_all)*MONTHLY_INV
n_yr=len(month_ends_all)/12

# Phase 1: Listaa kaupat
print("--- PHASE 1: Kaikki 17 kauppaa ---")
final, cagr, n, trades = run_sim(CFG_ROBUST, track=True)
print(f"   BASE (d=-50): {final:,.0f}e / CAGR {cagr*100:.2f}% / N={n}\n")
trades_sorted=sorted(trades, key=lambda x:-x["ret_pct"])
print(f"   Kaupat tuotto-jarjestyksessa:")
print(f"   {'#':>2}  {'Ticker':<7} {'Entry':>12} {'Exit':>12} {'Days':>5} {'Ret%':>10} {'Reason':>8}")
for i,t in enumerate(trades_sorted,1):
    d=(t["exit_date"]-t["entry_date"]).days
    print(f"   {i:>2}  {t['ticker']:<7} {t['entry_date'].strftime('%Y-%m-%d'):>12} "
          f"{t['exit_date'].strftime('%Y-%m-%d'):>12} {d:>5} {t['ret_pct']:>+9.1f}% {t['reason']:>8}")

# Phase 2: Remove top N
print("\n--- PHASE 2: Top-N poistettu ---")
trade_impact=[]
for t in trades:
    impact=t["proceeds"]-t["invested"]
    trade_impact.append({"ticker":t["ticker"],"entry":t["entry_date"],"impact":impact})
trade_impact_sorted=sorted(trade_impact,key=lambda x:-x["impact"])

for n_exclude in [0,1,2,3,5,7]:
    exc=set()
    for i in range(n_exclude):
        if i<len(trade_impact_sorted): exc.add(trade_impact_sorted[i]["ticker"])
    f,c,nt,_=run_sim(CFG_ROBUST, exclude_tickers=exc)
    exc_str=','.join(list(exc)[:4])+('...' if len(exc)>4 else '')
    print(f"   Poista top {n_exclude} ({exc_str}): {f:>13,.0f}e / CAGR {c*100:>5.1f}% / N={nt}")

# Phase 3: Walk-forward
print("\n--- PHASE 3: Walk-forward ---")
f_tr,c_tr,n_tr,_=run_sim(CFG_ROBUST, start_m="2015-01", end_m="2020-12")
f_te,c_te,n_te,_=run_sim(CFG_ROBUST, start_m="2021-01", end_m="2026-04")
f_full,c_full,n_full,_=run_sim(CFG_ROBUST)
print(f"   Train 2015-2020: {f_tr:>12,.0f}e  CAGR {c_tr*100:>5.1f}%  N={n_tr}")
print(f"   Test  2021-2026: {f_te:>12,.0f}e  CAGR {c_te*100:>5.1f}%  N={n_te}")
print(f"   Full 2015-2026:  {f_full:>12,.0f}e  CAGR {c_full*100:>5.1f}%  N={n_full}")

# Phase 4: Vuosittainen
print("\n--- PHASE 4: Vuosittainen tuotto (exit-vuoden mukaan) ---")
df=pd.DataFrame(trades)
df["year_exit"]=pd.to_datetime(df["exit_date"]).dt.year
for yr in sorted(df["year_exit"].unique()):
    g=df[df["year_exit"]==yr]
    total_inv=g["invested"].sum()
    total_pr=g["proceeds"].sum()
    avg_ret=g["ret_pct"].mean()
    print(f"   {yr}: n={len(g):2d}  inv={total_inv:>10,.0f}e  pr={total_pr:>12,.0f}e  net={total_pr-total_inv:>+12,.0f}e  avg={avg_ret:+.1f}%")

# Phase 5: Osakkeiden esiintyminen
print("\n--- PHASE 5: Osakkeet jotka valittiin ---")
tc=df.groupby("ticker").agg(n=("ret_pct","count"),avg=("ret_pct","mean"),
                            sum_ret=("proceeds",lambda x:x.sum()-df.loc[x.index,"invested"].sum())).sort_values("n",ascending=False)
print(f"   {'Ticker':<8} {'N':>3}  {'Avg%':>8}  {'Net Profit':>12}")
for t, row in tc.iterrows():
    print(f"   {t:<8} {int(row['n']):>3}  {row['avg']:>+7.1f}%  {row['sum_ret']:>+11,.0f}e")

# Save
trades_df=pd.DataFrame(trades)
trades_df.to_csv(r"C:\Users\puros\final_robust_trades.csv",index=False)

print(f"\n\n"+"="*78)
print("V10 FINAL YHTEENVETO")
print("="*78)
print(f"""
STRATEGIA: Deep-50 robusti
  Config: hold=680 SL=15 alpha>=15 dist<-50 cap=4
  Universumi: S&P 500+400 kasvusektorit + MEGA10, ei hype-listaa
  Kulut: 2% per osto + 2% per myynti
  Vero: 20% vuotuiset voitot (Suomi oy)

TULOKSET:
  Loppuarvo: {final:,.0f}e
  CAGR: {cagr*100:.2f}%
  Kauppoja: {n}
  Sijoitettu: {total_saved:,.0f}e

VERTAILU:
  v1 BASE (SL20 cap=4, alpha>12): CAGR 39.0%
  v4 Deep-70 h=600:                CAGR 50.6%
  v6 h=650 SL=15 a>15:             CAGR 76.1%
  v7 h=680 SL=15 a>15 d=-70:       CAGR 82.1% (vain 9 kauppaa)
  TAMA (d=-50):                     CAGR 81.4% (17 kauppaa - robustimpi!)
""")
print("="*78)
