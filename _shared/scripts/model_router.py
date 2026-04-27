"""Cross-bot model router.

Yhtenäinen rajapinta `claude -p` -kutsuihin kaikille boteille. Lukee tehtävätyypin
ja valitsee oikean mallin (Sonnet / Haiku / Opus) säännön mukaan.

Käyttö:
    from model_router import call_claude
    out, err, rc = call_claude(prompt, task_type="critic_judgement")

Säännöt: katso `_shared/memory/model_routing.md`.

WINDOWS: shutil.which resolvoi .CMD-shimin. Älä käytä shell=True (turvariski).
"""

import shutil
import subprocess
import sys
from pathlib import Path

# Lisää shared/scripts polkuun jotta voi importata muista boteista
_SHARED_SCRIPTS = Path(__file__).resolve().parent
if str(_SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SHARED_SCRIPTS))

# Quota-tracker integraatio
try:
    from quota_tracker import log_call as _log_quota, is_bot_paused as _is_paused
    _QUOTA_AVAILABLE = True
except Exception:
    _QUOTA_AVAILABLE = False
    def _log_quota(*args, **kwargs): pass
    def _is_paused(bot): return False, None

CLAUDE_PATH = shutil.which("claude") or "claude"


# ─────────────────────────── Säännöstö (synkronissa model_routing.md:n kanssa) ───────────────────────────
#
# Tehtävätyyppi → (primary_model, fallback_model)
# Pidetään minimissä — botin oma config voi yliajaa.

TASK_MODEL_MAP: dict[str, tuple[str, str | None]] = {
    # === HEAVY (Sonnet ensisijaisesti, Haiku-fallback) ===
    "creative_long_form":     ("sonnet", "haiku"),    # YT-script, blogiartikkeli, mainosteksti pitkä
    "creative_short_form":    ("sonnet", "haiku"),    # TikTok-vertical, hook-variant, Reddit-thread
    "code_generation":        ("sonnet", "haiku"),    # uusi koodi, refactoring isompi
    "architecture_design":    ("sonnet", "haiku"),    # systeemisuunnittelu, master-prompt
    "deep_analysis":          ("sonnet", "haiku"),    # data-analyysi, validointi monelta angle:lta
    "novel_idea_generation":  ("sonnet", "haiku"),    # alphahunter Visionary
    "structured_output":      ("sonnet", "haiku"),    # JSON-schema-pakotettu luova output

    # === LIGHT (Haiku riittää, ei merkittävää laatueroa) ===
    "classification":         ("haiku", None),        # kategoria, sentimentti, kyllä/ei
    "judgement_simple":       ("haiku", None),        # alphahunter Critic (HYL/HYV)
    "summarization_short":    ("haiku", None),        # 1-3 lauseen tiivistys
    "title_metadata":         ("haiku", None),        # YT-titlet, meta-tagit, alt-text
    "format_conversion":      ("haiku", None),        # JSON ↔ MD, kieliversiot
    "name_generation":        ("haiku", None),        # tuotenimet, brand-ehdotukset
    "translation_short":      ("haiku", None),        # < 500 sanaa
    "single_fact_lookup":     ("haiku", None),        # "mikä on X?", "milloin tapahtui Y?"

    # === ULTRA-HEAVY (Opus, vain harvoissa tilanteissa) ===
    "complex_reasoning":      ("opus", "sonnet"),     # multi-step-päätös jossa virheellä iso hinta
    "research_synthesis":     ("opus", "sonnet"),     # pitkä tutkimus + päätelmät
    "critical_review":        ("opus", "sonnet"),     # turvalliset/juridiset arvioinnit
}


def model_for_task(task_type: str, override_model: str | None = None,
                    override_fallback: str | None = None) -> tuple[str, str | None]:
    """Palauttaa (model, fallback_model) tehtävätyypille. Override-parametrit voittavat."""
    if override_model:
        return override_model, override_fallback
    return TASK_MODEL_MAP.get(task_type, ("sonnet", "haiku"))  # default: sonnet+haiku


def call_claude(
    prompt: str,
    task_type: str = "creative_long_form",
    timeout: int | None = 240,
    model: str | None = None,
    fallback_model: str | None = None,
    bot: str = "unknown",
    respect_pause: bool = True,
) -> tuple[str, str, int]:
    """Kutsuu `claude -p` valitulla mallilla. task_type-pohjainen routing automaattisesti.

    Args:
        prompt: Promptin teksti (lähetetään stdin:n kautta)
        task_type: Avain TASK_MODEL_MAP:sta (esim. "creative_long_form", "judgement_simple")
        timeout: Max-aika sekunteina. **None = ei aikarajaa** — tutkimus/debate saa kestää
            niin pitkään kuin vaatii. Käytä luokitteluun lyhyttä, syvätutkimukseen None:a.
        model: Yliajaa task_typen primary-mallin
        fallback_model: Yliajaa task_typen fallback-mallin
        bot: Botin nimi (alphahunter, videoempire, ...) — quota-trackerille + pause-checkille
        respect_pause: Jos True ja botti on tauotettu kiintiön takia → palauttaa rc=42 ja viestin

    Returns:
        (stdout, stderr, returncode)
    """
    # Pause-check ennen kutsua
    if respect_pause:
        paused, reason = _is_paused(bot)
        if paused:
            return "", f"PAUSED: {bot} on tauotettu kiintiöpaineen takia ({reason}). Yritä myöhemmin.", 42

    chosen_model, chosen_fallback = model_for_task(task_type, model, fallback_model)

    cmd = [CLAUDE_PATH, "-p"]
    if chosen_model:
        cmd.extend(["--model", chosen_model])
    if chosen_fallback:
        cmd.extend(["--fallback-model", chosen_fallback])

    stdout, stderr, rc = "", "", 1
    try:
        kwargs = dict(
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if timeout is not None:
            kwargs["timeout"] = timeout
        result = subprocess.run(cmd, **kwargs)
        stdout, stderr, rc = result.stdout or "", result.stderr or "", result.returncode
    except subprocess.TimeoutExpired:
        stderr, rc = f"Timeout after {timeout}s (model={chosen_model})", 124
    except FileNotFoundError:
        stderr, rc = f"claude CLI not in PATH (looked: {CLAUDE_PATH})", 127
    except Exception as exc:
        stderr, rc = f"{type(exc).__name__}: {exc}", 1

    # Lokita kvantiteetti-tracking
    fallback_used = bool(chosen_fallback) and ("fallback" in (stderr or "").lower() or "overload" in (stderr or "").lower())
    _log_quota(
        bot=bot,
        model=chosen_model,
        prompt_chars=len(prompt or ""),
        response_chars=len(stdout or ""),
        rc=rc,
        task_type=task_type,
        fallback_used=fallback_used,
    )

    return stdout, stderr, rc


def print_routing_table():
    """Tulostaa täydellisen routing-taulukon konsoliin (debug)."""
    print(f"{'Task type':<28} {'Primary':<10} {'Fallback':<10}")
    print("-" * 50)
    for task, (primary, fallback) in TASK_MODEL_MAP.items():
        print(f"{task:<28} {primary:<10} {fallback or '-':<10}")


if __name__ == "__main__":
    print_routing_table()
