#!/usr/bin/env python3
"""Banc d'essai de régression d'Astrel (plan P3, lot C ; `astrel/banc.py`).

    .venv/bin/python tools/banc_essai.py                  # palier 1 : 0 token
    .venv/bin/python tools/banc_essai.py --palier 2       # + vrais appels haiku (≈ 0,6 M tokens)
    .venv/bin/python tools/banc_essai.py --palier 2 --seul agenda-lu,rappel-programme
    .venv/bin/python tools/banc_essai.py --palier 2 --force   # moins d'une heure après le précédent

Palier 2 : à la demande seulement (choix de l'utilisateur), plafond 1 M tokens par
exécution, une exécution par heure sans --force, refusé pendant un tour de
réflexe et sous arrêt d'urgence ; serveur MCP factice, rien n'est envoyé.
Code de sortie : 0 tout réussi, 1 un échec, 2 refusé.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from astrel import banc  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Banc d'essai de régression d'Astrel")
    ap.add_argument("--palier", type=int, choices=(1, 2), default=1)
    ap.add_argument("--force", action="store_true", help="passer outre l'intervalle d'une heure (palier 2)")
    ap.add_argument("--seul", default="", help="identifiants de scénarios, séparés par des virgules")
    ap.add_argument("--plafond", type=int, default=banc.TOKEN_CAP,
                    help=f"tokens au plus pour le palier 2 (≤ {banc.TOKEN_CAP})")
    ap.add_argument("--json", action="store_true", help="résultat complet en JSON")
    args = ap.parse_args(argv)
    only = [s.strip() for s in args.seul.split(",") if s.strip()] or None
    try:
        out = banc.run(palier=args.palier, force=args.force, only=only, token_cap=args.plafond)
    except banc.BancRefuse as exc:
        print(f"Banc d'essai refusé : {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else banc.summary(out))
    if out.get("refused"):
        return 2
    return 0 if all(r["ok"] for r in out["results"]) else 1


if __name__ == "__main__":
    sys.exit(main())
