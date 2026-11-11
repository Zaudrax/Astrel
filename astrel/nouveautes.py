"""Versions et notes de version d'Astrel (10/11/2026).

Fichiers de la racine, suivis par git et publiés dans le dépôt partagé :
- `VERSION` : une ligne `MAJEUR.MINEUR.CORRECTIF` (ex. `2.1.0`). Hors de l'empreinte du garde-fou
  (contrairement à `astrel/__init__.py`) : monter la version n'oblige pas à `./run.sh doctor --accept`.
- `NOUVEAUTES.md` : une section `## 2.1.0 — 2026-11-10` par version, la plus récente en haut.

Module PUR (seulement `re`) : `update.py` le charge par son chemin, avant même que les dépendances
d'Astrel soient installées. Utilisé par `maj.py` (vérification et évènement `maj_dispo`) et
`tools/version.py` (monter la version, sortir les notes d'une Release GitHub).
"""
from __future__ import annotations

import re

_VERSION = re.compile(r"^\s*v?(\d+)\.(\d+)(?:\.(\d+))?")
# « ## 2.1.0 », « ## 2.1.0 — 2026-11-10 », « ## [2.1.0] - 2026-11-10 »
_TITRE = re.compile(r"^##\s+\[?v?(\d+\.\d+(?:\.\d+)?)\]?\s*(?:[—–-]+\s*(.*?))?\s*$")

NOTES_MAX_SECTIONS = 10
NOTES_MAX_OCTETS = 8192


def parse(texte: str | None) -> tuple[int, int, int] | None:
    """`"2.1.0"` → (2, 1, 0) ; suffixe ignoré (`2.0.0-dev` → (2, 0, 0)) ; illisible → None."""
    m = _VERSION.match(texte or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def lire_version(texte: str | None) -> str:
    """Contenu du fichier `VERSION` → la version (première ligne non vide), « » si illisible."""
    for ligne in (texte or "").splitlines():
        ligne = ligne.strip()
        if ligne:
            return ligne if parse(ligne) else ""
    return ""


def plus_recente(a: str | None, b: str | None) -> bool:
    """Vrai si `a` est strictement plus récente que `b` (une version illisible ne l'est jamais)."""
    va, vb = parse(a), parse(b)
    return va is not None and (vb is None or va > vb)


def est_majeure(nouvelle: str | None, actuelle: str | None) -> bool:
    """Saut de version majeure (2.x → 3.0) : le HUD l'annonce en grand, pas d'une simple pastille."""
    vn, va = parse(nouvelle), parse(actuelle)
    return vn is not None and va is not None and vn[0] > va[0]


def sections(texte: str | None) -> list[dict]:
    """Sections `## <version>` de NOUVEAUTES.md, dans l'ordre du fichier : {version, date, texte}."""
    out: list[dict] = []
    courant: dict | None = None
    for ligne in (texte or "").splitlines():
        m = _TITRE.match(ligne)
        if m:
            courant = {"version": m.group(1), "date": (m.group(2) or "").strip(), "lignes": []}
            out.append(courant)
        elif courant is not None:
            courant["lignes"].append(ligne)
    return [{"version": s["version"], "date": s["date"], "texte": "\n".join(s["lignes"]).strip()} for s in out]


def section(texte: str | None, version: str) -> dict | None:
    """La section d'une version précise (`tools/version.py --notes`), None si absente."""
    cible = parse(version)
    return next((s for s in sections(texte) if parse(s["version"]) == cible), None)


def depuis(texte: str | None, actuelle: str | None, jusqua: str | None = None) -> list[dict]:
    """Notes des versions PLUS RÉCENTES que `actuelle` (et pas au-delà de `jusqua`), la plus récente
    d'abord ; bornées (`NOTES_MAX_SECTIONS`, `NOTES_MAX_OCTETS`, une section trop longue est coupée)."""
    choisies = [s for s in sections(texte)
                if plus_recente(s["version"], actuelle) and not (jusqua and plus_recente(s["version"], jusqua))]
    choisies.sort(key=lambda s: parse(s["version"]) or (0, 0, 0), reverse=True)
    out: list[dict] = []
    reste = NOTES_MAX_OCTETS
    for s in choisies[:NOTES_MAX_SECTIONS]:
        brut = s["texte"].encode("utf-8")
        if len(brut) > reste:
            s = {**s, "texte": brut[:max(reste, 0)].decode("utf-8", "ignore").rstrip() + "\n…"}
        out.append(s)
        reste -= len(s["texte"].encode("utf-8"))
        if reste <= 0:
            break
    return out


def monter(version: str, cran: str) -> str:
    """`monter("2.1.3", "mineure")` → `2.2.0` ; cran = majeure, mineure ou correctif."""
    v = parse(version)
    if v is None:
        raise ValueError(f"version illisible : {version!r}")
    if cran == "majeure":
        return f"{v[0] + 1}.0.0"
    if cran == "mineure":
        return f"{v[0]}.{v[1] + 1}.0"
    if cran == "correctif":
        return f"{v[0]}.{v[1]}.{v[2] + 1}"
    raise ValueError(f"cran inconnu : {cran!r} (majeure, mineure ou correctif)")
