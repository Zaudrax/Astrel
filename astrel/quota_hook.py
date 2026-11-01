#!/usr/bin/env python3
"""Hook PreToolUse « * » d'Astrel (24/10, plan P34) : glisse l'ordre de quota à chaque SOUS-AGENT.

Le cœur (`astrel/quota_arret.py`) pose `runtime/quota-ordre.json` à 95 % de la fenêtre de 5 h
(phase « note ») puis à 98 % (phase « arret »). Ce hook, appelé avant CHAQUE outil, sort aussitôt
(bibliothèque standard seule, ni `astrel` ni site-packages : lancé en `-I -S`) si l'ordre est
absent ; sinon, pour un appel venu d'un sous-agent (champ `agent_id` de l'entrée, absent pour le
cerveau principal), il renvoie `additionalContext` UNE fois par agent et par phase (marqueur dans
`runtime/quota-ordre.d/`). Jamais de décision : ce n'est pas un garde-fou, le vrai gardien reste
`hooks/permission_hook.py`. Toute erreur : il se tait (code 0, rien sur stdout).
26/10 (plan P39) : prêt pour PostToolUse aussi (nom d'évènement lu dans l'entrée, même marqueur) ;
déclaré en PostToolUse par `brain.write_settings` seulement si le spike du plan le valide.
"""
import os
import sys

_RUNTIME = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime")
ORDRE = os.path.join(_RUNTIME, "quota-ordre.json")
MARQUES = os.path.join(_RUNTIME, "quota-ordre.d")
# 25/10 (audit p33-p34, M1) : échéance obligatoire et bornée, comme `quota_arret.ORDRE_MAX_S`.
ORDRE_MAX_S = 5 * 3600 + 900

NOTE = (
    "[Astrel, quota de l'abonnement : la fenêtre de 5 h est à {pct} %.] Note ton état MAINTENANT, puis "
    "continue ton travail : si tu as l'outil mcp__astrel__passation, appelle-le une fois (de = ton métier, "
    "a = astrel, fait = ce qui est fait, risques = l'étape suivante exacte, fichiers = chemins écrits, "
    "mission = celle de ta consigne). Ne lance aucune longue commande. À {stop} %, Astrel arrêtera les agents "
    "proprement et proposera à l'utilisateur de les relancer quand la limite se lèvera."
)
# 26/10 (plan P39) : plus de « moins d'une minute » ; un agent occupé a un délai de grâce, et son
# accusé (l'appel de passation) le fait arrêter dès la fiche rendue.
ARRET = (
    "[Astrel, quota de l'abonnement à {pct} % : Astrel va arrêter les agents.] Finis l'étape en cours et "
    "n'en commence aucune nouvelle ; dès que ta commande en cours se termine, dépose ta passation "
    "(mcp__astrel__passation) avec ton état À JOUR (fait, étape suivante exacte, fichiers), même si tu l'as "
    "déjà fait à la note. Astrel t'arrête juste après et proposera à l'utilisateur de te relancer à la levée de la limite."
)
EVENEMENTS = ("PreToolUse", "PostToolUse")  # même script pour les deux, marqueur commun (une fois par phase)


def _entier(v):
    try:
        return str(int(round(float(v))))
    except (TypeError, ValueError):
        return "?"


def main(stdin=None, ordre=ORDRE, marques=MARQUES, now=None, out=None):
    """Rend le texte glissé (« » : rien). Arguments pour les tests seulement."""
    if not os.path.exists(ordre) or os.path.islink(ordre):
        return ""  # cas courant : aucun import de plus, stdin non lu ; un lien n'est pas l'ordre du cœur
    import json
    import re
    import time

    payload = json.load(stdin or sys.stdin)
    agent = str(payload.get("agent_id") or "") if isinstance(payload, dict) else ""
    if not agent:
        return ""  # cerveau principal : jamais visé
    # Plan P39 : évènement lu dans l'entrée (PreToolUse, ou PostToolUse : à la FIN d'une commande lancée
    # avant l'ordre). Inconnu : on se tait (une réponse au mauvais nom serait rejetée par le CLI).
    evenement = payload.get("hook_event_name") or "PreToolUse"
    if evenement not in EVENEMENTS:
        return ""
    with open(ordre, encoding="utf-8") as fh:
        d = json.load(fh)
    phase = d.get("phase") if isinstance(d, dict) else None
    if phase not in ("note", "arret"):
        return ""
    until = d.get("until")
    now = time.time() if now is None else now
    if isinstance(until, bool) or not isinstance(until, (int, float)) or not now < until <= now + ORDRE_MAX_S:
        return ""  # sans échéance, périmé (cœur arrêté net) ou trop lointain : pas posé par le cœur
    if os.path.islink(marques):
        return ""  # dossier des marques remplacé par un lien : rien n'y est écrit
    os.makedirs(marques, exist_ok=True)
    # Marqueur par agent et par phase, SANS l'évènement : Pre et Post le partagent, l'ordre est dit une fois.
    nom = re.sub(r"[^A-Za-z0-9_-]", "_", agent)[:80] + "." + phase
    try:
        fd = os.open(os.path.join(marques, nom),
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        return ""  # déjà dit à cet agent dans cette phase
    os.close(fd)
    texte = (ARRET if phase == "arret" else NOTE).format(pct=_entier(d.get("pct")), stop=_entier(d.get("stop")))[:8000]
    (out or sys.stdout).write(json.dumps({"hookSpecificOutput": {"hookEventName": evenement,
                                                                 "additionalContext": texte}}, ensure_ascii=False))
    return texte


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 — jamais bloquant : le hook se tait
        pass
    sys.exit(0)
