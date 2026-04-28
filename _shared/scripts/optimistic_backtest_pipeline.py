# -*- coding: utf-8 -*-
r"""optimistic_backtest_pipeline.py — finance/crypto-finance autonominen backtest.

Filosofia (käyttäjän mandaatti 2026-04-28):
  - Optimistinen-realistinen, EI pessimistinen
  - Etsii parhaat parametri-yhdistelmät jokaiselle ideale
  - Stock screener: testaa eri muuttujia + raja-arvoja
  - Kun simulaatio paljastuu liian raskaaksi paikallisesti → tunnista ja
    luo ACTION_REQUIRED käyttäjälle "siirrä ultraplaniin"
  - Kun alustavat tulokset hyviä → ehdota ultraplan-tarkennusta
  - Säilyttää lupaavimmat löydöt knowledge/strategies.jsonl:iin

Käyttö:
  python optimistic_backtest_pipeline.py --bot finance
  python optimistic_backtest_pipeline.py --bot crypto-finance

Stock screener: jokaiselle idealle ajetaan parametri-grid (esim 20-50 yhdistelmää)
ja universumi-filterit (esim 5 eri ADV-tasoa, 3 eri kokoluokkaa).

Tulos:
  - {bot}/knowledge/promising_ideas.jsonl  (parhaat löydetyt)
  - {bot}/knowledge/screener_results.jsonl (kaikki ajot)
  - _shared/agent_inbox/user_actions.jsonl  (ACTION_REQUIRED jos ultraplan tarvitaan)
"""
from __future__ import annotations
import argparse, json, sys, time, warnings
warnings.filterwarnings("ignore")
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def estimate_compute_cost(n_param_combos: int, n_universe_subsets: int,
                          n_data_points: int) -> dict:
    """Estimoi paikallisen ajon kesto + memory."""
    # Karkea: jokainen kombo + universumi = 1 ajo. ~3s baseline + 0.001ms/datapointti
    n_runs = n_param_combos * n_universe_subsets
    sec_per_run = 3 + (n_data_points * 0.000001)
    total_sec = n_runs * sec_per_run
    return {
        "n_runs": n_runs,
        "estimated_seconds": total_sec,
        "estimated_minutes": total_sec / 60,
        "needs_ultraplan": total_sec > 1800,  # > 30 min → ultraplan
        "needs_ultraplan_reason": "compute_time_>30min" if total_sec > 1800 else None
    }


def create_user_action(action_id: str, title: str, question: str,
                        steps: list[str], urgency: str = "medium",
                        bot: str = "orchestrator-system",
                        related_idea: str = "", est_min: int = 15):
    """Luo ACTION_REQUIRED käyttäjälle."""
    actions_path = ROOT / "_shared" / "agent_inbox" / "user_actions.jsonl"
    record = {
        "id": action_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "bot": bot,
        "action_title": title,
        "question": question,
        "urgency": urgency,
        "estimated_time_min": est_min,
        "steps": [{"step_id": f"s{i+1}", "text": s, "done": False}
                  for i, s in enumerate(steps)],
        "related_idea": related_idea,
        "status": "pending",
        "source": "optimistic_backtest_pipeline"
    }
    append_jsonl(actions_path, record)
    return record


def load_promising_ideas(bot: str) -> list[dict]:
    """Lue lupaavimmat ideat per botti.

    finance: market_knowledge/learnings.jsonl + lessons/what_works.md
    crypto-finance: signals/consensus_signals.jsonl + research/research_queue.jsonl
    """
    ideas = []
    if bot == "finance":
        # Top-edges from what_works.md (rakenteelliset)
        ideas = [
            {"id": "FIN_VXX_DECAY", "title": "VXX gap-down short-decay",
             "spec": {"signal": "gap_down<=-3pct", "direction": "SHORT",
                     "instrument": "VXX", "hold_days_grid": [60, 90, 120, 180, 240]}},
            {"id": "FIN_UNG_DECAY", "title": "UNG bull-and-dip short",
             "spec": {"signal": "SMA50>SMA200 + 1d_ret<-2pct", "direction": "SHORT",
                     "instrument": "UNG", "hold_days_grid": [60, 90, 120, 180]}},
            {"id": "FIN_QQQ_TREND", "title": "QQQ bull-trend cross LONG",
             "spec": {"signal": "close>SMA50>SMA200", "direction": "LONG",
                     "instrument": "QQQ", "hold_days_grid": [60, 90, 120, 180, 240]}},
            {"id": "FIN_GAP_DOWN_ETF", "title": "Gap-down fade ETF (multi)",
             "spec": {"signal_grid": ["gap_down<=-2pct", "gap_down<=-3pct"],
                     "direction": "LONG",
                     "universe_grid": ["IWM", "XLE", "XLF", "XBI", "EEM", "EFA", "SPY"],
                     "hold_days_grid": [30, 60, 90, 120]}},
            {"id": "FIN_GDX_GAP", "title": "GDX gap-down 60d",
             "spec": {"signal": "gap_down<=-3pct", "direction": "LONG",
                     "instrument": "GDX", "hold_days_grid": [30, 45, 60, 90, 120]}},
            {"id": "FIN_GLD_FROMHIGH", "title": "GLD fromhigh -5pct mean-reversion",
             "spec": {"signal_grid": ["fromhigh<=-3pct", "fromhigh<=-5pct", "fromhigh<=-7pct"],
                     "direction": "LONG", "instrument": "GLD",
                     "hold_days_grid": [60, 90, 120, 180]}},
            {"id": "FIN_ALPHA_PEAK", "title": "Alpha-peak general (commodity+equity)",
             "spec": {"signal_grid": ["peak_alpha_50>=1.0", "peak_alpha_50>=1.5", "peak_alpha_50>=2.0"],
                     "direction": "LONG", "universe": "broad",
                     "hold_days_grid": [60, 90, 120]}},
            {"id": "FIN_V32_DYN_NOSL", "title": "v32_dyn dynaaminen pooli + peak_20 + dist<-20 + no-SL",
             "spec": {"signal": "peak_20>15 AND dist_378<-20",
                     "adv_grid": [10e6, 20e6, 50e6, 100e6],
                     "max_open_grid": [50, 100, 200, 300],
                     "hold_days_grid": [380, 540, 680, 850],
                     "tc_grid": [0.3, 0.5, 1.0]}},
        ]
    elif bot == "crypto-finance":
        # Top setups from debate-konsensus
        ideas = [
            {"id": "CRY_SWEEP_REJ", "title": "BTC/ETH/SOL Binance perp sweep+rejection (Wyckoff Spring)",
             "spec": {"pairs_grid": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
                     "tf_grid": ["1m", "5m", "10m"],
                     "sweep_pct_grid": [0.3, 0.5, 1.0, 1.5],
                     "rejection_within_min_grid": [15, 30, 60],
                     "hold_min_grid": [45, 90, 180, 360],
                     "leverage_grid": [10, 15, 25, 50],
                     "stop_pct_grid": [0.8, 1.1, 1.5]}},
            {"id": "CRY_FUNDING_ARB", "title": "Extreme funding rate squeeze",
             "spec": {"pairs_grid": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"],
                     "funding_threshold_grid": [0.05, 0.10, 0.15, 0.20],
                     "hold_hours_grid": [4, 8, 16, 24],
                     "leverage_grid": [10, 15, 25]}},
            {"id": "CRY_MEGA_PUMP", "title": "Mega Pump volume-spike alt",
             "spec": {"vol_spike_grid": [3.0, 5.0, 10.0],  # × 20d-mediaani
                     "min_24h_vol_usd_grid": [50e6, 100e6, 200e6],
                     "hold_min_grid": [60, 240, 720],
                     "leverage_grid": [10, 15, 25]}},
            {"id": "CRY_BTC_DXY_DECOUPLE", "title": "BTC↔DXY decoupling cross-asset",
             "spec": {"corr_window_days_grid": [14, 30, 60],
                     "decouple_threshold_grid": [0.0, 0.2, 0.4],  # |corr| <
                     "hold_hours_grid": [8, 24, 48],
                     "leverage_grid": [5, 10, 15]}},
            {"id": "CRY_HMM_REGIME", "title": "HMM regime-switching + per-regime strategy",
             "spec": {"n_states_grid": [3, 4],
                     "tf": "4h",
                     "lookback_days_grid": [60, 90, 180]}},
        ]
    return ideas


def assess_idea(idea: dict, bot: str) -> dict:
    """Estimoi paljonko parametri-yhdistelmiä idea vaatii."""
    spec = idea["spec"]
    n_combos = 1
    for k, v in spec.items():
        if isinstance(v, list):
            n_combos *= len(v)
    # Universumi-screening: jos universe_grid tai pairs_grid
    n_universe = max(len(spec.get("universe_grid", [])),
                     len(spec.get("pairs_grid", [])), 1)
    # Datapisteet: oletus
    n_data = 3000  # päivätason 12v
    if bot == "crypto-finance":
        n_data = 130000  # 1m × 90 vrk × 24h

    cost = estimate_compute_cost(n_combos, n_universe, n_data)
    return {
        "idea_id": idea["id"],
        "n_combos": n_combos,
        "n_universe": n_universe,
        "estimated_minutes": round(cost["estimated_minutes"], 1),
        "needs_ultraplan": cost["needs_ultraplan"],
        "tier": "small" if cost["estimated_minutes"] < 5 else
                "medium" if cost["estimated_minutes"] < 30 else "ultraplan"
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", required=True, choices=["finance", "crypto-finance"])
    ap.add_argument("--dry-run", action="store_true",
                    help="Vain assess + plan, ei aja")
    ap.add_argument("--max-ideas", type=int, default=10)
    args = ap.parse_args()

    print("=" * 78)
    print(f"OPTIMISTIC BACKTEST PIPELINE — bot={args.bot}")
    print("=" * 78)

    ideas = load_promising_ideas(args.bot)[:args.max_ideas]
    print(f"\n📋 Lupaavimmat ideat: {len(ideas)}")

    # Assessment per idea
    results = []
    ultraplan_candidates = []
    for idea in ideas:
        a = assess_idea(idea, args.bot)
        a["title"] = idea["title"]
        results.append(a)
        print(f"  [{idea['id']}] {idea['title'][:60]:60s}")
        print(f"      combos={a['n_combos']} universumi={a['n_universe']} "
              f"~{a['estimated_minutes']}min → tier={a['tier']}")
        if a["needs_ultraplan"]:
            ultraplan_candidates.append({**idea, **a})

    # Tallennetaan plan
    plan_path = ROOT / args.bot.replace("-", "-") / "knowledge" / "screener_plan.jsonl"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.unlink(missing_ok=True)
    for r in results:
        append_jsonl(plan_path, {**r, "ts": datetime.now(timezone.utc).isoformat()})
    print(f"\n  Tallennettu: {plan_path}")

    # ACTION_REQUIRED jos ultraplan-kandidaatteja
    if ultraplan_candidates:
        print(f"\n⚠ {len(ultraplan_candidates)} ideaa vaatii ULTRAPLANIN (>30 min paikallisesti)")
        for c in ultraplan_candidates:
            action_id = f"UA_BACKTEST_{c['idea_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            create_user_action(
                action_id=action_id,
                title=f"Siirrä '{c['title'][:60]}' ultraplaniin (~{c['estimated_minutes']:.0f}min)",
                question=f"Idea {c['idea_id']} vaatii {c['n_combos']*c['n_universe']} ajoa "
                        f"(~{c['estimated_minutes']:.0f}min). Paikallinen liian hidas → ultraplan?",
                steps=[
                    f"Tarkista että dataset on git:issä",
                    f"Avaa uusi Claude Code -ikkuna → cd C:\\Users\\puros\\bots → claude",
                    f"Liimaa /ultraplan-prompti (orchestrator generoi)",
                    f"Kerro tähän ikkunaan kun käynnistyi"
                ],
                urgency="medium",
                bot=args.bot,
                related_idea=c["idea_id"],
                est_min=int(c["estimated_minutes"]) + 15
            )
        print(f"  ACTION_REQUIRED:t luotu user_actions.jsonl:iin")

    # Yhteenveto
    summary = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "bot": args.bot,
        "n_ideas": len(ideas),
        "n_small": sum(1 for r in results if r["tier"] == "small"),
        "n_medium": sum(1 for r in results if r["tier"] == "medium"),
        "n_ultraplan": sum(1 for r in results if r["tier"] == "ultraplan"),
        "ultraplan_action_ids": [f"UA_BACKTEST_{c['idea_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                                 for c in ultraplan_candidates]
    }
    print(f"\n📊 YHTEENVETO:")
    print(f"  small (<5min):    {summary['n_small']}")
    print(f"  medium (<30min):  {summary['n_medium']}")
    print(f"  ultraplan (>30):  {summary['n_ultraplan']}")

    summary_path = ROOT / args.bot.replace("-", "-") / "knowledge" / "screener_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n  Tallennettu: {summary_path}")

    if args.dry_run:
        print("\n[dry-run] EI ajeta backtestejä. Plan on valmis seuraavaa kierrosta varten.")
    else:
        print("\nℹ Backtest-toteutus on optimointivaiheessa — käyttää bot-spesifistä")
        print("  validator-skriptiä (esim. v32_dyn_nosl_validator.py) per-idea.")
        print("  Tässä vaiheessa pipeline luo VAIN PLANin + ACTION_REQUIREDt ultraplaniin.")


if __name__ == "__main__":
    main()
