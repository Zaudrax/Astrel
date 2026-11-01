#!/usr/bin/env python3
"""Coffre de mémoire d'Astrel, à la main : revue du conservateur, sauvegardes.

    .venv/bin/python tools/vault.py review              # propositions (rien n'est modifié)
    .venv/bin/python tools/vault.py snapshot            # instantané du coffre maintenant
    .venv/bin/python tools/vault.py snapshots           # instantanés gardés
    .venv/bin/python tools/vault.py restore-snapshot ID # remet tout le coffre (état actuel gardé avant)
    .venv/bin/python tools/vault.py versions NOTE       # anciennes versions d'une note
    .venv/bin/python tools/vault.py restore NOTE ID     # rétablit une version (actuelle gardée avant)

Aucun appel de modèle. Un retour arrière ne supprime jamais de note.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrel.tools import memory, vault_backup, vault_review  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rv = sub.add_parser("review", help="revue du conservateur")
    rv.add_argument("--json", action="store_true")
    sub.add_parser("snapshot", help="instantané du coffre")
    sub.add_parser("snapshots", help="instantanés gardés")
    rs = sub.add_parser("restore-snapshot", help="remet tout le coffre à un instantané")
    rs.add_argument("id")
    vs = sub.add_parser("versions", help="anciennes versions d'une note")
    vs.add_argument("note")
    rn = sub.add_parser("restore", help="rétablit une version d'une note")
    rn.add_argument("note")
    rn.add_argument("id")
    args = ap.parse_args(argv)

    if args.cmd == "review":
        report, new = vault_review.run()
        if args.json:
            print(json.dumps({**report, "nouveaux": sorted(new)}, ensure_ascii=False, indent=1))
        else:
            print(vault_review.to_markdown(report))
            print(f"({len(new)} point(s) nouveau(x) depuis la passe précédente ; rapport : {vault_review.REPORT_MD})")
    elif args.cmd == "snapshot":
        print(vault_backup.snapshot(memory.VAULT, "à la main"))
    elif args.cmd == "snapshots":
        for s in vault_backup.list_snapshots():
            print(f"{s['id']}  {s.get('fichiers', '?')} fichiers  {s.get('raison', '')}")
    elif args.cmd == "restore-snapshot":
        print(vault_backup.restore_snapshot(memory.VAULT, args.id))
    elif args.cmd == "versions":
        for v in memory.versions(args.note):
            print(f"{v['id']}  {v['date']}  {v['caracteres']} octets")
    elif args.cmd == "restore":
        print(memory.versions(args.note, args.id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
