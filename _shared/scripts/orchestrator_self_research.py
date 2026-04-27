# -*- coding: utf-8 -*-
r"""orchestrator_self_research.py — orchestrator tutkii ulkoisista lähteistä
miten parantaa tapaa mitata, kehittää ja johtaa agenttiverkostoa.

Käyttäjän mandaatti 2026-04-26: luppoaika ei ole sallittua. Kun ei ole akuuttia
työtä, orchestrator perehtyy papereihin/GitHubeihin/blogeihin ja generoi
implementoitavia parannuksia omaan toimintaansa.

Strategia:
  1. Valitse rotaatiosta tutkimusaihe (tai jatka edellisestä jos kesken)
  2. Sonnet (laaja prompt) tuottaa selkeän kysymyksen + 5-10 hypoteesia + lähde-ehdotuksia
  3. Tallennetaan _shared/memory/orchestrator_research/<topic>_<ts>.md
  4. Jos tutkimuksesta löytyy konkreettinen implementaatio-idea →
     orchestrator_todo.jsonl saa uuden rivin
  5. Suoritettu = TS:llä yhden tunnin sisällä → skipataan jos ajettu äsken

Ajo (daemon):
  python orchestrator_self_research.py [--cooldown-min 60]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BOTS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BOTS_ROOT / "_shared" / "scripts"))

from model_router import call_claude

RESEARCH_DIR = BOTS_ROOT / "_shared" / "memory" / "orchestrator_research"
TODO_LOG = BOTS_ROOT / "_shared" / "memory" / "orchestrator_todo.jsonl"
LEARNINGS = BOTS_ROOT / "_shared" / "memory" / "orchestrator_learnings.jsonl"
ROTATION_FILE = RESEARCH_DIR / "_rotation_state.json"

TOPICS = [
    {
        "slug": "multi_agent_debate_architectures",
        "question": "Miten 5-7 persona-debate-arkkitehtuurin laatua mitataan ja parannetaan?",
        "keywords": "multi-agent debate, judge models, voting, ensemble, AAAI, NeurIPS",
    },
    {
        "slug": "agent_eval_benchmarks",
        "question": "Mitkä julkaistut agent-eval-benchmarkit (HELM, AgentBench, GAIA, SWE-bench) sopivat meidän botteihin?",
        "keywords": "agent benchmark, HELM, AgentBench, SWE-bench, GAIA, eval suite",
    },
    {
        "slug": "dspy_textgrad_prompt_optimization",
        "question": "DSpy/TextGrad/prompt-optimization — miten implementoidaan finance/leadgen-debateihin?",
        "keywords": "DSPy, TextGrad, prompt optimization, MIPRO, BootstrapFewShot",
    },
    {
        "slug": "persona_lattice_diversity",
        "question": "Cognitive diversity persona-latticessa — mitkä metriikat ennustavat synergiaa?",
        "keywords": "ensemble diversity, cognitive diversity, persona simulation, jury theorem",
    },
    {
        "slug": "rl_agent_self_improvement",
        "question": "RL-pohjainen agent self-improvement — miten kalibroidaan ilman katastrofaalista unohtamista?",
        "keywords": "agent RL, self-improvement, RLHF, RLAIF, constitutional AI",
    },
    {
        "slug": "retrieval_augmented_quality",
        "question": "Lähdelaatu RAG-ympäristössä — miten valitaan ja mitataan source quality?",
        "keywords": "retrieval augmented, source quality, citation accuracy, knowledge graph",
    },
    {
        "slug": "team_metrics_for_agents",
        "question": "DORA-tyyppiset team-metriikat agenttien kontekstissa — mitä mitata?",
        "keywords": "DORA metrics, agent productivity, deployment frequency, MTTR for AI",
    },
    {
        "slug": "cross_agent_knowledge_transfer",
        "question": "Miten agenttien välillä siirretään oppimista ilman cross-contamination?",
        "keywords": "knowledge transfer, cross-agent, distillation, federated learning",
    },
]


PROMPT = """Sinä OLET vain tutkimusagentti joka tuottaa TEKSTIVASTAUKSEN. ÄLÄ KÄYTÄ
työkaluja (Read, Edit, Bash, Write). ÄLÄ pyydä kirjoituslupaa.

Olet orchestrator-ekosysteemin tutkija. Sinun pitää koota ulkoisten lähteiden
(arxiv, NeurIPS/ICLR/ACL papereita, MIT-lisensoituja GitHub-koodeja, post-mortem
blogeja) pohjalta muistivihko aiheesta:

# Tutkimuskysymys
{question}

# Hakuvinkit (käytä omaa tietämystäsi näistä alueista)
{keywords}

# Konteksti
Tämä tutkimus implementoidaan **bots/**-ekosysteemiin (multi-agent setup, 7 bottia,
5-7 personaa per botti debatessa, Sonnet/Haiku-routing). Tavoitteena on parantaa
orchestratorin kykyä mitata, kehittää ja johtaa agentteja.

# Vastauksen rakenne

Aloita rivillä "FINDINGS:" suoraan, älä preamblea.

FINDINGS:
- [bullet 1: konkreettinen löydös 1-2 lauseen kuvauksella]
- [bullet 2: ...]
- [5-10 bulletia yhteensä, perustuen sinun parhaaseen tietämykseen kentästä]

KEY_REFERENCES:
- [Author (Year). Title. Lähde tai venue]
- [3-5 viittausta]

IMPLEMENTATION_IDEAS:
- [Idea 1: konkreettinen muutos meidän koodiin / arkkitehtuuriin]
- [Idea 2-4 samalla rakenteella]

PROPOSED_TODOS:
- [Todo 1: PRIORITY=high|medium|low | TITLE: ... | DESC: ...]
- [Jos jokin idea on heti toteutettavissa, kirjoita 1-3 todo:ta]

KEY_LEARNING:
- [yhden lauseen tiivistys mitä tämä tutkimus opetti orchestratorin johtamisesta]
"""


def load_rotation():
    if ROTATION_FILE.exists():
        try:
            return json.loads(ROTATION_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_idx": -1, "last_run": None}


def save_rotation(state):
    ROTATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    ROTATION_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def parse_response(text):
    out = {"findings": [], "references": [], "ideas": [], "todos": [], "learning": ""}
    section = None
    for raw in text.splitlines():
        s = raw.strip()
        s_clean = s.lstrip("*-#").lstrip("_").replace("**", "").strip()
        if not s:
            continue
        u = s_clean.upper()
        if u.startswith("FINDINGS"):
            section = "findings"; continue
        if u.startswith("KEY_REFERENCES") or u.startswith("REFERENCES"):
            section = "references"; continue
        if u.startswith("IMPLEMENTATION_IDEAS") or u.startswith("IDEAS"):
            section = "ideas"; continue
        if u.startswith("PROPOSED_TODOS") or u.startswith("TODOS"):
            section = "todos"; continue
        if u.startswith("KEY_LEARNING") or u.startswith("LEARNING"):
            section = "learning"; continue
        if section is None:
            continue
        if s.startswith("-") or s.startswith("*"):
            content = s.lstrip("-*").strip()
            if section == "learning":
                out["learning"] = content
            else:
                out[section].append(content)
        elif section == "learning" and not out["learning"]:
            out["learning"] = s_clean


    return out


def write_research_doc(topic, parsed, raw_text):
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    p = RESEARCH_DIR / f"{topic['slug']}_{ts}.md"
    body = [
        f"# {topic['question']}",
        f"\n_Tutkittu: {datetime.now(timezone.utc).isoformat()}_\n",
        "## Findings",
    ]
    for f in parsed["findings"]:
        body.append(f"- {f}")
    body.append("\n## Key references")
    for r in parsed["references"]:
        body.append(f"- {r}")
    body.append("\n## Implementation ideas")
    for i in parsed["ideas"]:
        body.append(f"- {i}")
    body.append("\n## Proposed TODOs")
    for t in parsed["todos"]:
        body.append(f"- {t}")
    body.append("\n## Key learning")
    body.append(parsed.get("learning", ""))
    body.append("\n---\n## Raw response (debug)")
    body.append("```")
    body.append(raw_text[:5000])
    body.append("```")
    p.write_text("\n".join(body), encoding="utf-8")
    return p


def append_todos(topic, todos):
    TODO_LOG.parent.mkdir(parents=True, exist_ok=True)
    added = 0
    for t in todos:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": "self_research",
            "topic_slug": topic["slug"],
            "raw": t[:500],
            "status": "pending",
        }
        # Heuristiikka: irrota PRIORITY ja TITLE
        u = t.upper()
        for level in ("HIGH", "MEDIUM", "LOW"):
            if level in u:
                rec["priority"] = level.lower()
                break
        with open(TODO_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        added += 1
    return added


def append_learning(topic, learning):
    if not learning:
        return
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "self_research",
        "topic_slug": topic["slug"],
        "topic_question": topic["question"],
        "note": learning[:600],
    }
    LEARNINGS.parent.mkdir(parents=True, exist_ok=True)
    with open(LEARNINGS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cooldown-min", type=int, default=60,
                    help="Skip jos viimeisestä ajosta < N min")
    ap.add_argument("--force", action="store_true",
                    help="Ohita cooldown")
    args = ap.parse_args()

    state = load_rotation()
    last_run = state.get("last_run")
    if last_run and not args.force:
        try:
            last_dt = datetime.fromisoformat(str(last_run).replace("Z", "+00:00"))
            age_min = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60.0
            if age_min < args.cooldown_min:
                print(f"cooldown — viim. {age_min:.0f}min sitten, skip")
                return 0
        except Exception:
            pass

    last_idx = state.get("last_idx", -1)
    next_idx = (last_idx + 1) % len(TOPICS)
    topic = TOPICS[next_idx]

    print(f"=== self-research [{topic['slug']}] ===")
    print(f"Q: {topic['question']}")

    prompt = PROMPT.format(question=topic["question"], keywords=topic["keywords"])
    text, err, rc = call_claude(
        prompt=prompt, task_type="deep_analysis", timeout=None,
        model="sonnet", fallback_model="haiku",
        bot=f"orchestrator:self_research:{topic['slug']}",
    )
    if rc != 0:
        print(f"  Sonnet rc={rc}: {(err or '')[:200]}")
        return 1

    parsed = parse_response(text)
    doc_path = write_research_doc(topic, parsed, text)
    n_todos = append_todos(topic, parsed["todos"])
    append_learning(topic, parsed["learning"])

    state["last_idx"] = next_idx
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_rotation(state)

    print(f"  ✓ doc: {doc_path.name}")
    print(f"  findings={len(parsed['findings'])}, ideas={len(parsed['ideas'])}, "
          f"todos={n_todos}")
    print(f"  learning: {parsed.get('learning','')[:140]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
