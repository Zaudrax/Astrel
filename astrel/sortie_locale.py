"""Sortie du moteur local nettoyée et contrôlée par le CŒUR (05/11/2026, lot 8, docs/chantiers/HYBRIDE-QWEN.md ;
rapport docs/chantiers/RECHERCHE-QWEN-2026-11-05.md, recommandations R1 et R3).

Un 9B suit mal les consignes de forme (IFBench 64,5) : ce que les consignes n'obtiennent pas toujours, un filtre
DÉTERMINISTE le garantit, sur le texte AFFICHÉ et DIT (il passe avant la voix et le HUD) :
- salutation en tête (« Bonjour <prénom> ! »), auto-présentation en tête (« En tant qu'Astrel… », « Je suis
  Astrel, … ») — sauf si l'utilisateur demande qui il est ;
- incise « sans Internet / en local » en tête de réponse, et toute REDITE de son fonctionnement dans la même réponse
  (la première mention reste : c'est souvent la raison d'un refus, permise par les consignes) — sauf si la
  demande parle d'Internet ou du local ;
- chemins internes (dossier jetable /tmp/astrel-local-…, configuration du CLI local) : le nom du fichier seul ;
- prénom en apostrophe (« …, <prénom>. »).
05/11 (prénom en réglage) : le prénom vient du PROFIL de l'utilisateur (`prenom()`, `config/profile.json` « nom »,
la même source que l'invite de Claude) ; aucun prénom n'est écrit dans ce code. Sans prénom : seules les
salutations génériques (« Bonjour ! », « Salut, ») sont retirées, et aucune apostrophe.
Rien n'est touché dans un bloc de code. Le texte passe phrase par phrase (la voix aussi le dit ainsi).

R1 : `marquer_urls` marque « (adresse non vérifiée) » toute adresse de la réponse qui n'a pas été VUE pendant le
tour (message de l'utilisateur, résultat d'un outil) : un 9B invente des sites (82 % d'hallucination sur les faits).
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Iterable

# Demande qui porte sur l'identité ou le fonctionnement : alors la présentation et la mention du local restent.
_SUR_SOI = re.compile(r"\b(qui es[- ]tu|tu es qui|présente[- ]toi|t'appelles|ton nom|qu'est[- ]ce que tu es|"
                      r"who are you|what are you|introduce yourself|your name)\b", re.I)
_SUR_INTERNET = re.compile(r"internet|en ligne|\bweb\b|\blocal|hors[- ]ligne|connexion|réseau|online|offline|"
                           r"\bsite|cherche|search|machine|fonctionnes|how do you work", re.I)

_SALUTS = r"bonjour|bonsoir|salut|coucou|hello|hey|hi"
_GENERIQUES = r"à toi|toi|there"
PRENOM_MAX = 40


def prenom() -> str:
    """05/11 (prénom en réglage) : prénom de l'utilisateur, celui de son PROFIL (`config/profile.json`, clé « nom » :
    la même source que l'invite de Claude, `brain.personalize`) ; "" sans profil (dépôt public, nouvelle machine).
    Une ligne, sans crochet ni caractère de contrôle, `PRENOM_MAX` caractères au plus."""
    try:
        from . import config
        brut = str((config.PROFILE or {}).get("nom") or "")
    except Exception:
        brut = ""
    return " ".join(re.sub(r"[\x00-\x1f\x7f<>\[\]{}]", " ", brut).split())[:PRENOM_MAX].strip()


def _motifs_prenom(nom: str) -> tuple[re.Pattern, re.Pattern]:
    """(salutation en tête, prénom en apostrophe) pour ce prénom. Sans prénom : salutation GÉNÉRIQUE seule (suivie
    d'une ponctuation ou de la fin : « Bonjour Léa ! » n'y perd pas que « Bonjour »), aucune apostrophe."""
    n = re.escape(nom) if nom else ""
    apres = rf"(?:\s+(?:{n + '|' if n else ''}{_GENERIQUES}))?"
    salut = re.compile(rf"^\s*(?:{_SALUTS})\b{apres}\s*(?:[,!.…:]+\s*|$)" if not n else
                       rf"^\s*(?:{_SALUTS})\b{apres}\s*[,!.…:]*\s*", re.I)
    apostrophe = re.compile(rf",\s*{n}\s*(?=[.!?…])|^\s*{n}\s*,\s*", re.I) if n else re.compile(r"(?!x)x")
    return salut, apostrophe
_PRESENTE = re.compile(r"^\s*(?:en tant qu['’]\s*(?:astrel|assistant|ia|intelligence artificielle|modèle|mod[eè]le)"
                       r"[^,.:;!?\n]*[,:]\s*|as (?:astrel|an? (?:ai|assistant|local model))[^,.:;!?\n]*[,:]\s*)", re.I)
_JE_SUIS = re.compile(r"^\s*(?:je suis astrel|moi,? c'est astrel|ici astrel|i am astrel|i'm astrel|astrel here)"
                      r"[^.!?\n]*[.!?]\s*", re.I)
_INCISE_LOCAL = re.compile(r"^\s*(?:(?:en local|localement|hors ligne|sans (?:accès à |connexion )?internet|"
                           r"(?:étant |comme je suis |je fonctionne )?(?:en mode local|sur ta machine))"
                           r"(?:\s*(?:,|et)\s*(?:sans (?:accès à |connexion )?internet|en local|hors ligne))?\s*,\s*|"
                           r"(?:locally|offline|without (?:internet|web) access)\s*,\s*)", re.I)
# Phrase de tête qui ne dit QUE son fonctionnement (« Je fonctionne en local, sans Internet. ») : retirée.
_PHRASE_LOCAL = re.compile(r"^\s*(?:je (?:fonctionne|tourne|suis|travaille|réponds)|j['’]opère|étant|comme je suis|"
                           r"i (?:run|work|am running)|running)\b[^.!?\n]{0,80}?(?:en local|localement|sans (?:accès à |"
                           r"connexion )?internet|hors ligne|sur ta machine|locally|offline|without internet)"
                           r"[^.!?\n]{0,60}[.!?]\s*$", re.I)
_MENTION_LOCAL = re.compile(r"sans (?:accès à |connexion )?internet|pas (?:d['’])?accès à internet|"
                            r"(?:accès|connexion) (?:à |au )?(?:internet|web)|hors ligne|\ben local\b|localement|"
                            r"mode local|modèle local|sur ta machine|no internet|without internet|"
                            r"no (?:web|internet) access|locally|offline", re.I)
_PARENTHESE_LOCAL = re.compile(r"\s*\((?:[^()]*?(?:internet|en local|hors ligne|offline|locally)[^()]*)\)", re.I)
_FIN_PHRASE = re.compile(r"(?<=[.!?…])\s+|\n")


def _internes() -> list[str]:
    racines = {"/tmp", "/var/tmp", tempfile.gettempdir().replace("\\", "/")}
    return sorted(racines)


def _motif_interne() -> re.Pattern:
    tmp = "|".join(re.escape(r) for r in _internes())
    try:
        from . import config
        cfg = re.escape(str(config.RUNTIME / "outils-local").replace("\\", "/"))
    except Exception:
        cfg = r"(?!x)x"
    return re.compile(rf"(?:(?:{tmp})/astrel-local-[^\s/`'\")»,;]*|{cfg})(/[^\s`'\")»,;]*)?")


_INTERNE = _motif_interne()
_DOSSIER_TECH = {"fr": "le dossier technique", "en": "the technical folder"}


def _chemins_internes(texte: str, langue: str) -> str:
    def rempl(m: re.Match) -> str:
        reste = (m.group(1) or "").strip("/")
        return Path(reste).name if reste else _DOSSIER_TECH.get(langue, _DOSSIER_TECH["fr"])
    return _INTERNE.sub(rempl, texte)


def _majuscule(texte: str) -> str:
    i = next((k for k, c in enumerate(texte) if c.isalpha()), None)
    return texte if i is None else texte[:i] + texte[i].upper() + texte[i + 1:]


class FiltreSortie:
    """Filtre en flux : `feed(morceau)` rend ce qui peut partir (phrases finies), `flush()` le reste."""

    def __init__(self, demande: str = "", langue: str = "fr", prenom_: str | None = None) -> None:
        self.langue = langue if langue in ("fr", "en") else "fr"
        # 05/11 : `prenom_` (tests, banc) ; None = celui du profil
        self._salut, self._apostrophe = _motifs_prenom(prenom() if prenom_ is None else str(prenom_ or "").strip())
        self.garder_soi = bool(_SUR_SOI.search(demande or ""))
        self.garder_local = bool(_SUR_INTERNET.search(demande or ""))
        self._buf, self._debut, self._mentions, self._code = "", True, 0, False
        self._retenu, self._rendu = "", False  # tête retirée ; rendue si rien d'autre ne vient (« Bonjour ! » seul)

    def feed(self, morceau: str) -> str:
        self._buf += str(morceau or "")
        coupe = None
        for m in _FIN_PHRASE.finditer(self._buf):
            coupe = m.end()
        if coupe is None:
            return ""
        pret, self._buf = self._buf[:coupe], self._buf[coupe:]
        return self._traiter(pret)

    def flush(self) -> str:
        pret, self._buf = self._buf, ""
        out = self._traiter(pret)
        if not self._rendu and not out.strip() and self._retenu.strip():
            out = self._retenu.strip()  # la réponse n'était qu'une salutation : elle reste
        return out

    def _traiter(self, texte: str) -> str:
        out = self._traiter_texte(texte)
        self._rendu = self._rendu or bool(out.strip())
        return out

    def _traiter_texte(self, texte: str) -> str:
        out = []
        for morceau in re.split(r"(```)", texte):
            if morceau == "```":
                self._code = not self._code
                self._debut = False
                out.append(morceau)
            elif self._code:
                out.append(morceau)
            else:
                out.append(self._phrases(morceau))
        return "".join(out)

    def _phrases(self, texte: str) -> str:
        out, pos = [], 0
        for m in list(_FIN_PHRASE.finditer(texte)) + [None]:
            fin = m.end() if m else len(texte)
            phrase = texte[pos:fin]
            pos = fin
            if phrase:
                out.append(self._phrase(phrase))
        return "".join(out)

    def _phrase(self, p: str) -> str:
        p = _chemins_internes(p, self.langue)
        p = self._apostrophe.sub("", p)
        if not self.garder_local:
            p = _PARENTHESE_LOCAL.sub("", p)
        if self._debut:
            if not p.strip():
                return "" if not p.strip("\n ") else p
            avant = p
            if not self.garder_soi:
                p = self._salut.sub("", p, count=1)
                p = _JE_SUIS.sub("", p, count=1)
                p = _PRESENTE.sub("", p, count=1)
            if not self.garder_local:
                p = _PHRASE_LOCAL.sub("", p, count=1)
                p = _INCISE_LOCAL.sub("", p, count=1)
            if not p.strip():
                self._retenu += avant
                return ""  # la phrase n'était qu'une salutation : la suivante devient le début
            if p != avant:
                p = _majuscule(p.lstrip())
            self._debut = False
        if not self.garder_local and _MENTION_LOCAL.search(p):
            self._mentions += 1
            if self._mentions > 1:  # redite de son fonctionnement dans la même réponse
                return "\n" if p.endswith("\n") else ""
        return p


def filtrer(texte: str, demande: str = "", langue: str = "fr", prenom_: str | None = None) -> str:
    """Le texte entier filtré (même résultat qu'en flux)."""
    f = FiltreSortie(demande, langue, prenom_)
    return (f.feed(texte) + f.flush()).strip()


# ------------------------------------------------------------------ R1 : adresses non vues dans le tour

_URL = re.compile(r"\bhttps?://[^\s<>\"'`»)\]]+|\bwww\.[a-z0-9-]+(?:\.[a-z0-9-]+)+[^\s<>\"'`»)\],;]*|"
                  r"(?<![@\w.-])[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:gouv\.fr|fr|com|org|net|io|eu|info|be|ch|ai|app|dev)"
                  r"\b(?:/[^\s<>\"'`»)\],;]*)?")
MARQUE = {"fr": " (adresse non vérifiée)", "en": " (unverified address)"}


def _cle(url: str) -> str:
    u = url.lower().rstrip(".,;:!?")
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.rstrip("/")


def urls(texte: str) -> list[str]:
    """Adresses écrites dans un texte (http(s)://, www., domaine nu des extensions courantes)."""
    return [m.group(0).rstrip(".,;:!?") for m in _URL.finditer(str(texte or ""))]


def marquer_urls(texte: str, vus: Iterable[str] = (), langue: str = "fr") -> str:
    """Ajoute la marque « (adresse non vérifiée) » après chaque adresse qui n'apparaît dans AUCUN des textes
    `vus` (message de l'utilisateur, faits du tour, résultats d'outils). Une adresse vue = son domaine et son chemin
    écrits tels quels dans ce que le tour a reçu. Rien n'est retiré : l'utilisateur voit l'adresse et sait qu'elle
    n'a pas été vérifiée. Dans un bloc de code : rien."""
    marque = MARQUE.get(langue, MARQUE["fr"])
    vu = " ".join(_cle(x) for v in vus for x in [str(v or "")] + urls(v)).lower()
    out, pos, code = [], 0, False
    for part in re.split(r"(```)", str(texte or "")):
        if part == "```":
            code = not code
            out.append(part)
            continue
        if code:
            out.append(part)
            continue
        res, pos = [], 0
        for m in _URL.finditer(part):
            brut = m.group(0)
            url = brut.rstrip(".,;:!?")
            fin = m.start() + len(url)
            if not _cle(url) or _cle(url) in vu or part[fin:fin + len(marque)] == marque:
                continue
            # lien markdown [texte](url) : la marque après la parenthèse
            if part[fin:fin + 1] == ")" and part[:m.start()].endswith("]("):
                fin += 1
            res.append(part[pos:fin] + marque)
            pos = fin
        res.append(part[pos:])
        out.append("".join(res))
    return "".join(out)
