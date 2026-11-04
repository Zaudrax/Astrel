"""Données sensibles d'une demande (04/11/2026, lot 3 du moteur local, docs/chantiers/HYBRIDE-QWEN.md).

`scan(texte, pieces)` dit, SANS modèle et toujours pareil pour le même texte, pourquoi une demande ne doit pas
partir chez Claude (Anthropic) sans l'accord de l'utilisateur : un secret (motifs du filtre de la mémoire), un IBAN
(clé vérifiée), un numéro de carte bancaire (Luhn), un numéro de sécurité sociale (clé vérifiée), un fichier
secret du garde-fou, ou un chemin dans une « zone confidentielle » (Réglages › Confidentialité). Il rend des
RAISONS courtes, jamais la valeur trouvée : elles vont dans la carte, le journal et `logs/routage.jsonl`.

Le cœur décide avec (`routeur.decider`) ; le garde-fou lit les mêmes zones (`guard.zones_confidentielles`) pour
ce que Claude lirait lui-même (Read, Grep, `cat`…).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

from .tools import memory_scan

# IBAN : 2 lettres, 2 chiffres de clé, 11 à 30 caractères (espaces permis par groupes de 4).
_IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30})\b")
# Carte : 13 à 19 chiffres, groupés par espaces ou tirets.
_CARTE = re.compile(r"(?<![\d-])(\d(?:[ -]?\d){12,18})(?![\d-])")
# Sécurité sociale (France) : sexe, année, mois, département (2A/2B), commune, ordre, CLÉ (15 caractères).
_NIR = re.compile(r"(?<!\d)([12])[ .]?(\d{2})[ .]?(0[1-9]|1[0-2]|[2-9]\d)[ .]?(\d{2}|2[AB])[ .]?(\d{3})[ .]?(\d{3})"
                  r"[ .]?(\d{2})(?!\d)")
# Ce qui ressemble à un chemin dans le texte : absolu, ~/…, ./…, ../…
_CHEMIN = re.compile(r"(?:(?<=\s)|^|(?<=[\"'(«]))((?:~|\.{1,2})?/[^\s\"'`)»,;]+|[A-Za-z]:\\[^\s\"'`)»,;]+)")


def _iban_valide(brut: str) -> bool:
    s = brut.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    r = s[4:] + s[:4]
    try:
        n = int("".join(str(int(c, 36)) for c in r))
    except ValueError:
        return False
    return n % 97 == 1


def _luhn(chiffres: str) -> bool:
    total, double = 0, False
    for c in reversed(chiffres):
        d = int(c) * (2 if double else 1)
        total += d - 9 if d > 9 else d
        double = not double
    return total % 10 == 0


def _carte_valide(brut: str) -> bool:
    s = re.sub(r"[ -]", "", brut)
    if not 13 <= len(s) <= 19 or len(set(s)) == 1 or s[0] not in "2345679":
        return False
    return _luhn(s)


def _nir_valide(m: re.Match) -> bool:
    corps = "".join(m.groups()[:6])
    cle = int(m.group(7))
    corps = corps.replace("2A", "19").replace("2B", "18")  # départements corses : règle officielle de la clé
    try:
        return 97 - int(corps) % 97 == cle
    except ValueError:
        return False


def zones() -> list[Path]:
    """Zones confidentielles (dossiers absolus), lues comme le garde-fou les lit."""
    from . import guard
    return guard.zones_confidentielles()


def _dedans(p: Path, racines: Iterable[Path]) -> bool:
    for r in racines:
        try:
            p.relative_to(r)
            return True
        except ValueError:
            continue
    return False


def _chemin_sensible(brut: str, racines_secret: list[Path], zs: list[Path]) -> str:
    """Raison si `brut` (chemin cité ou pièce jointe) désigne un secret du garde-fou ou une zone confidentielle."""
    try:
        p = Path(os.path.expanduser(brut.strip().strip("'\"«» "))).resolve()
    except (OSError, ValueError, RuntimeError):
        return ""
    if not p.is_absolute():
        return ""
    if _dedans(p, racines_secret):
        return "fichier secret d'Astrel"
    if _dedans(p, zs):
        return "chemin dans une zone confidentielle"
    return ""


def scan(texte: str, pieces: Iterable[str] = (), zones_conf: list[Path] | None = None) -> list[str]:
    """Raisons (sans doublon, dans l'ordre) qui rendent la demande sensible ; [] si rien.
    `pieces` : chemins des pièces jointes (ou leurs noms, alors seul le texte qu'elles apportent compte)."""
    texte = str(texte or "")
    raisons: list[str] = []

    def ajoute(r: str) -> None:
        if r and r not in raisons:
            raisons.append(r)

    for nature in memory_scan.find_secrets(texte):
        ajoute(nature)
    for m in _IBAN.finditer(texte.upper()):
        if _iban_valide(m.group(1)):
            ajoute("IBAN")
            break
    for m in _CARTE.finditer(texte):
        if _carte_valide(m.group(1)):
            ajoute("numéro de carte bancaire")
            break
    for m in _NIR.finditer(texte.upper()):
        if _nir_valide(m):
            ajoute("numéro de sécurité sociale")
            break
    try:
        from . import guard
        secrets = list(guard.SECRET_PATHS)
    except Exception:  # garde-fou illisible : on ne voit pas les secrets, la suite du scan tient
        secrets = []
    zs = zones() if zones_conf is None else list(zones_conf)
    for m in _CHEMIN.finditer(texte):
        ajoute(_chemin_sensible(m.group(1), secrets, zs))
    for piece in pieces:
        r = _chemin_sensible(str(piece), secrets, zs)
        if r:
            ajoute(f"pièce jointe : {r}")
    return raisons
