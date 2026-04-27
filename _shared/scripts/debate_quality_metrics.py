# -*- coding: utf-8 -*-
r"""debate_quality_metrics.py — multi-agent debate -laadun mittaus.

Toteuttaa self-research-tutkimuksen 3 high-prio-implementaatiota
(2026-04-26 multi_agent_debate_architectures):
  1. Position-drift-mittaus per kierros (KL-divergenssi confidence-vektoreille)
  2. Persona-diversiteetti-assertio (cosine similarity check)
  3. Täydellinen transkripti judge-arvioon (helper)

Käytetään KAIKKIEN bot-debate-orkestraattorien (finance, leadgen, videoempire,
design, rd-coworker, crypto-finance, betting) yhteisenä laatutyökaluna.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np


# ---------------------------------------------------------------------------
# Position-drift (KL-divergenssi)
# ---------------------------------------------------------------------------

def parse_confidence_from_response(text: str) -> float | None:
    """Etsi 'CONFIDENCE: X' tai 'POSITION_CONFIDENCE: X' rivi vastauksesta."""
    if not text:
        return None
    m = re.search(r"(?:POSITION_)?CONFIDENCE\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)",
                  text, re.IGNORECASE)
    if not m:
        return None
    try:
        v = float(m.group(1))
        return max(0.0, min(10.0, v)) / 10.0  # normalisoi 0-1
    except Exception:
        return None


def kl_divergence(p: np.ndarray, q: np.ndarray, eps: float = 1e-9) -> float:
    """Kullback-Leibler-divergenssi D_KL(P || Q)."""
    p = np.asarray(p, dtype=float) + eps
    q = np.asarray(q, dtype=float) + eps
    p /= p.sum()
    q /= q.sum()
    return float(np.sum(p * np.log(p / q)))


@dataclass
class PositionDriftResult:
    rounds: list[dict] = field(default_factory=list)
    total_drift_kl: float = 0.0
    converged: bool = False
    quality_label: str = ""  # "good", "echo_chamber", "diverging", "unstable"


def measure_position_drift(rounds: list[list[float]]) -> PositionDriftResult:
    """Mittaa per-kierros KL-divergenssi confidence-vektoreille.

    Args:
        rounds: lista per-kierros confidence-vektoreita,
                esim. [[0.7, 0.5, 0.8], [0.6, 0.6, 0.75], [0.65, 0.62, 0.74]]

    Returns:
        PositionDriftResult with kl-trajectory + label.
    """
    out = PositionDriftResult()
    if len(rounds) < 2:
        out.quality_label = "insufficient_rounds"
        return out

    prev = np.asarray(rounds[0], dtype=float)
    total = 0.0
    for i, current_raw in enumerate(rounds[1:], 1):
        current = np.asarray(current_raw, dtype=float)
        if len(current) != len(prev):
            continue
        kl = kl_divergence(prev, current)
        total += kl
        out.rounds.append({"round": i, "kl_from_prev": kl,
                           "vector": current.tolist()})
        prev = current

    out.total_drift_kl = total
    # Heuristinen luokittelu
    if total < 0.005:
        out.quality_label = "echo_chamber"  # kukaan ei muuttanut kantaansa
    elif total < 0.05:
        out.quality_label = "good"  # tervettä konvergenssia
    elif total < 0.2:
        out.quality_label = "diverging"  # paljon erimielisyyttä, mutta keskustelua
    else:
        out.quality_label = "unstable"  # epävakaa, ei konvergenssia

    out.converged = (out.quality_label in ("good",))
    return out


# ---------------------------------------------------------------------------
# Persona-diversiteetti (cosine similarity check)
# ---------------------------------------------------------------------------

def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    av = np.asarray(a, dtype=float)
    bv = np.asarray(b, dtype=float)
    na = np.linalg.norm(av)
    nb = np.linalg.norm(bv)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(av, bv) / (na * nb))


def check_persona_diversity(opening_positions: list[list[float]],
                              threshold: float = 0.92) -> dict:
    """Tarkista että avauspositiot ovat riittävän diversit.

    Args:
        opening_positions: lista per-persona avausta (pisteet/vektorit).
            Voi olla esim. [confidence, sentiment, riskiprofiili].

    Returns:
        {'diverse': bool, 'collisions': [(i, j, sim), ...]}
    """
    n = len(opening_positions)
    collisions = []
    for i in range(n):
        for j in range(i + 1, n):
            sim = cosine_similarity(opening_positions[i], opening_positions[j])
            if sim > threshold:
                collisions.append((i, j, sim))
    return {"diverse": len(collisions) == 0,
            "collisions": collisions,
            "total_pairs": n * (n - 1) // 2}


# ---------------------------------------------------------------------------
# Judge — täydellinen transkripti -arviointi (helper)
# ---------------------------------------------------------------------------

def build_judge_prompt_full_transcript(
    persona_responses: list[dict],
    synthesis: str,
    bot_context: str = "",
) -> str:
    """Generoi judge-prompt joka sisältää KOKO debattitranskriptin.

    PDF1: ChatEval-tutkimus +15-20% human-agreement vs pelkkä loppupositio.

    Args:
        persona_responses: [{slug, name, raw_response, confidence}, ...]
        synthesis: orkestraattorin tuottama synteesi
        bot_context: lisäkonteksti (esim. botin tehtävä)
    """
    persona_blocks = []
    for i, p in enumerate(persona_responses):
        block = f"""
<persona_{i+1}>
  <slug>{p.get('slug', '?')}</slug>
  <name>{p.get('name', '?')}</name>
  <confidence>{p.get('confidence', 'N/A')}</confidence>
  <full_response>
{p.get('raw_response', '')}
  </full_response>
</persona_{i+1}>
"""
        persona_blocks.append(block.strip())

    transcript = "\n\n".join(persona_blocks)

    return f"""Sinä OLET vain analyysi-/judge-agentti joka tuottaa TEKSTIVASTAUKSEN.
ÄLÄ KÄYTÄ työkaluja (Read, Edit, Bash, Write). ÄLÄ pyydä kirjoituslupaa.

Olet judge-tuomari multi-agent-debaten laadulle. Sinun annetaan KOKO
debate-transkripti, ei vain loppupositiot. Tämä on tutkitusti tärkeää
(ChatEval, Chan et al. 2023): +15-20% human-agreement-korrelaatio.

# Botin konteksti
{bot_context}

# Personien täydet vastaukset
{transcript}

# Orkestraattorin synteesi
{synthesis}

# Tehtäväsi

Arvioi debaten LAATU (ei vain lopputulos vaan PROSESSI):

1. **Diversity score (0-10)**: kuinka erilaisia olivat avauspositiot?
2. **Engagement score (0-10)**: vastasivatko personat toistensa argumentteihin
   vai esittivätkö rinnakkaisia monologeja?
3. **Position drift (0-10)**: muuttiko joku kantaansa toisten argumenttien pohjalta?
4. **Synteesin laatu (0-10)**: edustaako synteesi aitoa konvergenssia vai
   pelkkää keskiarvoistamista?
5. **Faktatarkkuus (0-10)**: kuinka monta tarkistettavissa olevaa väitettä
   esitettiin? Olivatko ne paikkansapitäviä?

Vastaa TÄSMÄLLEEN tällä rakenteella, plain text, aloita rivillä DIVERSITY:

DIVERSITY: <0-10>
ENGAGEMENT: <0-10>
DRIFT: <0-10>
SYNTHESIS_QUALITY: <0-10>
FACT_ACCURACY: <0-10>
OVERALL_QUALITY: <0-10>
KEY_STRENGTH: <yhden lauseen kuvaus debaten parhaasta puolesta>
KEY_WEAKNESS: <yhden lauseen kuvaus suurimmasta puutteesta>
RECOMMENDED_REROLL: yes | no
"""


if __name__ == "__main__":
    # Smoke-tests
    print("=== Position drift ===")
    rounds = [[0.7, 0.5, 0.8], [0.7, 0.5, 0.8], [0.7, 0.5, 0.8]]
    r = measure_position_drift(rounds)
    print(f"  Echo chamber: total={r.total_drift_kl:.4f} label={r.quality_label}")

    rounds2 = [[0.7, 0.5, 0.8], [0.6, 0.6, 0.75], [0.65, 0.62, 0.74]]
    r2 = measure_position_drift(rounds2)
    print(f"  Good debate: total={r2.total_drift_kl:.4f} label={r2.quality_label}")

    print("\n=== Persona diversity ===")
    opens = [[0.8, 0.2, 0.5], [0.81, 0.21, 0.51], [0.3, 0.7, 0.4]]
    div = check_persona_diversity(opens, threshold=0.92)
    print(f"  diverse={div['diverse']}, collisions={div['collisions']}")
