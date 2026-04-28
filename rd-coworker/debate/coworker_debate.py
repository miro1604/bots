# -*- coding: utf-8 -*-
r"""coworker_debate.py - Multi-Agent critique R&D-coworker -arkkityypeille.

5 personaa (Feynman / Knuth / Carmack / Karpathy / Taguchi) lukevat
samaa testisuunnitelmaa tai hypoteesia, antavat kritiikin omasta
filosofiasta, ja loppupelissa synthesoidaan tarkennettu suunnitelma.

Kayttotapaukset:
  hypothesis    : kayttaja antaa ongelmakuvauksen → kukin generoi hypoteesin
  test_plan     : kayttaja antaa testisuunnitelman → kukin kritisoi
  blend         : 2-rounded — generate → kritisoi muiden tuotokset → revise

Ajo:
  python rd-coworker/debate/coworker_debate.py --mode test_plan --input plan.md
  python rd-coworker/debate/coworker_debate.py --mode hypothesis --inline "..."

Output:
  rd-coworker/debate/debate_log.jsonl   - kaikki kierrokset
  rd-coworker/debate/syntheses.jsonl    - lopulliset synthesis-tulokset
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_RD_ROOT = Path(__file__).resolve().parents[1]
_BOTS_ROOT = _RD_ROOT.parent
_SHARED_SCRIPTS = _BOTS_ROOT / "_shared" / "scripts"
sys.path.insert(0, str(_SHARED_SCRIPTS))
sys.path.insert(0, str(_RD_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from model_router import call_claude


def _strip_md(s):
    if s is None:
        return None
    return s.replace("**", "").replace("__", "").replace("*", "").strip()

PERSONAS_FILE = _RD_ROOT / "agents" / "personas.jsonl"
DEBATE_LOG = _RD_ROOT / "debate" / "debate_log.jsonl"
SYNTHESES = _RD_ROOT / "debate" / "syntheses.jsonl"


def load_personas() -> list[dict]:
    out = []
    with open(PERSONAS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return [p for p in out if p.get("active")]


def load_persona_prompt(persona: dict) -> str:
    p = _RD_ROOT / persona["prompt_file"]
    return p.read_text(encoding="utf-8")


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ─────────────────── HYPOTHESIS GENERATION ───────────────────

def round_hypothesis(scenario: str, personas: list[dict], debate_id: str) -> list[dict]:
    print(f"\n=== HYPOTHESIS GENERATION ({len(personas)} personaa) ===")
    out = []
    for i, persona in enumerate(personas, 1):
        ptext = load_persona_prompt(persona)
        prompt = f"""{ptext}

---

# Tilanne / ongelma

{scenario}

# Tehtava

Esita oma hypoteesisi tasta tilanteesta omasta filosofiastasi kasin.

Vastaa rakenteessa:

HYPOTHESIS: <yhden lauseen ydin>
MECHANISM: <kausaaliketju komponenteista mitattavaan ulostuloon, 2-3 lausetta>
PREDICTION: <konkreettinen ennustettava signaali jos hypoteesi pitaa paikkansa>
FALSIFIER: <konkreettinen havainto joka kumoaisi taman>
CRITICAL_TEST: <yksinkertaisin testi joka erottaa taman muista hypoteeseista>
RISK_OF_BEING_WRONG: <mihin tama hypoteesi voi sortua>
"""
        print(f"  [{i}/{len(personas)}] {persona['slug']}...", end="", flush=True)
        t0 = time.time()
        text, err, rc = call_claude(
            prompt=prompt, task_type="deep_analysis", timeout=None,
            model="sonnet", fallback_model="haiku", bot=f"rd-coworker:{persona['slug']}"
        )
        dur = time.time() - t0
        if rc != 0:
            print(f" FAIL rc={rc}")
            continue

        parsed = {"persona": persona["slug"], "name": persona["name"], "raw": text}
        for line in text.splitlines():
            s = line.strip().lstrip("*").lstrip("_").strip()
            for key, label in [("hypothesis", "HYPOTHESIS:"), ("mechanism", "MECHANISM:"),
                                ("prediction", "PREDICTION:"), ("falsifier", "FALSIFIER:"),
                                ("critical_test", "CRITICAL_TEST:"), ("risk_wrong", "RISK_OF_BEING_WRONG:")]:
                if s.upper().startswith(label):
                    parsed[key] = s[len(label):].strip()
                    break

        parsed["duration_s"] = round(dur, 1)
        out.append(parsed)
        print(f" {parsed.get('hypothesis', '?')[:60]}... {dur:.1f}s")

        append_jsonl(DEBATE_LOG, {
            "debate_id": debate_id, "mode": "hypothesis", "persona": persona["slug"],
            "ts": datetime.now(timezone.utc).isoformat(), **parsed,
        })
    return out


# ─────────────────── TEST PLAN CRITIQUE ───────────────────

def round_critique(plan: str, personas: list[dict], debate_id: str) -> list[dict]:
    print(f"\n=== TEST PLAN CRITIQUE ({len(personas)} personaa) ===")
    out = []
    for i, persona in enumerate(personas, 1):
        ptext = load_persona_prompt(persona)
        prompt = f"""{ptext}

---

# Testisuunnitelma jota kritisoitavaksi

{plan}

# Tehtava

Kritisoi tama testisuunnitelma omasta filosofiastasi kasin. Loyda ne 2-3
asiaa joita juuri SINUN nakokulmasta katsoen tassa puuttuu, ovat vaarin
tai vaativat tarkennusta.

Vastaa rakenteessa:

VERDICT: APPROVE | APPROVE_WITH_CHANGES | REJECT | NEEDS_MORE_INFO
TOP_3_CRITIQUES:
  1. <kritiikki + miksi se on tarkea filosofiastasi>
  2. <...>
  3. <...>
SUGGESTED_CHANGES:
  - <konkreettinen muutosehdotus>
  - <...>
WHAT_I_WOULD_DO_DIFFERENTLY: <2-3 lausetta omasta lahestymistavasta>
"""
        print(f"  [{i}/{len(personas)}] {persona['slug']}...", end="", flush=True)
        t0 = time.time()
        text, err, rc = call_claude(
            prompt=prompt, task_type="deep_analysis", timeout=None,
            model="sonnet", fallback_model="haiku", bot=f"rd-coworker:{persona['slug']}"
        )
        dur = time.time() - t0
        if rc != 0:
            print(f" FAIL rc={rc}")
            continue

        parsed = {"persona": persona["slug"], "name": persona["name"], "raw": text}
        # Verdict + tekstikenttien parse
        for line in text.splitlines():
            s = _strip_md(line.strip().lstrip("*").lstrip("_"))
            if s and s.upper().startswith("VERDICT:"):
                v = s[8:].strip().split()[0] if s[8:].strip() else None
                parsed["verdict"] = _strip_md(v)
                break
        # Loput taltioidaan rawiksi (analyysissa kayttajalle nakyy raw-vastaus)
        parsed["duration_s"] = round(dur, 1)
        out.append(parsed)
        print(f" {parsed.get('verdict', '?')} {dur:.1f}s")

        append_jsonl(DEBATE_LOG, {
            "debate_id": debate_id, "mode": "critique", "persona": persona["slug"],
            "ts": datetime.now(timezone.utc).isoformat(), **parsed,
        })
    return out


# ─────────────────── SYNTHESIS ───────────────────

def synthesize(scenario: str, mode: str, results: list[dict]) -> dict:
    """Yhdista 5 personaa yhteen synthesis-promptiin (orkestraattorin LLM-kayttaja).

    Tama on metaproompti — eri persona, joka on saanut nahda kaikki 5 outputia
    ja on tehnyt pakkaa-niiden-paalta-paatoksen.
    """
    blob = ""
    for r in results:
        blob += f"\n\n## {r['persona']} ({r.get('name','')})\n{r.get('raw','')[:2000]}\n"

    prompt = f"""Olet R&D-tyojohtaja. Sinulla on viisi (5) eri filosofian alaista
asiantuntijaa: Feynman (first principles), Knuth (rigor), Carmack (pragmatism),
Karpathy (data/ML), Taguchi (robust design). He ovat kaikki katsoneet samaa
tilannetta ja antaneet oman vastauksensa.

Tehtavasi on synthesoida heidan vastaukset YHDEKSI tarkennetuksi suosituksi.
Ala valitse vain "yksi voittaja" — yhdista parhaat oivallukset eri filosofioista
ja merkitse selkeasti milloin he ovat eri mielta.

# Tilanne / mode

Mode: {mode}
{scenario[:2000]}

# Persoonien vastaukset
{blob[:10000]}

# Lopputulos

Tee seuraavat:

1. **YHTEINEN YDIN** (1-2 lausetta): mihin kaikki 5 olisivat samaa mielta
2. **TARKENNETTU SUOSITUS**: rakenne joka ottaa parhaat osat eri filosofioista
3. **JANNITTEET / KOMPROMISSIT**: missa kohdin he olivat eri mielta — kerro miten loppupelissa kommutoitiin (esim. "Knuth halusi 100 toistoa, Carmack 10; valitsin 30 koska...")
4. **BLIND SPOT** (max 1 kpl): mita he kaikki 5 jattivat huomioimatta? Onko jotain mita kukaan ei nahnyt?
5. **ACTION_ITEMS** (3 max): konkreettiset seuraavat askeleet
"""
    print(f"\n=== SYNTHESIS ===")
    t0 = time.time()
    text, err, rc = call_claude(
        prompt=prompt, task_type="deep_analysis", timeout=None,
        model="sonnet", fallback_model="haiku", bot="rd-coworker:synthesis"
    )
    dur = time.time() - t0
    print(f"Synthesis valmis ({dur:.1f}s)")
    return {"text": text, "duration_s": round(dur, 1), "rc": rc}


# ─────────────────── MAIN ───────────────────

def run_debate(scenario: str, mode: str) -> dict:
    debate_id = f"DEB_RD_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    print(f"\n=== DEBATE {debate_id} (mode={mode}) ===")

    personas_all = load_personas()
    # Token-budget B-leikkaus 2026-04-28: 4 random personaa per cycle (oli kaikki)
    import random as _random, os as _os
    n_sample = int(_os.environ.get("DEBATE_N_PERSONAS", "4"))
    if len(personas_all) > n_sample:
        personas = _random.sample(personas_all, n_sample)
    else:
        personas = personas_all
    print(f"Personas (sampled {len(personas)}/{len(personas_all)}): {[p['slug'] for p in personas]}")

    if mode == "hypothesis":
        results = round_hypothesis(scenario, personas, debate_id)
    elif mode == "test_plan":
        results = round_critique(scenario, personas, debate_id)
    elif mode == "blend":
        # generate → revise. Toteuta tarvittaessa.
        print("Mode 'blend' ei viela toteutettu (TODO).")
        return {"verdict": "TODO_BLEND_MODE"}
    else:
        raise ValueError(f"Unknown mode: {mode}")

    if len(results) < 3:
        print(f"Liian vahan tuloksia ({len(results)}), keskeytan.")
        return {"verdict": "ABORT_INSUFFICIENT", "n": len(results)}

    synthesis = synthesize(scenario, mode, results)

    record = {
        "debate_id": debate_id,
        "mode": mode,
        "n_personas": len(results),
        "ts": datetime.now(timezone.utc).isoformat(),
        "scenario_preview": scenario[:500],
        "synthesis": synthesis.get("text"),
    }
    append_jsonl(SYNTHESES, record)

    print("\n=== SYNTHESIS OUTPUT ===")
    print(synthesis.get("text", "")[:3000])
    return record


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["hypothesis", "test_plan", "blend"], default="test_plan")
    p.add_argument("--input", help="Path to scenario/plan text file")
    p.add_argument("--inline", help="Inline scenario string")
    args = p.parse_args()

    if args.input:
        scenario = Path(args.input).read_text(encoding="utf-8")
    elif args.inline:
        scenario = args.inline
    else:
        # Default smoke scenario
        scenario = """
Ongelmakuvaus (smoke-test): jarruosa ABC-12 valmistuksesta tulleessa eraan
n=120 prototyypeissa havaittiin 8 yksikolla kestoaika 2-5x lyhyempi kuin
suunnittelutavoite (1500 h). Loput 112 yksikkoa kayttaytyivat normaalisti.
Materiaali on AISI 4140 -teras, kovuus HRC 32-38 spec-rajojen sisalla.
Kayttoolosuhteet: kuiva, 20-25 C, ei sykista kuormitusta.

Mahdollisia syita on harkittu:
- materiaalipoikkeama batchien valilla
- piilevamallin lampokasittely-poikkeama
- kasittelyvirheet asennuksessa
- kayttotilan kohinatekijat (mahd. kosteus, sykista kuormitus jonka asentaja jatti raportoimatta)

Tehtavasi: arvioi tama testi/diagnoosi-suunnitelma:
1. Inspektoi kaikki 8 epaonnistunutta yksikkoa SEM:lla mikrorakenteen osalta
2. Mittaa kovuusprofiilit pinnan ja syvalla
3. Tee EDS-koostumusanalyysit
4. Vertaile niihin 5 satunnaisotettuun normaalisti kayttaytyvaan yksikkoon
5. Loppupaatos riippuen siita mita SEM:lla nahdaan
"""
        print("(Kaytetaan default smoke-skenaariota — jarruosa ABC-12)")

    run_debate(scenario, args.mode)


if __name__ == "__main__":
    main()
