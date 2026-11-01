#!/usr/bin/env python3
"""Hook de suivi des outils d'Astrel (01/11/2026, accord explicite de l'utilisateur).

Déclaré par `brain.write_settings` sur Write, Edit, MultiEdit, NotebookEdit et Bash :
- `PreToolUse` : instantané de la version d'avant de chaque fichier que l'outil va écrire
  (`astrel/instantanes.py`, « annule ce que tu viens de faire ») ; n'écrit JAMAIS rien sur la
  sortie : aucune décision, le garde-fou (`permission_hook.py`) reste seul juge ;
- `PostToolUse`, `PostToolUseFailure` : empreinte du fichier écrit (même module) et, pour un
  Bash qui échoue sur un piège de Debian (PEP 668 « externally-managed-environment »,
  `python` ou `pip` absents), un INDICE court pour le modèle (`additionalContext`, ≈ 30 tokens,
  seulement à l'échec ; idées 74 à 83 de docs/idees/HERMES-cli-docs-tests.md).

Ce n'est PAS un garde-fou : une panne ici ne bloque rien, le hook se tait (code 0, sortie vide).
`ASTREL_INSTANTANES=0` coupe les instantanés, `ASTREL_INDICES=0` les indices.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# (motif cherché dans la sortie de l'échec, indice donné au modèle)
INDICES = (
    (re.compile(r"externally[- ]managed[- ]environment", re.I),
     "[Indice d'Astrel] Debian (PEP 668) refuse pip hors d'une venv : `.venv/bin/python -m pip …` (ou "
     "`python3 -m venv .venv` d'abord) ; jamais `--break-system-packages`."),
    (re.compile(r"(?:^|[\s:])python: (?:command )?not found|command not found: python\b"
                r"|['\"]?python['\"]?: No such file or directory", re.I | re.M),
     "[Indice d'Astrel] Debian n'a pas de `python`, seulement `python3` ; dans Astrel : `.venv/bin/python`."),
    (re.compile(r"(?:^|[\s:])pip3?: (?:command )?not found|command not found: pip3?\b", re.I | re.M),
     "[Indice d'Astrel] pas de `pip` ici : `.venv/bin/python -m pip …` dans une venv (Debian, PEP 668)."),
)
SORTIE_MAX = 20000  # on ne lit que la fin d'une longue sortie


def _off(var: str) -> bool:
    return os.environ.get(var, "1").strip().lower() in ("0", "off", "non", "false")


def _sortie(payload: dict) -> str:
    """Texte de l'échec (PostToolUseFailure : `error`) ou de la réponse de l'outil (PostToolUse)."""
    parts = []
    if isinstance(payload.get("error"), str):
        parts.append(payload["error"])
    resp = payload.get("tool_response")
    if isinstance(resp, str):
        parts.append(resp)
    elif isinstance(resp, dict):
        parts += [str(resp.get(k)) for k in ("stderr", "stdout", "output", "error") if resp.get(k)]
    return "\n".join(parts)[-SORTIE_MAX:]


def indice(payload: dict) -> str:
    """Indice(s) pour un Bash qui ÉCHOUE sur un piège de Debian ; "" sinon. Fonction pure. 01/11
    (relecture) : jamais sur un PostToolUse réussi dont la sortie contiendrait le motif."""
    if payload.get("tool_name") != "Bash" or payload.get("hook_event_name") != "PostToolUseFailure":
        return ""
    texte = _sortie(payload)
    if not texte:
        return ""
    trouves = [hint for motif, hint in INDICES if motif.search(texte)]
    return " ".join(trouves[:2])


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        event = payload.get("hook_event_name")
        if event == "PreToolUse":
            if not _off("ASTREL_INSTANTANES"):
                from astrel import instantanes
                instantanes.avant(payload)
            return 0  # jamais de décision ici
        if event not in ("PostToolUse", "PostToolUseFailure"):
            return 0
        if not _off("ASTREL_INSTANTANES"):
            from astrel import instantanes
            instantanes.apres(payload)
        hint = "" if _off("ASTREL_INDICES") else indice(payload)
        if hint:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": hint}},
                             ensure_ascii=False))
    except Exception:  # noqa: BLE001 — hook de confort : dans le doute, se taire
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
