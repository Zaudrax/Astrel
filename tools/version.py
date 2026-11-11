#!/usr/bin/env python3
"""Version d'Astrel et notes de version (10/11/2026 ; `astrel/nouveautes.py`).

    .venv/bin/python tools/version.py                       # version actuelle
    .venv/bin/python tools/version.py --monter mineure      # 2.0.0 → 2.1.0 (majeure, mineure, correctif)
    .venv/bin/python tools/version.py --notes               # notes de la version actuelle (Release GitHub)
    .venv/bin/python tools/version.py --verifier            # VERSION a bien sa section dans NOUVEAUTES.md

`--monter` réécrit `VERSION` et ajoute en haut de `NOUVEAUTES.md` une section datée du jour, à remplir
avant le commit. Rien n'est commité, étiqueté ni poussé ici : la publication suit docs/PARTAGE.md
(« Publier une version »). Une version MAJEURE ouvre la fenêtre des nouveautés chez chaque utilisateur :
la réserver aux changements qu'il doit connaître (installation, comportement, réglages à revoir).
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = ROOT / "VERSION"
NOTES = ROOT / "NOUVEAUTES.md"
A_REMPLIR = "- (à remplir : ce qui change pour la personne qui utilise Astrel)"


def _nouveautes():
    spec = importlib.util.spec_from_file_location("astrel_nouveautes", ROOT / "astrel" / "nouveautes.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _lire(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def ajouter_section(notes: str, version: str, date: str) -> str:
    """Nouvelle section `## <version> — <date>` avant la première section existante (ou à la fin)."""
    bloc = f"## {version} — {date}\n\n{A_REMPLIR}\n\n"
    lignes = notes.splitlines(keepends=True)
    for i, ligne in enumerate(lignes):
        if ligne.startswith("## "):
            return "".join(lignes[:i]) + bloc + "".join(lignes[i:])
    base = notes if notes.strip() else "# Nouveautés d'Astrel\n"
    return base.rstrip("\n") + "\n\n" + bloc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Version d'Astrel et notes de version.")
    ap.add_argument("--monter", choices=("majeure", "mineure", "correctif"))
    ap.add_argument("--notes", nargs="?", const="", metavar="VERSION",
                    help="affiche les notes d'une version (par défaut : la version actuelle)")
    ap.add_argument("--verifier", action="store_true")
    args = ap.parse_args(argv)
    nv = _nouveautes()
    actuelle = nv.lire_version(_lire(VERSION))
    if not actuelle:
        print("VERSION absente ou illisible (attendu : une ligne MAJEUR.MINEUR.CORRECTIF).", file=sys.stderr)
        return 1

    if args.monter:
        nouvelle = nv.monter(actuelle, args.monter)
        VERSION.write_text(nouvelle + "\n", encoding="utf-8")
        NOTES.write_text(ajouter_section(_lire(NOTES), nouvelle, dt.date.today().isoformat()), encoding="utf-8")
        print(f"{actuelle} → {nouvelle}" + ("  (MAJEURE : la fenêtre des nouveautés s'ouvrira chez chacun)"
                                            if args.monter == "majeure" else ""))
        print(f"Remplis la section {nouvelle} de NOUVEAUTES.md avant de commiter.")
        return 0

    if args.notes is not None:
        cible = args.notes or actuelle
        sec = nv.section(_lire(NOTES), cible)
        if sec is None or not sec["texte"]:
            print(f"Aucune note pour {cible} dans NOUVEAUTES.md.", file=sys.stderr)
            return 1
        print(sec["texte"])
        return 0

    if args.verifier:
        sec = nv.section(_lire(NOTES), actuelle)
        if sec is None:
            print(f"NOUVEAUTES.md n'a pas de section {actuelle}.", file=sys.stderr)
            return 1
        if not sec["texte"] or A_REMPLIR in sec["texte"]:
            print(f"La section {actuelle} de NOUVEAUTES.md est encore à remplir.", file=sys.stderr)
            return 1
        print(f"{actuelle} : notes présentes.")
        return 0

    print(actuelle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
