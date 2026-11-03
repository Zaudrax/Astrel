"""Preuve de fin de mission (02/11/2026, plan P43, décision de l'utilisateur) : une mission n'est
FINIE que si son critère est VÉRIFIÉ ici, jamais sur la parole de Claude.

« MISSION : FINIE » n'est qu'une DÉCLARATION : elle déclenche ce contrôle, rien de plus.
Le critère est DÉCLARATIF et TYPÉ (aucune commande, aucun shell, aucun code du projet lancé) :

    fichier_existe     chemin, genre (fichier|dossier), taille_min ?
    fichier_contient   chemin, texte (littéral), absent ?
    empreinte          chemin, sha256
    dossier_contient   chemin, attendus (noms relatifs)
    git_head           depot, branche ?, avance ?   (.git LU en Python : git n'est jamais lancé ;
                       `avance` = HEAD DIFFÉRENT de celui du lancement, pas « un commit de plus » :
                       l'objet commit n'est pas lu, un retour en arrière passe aussi)
    commande_observee  dossier, commande   (lot C, 03/11 : le cœur ne lance RIEN ; Claude lance
                       lui-même « cd <dossier> && <commande> » avec son outil Bash, sous le garde-fou,
                       et le cœur lit dans le flux du CLI le code de sortie de CET appel, au premier
                       plan, sans aucune écriture après lui ; critère de l'utilisateur seulement)

plus `figer` (facultatif) : fichiers dont l'empreinte, relevée par le cœur au lancement, ne doit
pas avoir changé (des tests que la mission n'a pas le droit de retoucher).

Le périmètre est HOSTILE (Claude y écrit) : chaque chemin est ouvert composant par composant
depuis « / », relatif au descripteur du dossier parent (`dir_fd`), sans JAMAIS suivre un lien
(`O_NOFOLLOW` partout, l'équivalent de `openat2(RESOLVE_NO_SYMLINKS)`), dans un dossier du
périmètre seulement, sans changer de système de fichiers, ni lien dur, ni fichier spécial ; la
lecture se fait sur le descripteur déjà contrôlé (pas de course entre contrôle et lecture), et
un fichier qui change pendant la lecture est « instable ». Un secret (garde-fou) ou `runtime/` :
refusé. Rien n'est écrit, rien n'est lancé.

Le rapport ne contient que des CODES et des faits produits ici (taille, début d'empreinte,
nombre de manquants) : jamais le contenu d'un fichier. Sans `dir_fd` (Windows) : tout contrôle
de fichier est « bloqué », jamais réussi.

Ce module ne connaît ni le cœur ni le HUD : `mission.py` lui passe un instantané figé et un
`doit_arreter` (pause, arrêt, ESTOP), et décide seul de ce que le résultat devient.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shlex
import stat
import time
from collections.abc import Callable
from typing import Any

from . import config

VERSION = 1
MAX_JSON = 4096                 # critère sérialisé
MAX_CONTROLES = 10
MAX_FIGER = 20
MAX_ATTENDUS = 50
MAX_CHEMIN = 300
MAX_TEXTE = 200
MAX_CONTIENT = 2 * 1024 * 1024  # fichier lu par `fichier_contient`
MAX_EMPREINTE = 50 * 1024 * 1024
MAX_GIT = 2 * 1024 * 1024       # HEAD, ref, packed-refs
DUREE_MAX_S = 15.0
BLOC = 1024 * 1024

OK, ECHEC, BLOQUE = "ok", "echec", "bloque"
VERIFIEE, EN_ECHEC, BLOQUEE, INTERROMPUE = "verifiee", "echec", "bloquee", "interrompue"

_CHAMPS = {
    "fichier_existe": ({"type", "chemin", "genre"}, {"taille_min"}),
    "fichier_contient": ({"type", "chemin", "texte"}, {"absent"}),
    "empreinte": ({"type", "chemin", "sha256"}, set()),
    "dossier_contient": ({"type", "chemin", "attendus"}, set()),
    "git_head": ({"type", "depot"}, {"branche", "avance"}),
    "commande_observee": ({"type", "dossier", "commande"}, set()),
}
MAX_COMMANDE = 300
_GENRES = ("fichier", "dossier")
_SHA = re.compile(r"[0-9a-f]{64}")
_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_REF = re.compile(r"refs/heads/[A-Za-z0-9._/-]{1,200}")
_BRANCHE = re.compile(r"[A-Za-z0-9._/-]{1,100}")
_CONTROLE_CAR = re.compile(r"[\x00-\x1f\x7f]")
_PORTABLE = (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY") and os.open in os.supports_dir_fd
             and os.stat in os.supports_dir_fd)


class CritereInvalide(ValueError):
    """Critère refusé au lancement (le message dit pourquoi, en clair)."""


class _Arret(Exception):
    pass


class _Bloque(Exception):
    def __init__(self, code: str, **infos: Any) -> None:
        super().__init__(code)
        self.code, self.infos = code, infos


class _Absent(Exception):
    pass


# ------------------------------------------------------------------ schéma

def _chaine(v: Any, nom: str, maxi: int) -> str:
    if not isinstance(v, str) or not v or len(v) > maxi or _CONTROLE_CAR.search(v):
        raise CritereInvalide(f"« {nom} » : texte de 1 à {maxi} caractères, sans caractère de contrôle")
    return v


def _parties(chemin: str) -> list[str]:
    """Composants d'un chemin ABSOLU, sans « . » ni « .. » ni partie vide douteuse."""
    if not chemin.startswith("/"):
        raise CritereInvalide(f"chemin absolu attendu : {chemin[:80]}")
    parts = [p for p in chemin.split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise CritereInvalide(f"« . » et « .. » sont refusés dans un chemin : {chemin[:80]}")
    return parts


def _sous(parts: list[str], racine: list[str]) -> bool:
    return len(parts) >= len(racine) and parts[:len(racine)] == racine


def _racine_de(chemin: str, scope: list[str]) -> str:
    """Le dossier du périmètre qui contient `chemin` (le plus profond), ou une erreur."""
    parts = _parties(chemin)
    meilleure = ""
    for r in scope:
        rp = [p for p in str(r).split("/") if p]
        if rp and _sous(parts, rp) and len(rp) > len([p for p in meilleure.split("/") if p]):
            meilleure = "/" + "/".join(rp)
    if not meilleure:
        raise CritereInvalide(f"chemin hors du périmètre de la mission : {chemin[:80]}")
    return meilleure


def _chemin(v: Any, nom: str, scope: list[str]) -> str:
    s = _chaine(v, nom, MAX_CHEMIN)
    _racine_de(s, scope)
    return "/" + "/".join(_parties(s))


def _rel(v: Any) -> str:
    s = _chaine(v, "attendus", MAX_CHEMIN)
    if s.startswith("/"):
        raise CritereInvalide(f"« attendus » : nom relatif au dossier, pas absolu : {s[:80]}")
    parts = [p for p in s.split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        raise CritereInvalide(f"« attendus » : « . » et « .. » sont refusés : {s[:80]}")
    return "/".join(parts)


def _booleen(v: Any, nom: str) -> bool:
    if not isinstance(v, bool):
        raise CritereInvalide(f"« {nom} » : vrai ou faux")
    return v


def _controle(c: Any, scope: list[str]) -> dict:
    if not isinstance(c, dict):
        raise CritereInvalide("chaque contrôle est un objet {type, …}")
    t = c.get("type")
    if not isinstance(t, str) or t not in _CHAMPS:
        raise CritereInvalide(f"type de contrôle inconnu : {str(t)[:40]} (permis : {', '.join(_CHAMPS)})")
    requis, options = _CHAMPS[t]
    cles = set(c)
    if requis - cles:
        raise CritereInvalide(f"« {t} » : il manque {', '.join(sorted(requis - cles))}")
    if cles - requis - options:
        raise CritereInvalide(f"« {t} » : champ inconnu {', '.join(sorted(cles - requis - options))}")
    out: dict[str, Any] = {"type": t}
    if t == "commande_observee":
        out["dossier"] = _chemin(c["dossier"], "dossier", scope)
        out["commande"] = _chaine(c["commande"], "commande", MAX_COMMANDE).strip()
        if not out["commande"]:
            raise CritereInvalide("« commande » : vide")
        return out
    if t == "git_head":
        out["depot"] = _chemin(c["depot"], "depot", scope)
        if "branche" in c:
            b = _chaine(c["branche"], "branche", 100)
            if not _BRANCHE.fullmatch(b) or ".." in b:
                raise CritereInvalide("« branche » : nom de branche simple attendu")
            out["branche"] = b
        if "avance" in c:
            out["avance"] = _booleen(c["avance"], "avance")
        return out
    out["chemin"] = _chemin(c["chemin"], "chemin", scope)
    if t == "fichier_existe":
        if c["genre"] not in _GENRES:
            raise CritereInvalide("« genre » : fichier ou dossier")
        out["genre"] = c["genre"]
        if "taille_min" in c:
            v = c["taille_min"]
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 2 ** 31:
                raise CritereInvalide("« taille_min » : entier de 0 à 2³¹")
            out["taille_min"] = v
    elif t == "fichier_contient":
        out["texte"] = _chaine(c["texte"], "texte", MAX_TEXTE)
        if "absent" in c:
            out["absent"] = _booleen(c["absent"], "absent")
    elif t == "empreinte":
        sha = c["sha256"]
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise CritereInvalide("« sha256 » : 64 caractères hexadécimaux en minuscules")
        out["sha256"] = sha
    elif t == "dossier_contient":
        a = c["attendus"]
        if not isinstance(a, list) or not 1 <= len(a) <= MAX_ATTENDUS:
            raise CritereInvalide(f"« attendus » : liste de 1 à {MAX_ATTENDUS} noms")
        out["attendus"] = [_rel(x) for x in a]
    return out


def valider_critere(obj: Any, scope: list[str]) -> dict:
    """Critère typé → forme normalisée (chemins sans « // », champs connus seulement).
    Lève `CritereInvalide`. `scope` : dossiers ABSOLUS du périmètre de la mission."""
    if isinstance(obj, str):
        if len(obj.encode("utf-8")) > MAX_JSON:
            raise CritereInvalide(f"critère trop long (plus de {MAX_JSON} octets)")
        try:
            obj = json.loads(obj)
        except ValueError as exc:
            raise CritereInvalide(f"critère illisible (JSON attendu) : {exc}") from None
    if not isinstance(obj, dict):
        raise CritereInvalide("le critère est un objet {version, controles, figer}")
    if set(obj) - {"version", "controles", "figer"}:
        raise CritereInvalide(f"champ inconnu dans le critère : {', '.join(sorted(set(obj) - {'version', 'controles', 'figer'}))}")
    if obj.get("version", VERSION) != VERSION or isinstance(obj.get("version"), bool):
        raise CritereInvalide(f"version du critère inconnue (attendue : {VERSION})")
    try:
        taille = len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise CritereInvalide("critère illisible") from None
    if taille > MAX_JSON:
        raise CritereInvalide(f"critère trop long (plus de {MAX_JSON} octets)")
    controles = obj.get("controles")
    if not isinstance(controles, list) or not 1 <= len(controles) <= MAX_CONTROLES:
        raise CritereInvalide(f"« controles » : liste de 1 à {MAX_CONTROLES} contrôles")
    figer = obj.get("figer", [])
    if not isinstance(figer, list) or len(figer) > MAX_FIGER:
        raise CritereInvalide(f"« figer » : liste de {MAX_FIGER} fichiers au plus")
    scope = [str(s) for s in scope]
    return {"version": VERSION, "controles": [_controle(c, scope) for c in controles],
            "figer": list(dict.fromkeys(_chemin(f, "figer", scope) for f in figer))}


_MOTS = {
    "existe": "existe", "exists": "existe", "dossier": "dossier", "dir": "dossier",
    "contient": "contient", "contains": "contient", "sans": "sans", "lacks": "sans",
    "sha256": "sha256", "contenu": "contenu", "has": "contenu", "branche": "branche", "branch": "branche",
    "commit": "commit", "figer": "figer", "freeze": "figer", "commande": "commande", "command": "commande",
}
MAX_LIGNES = 30


def lire_lignes(texte: str, base: str) -> dict:
    """Le critère écrit À LA MAIN dans le HUD, une ligne par contrôle, → critère brut (validé
    ensuite par `valider_critere` comme les autres). Chemin relatif : depuis `base`.

        existe docs/RAPPORT.md            dossier dist
        contient README.md :: OK          sans journal.txt :: ERREUR
        sha256 build/app.bin <64 hex>     contenu dist :: index.html, app.js
        branche . main                    commit .
        commande . :: .venv/bin/python -m pytest -q
        figer tests/test_calcul.py        # commentaire
    """
    controles: list[dict] = []
    figer: list[str] = []

    def chemin(brut: str, n: int) -> str:
        brut = brut.strip().strip("'\"")
        if not brut:
            raise CritereInvalide(f"ligne {n} : chemin manquant")
        if brut == ".":
            return base
        return brut if brut.startswith("/") else base.rstrip("/") + "/" + brut

    lignes = [ligne.strip() for ligne in str(texte or "").splitlines()]
    lignes = [(n, ligne) for n, ligne in enumerate(lignes, 1) if ligne and not ligne.startswith("#")]
    if len(lignes) > MAX_LIGNES:
        raise CritereInvalide(f"{MAX_LIGNES} lignes au plus")
    for n, ligne in lignes:
        mot, _, reste = ligne.partition(" ")
        genre = _MOTS.get(mot.lower())
        if not genre:
            raise CritereInvalide(f"ligne {n} : « {mot[:30]} » inconnu (existe, dossier, contient, sans, sha256, "
                                  "contenu, branche, commit, commande, figer)")
        if genre in ("contient", "sans", "contenu", "commande"):
            cible, sep, valeur = reste.partition(" :: ")
            if not sep or not valeur.strip():
                raise CritereInvalide(f"ligne {n} : « {mot} chemin :: … » attendu")
            if genre == "commande":
                controles.append({"type": "commande_observee", "dossier": chemin(cible, n), "commande": valeur.strip()})
            elif genre == "contenu":
                noms = [x.strip() for x in valeur.split(",") if x.strip()]
                controles.append({"type": "dossier_contient", "chemin": chemin(cible, n), "attendus": noms})
            else:
                c = {"type": "fichier_contient", "chemin": chemin(cible, n), "texte": valeur.strip()}
                if genre == "sans":
                    c["absent"] = True
                controles.append(c)
        elif genre in ("sha256", "branche"):
            cible, _, valeur = reste.rstrip().rpartition(" ")
            if not cible.strip() or not valeur:
                raise CritereInvalide(f"ligne {n} : « {mot} chemin valeur » attendu")
            if genre == "sha256":
                controles.append({"type": "empreinte", "chemin": chemin(cible, n), "sha256": valeur.lower()})
            else:
                controles.append({"type": "git_head", "depot": chemin(cible, n), "branche": valeur})
        elif genre == "commit":
            controles.append({"type": "git_head", "depot": chemin(reste, n), "avance": True})
        elif genre == "figer":
            figer.append(chemin(reste, n))
        else:
            controles.append({"type": "fichier_existe", "chemin": chemin(reste, n),
                              "genre": "dossier" if genre == "dossier" else "fichier"})
    return {"version": VERSION, "controles": controles, "figer": figer}


def empreinte_critere(critere: dict) -> str:
    """Empreinte du JSON CANONIQUE (clés triées) : l'approbation et le contrôle d'intégrité y sont liés."""
    canon = json.dumps(critere, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def commande_attendue(c: dict) -> str:
    """Le texte EXACT que Claude doit lancer (le dossier fait partie de la commande : le dossier
    courant de l'outil Bash ne se lit pas dans le flux)."""
    return f"cd {shlex.quote(c['dossier'])} && {c['commande']}"


def decrire(critere: dict | None) -> list[str]:
    """Le critère en clair, une ligne par contrôle (prompt de la mission, compte rendu)."""
    if not critere:
        return []
    out = []
    for c in critere.get("controles", []):
        t = c["type"]
        if t == "fichier_existe":
            taille = f" d'au moins {c['taille_min']} octets" if c.get("taille_min") else ""
            out.append(f"le {c['genre']} {c['chemin']} existe{taille}")
        elif t == "fichier_contient":
            out.append(f"{c['chemin']} {'ne contient pas' if c.get('absent') else 'contient'} « {c['texte']} »")
        elif t == "empreinte":
            out.append(f"{c['chemin']} a l'empreinte sha256 {c['sha256']}")  # entière : 12 car. = un « préfixe »
        elif t == "dossier_contient":
            out.append(f"{c['chemin']} contient {', '.join(c['attendus'][:5])}"
                       + (f" (et {len(c['attendus']) - 5} autres)" if len(c["attendus"]) > 5 else ""))
        elif t == "git_head":
            bits = []
            if c.get("branche"):
                bits.append(f"sur la branche {c['branche']}")
            if c.get("avance"):
                bits.append("avec un HEAD différent de celui du lancement")
            out.append(f"le dépôt {c['depot']} " + (" et ".join(bits) or "est lisible"))
        elif t == "commande_observee":
            out.append(f"la commande « {commande_attendue(c)} », lancée par toi avec l'outil Bash, réussit "
                       "après ta dernière modification")
    for f in critere.get("figer", []):
        out.append(f"{f} n'a pas changé depuis le lancement")
    return out


# ------------------------------------------------------------------ ouverture sûre

def _points_de_montage() -> frozenset[str]:
    """Points de montage du système (Linux : `/proc/self/mountinfo`), relus à chaque vérification.
    Sous la racine du périmètre, un point de montage n'est même pas `stat`é : un montage FUSE peut
    bloquer sans fin sur un simple `lstat`. Ailleurs (pas de /proc) : vide, `st_dev` reste le filet."""
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as f:
            lignes = f.read(4 * 1024 * 1024).splitlines()
    except OSError:
        return frozenset()
    out = set()
    for ligne in lignes:
        bits = ligne.split(" ")
        if len(bits) > 4:
            out.add(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), bits[4]))
    return frozenset(out)


def _lecture_permise(chemin: str) -> bool:
    """Le garde-fou demanderait-il pour LIRE ce fichier (secret) ? `runtime/` : jamais lu ici."""
    parts = [p for p in chemin.split("/") if p]
    runtime = [p for p in str(config.RUNTIME.resolve()).split("/") if p]
    if _sous(parts, runtime):
        return False
    from . import guard  # import tardif : le garde-fou est lourd, et ce module reste testable seul
    return not guard.classify("Read", {"file_path": chemin}).needs_confirm


class _Ouverture:
    """Ouvre `chemin` sous `racine` sans suivre aucun lien. `fd` : descripteur du dernier
    composant (fermé par `close`) ; `racine_st` : stat du dossier racine du périmètre."""

    def __init__(self, chemin: str, racine: str, ancre: dict | None, dossier: bool) -> None:
        if not _PORTABLE:
            raise _Bloque("plateforme")
        parts, rparts = _parties(chemin), _parties(racine)
        if not _sous(parts, rparts):
            raise _Bloque("hors_perimetre")
        self.fd = -1
        montages = _points_de_montage()
        for i in range(len(rparts), len(parts)):  # sous la racine : jamais un point de montage
            if "/" + "/".join(parts[:i + 1]) in montages:
                raise _Bloque("montage")
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        self.racine_st = os.fstat(fd) if not rparts else None
        try:
            for i, nom in enumerate(parts):
                dernier = i == len(parts) - 1
                try:
                    st = os.stat(nom, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    raise _Absent() from None
                except (OSError, ValueError):  # ValueError : octet NUL venu d'un fichier (`gitdir:`)
                    raise _Bloque("illisible") from None
                if stat.S_ISLNK(st.st_mode):
                    raise _Bloque("lien")
                if not dernier and not stat.S_ISDIR(st.st_mode):
                    raise _Absent()
                if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
                    raise _Bloque("special")  # jamais OUVERT : un périphérique peut réagir à l'ouverture
                if self.racine_st is not None and st.st_dev != self.racine_st.st_dev:
                    raise _Bloque("montage")
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
                if stat.S_ISDIR(st.st_mode):
                    flags |= os.O_DIRECTORY
                try:
                    nfd = os.open(nom, flags, dir_fd=fd)
                except FileNotFoundError:
                    raise _Bloque("instable") from None
                except OSError as exc:
                    raise _Bloque("lien" if exc.errno in (errno.ELOOP, errno.EMLINK) else "illisible") from None
                os.close(fd)
                fd = nfd
                ost = os.fstat(fd)
                if (ost.st_dev, ost.st_ino) != (st.st_dev, st.st_ino):
                    raise _Bloque("instable")  # remplacé entre le contrôle et l'ouverture
                if i == len(rparts) - 1:
                    if ancre and (ost.st_dev, ost.st_ino) != (ancre.get("dev"), ancre.get("ino")):
                        raise _Bloque("racine")  # le dossier du périmètre n'est plus celui du lancement
                    self.racine_st = ost
            st = os.fstat(fd)
            if dossier and not stat.S_ISDIR(st.st_mode):
                raise _Bloque("genre", attendu="dossier")
            if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                raise _Bloque("lien_dur")
            self.fd, self.st = fd, st
        except BaseException:
            os.close(fd)
            raise

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def _lire(o: _Ouverture, maxi: int, garde: Callable[[], None]) -> bytes:
    """Contenu du fichier ouvert, au plus `maxi` octets ; « instable » s'il change pendant la lecture."""
    avant = o.st
    if avant.st_size > maxi:
        raise _Bloque("trop_gros", maxi=maxi)
    morceaux, total = [], 0
    while True:
        garde()
        b = os.read(o.fd, min(BLOC, avant.st_size + 1 - total))
        if not b:
            break
        morceaux.append(b)
        total += len(b)
        if total > avant.st_size:
            raise _Bloque("instable")  # il grossit pendant la lecture : jamais plus que la taille vue
    apres = os.fstat(o.fd)
    if (apres.st_size, apres.st_mtime_ns, apres.st_ctime_ns) != (avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns):
        raise _Bloque("instable")
    return b"".join(morceaux)


def _sha_fichier(o: _Ouverture, garde: Callable[[], None]) -> str:
    avant = o.st
    if avant.st_size > MAX_EMPREINTE:
        raise _Bloque("trop_gros", maxi=MAX_EMPREINTE)
    h, total = hashlib.sha256(), 0
    while True:
        garde()
        b = os.read(o.fd, min(BLOC, avant.st_size + 1 - total))
        if not b:
            break
        total += len(b)
        if total > avant.st_size:
            raise _Bloque("instable")
        h.update(b)
    apres = os.fstat(o.fd)
    if (apres.st_size, apres.st_mtime_ns, apres.st_ctime_ns) != (avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns):
        raise _Bloque("instable")
    return h.hexdigest()


class _Lecteur:
    """Toutes les lectures d'une vérification : périmètre, ancrage, garde-fou, délai, arrêt."""

    def __init__(self, scope: list[str], ancrage: dict | None, doit_arreter: Callable[[], bool],
                 horloge: Callable[[], float], lecture_permise: Callable[[str], bool]) -> None:
        self.scope = [str(s) for s in scope]
        self.ancres = {a.get("chemin"): a for a in (ancrage or {}).get("racines", []) if isinstance(a, dict)}
        self.doit_arreter, self.horloge = doit_arreter, horloge
        self.lecture_permise = lecture_permise
        self.fin = horloge() + DUREE_MAX_S

    def garde(self) -> None:
        if self.doit_arreter():
            raise _Arret()
        if self.horloge() > self.fin:
            raise _Bloque("delai")

    def ouvrir(self, chemin: str, dossier: bool = False) -> _Ouverture:
        self.garde()
        try:
            racine = _racine_de(chemin, self.scope)
        except CritereInvalide:
            raise _Bloque("hors_perimetre") from None
        if not self.lecture_permise(chemin):
            raise _Bloque("protege")
        return _Ouverture(chemin, racine, self.ancres.get(racine), dossier)

    def contenu(self, chemin: str, maxi: int) -> bytes:
        """Deux essais si le fichier change pendant la lecture, puis « instable »."""
        for essai in range(2):
            o = self.ouvrir(chemin)
            try:
                return _lire(o, maxi, self.garde)
            except _Bloque as exc:
                if exc.code != "instable" or essai:
                    raise
            finally:
                o.close()
        raise _Bloque("instable")

    def sha(self, chemin: str) -> str:
        for essai in range(2):
            o = self.ouvrir(chemin)
            try:
                if not stat.S_ISREG(o.st.st_mode):
                    raise _Bloque("genre", attendu="fichier")
                return _sha_fichier(o, self.garde)
            except _Bloque as exc:
                if exc.code != "instable" or essai:
                    raise
            finally:
                o.close()
        raise _Bloque("instable")


# ------------------------------------------------------------------ git, lu sans git

def _git_dir(lect: _Lecteur, depot: str) -> tuple[str, str]:
    """(dossier git, dossier des refs) du dépôt, tous deux DANS le périmètre."""
    point_git = depot.rstrip("/") + "/.git"
    try:
        o = lect.ouvrir(point_git, dossier=False)
    except _Absent:
        raise _Bloque("git") from None
    try:
        est_dossier = stat.S_ISDIR(o.st.st_mode)
    finally:
        o.close()
    if est_dossier:
        gitdir = point_git
    else:  # worktree ou sous-module : « gitdir: … », qui doit rester dans le périmètre
        ligne = lect.contenu(point_git, 4096).decode("utf-8", "replace").strip()
        if not ligne.startswith("gitdir:") or _CONTROLE_CAR.search(ligne):
            raise _Bloque("git")
        cible = ligne[7:].strip()
        if not cible.startswith("/"):
            cible = depot.rstrip("/") + "/" + cible
        gitdir = _normaliser(cible)
    commun = gitdir
    try:
        c = lect.contenu(gitdir + "/commondir", 4096).decode("utf-8", "replace").strip()
        if not c or _CONTROLE_CAR.search(c):
            raise _Bloque("git")
        commun = _normaliser(c if c.startswith("/") else gitdir + "/" + c)
    except _Absent:
        pass
    return gitdir, commun


def _normaliser(chemin: str) -> str:
    """Chemin absolu dont les « .. » sont résolus SUR LE TEXTE (sans suivre de lien) ; le
    résultat passe ensuite par le périmètre et l'ouverture sûre comme les autres."""
    out: list[str] = []
    for p in chemin.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if out:
                out.pop()
            continue
        out.append(p)
    return "/" + "/".join(out)


def lire_head(lect: _Lecteur, depot: str) -> tuple[str, str]:
    """(empreinte du commit HEAD, branche ou « » si détaché)."""
    gitdir, commun = _git_dir(lect, depot)
    head = lect.contenu(gitdir + "/HEAD", MAX_GIT).decode("utf-8", "replace").strip()
    if _OID.fullmatch(head):
        return head, ""
    if not head.startswith("ref:"):
        raise _Bloque("git")
    ref = head[4:].strip()
    if not _REF.fullmatch(ref) or ".." in ref:
        raise _Bloque("git")
    branche = ref[len("refs/heads/"):]
    try:
        oid = lect.contenu(commun + "/" + ref, MAX_GIT).decode("utf-8", "replace").strip()
        if _OID.fullmatch(oid):
            return oid, branche
        raise _Bloque("git")
    except _Absent:
        pass
    try:
        packed = lect.contenu(commun + "/packed-refs", MAX_GIT).decode("utf-8", "replace")
    except _Absent:
        return "", branche  # branche sans commit (dépôt neuf)
    for ligne in packed.splitlines():
        bits = ligne.split(" ", 1)
        if len(bits) == 2 and bits[1].strip() == ref and _OID.fullmatch(bits[0]):
            return bits[0], branche
    return "", branche


# ------------------------------------------------------------------ contrôles

def _un(lect: _Lecteur, c: dict, ancrage: dict) -> tuple[str, str, dict]:
    t = c["type"]
    if t == "fichier_existe":
        try:
            o = lect.ouvrir(c["chemin"], dossier=c["genre"] == "dossier")
        except _Absent:
            return ECHEC, "absent", {}
        try:
            if c["genre"] == "fichier" and not stat.S_ISREG(o.st.st_mode):
                return ECHEC, "genre", {"attendu": "fichier"}
            taille = o.st.st_size if stat.S_ISREG(o.st.st_mode) else 0
            if c.get("taille_min") and taille < c["taille_min"]:
                return ECHEC, "taille", {"taille": taille, "mini": c["taille_min"]}
            return OK, "existe", {"taille": taille} if c["genre"] == "fichier" else {}
        finally:
            o.close()
    if t == "fichier_contient":
        try:
            data = lect.contenu(c["chemin"], MAX_CONTIENT)
        except _Absent:
            return ECHEC, "absent", {}
        trouve = c["texte"].encode("utf-8") in data
        if c.get("absent"):
            return (ECHEC, "texte_present", {}) if trouve else (OK, "texte_absent", {})
        return (OK, "texte_trouve", {}) if trouve else (ECHEC, "texte_manquant", {})
    if t == "empreinte":
        try:
            sha = lect.sha(c["chemin"])
        except _Absent:
            return ECHEC, "absent", {}
        if sha == c["sha256"]:
            return OK, "empreinte", {"sha": sha[:12]}
        return ECHEC, "empreinte_differente", {"sha": sha[:12], "attendu": c["sha256"][:12]}
    if t == "dossier_contient":
        manquants = []
        for nom in c["attendus"]:
            try:
                lect.ouvrir(c["chemin"].rstrip("/") + "/" + nom, dossier=False).close()
            except _Absent:
                manquants.append(nom)
        if manquants:
            return ECHEC, "manquants", {"n": len(manquants), "noms": [m[:60] for m in manquants[:3]]}
        return OK, "dossier_complet", {"n": len(c["attendus"])}
    if t == "git_head":
        oid, branche = lire_head(lect, c["depot"])
        infos = {"head": oid[:12], "branche": branche[:60]}
        if c.get("branche") and branche != c["branche"]:
            return ECHEC, "autre_branche", {**infos, "attendu": c["branche"]}
        if c.get("avance"):
            avant = next((h.get("sha", "") for h in ancrage.get("heads", []) if h.get("depot") == c["depot"]), None)
            if avant is None or not oid or oid == avant:
                return ECHEC, "pas_de_commit", infos
        return OK, "head", infos
    raise _Bloque("type")


def _observee(c: dict, obs: dict) -> tuple[str, str, dict]:
    """Lot C : rien n'est lancé ni lu sur le disque ; seulement les observations du cœur (flux du
    CLI) figées dans l'instantané. La DERNIÈRE exécution de la commande exacte fait foi."""
    attendu = commande_attendue(c)
    vues = [o for o in obs.get("commandes") or [] if isinstance(o, dict) and o.get("cmd") == attendu]
    if not vues:
        return ECHEC, "commande_absente", {}
    o = vues[-1]
    if o.get("etat") == "en_cours":
        return ECHEC, "commande_en_cours", {}
    if o.get("etat") != OK:
        return ECHEC, "commande_echouee", {"sortie": str(o.get("code") or "")[:40]}
    if int(obs.get("ecriture") or 0) > int(o.get("seq") or 0):
        return ECHEC, "commande_perimee", {"outil": str(obs.get("ecriture_outil") or "")[:40]}
    return OK, "commande_reussie", {}


def _figes(lect: _Lecteur, ancrage: dict) -> list[dict]:
    out = []
    for f in ancrage.get("figer", []):
        chemin, attendu = str(f.get("chemin") or ""), str(f.get("sha256") or "")
        try:
            sha = lect.sha(chemin)
            ok = sha == attendu
            out.append({"type": "figer", "cible": chemin, "etat": OK if ok else ECHEC,
                        "code": "fige" if ok else "fige_modifie", "infos": {"sha": sha[:12]}})
        except _Absent:
            out.append({"type": "figer", "cible": chemin, "etat": ECHEC, "code": "fige_supprime", "infos": {}})
    return out


def verifier(instantane: dict, doit_arreter: Callable[[], bool] = lambda: False,
             horloge: Callable[[], float] = time.monotonic,
             lecture_permise: Callable[[str], bool] = _lecture_permise) -> dict:
    """Vérifie le critère de l'instantané (copie figée par `mission.debut_verification`).
    Ne lève jamais : tout problème devient un contrôle « bloque ». Statut : verifiee (tout est
    ok), echec (au moins un échec, aucun blocage), bloquee, ou interrompue (pause, arrêt, ESTOP)."""
    debut, t0 = time.time(), horloge()
    critere = instantane.get("critere") or {}
    ancrage = instantane.get("ancrage") or {}
    rapport: dict[str, Any] = {
        "verification_id": instantane.get("verification_id", ""), "mission_id": instantane.get("mission_id", ""),
        "generation": instantane.get("generation", 0), "at": debut, "critere_sha": instantane.get("critere_sha", ""),
        "origine": instantane.get("origine", ""), "approuve": bool(instantane.get("approuve")),
        "controles": [],
    }

    def fin(statut: str) -> dict:
        rapport["statut"], rapport["verifie"] = statut, statut == VERIFIEE
        rapport["duree_ms"] = int((horloge() - t0) * 1000)
        return rapport

    if not critere or empreinte_critere(critere) != instantane.get("critere_sha"):
        rapport["controles"].append({"type": "critere", "cible": "", "etat": BLOQUE, "code": "critere_modifie", "infos": {}})
        return fin(BLOQUEE)
    lect = _Lecteur(instantane.get("scope") or [], ancrage, doit_arreter, horloge, lecture_permise)
    try:
        for n, c in enumerate(critere.get("controles", []), 1):
            cible = c.get("chemin") or c.get("depot") or c.get("dossier") or ""
            try:
                if c["type"] == "commande_observee":
                    etat, code, infos = _observee(c, instantane.get("observations") or {})
                else:
                    etat, code, infos = _un(lect, c, ancrage)
            except _Bloque as exc:
                etat, code, infos = BLOQUE, exc.code, exc.infos
            except _Absent:
                etat, code, infos = ECHEC, "absent", {}
            except (OSError, ValueError):
                etat, code, infos = BLOQUE, "illisible", {}
            rapport["controles"].append({"n": n, "type": c["type"], "cible": cible, "etat": etat, "code": code,
                                         "infos": infos})
        try:
            rapport["controles"].extend(_figes(lect, ancrage))
        except _Bloque as exc:
            rapport["controles"].append({"type": "figer", "cible": "", "etat": BLOQUE, "code": exc.code,
                                         "infos": exc.infos})
    except _Arret:
        return fin(INTERROMPUE)
    etats = {c["etat"] for c in rapport["controles"]}
    return fin(BLOQUEE if BLOQUE in etats else EN_ECHEC if ECHEC in etats else VERIFIEE)


def ancrer(critere: dict | None, scope: list[str], doit_arreter: Callable[[], bool] = lambda: False,
           lecture_permise: Callable[[str], bool] = _lecture_permise) -> dict:
    """Relevé du LANCEMENT, fait par le cœur (jamais fourni par Claude) : identité de chaque
    dossier du périmètre qui existe, empreinte des fichiers figés, HEAD des dépôts suivis
    (`avance`). Lève `CritereInvalide` si un fichier figé ou un HEAD demandé est illisible."""
    racines = []
    for r in scope:
        try:
            o = _Ouverture(str(r), str(r), None, dossier=True)
        except (_Bloque, _Absent, OSError, ValueError, CritereInvalide):
            continue  # dossier à créer par la mission, ou spécial : vérifié sans ancre
        try:
            racines.append({"chemin": str(r), "dev": o.st.st_dev, "ino": o.st.st_ino})
        finally:
            o.close()
    out: dict[str, Any] = {"racines": racines, "figer": [], "heads": []}
    if not critere:
        return out
    lect = _Lecteur(scope, out, doit_arreter, time.monotonic, lecture_permise)
    for f in critere.get("figer", []):
        try:
            out["figer"].append({"chemin": f, "sha256": lect.sha(f)})
        except (_Bloque, _Absent, OSError, ValueError) as exc:
            raise CritereInvalide(f"fichier à figer illisible au lancement : {f[:80]} "
                                  f"({getattr(exc, 'code', 'absent')})") from None
    for c in critere.get("controles", []):
        if c["type"] == "git_head" and c.get("avance"):
            try:
                oid, _b = lire_head(lect, c["depot"])
            except (_Bloque, _Absent, OSError, ValueError) as exc:
                raise CritereInvalide(f"HEAD du dépôt illisible au lancement : {c['depot'][:80]} "
                                      f"({getattr(exc, 'code', 'absent')})") from None
            out["heads"].append({"depot": c["depot"], "sha": oid})
    return out


def resume_echec(rapport: dict | None) -> str:
    """Une ligne, pour Claude après un échec (codes seulement, jamais de contenu de fichier)."""
    if not rapport:
        return ""
    rates = [c for c in rapport.get("controles", []) if c.get("etat") != OK]
    bouts = []
    for c in rates[:4]:
        cible = (c.get("cible") or "")[-80:]
        bouts.append(f"{c.get('type')} {cible} : {c.get('code')}".strip())
    return "; ".join(bouts)[:300]
