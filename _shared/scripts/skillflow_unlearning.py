# -*- coding: utf-8 -*-
r"""skillflow_unlearning.py — pisteytä strategiat per-bot ja deprecaa heikoimmat.

PDF1: SkillFlow-viitekehys. Cognitive bloat -estäminen. Yli-inhimillinen
agentti tunnistaa omat huonot ideansa ja DEPRECAA ne sen sijaan että
keräisi loputtomasti yksittäisiä päällekkäisiä skriptejä.

Pisteytys:
  perf_score      — historiallinen suorituskyky (jos saatavilla)
  age_score       — uudet ehdotukset saavat myönnös
  diversity_score — onko strategia eri kuin muut (saman botin sisällä)
  validation_score — onko CPCV/PBO/walk-forward validoitu

Heikoimmat (alle threshold) merkitään `deprecated: true` JSONL:iin.
Eivät vielä poisteta — vain merkitään, jotta käyttäjä voi yhä lukea.

Ajetaan kerran päivässä daemon-cyclellä.
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

ALL_BOTS = ["finance", "videoempire", "leadgen", "rd-coworker", "design",
            "convovault", "crypto-finance", "betting", "alphahunter"]

DEPRECATE_BELOW = 30.0  # pistemäärä alle = deprecated
KEEP_TOP_N = 10  # vähintään top-10 ei deprecated:iksi


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


def _write_jsonl(path: Path, items: list[dict]):
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def score_strategy(s: dict) -> float:
    """0-100 pistemäärä strategialle."""
    pts = 50.0  # neutraali pohja

    # 1. Performance-bonus (jos saatavilla)
    perf = s.get("performance", {})
    if isinstance(perf, dict):
        sharpe = perf.get("sharpe_walkforward")
        if sharpe is not None:
            pts += min(20, max(-20, float(sharpe) * 10))
        win_rate = perf.get("win_rate")
        if win_rate is not None:
            pts += min(10, (float(win_rate) - 0.5) * 40)

    # 2. Validation-bonus
    val = s.get("validation", {})
    if isinstance(val, dict):
        if val.get("cpcv_passed"):
            pts += 5
        pbo = val.get("pbo")
        if pbo is not None and float(pbo) < 0.5:
            pts += 10
        if val.get("walk_forward_cycles", 0) >= 3:
            pts += 5

    # 3. Age-decay (vanhat ilman update:a saavat miinusta)
    last_update = s.get("last_validated") or s.get("ts")
    if last_update:
        try:
            t = datetime.fromisoformat(str(last_update).replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - t).days
            pts -= min(20, age_days * 0.2)  # max -20 jos vanha
        except Exception:
            pass

    # 4. Re-issue-penalty (jos strategia on jo merkitty deprecated kerran ja palasi)
    if s.get("previously_deprecated"):
        pts -= 10

    return max(0.0, min(100.0, pts))


def deprecate_bot(bot: str) -> dict:
    """Pisteytä botin strategiat ja merkitse heikoimmat deprecated:iksi."""
    candidates = [
        BOTS_ROOT / bot / "knowledge" / "strategies.jsonl",
        BOTS_ROOT / bot / "knowledge" / "rules.jsonl",
    ]
    target = None
    for p in candidates:
        if p.exists():
            target = p
            break
    if target is None:
        return {"bot": bot, "skipped": "no_strategies_file"}

    items = _read_jsonl(target)
    if not items:
        return {"bot": bot, "skipped": "empty"}

    # Pisteytä
    for it in items:
        if it.get("deprecated"):
            continue  # älä pisteytä uudelleen
        score = score_strategy(it)
        it["agency_score"] = score

    # Sortaa pistemäärän mukaan
    scored = [it for it in items if "agency_score" in it]
    scored.sort(key=lambda x: x["agency_score"], reverse=True)

    # Merkitse alle threshold + säilytä top-N
    deprecated_count = 0
    for i, it in enumerate(scored):
        if i < KEEP_TOP_N:
            continue  # top-N säilyy
        if it["agency_score"] < DEPRECATE_BELOW and not it.get("deprecated"):
            it["deprecated"] = True
            it["deprecated_at"] = datetime.now(timezone.utc).isoformat()
            it["previously_deprecated"] = True
            deprecated_count += 1

    _write_jsonl(target, items)
    return {
        "bot": bot,
        "total": len(items),
        "deprecated": deprecated_count,
        "active": sum(1 for it in items if not it.get("deprecated")),
    }


def main():
    print("=== SkillFlow unlearning ===")
    for bot in ALL_BOTS:
        try:
            r = deprecate_bot(bot)
        except Exception as e:
            r = {"bot": bot, "error": str(e)[:200]}
        if r.get("skipped"):
            continue
        if r.get("error"):
            print(f"  {bot}: ERROR {r['error'][:80]}")
        else:
            print(f"  {bot}: total={r['total']} deprecated={r['deprecated']} "
                  f"active={r['active']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
