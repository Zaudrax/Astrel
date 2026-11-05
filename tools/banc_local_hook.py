#!/usr/bin/env python3
"""Hook PreToolUse du BANC du moteur local avec outils (04/11/2026, lot 4, tools/banc_local.py).

Le vrai garde-fou demanderait une carte sur le HUD de l'utilisateur pour un secret d'Astrel : le banc ne doit
rien montrer. Ce hook de banc prouve seulement que le CLI local APPELLE le hook à chaque outil et OBÉIT à son
refus : il note chaque appel (`BANC_LOCAL_JOURNAL`) et refuse tout ce qui vise un fichier « piege-hook ».
Le vrai hook est branché en production (`outils_local.reglages_hook`, tests/test_outils_local.py).

05/11 (lot 5) : les outils du serveur `astrel_local` sont jugés par le VRAI code du garde-fou
(`guard._refus_outil_local`, sur la liste jetable du banc `BANC_LOCAL_COMMANDES`, puis `guard.classify`) :
refusé ou à confirmer → refusé (jamais de carte), sinon autorisé, comme le ferait `hooks/permission_hook.py`.
05/11 (lot 7B) : les outils `web_local` et `playwright_local` sont jugés par le VRAI code du garde-fou
(`guard._refus_web_local` : outils du navigateur permis, adresse de `browser_navigate` dans le registre du tour),
sans le contrôle du vrai CLI local (le banc n'est pas un enfant du cœur) ; sensibles = libres, comme en autonome."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _decision(decision: str, raison: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                             "permissionDecisionReason": raison}}, ensure_ascii=False))


def main() -> int:
    try:
        entree = json.load(sys.stdin)
    except ValueError:
        print("entrée illisible", file=sys.stderr)
        return 2
    outil = str(entree.get("tool_name") or "")
    decision = ""
    if outil.startswith("mcp__astrel_local__"):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from astrel import guard

        liste = os.environ.get("BANC_LOCAL_COMMANDES") or None
        why = guard._refus_outil_local(outil.rsplit("__", 1)[-1], entree.get("tool_input"),
                                       fichier=Path(liste) if liste else None)
        if not why and guard.classify(outil, entree.get("tool_input") or {}).needs_confirm:
            why = "à confirmer : refusé au banc (aucune carte)"
        decision = "deny" if why else "allow"
        _decision(decision, why or "action autorisée par le garde-fou (banc)")
    elif outil.startswith(("mcp__web_local__", "mcp__playwright_local__")):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from astrel import guard

        why = guard._refus_web_local(outil, entree.get("tool_input") or {})
        decision = "deny" if why else "allow"
        _decision(decision, why or "outil web autorisé par le garde-fou (banc)")
    elif "piege-hook" in json.dumps(entree.get("tool_input") or {}, ensure_ascii=False):
        decision = "deny"
        _decision(decision, "refusé par le garde-fou (banc)")
    journal = os.environ.get("BANC_LOCAL_JOURNAL")
    if journal:
        with open(journal, "a", encoding="utf-8") as f:
            f.write(json.dumps({"outil": outil, "entree": entree.get("tool_input"), "decision": decision},
                               ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
