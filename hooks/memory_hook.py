#!/usr/bin/env python3
"""Hook UserPromptSubmit de Claude Code : rappel automatique de la mémoire.

À chaque message de l'utilisateur, ajoute en contexte (`additionalContext`) une ligne
courte : les notes du coffre dont le titre correspond à ce qu'il dit, et
« procédure pertinente : X ». Sans modèle, en quelques millisecondes. Toute la
logique est dans `astrel/rappel.py` (réglages, mesure : `docs/RAPPEL-MEMOIRE.md`).

Ce n'est PAS un garde-fou et il ne bloque JAMAIS : toujours le code 0 (le code 2
d'un UserPromptSubmit effacerait le message de l'utilisateur), et toute panne
(entrée illisible, index absent, FTS5 manquant) = rien d'ajouté.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    try:
        from astrel import rappel

        payload = json.load(sys.stdin)
        out = rappel.hook_output(payload) if isinstance(payload, dict) else None
        if out:
            print(json.dumps(out, ensure_ascii=False))
    except Exception:
        pass  # rappel seulement : le message part tel quel
    return 0


if __name__ == "__main__":
    sys.exit(main())
