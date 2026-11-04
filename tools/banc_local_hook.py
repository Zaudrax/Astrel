#!/usr/bin/env python3
"""Hook PreToolUse du BANC du moteur local avec outils (04/11/2026, lot 4, tools/banc_local.py).

Le vrai garde-fou demanderait une carte sur le HUD de l'utilisateur pour un secret d'Astrel : le banc ne doit
rien montrer. Ce hook de banc prouve seulement que le CLI local APPELLE le hook à chaque outil et OBÉIT à son
refus : il note chaque appel (`BANC_LOCAL_JOURNAL`) et refuse tout ce qui vise un fichier « piege-hook ».
Le vrai hook est branché en production (`outils_local.reglages_hook`, tests/test_outils_local.py)."""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    try:
        entree = json.load(sys.stdin)
    except ValueError:
        print("entrée illisible", file=sys.stderr)
        return 2
    journal = os.environ.get("BANC_LOCAL_JOURNAL")
    if journal:
        with open(journal, "a", encoding="utf-8") as f:
            f.write(json.dumps({"outil": entree.get("tool_name"), "entree": entree.get("tool_input")},
                               ensure_ascii=False) + "\n")
    if "piege-hook" in json.dumps(entree.get("tool_input") or {}, ensure_ascii=False):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "refusé par le garde-fou (banc)"}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
