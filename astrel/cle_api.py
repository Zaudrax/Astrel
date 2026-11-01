"""Assistant de clé du CLI `claude` (`apiKeyHelper`, 20/10, plan P11).

Le CLI lance cette commande quand il lui faut une clé, et prend ce qu'elle imprime :
la clé reste ainsi hors de l'environnement du cerveau (donc de son Bash, de ses
outils et de `ps`) et hors de la ligne de commande. Seul le CHEMIN du fichier est
passé en argument :

    python -I astrel/cle_api.py anthropic <config/secrets.json>   # clé API Claude
    python -I astrel/cle_api.py passerelle <runtime/passerelle-jeton>  # jeton local de la passerelle

Imprime la clé et rien d'autre ; absente ou illisible : rien sur stdout, un message
sans secret sur stderr, code 1. Bibliothèque standard seulement (lancé souvent, vite).
"""
from __future__ import annotations

import json
import sys

NOM_CLE = "CLE_API_ANTHROPIC"


def lire(kind: str, path: str) -> str:
    if kind == "anthropic":
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return str(data.get(NOM_CLE) or "").strip() if isinstance(data, dict) else ""
    if kind == "passerelle":
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    raise ValueError("genre inconnu")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage : cle_api.py anthropic|passerelle <fichier>", file=sys.stderr)
        return 2
    try:
        cle = lire(argv[0], argv[1])
    except (OSError, ValueError) as exc:
        print(f"clé illisible ({type(exc).__name__})", file=sys.stderr)
        return 1
    if not cle:
        print("aucune clé enregistrée (Réglages › Fournisseur du HUD)", file=sys.stderr)
        return 1
    sys.stdout.write(cle)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
