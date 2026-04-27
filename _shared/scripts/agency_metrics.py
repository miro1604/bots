# -*- coding: utf-8 -*-
r"""agency_metrics.py — AgencyBench-light + SkillFlow per-bot mittari.

PDF1: agentin autonomisen suorituskyvyn mittaaminen tuotantotason
työkuormalla. Yksinkertaistettu versio kotikehityksen tasolle.

Mittarit per-bot:
  - context_tokens_per_cycle (käytetyt promptit + vastaukset)
  - tool_calls_per_cycle  (subprocess-kutsut)
  - completion_rate       (cycle_done / total_cycles)
  - cognitive_compactness (strategies/learnings ratio)
  - skill_inflation       (kuinka monta uutta skripti/strategia per päivä)

Ajetaan daemon-cyclellä, kirjoittaa _shared/memory/agency_metrics.jsonl
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BOTS_ROOT = Path(__file__).resolve().parents[2]
WATCHDOG = BOTS_ROOT / "_shared" / "memory" / "watchdog"
QUOTA_LOG = BOTS_ROOT / "_shared" / "memory" / "quota_log.jsonl"
METRICS_LOG = BOTS_ROOT / "_shared" / "memory" / "agency_metrics.jsonl"


ALL_BOTS = ["alphahunter", "finance", "videoempire", "leadgen",
            "rd-coworker", "design", "convovault", "crypto-finance", "betting"]


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def get_completion_rate(bot: str, lookback_hours: int = 24) -> dict:
    """Lue heartbeat-historiasta cycle_done vs cycle_failed -suhdetta."""
    hb_path = WATCHDOG / f"{bot}_heartbeat.json"
    if not hb_path.exists():
        return {"completion_rate": None, "cycles": 0}
    # Tämä on yksinkertainen — tarkempi versio lukisi heartbeat-historian
    try:
        d = json.loads(hb_path.read_text(encoding="utf-8"))
        return {
            "current_state": d.get("state"),
            "cycle_count": d.get("cycle", 0),
            "outcome": d.get("outcome", "unknown"),
        }
    except Exception:
        return {"completion_rate": None, "cycles": 0}


def get_context_tokens(bot: str, lookback_hours: int = 24) -> dict:
    """Lue quota_log:sta käytetyt promptit + vastaukset."""
    rows = _read_jsonl(QUOTA_LOG)
    cutoff = datetime.now(timezone.utc).timestamp() - lookback_hours * 3600
    total_prompt = 0
    total_response = 0
    n_calls = 0
    for r in rows:
        if not r.get("bot", "").startswith(bot):
            continue
        ts = r.get("ts", "")
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if t < cutoff:
            continue
        total_prompt += r.get("prompt_chars", 0)
        total_response += r.get("response_chars", 0)
        n_calls += 1
    # Heuristic: 1 token ~ 4 chars
    return {
        "tokens_in": total_prompt // 4,
        "tokens_out": total_response // 4,
        "calls": n_calls,
    }


def get_cognitive_compactness(bot: str) -> dict:
    """Mittaa strategies vs learnings -suhdetta. Pieni = paisuneet säännöt."""
    candidates = [
        BOTS_ROOT / bot / "knowledge" / "strategies.jsonl",
        BOTS_ROOT / bot / "research" / "learnings.jsonl",
        BOTS_ROOT / bot / "knowledge" / "learnings.jsonl",
    ]
    strategies = 0
    learnings = 0
    for p in candidates:
        if not p.exists():
            continue
        rows = _read_jsonl(p)
        if "strategies" in p.name:
            strategies += len(rows)
        else:
            learnings += len(rows)
    if learnings == 0:
        return {"strategies": strategies, "learnings": 0,
                "compactness_ratio": None}
    # Compactness = learnings_jotka_johtaneet_strategioihin / total_learnings
    return {
        "strategies": strategies,
        "learnings": learnings,
        "compactness_ratio": strategies / max(1, learnings),
    }


def compute_agency_score(bot: str) -> dict:
    """Yhdistä kaikki mittarit yhdeksi agency-pisteeksi (0-100)."""
    completion = get_completion_rate(bot)
    tokens = get_context_tokens(bot)
    compact = get_cognitive_compactness(bot)

    cycles = completion.get("cycle_count", 0)
    calls = tokens.get("calls", 0)
    compactness = compact.get("compactness_ratio") or 0.0

    # Empiirinen scoring (PDF1: cycle-määrä + tool-call-density + compactness)
    score = 0.0
    score += min(40, cycles * 0.5)        # max 40 pts cycle-määrästä
    score += min(30, calls * 0.5)          # max 30 pts tool-callien määrästä
    score += min(30, compactness * 30)     # max 30 pts compactness:sta

    return {
        "bot": bot,
        "ts": datetime.now(timezone.utc).isoformat(),
        "score": round(score, 2),
        "cycles": cycles,
        "tokens_in_24h": tokens.get("tokens_in", 0),
        "tokens_out_24h": tokens.get("tokens_out", 0),
        "calls_24h": calls,
        "strategies": compact.get("strategies", 0),
        "learnings": compact.get("learnings", 0),
        "compactness_ratio": compactness,
    }


def main():
    METRICS_LOG.parent.mkdir(parents=True, exist_ok=True)
    summary = []
    for bot in ALL_BOTS:
        try:
            m = compute_agency_score(bot)
        except Exception as e:
            m = {"bot": bot, "error": str(e)[:200]}
        summary.append(m)
        with open(METRICS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    # Tulosta yhteenveto
    print("=== AgencyBench-light per-bot ===")
    for m in summary:
        if "error" in m:
            print(f"  {m['bot']}: ERROR {m['error'][:80]}")
        else:
            print(f"  {m['bot']:18s} score={m.get('score',0):5.1f}  "
                  f"cycles={m.get('cycles',0):3d}  "
                  f"calls/24h={m.get('calls_24h',0):4d}  "
                  f"compactness={m.get('compactness_ratio',0):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
