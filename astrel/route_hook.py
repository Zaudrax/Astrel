#!/usr/bin/env python3
"""Hook PreToolUse d'Astrel sur l'outil Agent : choisit le MODÈLE du sous-agent
(voir `astrel/agents.py`), sans dépendre de la mémoire du cerveau.

Ce n'est PAS un garde-fou : le vrai gardien reste `hooks/permission_hook.py`
(protégé), qui tourne en parallèle et dont un refus l'emporte toujours. Une
panne ici ne bloque rien : le hook se tait et l'agent part comme demandé.
Seules exceptions, voulues : arrêt d'urgence posé → aucun nouvel agent ; mémoire du PC
en blocage (plan P20, `astrel/memoire.py`, blocage activé) → ni nouvel agent ni navigation
du navigateur de test Playwright (Chrome naît à la première navigation). État mémoire
absent, périmé ou illisible : on laisse passer. Rien n'est fermé ni tué. Arrêt des agents sur
quota en cours (plan P34, `astrel/quota_arret.py`, phase « arret ») → aucun nouvel agent.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Navigateurs de test (`playwright`, `playwright2..4`, voir config.BROWSER_EXTRA) : même motif que
# le matcher déclaré par `brain.write_settings`.
NAVIGATE_RE = re.compile(r"mcp__playwright\d*__browser_navigate")


def _emit(decision: str, why: str, updated: dict | None = None) -> None:
    out: dict = {"hookEventName": "PreToolUse", "permissionDecision": decision,
                 "permissionDecisionReason": f"Astrel : {why}"}
    if updated is not None:
        out["updatedInput"] = updated
    print(json.dumps({"hookSpecificOutput": out}, ensure_ascii=False))


def _pression(quoi: str) -> str:
    try:
        from astrel import memoire

        return memoire.refus(quoi)
    except Exception:
        return ""  # mesure illisible : on laisse passer


def _quota() -> str:
    try:
        from astrel import quota_arret

        return quota_arret.refus("nouvel agent")
    except Exception:
        return ""  # ordre illisible : on laisse passer


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool = str(payload.get("tool_name", ""))
        if NAVIGATE_RE.fullmatch(tool):
            why = _pression("navigateur de test (Chrome)")
            if why:
                _emit("deny", why)
            return 0
        if tool not in ("Agent", "Task"):
            return 0
        from astrel import agents

        tool_input = payload.get("tool_input") or {}
        decision, updated, why = agents.route(tool_input, estop=agents.estop_on(),
                                              pression=_pression("nouvel agent"), quota=_quota())
        agents.log_route(tool_input, decision, updated, why)
        if not decision:
            return 0
        _emit(decision, why, updated)
    except Exception:
        return 0  # routage seulement : en cas de doute, ne rien changer
    return 0


if __name__ == "__main__":
    sys.exit(main())
