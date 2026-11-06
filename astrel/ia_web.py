"""Socle commun des générateurs IA par le navigateur (plan P40 A1, 27/10/2026).

Demande de l'utilisateur (27/10) : plusieurs comptes Google pour le volume, repli automatique sur
le compte suivant, épuisés remis à zéro au reset quotidien, choix du moteur ET du modèle en
auto (défaut) ou en manuel. Ce module est COMMUN aux images (`images_ia.py`) et à la vidéo
(plan B) ; il ne lance rien et n'affiche rien (codes seulement, textes d'écran chez l'appelant) :

- catalogue `config/ia-web.json` (lu aussi par `imagegen/`) : services, modèles, plafonds ;
- comptes `runtime/imagegen/comptes.json` {"comptes": [{alias, fournisseur}]}, dans l'ordre du
  repli ; absent : repris de `ASTREL_IMAGES_COMPTES` (`config.IMAGES_COMPTES`, « amw:gemini »
  → amw google), rien n'est écrit ;
- état des quotas `runtime/imagegen/etat.json` v2, par (alias, service), en UNITÉS (1 image ;
  N crédits pour une vidéo) ; la v1 (un moteur par alias) est migrée à la lecture, copie gardée
  dans `etat.v1.json` à la première écriture ;
- choix du (compte, service, modèle) : `choisir` ;
- verrou `CHROME` : un seul Chrome à la fois, images et vidéos confondues (`chrome_libre`) ;
- lancement du worker Node (`executer_worker`) et son arrêt SANS Chrome orphelin (`arreter`, P41 B3).

P40 A4 (28/10, demande de l'utilisateur : « coder les étapes manquantes ») : modèles PROPOSÉS
(`modeles_proposes` : le défaut seul tant que `modeles_releves` est faux), pièces jointes
(`pieces`, filtre du cœur ; `accepte_pieces` d'après le catalogue), gestion des comptes
(`ajouter_compte`, `retirer_compte`, `ordonner_comptes`), ordre du mode auto (`choisir(ordre=)`).
La connexion d'un compte (seul lancement d'un Chrome visible) est dans `ia_connexion.py`.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import config, hors_ligne
from .platform import systeme
from .platform import enfants

log = logging.getLogger("astrel.ia_web")

CATALOGUE_FILE = config.ROOT / "config" / "ia-web.json"
FOURNISSEURS = ("google", "openai", "aucun")
TYPES = ("image", "video")
FENETRES = ("jour", "24h")
AFFICHAGES = ("masquee", "visible")
MODES = ("auto", "manuel")
ALIAS = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
# Service sans compte (Bing) : profil anonyme persistant sous cet alias, jamais déclarable.
ALIAS_ANONYME = "anonyme"
MODELE_DEFAUT = "defaut"
EPUISE_REPLI_S = 24 * 3600   # repli provisoire tant que l'heure du reset n'est pas mesurée
SONDE_S = 6 * 3600           # un compte épuisé est re-sondé au plus toutes les 6 h
COMPTES_MAX = 12
# Champs propres au COMPTE (session, défi anti-robot) : partagés par tous ses services.
CHAMPS_COMPTE = ("a_reconnecter", "pause_jusqua")

# Un seul Chrome à la fois (images et vidéos) : pris autour de chaque lancement du worker.
CHROME = threading.Lock()
ARRET_SONDE_S = 1.0     # annulation, arrêt d'urgence, délai : regardés chaque seconde pendant un worker
ARRET_GRACE_S = 8.0     # SIGTERM : Playwright ferme Chrome proprement ; au-delà, SIGKILL

# Repli si le catalogue est illisible : les trois services connus, modèle par défaut du site.
_REPLI = {
    "gemini": {"type": "image", "libelle": "Gemini", "fournisseur": "google",
               "url": "https://gemini.google.com/app", "plafond": 20, "fenetre": "jour", "rang": 1, "pieces": True},
    "chatgpt": {"type": "image", "libelle": "ChatGPT", "fournisseur": "openai",
                "url": "https://chatgpt.com/", "plafond": 3, "fenetre": "24h", "rang": 2, "pieces": True},
    "bing": {"type": "image", "libelle": "Bing Image Creator", "fournisseur": "aucun",
             "url": "https://www.bing.com/images/create", "plafond": 15, "fenetre": "jour", "rang": 3},
}


# --------------------------------------------------------------------------- catalogue

_cache: dict[str, Any] = {"cle": None, "services": None}
_cache_lock = threading.Lock()


def _durees_modele(brut: Any) -> dict[int, int]:
    """{secondes: crédits} lu dans le catalogue ; clé ou coût illisible ignoré."""
    out: dict[int, int] = {}
    for cle, val in (brut.items() if isinstance(brut, dict) else ()):
        try:
            s, c = int(cle), int(val)
        except (TypeError, ValueError):
            continue
        if 1 <= s <= 60 and not isinstance(val, bool) and 1 <= c <= 1000:
            out[s] = c
    return dict(sorted(out.items()))


def _modeles(brut: Any) -> list[dict]:
    out: list[dict] = []
    for m in brut if isinstance(brut, list) else []:
        if not isinstance(m, dict) or not ALIAS.fullmatch(str(m.get("id") or "")):
            continue
        try:
            cout = max(1, min(1000, int(m.get("cout") or 1)))
        except (TypeError, ValueError):
            cout = 1
        entree = {"id": str(m["id"]), "libelle": str(m.get("libelle") or m["id"])[:80], "cout": cout}
        if m.get("gratuit") is False:  # P41 : modèle des formules payantes (Flow Quality), refusé
            entree["gratuit"] = False
        # P41 B3 (28/10) : durées PROPOSÉES par ce modèle et leur coût, {"8": 10} (secondes →
        # crédits, relevé B0). Absent : durées du service (`reglages_video`) au coût du modèle.
        durees = _durees_modele(m.get("durees"))
        if durees:
            entree["durees"] = durees
        out.append(entree)
    return out or [{"id": MODELE_DEFAUT, "libelle": "Modèle par défaut du site", "cout": 1}]


# Réglages propres à la VIDÉO (plan P41, `videos_ia.py`) : durées proposées (s), durée par défaut,
# créneau de pointe en heures UTC [début, fin[ (Flow : 14-17) ; absent = pas de créneau.
DUREES_REPLI = (4, 6, 8)


def _video(brut: Any) -> dict:
    b = brut if isinstance(brut, dict) else {}
    durees = sorted({d for d in (b.get("durees") if isinstance(b.get("durees"), list) else [])
                     if isinstance(d, int) and not isinstance(d, bool) and 1 <= d <= 60}) or list(DUREES_REPLI)
    defaut = b.get("duree_defaut")
    pointe = b.get("pointe_utc")
    if not (isinstance(pointe, list) and len(pointe) == 2 and all(isinstance(h, int) and not isinstance(h, bool)
                                                                  for h in pointe) and 0 <= pointe[0] < pointe[1] <= 24):
        pointe = None
    return {"durees": durees, "duree_defaut": defaut if defaut in durees else durees[-1],
            "pointe_utc": list(pointe) if pointe else None}


def _service(nom: str, brut: dict) -> dict | None:
    if not ALIAS.fullmatch(nom) or not isinstance(brut, dict):
        return None
    if brut.get("type") not in TYPES or brut.get("fournisseur") not in FOURNISSEURS:
        return None
    try:
        plafond = max(1, min(1000, int(brut.get("plafond") or 20)))
        rang = int(brut.get("rang") or 99)
    except (TypeError, ValueError):
        return None
    s = {"nom": nom, "type": brut["type"], "libelle": str(brut.get("libelle") or nom)[:60],
         "fournisseur": brut["fournisseur"], "url": str(brut.get("url") or ""),
         "modeles": _modeles(brut.get("modeles")), "modeles_releves": bool(brut.get("modeles_releves")),
         "plafond": plafond, "fenetre": brut.get("fenetre") if brut.get("fenetre") in FENETRES else "jour",
         "rang": rang,
         # P40 A4 : le site prend des pièces jointes (worker : `joindre`) ; absent = non (Bing)
         "pieces": brut.get("pieces") is True,
         # 29/10 (Dreamina, décision de l'utilisateur) : « limite »: « solde » = la vraie limite est le
         # solde affiché par le site, lu par le worker avant l'envoi ; `plafond` n'est qu'une estimation
         "solde": brut.get("limite") == "solde",
         # 30/10 : « connexion »: « site » = le site a SA connexion (Dreamina/CapCut), hors session Google
         "connexion_site": brut.get("connexion") == "site"}
    if brut["type"] == "video":
        s["video"] = _video(brut.get("video"))
    return s


def catalogue() -> dict[str, dict]:
    """Services du catalogue (relu s'il a changé) ; illisible : repli intégré, journalisé."""
    path = CATALOGUE_FILE
    try:
        st = path.stat()
        cle = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        cle = (str(path), None, None)
    with _cache_lock:
        if _cache["cle"] == cle and _cache["services"] is not None:
            return _cache["services"]
        services: dict[str, dict] = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for nom, brut in (data.get("services") or {}).items():
                s = _service(str(nom), brut)
                if s:
                    services[s["nom"]] = s
        except (OSError, ValueError, AttributeError) as exc:
            log.warning("catalogue IA illisible (%s) : repli intégré", exc)
        if not services:
            services = {n: s for n, b in _REPLI.items() if (s := _service(n, b))}
        _cache["cle"], _cache["services"] = cle, services
        return services


def services(type_: str = "image") -> list[str]:
    """Services de ce type, dans l'ordre du mode auto (rang, puis nom)."""
    cat = catalogue()
    return sorted((n for n, s in cat.items() if s["type"] == type_), key=lambda n: (cat[n]["rang"], n))


def info(service: str) -> dict | None:
    return catalogue().get(service)


def libelle(service: str) -> str:
    s = info(service)
    return s["libelle"] if s else ""


def fournisseur(service: str) -> str:
    s = info(service)
    return s["fournisseur"] if s else ""


def modeles(service: str) -> list[dict]:
    s = info(service)
    return list(s["modeles"]) if s else []


def modele_defaut(service: str) -> str:
    ms = modeles(service)
    return ms[0]["id"] if ms else MODELE_DEFAUT


def modele_valide(service: str, modele: str) -> bool:
    return any(m["id"] == modele for m in modeles(service))


def cout(service: str, modele: str = "", duree: int = 0) -> int:
    """Unités décomptées par génération (1 image ; N crédits pour une vidéo). `duree` (vidéo) :
    coût de cette durée si le modèle en a un relevé, sinon celui du modèle."""
    for m in modeles(service):
        if m["id"] == (modele or modele_defaut(service)):
            return (m.get("durees") or {}).get(duree, m["cout"])
    return 1


def durees_video(service: str, modele: str = "") -> dict[int, int]:
    """{secondes: crédits} PROPOSÉS pour ce modèle vidéo (défaut du service si vide) : ceux du
    modèle s'ils sont relevés, sinon les durées du service au coût du modèle. Vide : modèle inconnu."""
    m = modele_info(service, modele)
    if not m:
        return {}
    if m.get("durees"):
        return dict(m["durees"])
    return {d: m["cout"] for d in reglages_video(service)["durees"]}


def duree_video(service: str, modele: str, duree: int, libre: bool = False) -> int | None:
    """Durée (s) d'une vidéo sur ce service : `duree` si le modèle la propose ; sinon sa durée par
    défaut si la durée n'a pas été demandée (`libre`), None si elle l'a été (service à sauter)."""
    if duree in durees_video(service, modele):
        return duree
    return duree_defaut(service, modele) if libre else None


def duree_defaut(service: str, modele: str = "") -> int:
    """Durée par défaut d'une vidéo : celle du service si le modèle la propose, sinon la plus longue."""
    durees = durees_video(service, modele)
    d = reglages_video(service)["duree_defaut"]
    return d if d in durees else (max(durees) if durees else d)


def modele_info(service: str, modele: str = "") -> dict | None:
    """{id, libelle, cout[, gratuit: False]} du modèle (défaut du service si vide), None si inconnu."""
    cible = modele or modele_defaut(service)
    return next((dict(m) for m in modeles(service) if m["id"] == cible), None)


def modeles_proposes(service: str) -> list[dict]:
    """Modèles qu'on peut PROPOSER à l'utilisateur (P40 A4) : ceux du catalogue, et seulement le
    défaut du service tant que les modèles réels du site ne sont pas relevés (`modeles_releves`
    faux) ; jamais un nom inventé, jamais un modèle payant (`gratuit` faux)."""
    s = info(service)
    if not s:
        return []
    ms = [dict(m) for m in s["modeles"] if m.get("gratuit") is not False]
    if not s["modeles_releves"]:
        ms = [m for m in ms if m["id"] == MODELE_DEFAUT] or ms[:1]
    return ms


def modele_propose(service: str, modele: str) -> bool:
    return any(m["id"] == modele for m in modeles_proposes(service))


def limite_solde(service: str) -> bool:
    """La limite du service est le SOLDE du site (catalogue, `limite`: `solde` ; Dreamina) : le
    plafond ne bloque rien (le worker lit le solde avant l'envoi et refuse s'il ne couvre pas le
    coût), un modèle plus cher que le plafond reste permis."""
    s = info(service)
    return bool(s and s.get("solde"))


def connexion_propre(service: str) -> bool:
    """Le site a sa propre connexion (catalogue `connexion`: `site`, Dreamina) : une session perdue
    y est notée pour CE service (`site_a_reconnecter`), pas pour tout le compte."""
    s = info(service)
    return bool(s and s.get("connexion_site"))


def accepte_pieces(service: str) -> bool:
    """Le site prend des pièces jointes (catalogue, `pieces` vrai) ; Bing non."""
    s = info(service)
    return bool(s and s.get("pieces"))


# --------------------------------------------------------------------------- pièces jointes

# Filtre du CŒUR (P40 A4), le même que imagegen/pieces.mjs (qui le réapplique) et que
# videos_ia.image_depart : pièces jointes d'Astrel ou dossier des projets (`config.WORKDIR`), hors secrets et dossiers
# privés (medias.refusal), fichier ordinaire à un seul nom, 5 Mo, PNG/JPEG/WebP lus dans les OCTETS.
PIECES_MAX = 6
PIECE_MAX_OCTETS = 5 * 1024 * 1024
_PIECE_TYPES = ("image/png", "image/jpeg", "image/webp")


def piece(brut: Any) -> tuple[str, str]:
    """(chemin résolu, "") d'une pièce jointe permise ; sinon ("", raison)."""
    from . import attachments, medias  # tardif : medias importe le garde-fou

    texte = str(brut or "").strip() if isinstance(brut, str) else ""
    if not texte or not Path(texte).is_absolute():
        return "", "chemin absolu attendu"
    try:
        reel = Path(texte).resolve(strict=True)
        st = os.stat(reel, follow_symlinks=False)
    except (OSError, RuntimeError, ValueError):  # ValueError : caractère NUL dans le chemin
        return "", "fichier introuvable"
    # 05/11 : le dossier des projets de la machine (config.WORKDIR), plus un chemin écrit en dur
    racines = [attachments.ATTACH_DIR.resolve(), Path(config.WORKDIR).resolve()]
    if not any(reel != r and reel.is_relative_to(r) for r in racines):
        return "", "hors des pièces jointes et du dossier des projets"
    raison = medias.refusal(reel)
    if raison:
        return "", raison
    if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
        return "", "pas un fichier ordinaire"
    if st.st_size > PIECE_MAX_OCTETS:
        return "", "image trop lourde (5 Mo au plus)"
    try:
        fd = systeme.ouvrir_sans_lien(reel, os.O_RDONLY | systeme.NONBLOCK)
    except OSError:
        return "", "fichier illisible"
    with os.fdopen(fd, "rb") as f:
        genre = medias.sniff(f.read(16))
    if not genre or genre[1] not in _PIECE_TYPES:
        return "", "pas une image PNG, JPEG ou WebP"
    return str(reel), ""


def pieces(liste: Any) -> tuple[list[str], str]:
    """Chemins résolus de toutes les pièces d'une demande (6 au plus, sans doublon) ; au premier
    refus : ([], « pièce jointe N refusée : raison »)."""
    if liste is None:
        return [], ""
    if not isinstance(liste, (list, tuple)):
        return [], "pièces jointes : liste de chemins attendue"
    brutes = [p for p in liste if not (isinstance(p, str) and not p.strip())]
    if len(brutes) > PIECES_MAX:
        return [], f"{PIECES_MAX} pièces jointes au plus"
    out: list[str] = []
    for i, brut in enumerate(brutes, 1):
        reel, raison = piece(brut)
        if raison:
            return [], f"pièce jointe {i} refusée : {raison}"
        if reel in out:
            return [], "la même pièce jointe deux fois"
        out.append(reel)
    return out, ""


def reglages_video(service: str) -> dict:
    """{durees, duree_defaut, pointe_utc} d'un service vidéo (repli : 4/6/8 s, 8, pas de pointe)."""
    s = info(service)
    return dict((s or {}).get("video") or _video(None))


# Script Node du worker par type de média (P41 : « ia_web.py choisit le script par type »).
WORKERS = {"image": "worker.mjs", "video": "video.mjs"}


def script(type_: str) -> Path:
    return config.IMAGES_WORKER_DIR / WORKERS[type_]


def plafond(service: str) -> int:
    """Unités permises par compte sur la fenêtre du service : `config.IMAGES_PLAFONDS`
    (variables `ASTREL_IMAGES_PLAFOND_*`) d'abord, puis le catalogue."""
    if service in config.IMAGES_PLAFONDS:
        return int(config.IMAGES_PLAFONDS[service])
    s = info(service)
    return s["plafond"] if s else 20


def fenetre(service: str) -> str:
    """« jour » (remise à zéro à minuit) ou « 24h » (24 h après la première unité)."""
    if service in config.IMAGES_FENETRES:
        return config.IMAGES_FENETRES[service]
    s = info(service)
    return s["fenetre"] if s else "jour"


# --------------------------------------------------------------------------- comptes

def chemin_comptes() -> Path:
    return config.IMAGES_RUNTIME / "comptes.json"


def _comptes_legacy() -> list[dict]:
    """`ASTREL_IMAGES_COMPTES` (« alias:moteur ») : le fournisseur est celui du moteur."""
    out = []
    for paire in config.IMAGES_COMPTES:
        if len(paire) != 2:
            continue
        alias, moteur = paire
        f = fournisseur(moteur)
        if f and f != "aucun":
            out.append({"alias": alias, "fournisseur": f})
    return out


def _valides(brut: Any) -> list[dict]:
    out: list[dict] = []
    vus: set[str] = set()
    for c in brut if isinstance(brut, list) else []:
        if not isinstance(c, dict):
            continue
        alias, f = str(c.get("alias") or ""), str(c.get("fournisseur") or "")
        if not ALIAS.fullmatch(alias) or alias == ALIAS_ANONYME or alias in vus:
            continue
        if f not in FOURNISSEURS or f == "aucun":
            continue
        vus.add(alias)
        out.append({"alias": alias, "fournisseur": f})
    return out[:COMPTES_MAX]


def comptes() -> list[dict]:
    """[{alias, fournisseur}] dans l'ordre du repli. `comptes.json` absent : repris de
    `ASTREL_IMAGES_COMPTES` ; illisible : aucun compte (journalisé), jamais l'ancien réglage."""
    path = chemin_comptes()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _valides(_comptes_legacy())
    except (OSError, ValueError) as exc:
        cle = _cle_fichier(path)
        if _comptes_signale.get("cle") != cle:  # une ligne par version du fichier, pas à chaque appel
            _comptes_signale["cle"] = cle
            log.warning("comptes IA illisibles (%s) : aucun compte", exc)
        return []
    return _valides(data.get("comptes") if isinstance(data, dict) else None)


_comptes_signale: dict[str, Any] = {}
_etat_signale: dict[str, Any] = {}


def _cle_fichier(path: Path) -> tuple:
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), None, None)


def comptes_illisibles() -> bool:
    """`comptes.json` présent mais illisible (le refus le dit, au lieu de « non connecté »)."""
    path = chemin_comptes()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    return not isinstance(data, dict)


def ecrire_comptes(liste: list[dict]) -> list[dict]:
    """Écriture atomique 0600 de `comptes.json` (réglages, lot A4). Rend ce qui a été gardé."""
    gardes = _valides(liste)
    _ecrire_json(chemin_comptes(), {"comptes": gardes})
    return gardes


# Gestion des comptes (P40 A4, commandes `image_compte_*` du cœur, l'utilisateur seul). Rendent
# (comptes après, code) ; code vide = fait, sinon : alias | fournisseur | existe | inconnu | max |
# ordre | comptes (comptes.json illisible : jamais écrasé). Le profil d'un compte retiré reste sur
# le disque (cookies) : rien n'est supprimé ici, la connexion d'un même alias le reprend.
_COMPTES_LOCK = threading.Lock()


def _base_comptes() -> list[dict] | None:
    """Liste actuelle pour une écriture ; None si comptes.json est illisible."""
    return None if comptes_illisibles() else comptes()


def ajouter_compte(alias: str, fournisseur_: str) -> tuple[list[dict], str]:
    alias, fournisseur_ = str(alias or "").strip().lower(), str(fournisseur_ or "").strip().lower()
    if not ALIAS.fullmatch(alias) or alias == ALIAS_ANONYME:
        return comptes(), "alias"
    if fournisseur_ not in FOURNISSEURS or fournisseur_ == "aucun":
        return comptes(), "fournisseur"
    with _COMPTES_LOCK:
        base = _base_comptes()
        if base is None:
            return [], "comptes"
        if any(c["alias"] == alias for c in base):
            return base, "existe"
        if len(base) >= COMPTES_MAX:
            return base, "max"
        return ecrire_comptes([*base, {"alias": alias, "fournisseur": fournisseur_}]), ""


def retirer_compte(alias: str) -> tuple[list[dict], str]:
    alias = str(alias or "").strip().lower()
    with _COMPTES_LOCK:
        base = _base_comptes()
        if base is None:
            return [], "comptes"
        if not any(c["alias"] == alias for c in base):
            return base, "inconnu"
        return ecrire_comptes([c for c in base if c["alias"] != alias]), ""


def renommer_compte(ancien: str, nouveau: str,
                    deplacer: Callable[[str, str], str] = lambda a, n: "") -> tuple[list[dict], str]:
    """30/10 (demande : « renommer en cas d'erreur sur le nom ») : même place dans l'ordre, même
    fournisseur. `deplacer(ancien, nouveau)` déplace profil, compteur et état (rend un code, vide =
    fait) ; il est appelé sous le verrou des comptes, AVANT l'écriture de comptes.json."""
    ancien, nouveau = str(ancien or "").strip().lower(), str(nouveau or "").strip().lower()
    if not ALIAS.fullmatch(nouveau) or nouveau == ALIAS_ANONYME:
        return comptes(), "alias"
    with _COMPTES_LOCK:
        base = _base_comptes()
        if base is None:
            return [], "comptes"
        if not any(c["alias"] == ancien for c in base):
            return base, "inconnu"
        if nouveau == ancien:
            return base, ""
        if any(c["alias"] == nouveau for c in base):
            return base, "existe"
        code = deplacer(ancien, nouveau)
        if code:
            return base, code
        return ecrire_comptes([{**c, "alias": nouveau} if c["alias"] == ancien else c for c in base]), ""


def ordonner_comptes(ordre: Any) -> tuple[list[dict], str]:
    """`ordre` : TOUS les alias, chacun une fois, dans l'ordre voulu du repli."""
    voulus = [str(a or "").strip().lower() for a in ordre] if isinstance(ordre, (list, tuple)) else []
    with _COMPTES_LOCK:
        base = _base_comptes()
        if base is None:
            return [], "comptes"
        par_alias = {c["alias"]: c for c in base}
        if sorted(voulus) != sorted(par_alias) or len(set(voulus)) != len(voulus):
            return base, "ordre"
        return ecrire_comptes([par_alias[a] for a in voulus]), ""


def comptes_du_service(service: str) -> list[str]:
    """Alias qui servent ce service, dans l'ordre ; service sans compte : l'alias anonyme."""
    f = fournisseur(service)
    if not f:
        return []
    if f == "aucun":
        return [ALIAS_ANONYME]
    return [c["alias"] for c in comptes() if c["fournisseur"] == f]


def paires(type_: str = "image", prets: tuple[str, ...] | None = None) -> list[tuple[str, str]]:
    """(alias, service) connus pour ce type, dans l'ordre du mode auto ; le profil anonyme
    seulement si son service est prêt (`prets`)."""
    out = []
    for service in services(type_):
        for alias in comptes_du_service(service):
            if alias == ALIAS_ANONYME and prets is not None and service not in prets:
                continue
            out.append((alias, service))
    return out


def _ecrire_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- état des quotas

def _jour(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def reprise_apres(texte: str, now: float) -> float | None:
    """Heure de reprise lue par le worker dans le message du site (« 15:00 », « +3h »,
    « demain »), en horodatage ; None si le site n'en donne pas (jamais devinée)."""
    texte = str(texte or "").strip()
    lt = time.localtime(now)
    if texte == "demain":
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, 0, 0, 0, 0, 0, -1))
    m = re.fullmatch(r"\+(\d{1,2})h", texte)
    if m:
        return now + int(m.group(1)) * 3600
    m = re.fullmatch(r"(\d{2}):(\d{2})", texte)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, int(m.group(1)), int(m.group(2)), 0, 0, 0, -1))
        return t if t > now else time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, int(m.group(1)),
                                              int(m.group(2)), 0, 0, 0, -1))
    return None


_ETAT_LOCK = threading.RLock()  # partagé : images et vidéo écrivent le même fichier


def migrer_v1(data: dict) -> dict:
    """v1 {"comptes": {alias: {moteur, jour, n, …}}} → v2 {"version": 2, "comptes": {alias:
    {"compte": {a_reconnecter, pause_jusqua}, "services": {moteur: {jour, n, …}}}}}."""
    comptes_v2: dict[str, dict] = {}
    for alias, c in (data.get("comptes") or {}).items():
        if not isinstance(c, dict):
            continue
        c = dict(c)
        service = str(c.pop("moteur", "") or "gemini")
        compte = {k: c.pop(k) for k in CHAMPS_COMPTE if k in c}
        comptes_v2[str(alias)] = {"compte": compte, "services": {service: c}}
    return {"version": 2, "comptes": comptes_v2}


class Etat:
    """`runtime/imagegen/etat.json` v2 : par alias, champs du COMPTE (`a_reconnecter`,
    `pause_jusqua`) et, par service, {jour, n, debut, epuise_le, message_vu, reprise_apres,
    derniere_sonde, dernier_succes}. `n` : UNITÉS parties sur la fenêtre du service (`jour` :
    date du compteur ; `debut` : heure de la première unité d'une fenêtre de 24 h). Écriture
    atomique (temporaire puis `os.replace`, 0600). Service omis : le premier noté pour cet
    alias, sinon « gemini » (appels d'avant P40)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.IMAGES_ETAT_FILE
        self._lock = _ETAT_LOCK

    def _lire(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 2, "comptes": {}}
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("comptes"), dict):
            cle = _cle_fichier(self.path)
            if _etat_signale.get("cle") != cle:  # une ligne par version du fichier
                _etat_signale["cle"] = cle
                log.warning("état des quotas IA illisible : lu comme vide, copie gardée à la prochaine écriture")
            return {"version": 2, "comptes": {}}
        if data.get("version") != 2:
            return migrer_v1(data)
        return data

    def _ecrire(self, data: dict) -> None:
        if self.path.exists():
            v1 = self.path.with_name(f"{self.path.stem}.v1{self.path.suffix}")
            try:
                ancien = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                ancien = None
            try:
                if isinstance(ancien, dict) and isinstance(ancien.get("comptes"), dict):
                    if ancien.get("version") != 2 and not v1.exists():
                        shutil.copyfile(self.path, v1)
                        os.chmod(v1, 0o600)
                else:  # illisible ou de forme inconnue : gardé à côté avant d'être remplacé
                    garde = self.path.with_name(f"{self.path.stem}.illisible-{int(time.time())}{self.path.suffix}")
                    shutil.copyfile(self.path, garde)
                    os.chmod(garde, 0o600)
                    log.warning("état des quotas IA illisible gardé dans %s", garde.name)
            except OSError:
                log.warning("copie de l'état des quotas IA impossible avant écriture")
        _ecrire_json(self.path, data)

    @staticmethod
    def _service_par_defaut(entree: dict, moteur: str) -> str:
        if moteur:
            return moteur
        noms = list((entree.get("services") or {}).keys())
        return noms[0] if noms else "gemini"

    def compte(self, alias: str, now: float | None = None, moteur: str = "") -> dict:
        """Copie de l'état (alias, service), compteur remis à zéro si sa fenêtre est passée
        (minuit pour « jour », 24 h après la première unité pour « 24h »). Porte aussi les
        champs du compte, et `moteur`/`service`."""
        now = time.time() if now is None else now
        with self._lock:
            entree = self._lire()["comptes"].get(alias) or {}
        service = self._service_par_defaut(entree, moteur)
        c = dict((entree.get("services") or {}).get(service) or {})
        if fenetre(service) == "24h":
            if c.get("debut") and now - float(c["debut"]) >= 86400:
                c["n"], c["debut"] = 0, None
        elif c.get("jour") != _jour(now):
            c["jour"], c["n"] = _jour(now), 0
        c.setdefault("n", 0)
        c.update({k: v for k, v in (entree.get("compte") or {}).items() if k in CHAMPS_COMPTE})
        c["moteur"] = c["service"] = service
        return c

    def _maj(self, alias: str, now: float, moteur: str = "", **champs: Any) -> dict:
        with self._lock:
            data = self._lire()
            c = self.compte(alias, now, moteur)
            service = c["service"]
            c.update(champs)
            entree = data["comptes"].setdefault(alias, {})
            entree["compte"] = {k: c[k] for k in CHAMPS_COMPTE if k in c}
            entree.setdefault("services", {})[service] = {
                k: v for k, v in c.items() if k not in CHAMPS_COMPTE and k not in ("moteur", "service")}
            data["version"] = 2
            self._ecrire(data)
            return c

    def disponible(self, alias: str, moteur: str, now: float | None = None,
                   unites: int = 1) -> tuple[bool, str, bool]:
        """(oui, raison du non : quota|plafond|captcha, sonde) : `sonde` vrai quand le compte
        est épuisé mais qu'un essai de contrôle est permis. Épuisé un autre jour sur un service
        à fenêtre « jour » : de nouveau essayé après minuit (reset quotidien, demande du 27/10).
        `unites` (P41) : coût de la génération voulue ; « plafond » si elle ne tient plus dans la
        fenêtre (vidéo : 40 crédits pris sur 50, Fast à 20 refusé). 1 = règle des images."""
        now = time.time() if now is None else now
        c = self.compte(alias, now, moteur)
        if (c.get("pause_jusqua") or 0) > now:
            return False, "captcha", False
        if not limite_solde(c["service"]) and c["n"] + max(1, int(unites)) > plafond(c["service"]):
            return False, "plafond", False
        trop = c.get("trop_cher")  # site limité au solde : ce coût (ou plus) refusé aujourd'hui
        if trop and _jour(float(c.get("trop_cher_le") or 0)) == _jour(now) and max(1, int(unites)) >= trop:
            return False, "quota", False
        epuise = c.get("epuise_le")
        if not epuise:
            return True, "", False
        reprise = c.get("reprise_apres")
        if (reprise and now >= reprise) or now >= epuise + EPUISE_REPLI_S:
            return True, "", True
        if fenetre(c["service"]) == "jour" and _jour(epuise) != _jour(now) \
                and (c.get("derniere_sonde") is None or _jour(c["derniere_sonde"]) != _jour(now)):
            return True, "", True
        if now - max(epuise, c.get("derniere_sonde") or 0) >= SONDE_S:
            return True, "", True
        return False, "quota", False

    def compter(self, alias: str, moteur: str, now: float | None = None, unites: int = 1) -> int:
        """Génération partie (évènement `envoye` du worker) : `unites` comptent dans le plafond
        du service. Fenêtre « 24h » : la première unité en fixe le départ."""
        now = time.time() if now is None else now
        with self._lock:
            c = self.compte(alias, now, moteur)
            champs: dict[str, Any] = {"n": c["n"] + max(1, int(unites))}
            if fenetre(c["service"]) == "24h" and not c.get("debut"):
                champs["debut"] = now
            self._maj(alias, now, c["service"], **champs)
            return champs["n"]

    def rendre(self, alias: str, moteur: str, now: float | None = None, unites: int = 1) -> int:
        """Crédits comptés à `envoye` mais pas débités par le site (P41 B2 : pointe, rythme ou
        activité inhabituelle annoncés APRÈS l'envoi) : retirés du compteur, jamais sous zéro.
        Fenêtre « 24h » revenue à zéro : son départ est effacé (aucune unité ne l'a ouverte)."""
        now = time.time() if now is None else now
        with self._lock:
            c = self.compte(alias, now, moteur)
            champs: dict[str, Any] = {"n": max(0, c["n"] - max(0, int(unites)))}
            if fenetre(c["service"]) == "24h" and champs["n"] == 0:
                champs["debut"] = None
            self._maj(alias, now, c["service"], **champs)
            return champs["n"]

    def epuise(self, alias: str, message: str, reprise: str, sonde: bool, now: float | None = None,
               moteur: str = "") -> dict:
        now = time.time() if now is None else now
        with self._lock:
            c = self.compte(alias, now, moteur)
            champs: dict[str, Any] = {"message_vu": str(message or "")[:500],
                                      "reprise_apres": reprise_apres(reprise, now)}
            if sonde and c.get("epuise_le"):
                champs["derniere_sonde"] = now  # l'épuisement d'origine reste la référence
            else:
                champs.update(epuise_le=now, derniere_sonde=None)
            return self._maj(alias, now, c["service"], **champs)

    def trop_cher(self, alias: str, moteur: str, unites: int, now: float | None = None) -> int:
        """Site limité à son SOLDE (Dreamina, 29/10) : le solde ne couvrait pas `unites` crédits.
        Pas un épuisement du compte : seuls les coûts au moins aussi chers sont refusés jusqu'à
        minuit (un modèle moins cher y est encore essayé). Rend le seuil retenu."""
        now = time.time() if now is None else now
        with self._lock:
            c = self.compte(alias, now, moteur)
            avant = c.get("trop_cher") if _jour(float(c.get("trop_cher_le") or 0)) == _jour(now) else None
            seuil = min(int(avant), int(unites)) if avant else int(unites)
            self._maj(alias, now, c["service"], trop_cher=seuil, trop_cher_le=now)
            return seuil

    def succes(self, alias: str, now: float | None = None, moteur: str = "") -> float | None:
        """Génération obtenue : l'épuisement éventuel est levé. Rend les secondes écoulées
        depuis l'épuisement (pour mesurer le reset réel), None si le compte n'était pas épuisé."""
        now = time.time() if now is None else now
        with self._lock:
            c = self.compte(alias, now, moteur)
            epuise = c.get("epuise_le")
            self._maj(alias, now, c["service"], epuise_le=None, message_vu="", reprise_apres=None,
                      derniere_sonde=None, dernier_succes=now, a_reconnecter=False, site_a_reconnecter=False)
            return (now - epuise) if epuise else None

    def renommer(self, ancien: str, nouveau: str) -> None:
        """Compte renommé : son état (compteurs, épuisement, à reconnecter) le suit."""
        with self._lock:
            data = self._lire()
            if ancien in data["comptes"]:
                data["comptes"][nouveau] = data["comptes"].pop(ancien)
                self._ecrire(data)

    def noter(self, alias: str, now: float | None = None, moteur: str = "", **champs: Any) -> dict:
        now = time.time() if now is None else now
        return self._maj(alias, now, moteur, **champs)


# --------------------------------------------------------------------------- choix

@dataclass
class Choix:
    alias: str
    service: str
    modele: str
    sonde: bool


@dataclass
class Refus:
    code: str               # quota | plafond | captcha | session | comptes (comptes.json illisible)
    proposition: str = ""   # manuel : autre service disponible (« Essayer avec X »), jamais pris seul


def cle_essai(alias: str, service: str) -> str:
    return f"{alias}/{service}"


def choisir(*, mode: str, service: str = "", modele: str = "", essais: list[str] | tuple[str, ...] = (),
            prets: tuple[str, ...] | list[str], etat: Etat, profil_existe: Callable[[str], bool],
            type_: str = "image", now: float | None = None, proposer: bool = True,
            ordre: list[str] | tuple[str, ...] | None = None, pieces: bool = False,
            duree: int = 0, duree_libre: bool = False) -> Choix | Refus:
    """Prochain (compte, service, modèle) à essayer.

    AUTO (défaut) : services prêts dans l'ordre du catalogue, comptes dans l'ordre de
    `comptes.json` ; un compte épuisé, au plafond, au repos ou non connecté est sauté ; modèle
    par défaut de chaque service. MANUEL : service ET modèle fixés, repli seulement sur un autre
    compte du même service ; jamais sur un autre moteur : le refus porte `proposition` (premier
    autre service prêt et disponible) pour un bouton « Essayer avec X » que l'utilisateur clique.
    `essais` : clés `cle_essai` déjà tentées pour cette demande (jamais reprises). `proposer` faux :
    pas de proposition (appel interne qui la cherche, sinon récursion sans fin).
    P40 A4 : `ordre` (réglage « ordre des moteurs ») remplace l'ordre du catalogue en auto (un
    service connu absent de la liste vient après, dans l'ordre du catalogue) ; `pieces` vrai : seuls
    les services qui prennent des pièces jointes sont essayés ou proposés.
    P41 B5 (vidéo, plusieurs sites) : `duree` (s) est sautée par un service dont le modèle ne la
    propose pas (Dreamina : 5 s, Flow : 8 s), sauf `duree_libre` (durée pas demandée) : ce service
    est alors essayé à SA durée par défaut (`duree_video`)."""
    now = time.time() if now is None else now
    manuel = mode == "manuel"
    du_type = services(type_)
    if ordre:
        du_type = [s for s in dict.fromkeys(ordre) if s in du_type] + [s for s in du_type if s not in ordre]
    candidats = [service] if manuel else [s for s in du_type if s in prets and (not pieces or accepte_pieces(s))]
    raisons: list[str] = []
    for s in candidats:
        m = (modele or modele_defaut(s)) if manuel else modele_defaut(s)
        d = duree_video(s, m, duree, duree_libre) if duree else 0
        if d is None:
            continue
        for alias in comptes_du_service(s):
            if cle_essai(alias, s) in essais:
                continue
            if fournisseur(s) != "aucun" and not profil_existe(alias):
                raisons.append("non_connecte")
                continue
            ok, raison, sonde = etat.disponible(alias, s, now, unites=cout(s, m, d))
            if ok:
                return Choix(alias, s, m, sonde)
            raisons.append(raison)
    if "quota" in raisons or (essais and not raisons):
        code = "quota"
    elif "plafond" in raisons:
        code = "plafond"
    elif "captcha" in raisons:
        code = "captcha"
    elif not raisons and comptes_illisibles():
        code = "comptes"    # comptes.json illisible : pas « non connecté », qui tromperait
    else:
        code = "session"
    proposition = ""
    if manuel and proposer:
        for s in du_type:
            if s == service or s not in prets or (pieces and not accepte_pieces(s)):
                continue
            autre = choisir(mode="manuel", service=s, prets=prets, etat=etat, profil_existe=profil_existe,
                            type_=type_, now=now, proposer=False, pieces=pieces)
            if isinstance(autre, Choix):
                proposition = s
                break
    return Refus(code, proposition)


# --------------------------------------------------------------------------- worker Node

@contextlib.contextmanager
def chrome_libre(arreter_si: Callable[[], bool] = lambda: False):
    """Le verrou `CHROME`, attendu par tranches de `ARRET_SONDE_S` : rend False SANS l'avoir pris si
    `arreter_si()` devient vrai pendant l'attente (vidéo annulée pendant qu'une image tient Chrome)."""
    while not CHROME.acquire(timeout=ARRET_SONDE_S):
        if arreter_si():
            yield False
            return
    try:
        yield True
    finally:
        CHROME.release()


def node() -> str | None:
    from .mcp_config import _node
    return _node()


def env_worker() -> dict[str, str]:
    """Environnement du worker : sans clé ni variable de Claude, sans le rythme de test ; avec le dossier
    des projets du cœur (`ASTREL_WORKDIR`, 05/11 : racine permise des pièces jointes, imagegen/pieces.mjs)."""
    drop = ("ANTHROPIC_", "CLE_API_", "CLAUDE", "OPENAI_", "GEMINI_")
    env = {k: v for k, v in os.environ.items() if not k.startswith(drop)}
    env.pop("ASTREL_IMAGEGEN_RYTHME", None)
    env["ASTREL_WORKDIR"] = str(config.WORKDIR)
    return env


def executer_worker(argv: list[str], entree: dict, ligne: Callable[[dict], None], *, delai_s: float,
                    arreter_si: Callable[[], bool] = lambda: False, nom: str = "worker") -> dict:
    """Lance le worker Node (images ou vidéo), lui passe `entree` (une ligne JSON), donne chaque ligne
    d'étape à `ligne` et rend la dernière {t: fini|echec}. Une garde regarde chaque seconde le délai
    (`delai_s`) et `arreter_si()` (annulation, arrêt d'urgence) : le worker est alors arrêté AVEC son
    Chrome (`arreter`), et la sortie se termine d'elle-même."""
    if hors_ligne.actif():  # 04/11 : le worker pilote Chrome vers des sites en ligne
        return {"t": "echec", "code": "hors_ligne", "message": hors_ligne.REFUS}
    # 01/11 : le worker ferme son entrée tout de suite (la fin du tube ne le prévient pas) : il meurt
    # avec le cœur (Linux, `platform.enfants`), et Playwright ferme alors son Chrome (SIGTERM).
    proc = enfants.popen(argv, cwd=str(config.IMAGES_WORKER_DIR), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            env=env_worker(), start_new_session=True)
    profil = entree.get("profil") or None
    fin_delai = time.monotonic() + delai_s

    def garde() -> None:
        while proc.poll() is None:
            if arreter_si() or time.monotonic() > fin_delai:
                arreter(proc, profil, nom)
                return
            time.sleep(ARRET_SONDE_S)

    threading.Thread(target=garde, name=f"{nom}-garde", daemon=True).start()
    erreurs: list[str] = []
    lecteur = threading.Thread(target=lambda: erreurs.extend(proc.stderr.readlines()[-20:]), daemon=True)
    lecteur.start()
    fin: dict = {"t": "echec", "code": "interne", "message": "worker arrêté sans réponse"}
    try:
        assert proc.stdin and proc.stdout
        proc.stdin.write(json.dumps(entree, ensure_ascii=False) + "\n")
        proc.stdin.close()
        for brut in proc.stdout:
            try:
                msg = json.loads(brut)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("t") in ("fini", "echec"):
                fin = msg
            else:
                ligne(msg)
        proc.wait(timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        arreter(proc, profil, nom)
        fin = {"t": "echec", "code": "interne", "message": str(exc)}
    finally:
        lecteur.join(timeout=2)
    for e in erreurs:
        log.info("%s : %s", nom, e.rstrip()[:300])
    return fin


def _signal_groupe(proc: subprocess.Popen, sig: int) -> None:
    if systeme.UNIX:
        try:
            os.killpg(proc.pid, sig)
            return
        except (OSError, ProcessLookupError):
            pass
    else:
        systeme.tuer_arbre(proc.pid, sig)  # 03/11 : Windows, pas de groupe : l'arbre (taskkill /T /F)
    try:
        proc.send_signal(sig)
    except OSError:
        pass


def chrome_du_profil(profil: Path | str) -> list[int]:
    """PID des processus de l'utilisateur lancés avec `--user-data-dir=<profil>` : le Chrome de Playwright vit
    dans SON groupe de processus (détaché), hors de celui du worker."""
    return systeme.processus_avec(f"--user-data-dir={Path(profil)}")  # 03/11 : /proc ou psutil


def vivant(pid: int) -> bool:
    return systeme.pid_vivant(pid)


def arreter(proc: subprocess.Popen, profil: Path | str | None = None, nom: str = "worker") -> list[int]:
    """Arrête le worker et son Chrome, sans orphelin : SIGTERM au groupe du worker (Playwright ferme
    alors Chrome), SIGKILL après `ARRET_GRACE_S`, puis tout processus encore lancé sur le profil du
    compte (SIGTERM puis SIGKILL). Bloquant jusqu'à ~2 × la grâce. Rend les PID qui ont survécu."""
    _signal_groupe(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=ARRET_GRACE_S)
    except subprocess.TimeoutExpired:
        log.warning("%s %d sourd à SIGTERM : SIGKILL", nom, proc.pid)
        _signal_groupe(proc, systeme.SIGKILL)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.error("%s %d toujours vivant après SIGKILL", nom, proc.pid)
    if profil is None:
        return []
    restants = chrome_du_profil(profil)
    for sig in (signal.SIGTERM, systeme.SIGKILL):
        for pid in restants:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        fin = time.monotonic() + ARRET_GRACE_S
        while restants and time.monotonic() < fin:
            time.sleep(0.2)
            restants = [p for p in restants if vivant(p)]
        if not restants:
            break
    if restants:
        log.error("Chrome du profil %s toujours vivant : %s", profil, restants)
    else:
        log.info("%s %d arrêté, aucun Chrome orphelin", nom, proc.pid)
    return restants
