"""Mot d'éveil par le TEXTE (25/10, plan P36, demande de l'utilisateur : « Astrel » au lieu de l'ancien mot d'éveil).

Le modèle audio `hey_jarvis` d'openWakeWord ne connaît qu'un mot, fixe. La voie texte suit le nom
affiché (`assistant_name`, « Astrel » par défaut) sans entraînement : une phrase courte captée par le
VAD est transcrite par un Whisper local (`listener.WakeWord`), puis ce module cherche le nom dans ses
premiers mots. Tout est PUR ici (ni micro, ni modèle, ni fichier), sauf `noms_actuels`.

Comparaison : textes pliés (minuscules, sans accents ni ponctuation), nom cherché dans les `MOTS`
premiers mots, mots voisins recollés (« a strel » = « astrel »), distance d'édition ≤ 1 pour un nom
d'au moins `LONGUEUR_FLOUE` lettres, égalité exacte en dessous. Whisper écrit « Astral » ou
« Astrelle » pour « Astrel » (essai du 23/10, Piper + Whisper base) : ce sont des variantes du réglage
`wake_names`. Quelques vrais mots à une lettre du nom (« astre », « astres », « austral ») sont
écartés, sauf s'ils sont eux-mêmes un nom choisi.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

# Le nom doit tomber dans les 3 premiers mots (« Hey Astrel », « OK Astrel », « Dis Astrel »).
MOTS = 3
# Sous cette longueur (nom plié, sans espace), seule l'égalité compte : « Max » ≠ « mas ».
LONGUEUR_FLOUE = 5
DISTANCE_MAX = 1
# Variantes par défaut du réglage `wake_names` (en plus du nom affiché), séparées par des virgules.
NOMS_DEFAUT = "astral, astrelle, a strel, hey astrel, ok astrel"
# Vrais mots français ou anglais à une lettre de « astrel » / « astral » : jamais pris pour le nom
# par la tolérance (« les astres », « l'hémisphère austral »). Un nom choisi égal l'emporte.
EXCLUS = frozenset({"astre", "astres", "austral", "australe", "australes", "austraux"})

_NON_MOT = re.compile(r"[^a-z0-9]+")


def plie(texte: str) -> str:
    """Minuscules, sans accents, mots séparés par une espace (« L'Astrel, » → « l astrel »)."""
    t = unicodedata.normalize("NFKD", str(texte or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return " ".join(_NON_MOT.sub(" ", t).split())


def distance(a: str, b: str) -> int:
    """Distance d'édition (Levenshtein) entre deux mots."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def proche(mot: str, cible: str) -> bool:
    """`mot` (plié, sans espace) vaut-il le nom `cible` (plié, sans espace) ?"""
    if not mot or not cible:
        return False
    if mot == cible:
        return True
    if len(cible) < LONGUEUR_FLOUE or mot in EXCLUS or abs(len(mot) - len(cible)) > DISTANCE_MAX:
        return False
    return distance(mot, cible) <= DISTANCE_MAX


def noms(nom_affiche: str, reglage: str | Iterable[str] = NOMS_DEFAUT) -> list[str]:
    """Noms qui réveillent : le nom affiché d'abord, puis les variantes du réglage (texte « a, b » ou
    liste), sans doublon (comparés pliés), sans vide."""
    variantes = reglage.split(",") if isinstance(reglage, str) else list(reglage or ())
    vus: set[str] = set()
    out: list[str] = []
    for n in [nom_affiche, *variantes]:
        n = " ".join(str(n or "").split())
        cle = plie(n).replace(" ", "")
        if cle and cle not in vus:
            vus.add(cle)
            out.append(n)
    return out


def trouve(texte: str, noms_eveil: Iterable[str], mots: int = MOTS) -> str:
    """Le nom (tel qu'écrit dans `noms_eveil`) reconnu dans les `mots` premiers mots de `texte`, sinon
    « ». Les mots voisins sont aussi essayés recollés (« a strel », « hey astrel »)."""
    tete = plie(texte).split()[:mots]
    if not tete:
        return ""
    cibles = [(n, plie(n).replace(" ", "")) for n in noms_eveil]
    exactes = {c for _n, c in cibles if c}
    for i in range(len(tete)):
        for k in range(1, len(tete) - i + 1):
            morceau = "".join(tete[i:i + k])
            if morceau in exactes:
                return next(n for n, c in cibles if c == morceau)
    for i in range(len(tete)):
        for k in range(1, len(tete) - i + 1):
            morceau = "".join(tete[i:i + k])
            for n, c in cibles:
                if proche(morceau, c):
                    return n
    return ""


def mots_indices(noms_eveil: Iterable[str]) -> str:
    """`hotwords` de Whisper : le nom affiché SEUL (le premier). Mesuré le 25/10 (banc, Piper) : la liste
    complète (« Astrel, astral, astrelle ») ratait plus de « Astrel » et faisait écrire « Astel » pour
    « Estelle » ; les variantes restent acceptées par `trouve`."""
    for n in noms_eveil:
        if n and n.strip():
            return n.strip()
    return ""


def noms_actuels() -> list[str]:
    """Noms en vigueur : nom affiché (`i18n.assistant_name`) + réglage `wake_names` (repli :
    `config.WAKE_NAMES`). Relu à chaque appel (réglage « hot »)."""
    from . import config, i18n

    reglage = getattr(config, "WAKE_NAMES", NOMS_DEFAUT)
    try:
        from .settings import reglages

        reglage = reglages.value("wake_names")
    except Exception:
        pass  # réglage absent (ancien settings.py) ou illisible : valeur de config
    return noms(i18n.assistant_name(), reglage if isinstance(reglage, (str, list, tuple)) else NOMS_DEFAUT)
