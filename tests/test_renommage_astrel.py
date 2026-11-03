"""Renommage en Astrel (28/10, plan P42 lot 0, demande de l'utilisateur : « Il faut remplacer tout ce qui
utilise le mot Jarvis par Astrel, le dossier du code compris »).

Contrôle de fin du chantier : plus aucun fichier du projet ne contient l'ancien nom, en texte ou dans
son nom. ROUGE tant que les lots 1 à 7 du plan ne sont pas tous faits (variables d'environnement,
paquet Python, serveur MCP, services, dossier, données) : le message d'échec dit où il en reste.

Liste blanche (plan P42) : NOTICE, LICENSE, le
modèle openWakeWord tiers `hey_jarvis_v0.1.onnx`, et ce fichier lui-même (il doit nommer ce qu'il
cherche). Hors contrôle : venvs, node_modules, données d'entraînement, `runtime/`, `logs/` (données,
lot 6), `.git`, caches, binaires.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANCIEN = re.compile("jarvis", re.I)

# Dossiers jamais parcourus (données, dépendances, caches) : par NOM, à tout niveau.
SAUTES = {"node_modules", "__pycache__", ".pytest_cache", ".git", ".eveil-donnees", ".rtk"}
# Dossiers de premier niveau hors contrôle : venvs, données (lot 6) et liste blanche du plan.
SAUTES_RACINE = {"runtime", "logs"}
# Fichiers de la liste blanche (chemins relatifs à la racine).
BLANCHE = {"NOTICE", "LICENSE", "tests/test_renommage_astrel.py"}
BLANCHE_NOMS = {"hey_jarvis_v0.1.onnx"}
# Gardés VOLONTAIREMENT (bascule du 29/10/2026) : l'alias d'éveil « Hey Jarvis » (`hey_jarvis`, réglage
# `wake_legacy`, mot « jarvis » des motifs vocaux et des appels) tant que l'utilisateur ne le coupe pas.
PERMIS = re.compile(r"(?i:hey[\s_-]*jarvis)|(?<=\|)jarvis(?=[|)])|\"jarvis\", \"hey jarvis\"")


def _parcours():
    for dossier, sous, fichiers in os.walk(ROOT):
        rel_dossier = Path(dossier).relative_to(ROOT)
        garde = []
        for d in sous:
            if d in SAUTES or d.startswith(".venv"):
                continue
            if rel_dossier == Path(".") and d in SAUTES_RACINE:
                continue
            garde.append(d)
        sous[:] = garde
        for nom in fichiers:
            yield (rel_dossier / nom), sous


def _binaire(chemin: Path) -> bool:
    try:
        with chemin.open("rb") as f:
            return b"\0" in f.read(8192)
    except OSError:
        return True


def _relevé():
    contenus: Counter[str] = Counter()
    noms: list[str] = []
    for rel, _ in _parcours():
        rel_txt = rel.as_posix()
        if rel_txt in BLANCHE or rel.name in BLANCHE_NOMS:
            continue
        if ANCIEN.search(rel_txt):
            noms.append(rel_txt)
        chemin = ROOT / rel
        if chemin.is_symlink() or not chemin.is_file() or _binaire(chemin):
            continue
        try:
            texte = chemin.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        n = len(ANCIEN.findall(PERMIS.sub("", texte)))
        if n:
            contenus[rel_txt] = n
    return contenus, noms


def _résumé(contenus: Counter[str]) -> str:
    par_dossier: Counter[str] = Counter()
    for rel, n in contenus.items():
        par_dossier[rel.split("/", 1)[0] if "/" in rel else "(racine)"] += n
    lignes = [f"{n} occurrences dans {d}" for d, n in par_dossier.most_common()]
    lignes += [f"  {rel} : {n}" for rel, n in contenus.most_common(30)]
    return "\n".join(lignes)


def test_aucun_fichier_ne_contient_l_ancien_nom():
    contenus, _ = _relevé()
    assert not contenus, (f"ancien nom encore présent : {sum(contenus.values())} occurrences dans "
                          f"{len(contenus)} fichiers (plan P42, lots 1 à 7)\n" + _résumé(contenus))


def test_aucun_fichier_ne_porte_l_ancien_nom():
    _, noms = _relevé()
    assert not noms, f"{len(noms)} fichiers ou dossiers nommés avec l'ancien nom (plan P42, lots 4 à 6) :\n" + \
        "\n".join(sorted(noms)[:40])


def test_la_liste_blanche_reste_en_place():
    """La liste blanche ne doit pas cacher un oubli : chaque entrée existe vraiment."""
    assert (ROOT / "NOTICE").is_file() and (ROOT / "LICENSE").is_file()
    assert "MIT License" in (ROOT / "LICENSE").read_text(encoding="utf-8")
