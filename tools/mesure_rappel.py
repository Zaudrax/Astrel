#!/usr/bin/env python3
"""Mesure du rappel automatique de la mémoire (`astrel/rappel.py`) sur les VRAIES
phrases de l'utilisateur, lues dans l'index de l'historique (`runtime/history.db`).

    .venv/bin/python tools/mesure_rappel.py                 # chiffres globaux
    .venv/bin/python tools/mesure_rappel.py --sample 120    # + échantillon à juger
    .venv/bin/python tools/mesure_rappel.py --note-min 4 --proc-min 5   # autre seuil

Rien n'est écrit (ni l'index de l'historique ni `rappel-vus.json`) : on appelle
`rappel.suggest`, sans filtre de session. Résultat de la mesure du 15/10/2026 et
méthode de jugement : `docs/RAPPEL-MEMOIRE.md`.
"""
from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from astrel import config, rappel  # noqa: E402


def phrases(db: Path) -> list[str]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [t for (t,) in con.execute(
            "SELECT text FROM messages WHERE role = 'user' AND auto = 0 ORDER BY ts")]
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=config.HISTORY_DB)
    ap.add_argument("--sample", type=int, default=0, help="nombre de phrases NON triviales à afficher")
    ap.add_argument("--seed", type=int, default=15)
    ap.add_argument("--only-hits", action="store_true", help="n'afficher que les phrases avec une proposition")
    ap.add_argument("--width", type=int, default=220, help="caractères de la phrase affichés")
    ap.add_argument("--note-min", type=float, default=rappel.NOTE_MIN_SCORE)
    ap.add_argument("--proc-min", type=float, default=rappel.PROCEDURE_MIN_SCORE)
    args = ap.parse_args()
    rappel.NOTE_MIN_SCORE, rappel.PROCEDURE_MIN_SCORE = args.note_min, args.proc_min

    rows = phrases(args.db)
    kinds: Counter = Counter()
    per_note: Counter = Counter()
    done = []
    for prompt in rows:
        if rappel.is_automatic(prompt):
            kinds["automatique"] += 1
            continue
        if rappel.is_trivial(rappel.clean(prompt)):
            kinds["trivial"] += 1
            continue
        why: dict = {}
        notes, proc = rappel.suggest(prompt, why)
        kinds["avec note" if notes else "sans note"] += 1
        kinds["avec procédure"] += bool(proc)
        per_note.update(notes + ([proc] if proc else []))
        done.append((prompt, notes, proc, why))

    print(f"{len(rows)} messages de l'utilisateur ; seuils note {args.note_min}, procédure {args.proc_min}")
    for k in ("automatique", "trivial", "sans note", "avec note", "avec procédure"):
        print(f"  {k:16} {kinds[k]:5}")
    print("notes les plus proposées :")
    for path, n in per_note.most_common(12):
        print(f"  {n:4}  {path}")
    if args.sample:
        random.seed(args.seed)
        picked = random.sample(done, min(args.sample, len(done)))
        if args.only_hits:
            picked = [d for d in picked if d[1] or d[2]]
        for prompt, notes, proc, why in picked:
            print("\n###", " ".join(rappel.clean(prompt).split())[:args.width])
            for p in notes:
                print("   note       ", p, why.get(p))
            if proc:
                print("   procédure  ", proc, why.get(proc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
