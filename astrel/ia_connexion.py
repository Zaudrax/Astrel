"""Connexion d'un compte d'IA web (plan P40 lot A4, 28/10/2026).

Demande de l'utilisateur (28/10) : plusieurs comptes (Google surtout) pour le volume gratuit d'images
et de vidéos. Un compte se connecte UNE fois, À LA MAIN : ce module ouvre un Chrome NORMAL et
VISIBLE sur le profil persistant du compte (`runtime/imagegen/profils/<alias>`), sur la page du
service, et n'y touche plus. Ni pilotage, ni port de débogage, ni `--enable-automation`, ni
fenêtre cachée : l'utilisateur tape lui-même son identifiant et son mot de passe, Astrel ne les voit
jamais, puis ferme la fenêtre.

Règles (plan P40 étape 9) :
- seule fenêtre visible permise sur l'écran de l'utilisateur, et seulement sur SA demande explicite :
  la commande `image_compte_connecter` est refusée par le cœur si elle vient de Claude
  (`_from_claude`) ; rien ici ne l'ouvre de soi-même (un échec « session » ne l'ouvre pas) ;
- jamais deux Chrome sur le même profil : refus si un worker le tient (`<profil>.lock` d'un pid
  vivant, imagegen/noyau.mjs `verrouiller`) ou si Chrome l'a déjà ouvert (`SingletonLock`) ;
- pendant la connexion, le profil est VERROUILLÉ par le même `<profil>.lock` (pid du Chrome) :
  un worker d'images ou de vidéos qui le voudrait reçoit `occupe` ; le verrou est rendu à la
  fermeture de la fenêtre (fil de surveillance), ou repris par le worker si le pid est mort ;
- rien sous arrêt d'urgence ni mémoire du PC en « blocage » (vérifié par l'appelant, images_ia).
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import config, ia_web
from .platform import systeme

log = logging.getLogger("astrel.ia_connexion")

# Même navigateur que le worker (imagegen/noyau.mjs CHROME) : un profil ouvert par une autre
# version de Chrome pourrait être migré et ne plus servir au worker.
CHROME = os.environ.get("ASTREL_IMAGEGEN_CHROME") or "/usr/bin/google-chrome"
# Drapeaux qui feraient de ce Chrome un navigateur piloté : jamais (test_connexion_sans_pilotage).
INTERDITS = ("--remote-debugging", "--enable-automation", "--headless", "--remote-allow-origins",
             "--user-agent", "--disable-blink-features")
_ENV_RETIRES = ("ANTHROPIC_", "CLE_API_", "CLAUDE", "OPENAI_", "GEMINI_", "ASTREL_IMAGEGEN_RYTHME")

_lock = threading.Lock()
_ouvertes: dict[str, Any] = {}           # alias → processus Chrome de la connexion
on_change: Callable[[], None] | None = None  # appelé à l'ouverture et à la fermeture (état du HUD)
# 30/10 (demande : « il faut que ce soit dynamique ») : à la fermeture de la fenêtre, la session du
# fournisseur est relue dans le profil ; `on_session(alias, ouverte)` met l'état du compte à jour.
on_session: Callable[[str, bool], None] | None = None

# Cookie de session par fournisseur : (domaines, noms). Seuls l'hôte, le nom et l'expiration sont lus,
# JAMAIS la valeur (chiffrée de toute façon) : présent et non expiré = compte connecté.
SESSIONS = {
    "google": (("google.com",), ("SID", "__Secure-1PSID", "__Secure-3PSID")),
    "openai": (("chatgpt.com", "openai.com"), ("__Secure-next-auth.session-token",)),
}
# Sites à connexion PROPRE (catalogue `connexion`: `site`), par service (30/10 : noms relevés dans le
# profil après une vraie connexion à Dreamina, valeurs jamais lues)
SESSIONS_SITE = {
    "dreamina": (("capcut.com",), ("sessionid", "sid_tt")),
}
_EPOQUE_CHROME = 11644473600  # secondes entre 1601 (horloge des cookies de Chrome) et 1970


def session_ouverte(p: Path, fournisseur: str, now: float | None = None) -> bool | None:
    """Le profil `p` a-t-il une session ouverte chez `fournisseur` ? None : on ne sait pas (fournisseur
    sans cookie connu, base absente ou illisible). À appeler fenêtre FERMÉE (Chrome tient la base)."""
    regle = SESSIONS.get(fournisseur) or SESSIONS_SITE.get(fournisseur)
    base = p / "Default" / "Cookies"
    if regle is None or not base.is_file():
        return None
    domaines, noms = regle
    maintenant = int(((time.time() if now is None else now) + _EPOQUE_CHROME) * 1_000_000)
    hotes = " OR ".join("host_key = ? OR host_key LIKE ?" for _ in domaines)
    params: list[Any] = [x for d in domaines for x in (d, f"%.{d}")]
    marques = ",".join("?" * len(noms))
    try:
        con = sqlite3.connect(f"file:{base}?mode=ro&immutable=1", uri=True, timeout=2)
        try:
            ligne = con.execute(f"SELECT 1 FROM cookies WHERE ({hotes}) AND name IN ({marques}) "
                                "AND (expires_utc = 0 OR expires_utc > ?) LIMIT 1",
                                [*params, *noms, maintenant]).fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        log.warning("session du profil %s illisible (%s)", p.name, exc)
        return None
    return ligne is not None


def profil(alias: str) -> Path:
    return config.IMAGES_PROFILS / alias


def _pid_vivant(pid: int) -> bool:
    return systeme.pid_existe(pid)  # 03/11 : sous Windows, os.kill(pid, 0) n'est pas un simple test


def verrou_tenu(p: Path) -> bool:
    """`<profil>.lock` d'un worker (ou d'une connexion) dont le pid vit encore."""
    try:
        brut = Path(f"{p}.lock").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    except OSError:
        return True  # illisible : prudence, comme le worker (« occupe »)
    return not re.fullmatch(r"\d+", brut) or _pid_vivant(int(brut))


def chrome_ouvert(p: Path) -> bool:
    """`SingletonLock` du profil (lien « hôte-pid ») d'un Chrome encore vivant sur cette machine."""
    try:
        cible = os.readlink(p / "SingletonLock")
    except OSError:
        return False
    hote, _, pid = cible.rpartition("-")
    if hote and hote != systeme.nom_machine():
        return True  # profil ouvert depuis une autre machine : on n'y touche pas
    return pid.isdigit() and _pid_vivant(int(pid))


def ouverte(alias: str) -> bool:
    """Une fenêtre de connexion de ce compte est-elle encore ouverte ?"""
    with _lock:
        proc = _ouvertes.get(alias)
    return proc is not None and proc.poll() is None


def ouvertes() -> list[str]:
    with _lock:
        aliases = list(_ouvertes)
    return [a for a in aliases if ouverte(a)]


def url_du_fournisseur(fournisseur: str) -> str:
    """Page où se connecter : celle du premier service du catalogue servi par ce fournisseur
    (images d'abord, dans l'ordre du mode auto), en https seulement."""
    for type_ in ia_web.TYPES:
        for s in ia_web.services(type_):
            info = ia_web.info(s) or {}
            if info.get("fournisseur") == fournisseur and str(info.get("url") or "").startswith("https://"):
                return str(info["url"])
    return ""


def urls_du_fournisseur(fournisseur: str) -> list[str]:
    """30/10 : TOUTES les pages des services de ce fournisseur (images puis vidéos, sans doublon) : la
    fenêtre de connexion ouvre un onglet par site (Gemini, Flow, Dreamina…), l'utilisateur connecte tout
    d'un coup (Dreamina a sa propre connexion, que l'onglet Gemini seul ne faisait pas)."""
    urls: list[str] = []
    for type_ in ia_web.TYPES:
        for s in ia_web.services(type_):
            info = ia_web.info(s) or {}
            u = str(info.get("url") or "")
            if info.get("fournisseur") == fournisseur and u.startswith("https://") and u not in urls:
                urls.append(u)
    return urls


def argv(p: Path, url: str | list[str]) -> list[str]:
    """Chrome ordinaire : le profil du compte, la page (ou les pages, un onglet chacune) des services."""
    urls = [url] if isinstance(url, str) else list(url)
    return [CHROME, f"--user-data-dir={p}", "--no-first-run", "--no-default-browser-check", "--new-window", *urls]


def env() -> dict[str, str]:
    """Environnement de la session (affichage, D-Bus) sans clé ni variable de Claude."""
    return {k: v for k, v in os.environ.items() if not k.startswith(_ENV_RETIRES)}


def _prendre_verrou(p: Path) -> int | None:
    """Crée `<profil>.lock` (O_EXCL, 0600) au pid du cœur ; None s'il est déjà pris."""
    try:
        return os.open(f"{p}.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return None


def _effacer_verrou_mort(p: Path) -> None:
    """Efface `<profil>.lock` s'il porte le pid d'un processus MORT, relu juste avant l'effacement.
    Vide, illisible ou vivant : laissé (O_EXCL dira ensuite « occupe »)."""
    lock = Path(f"{p}.lock")
    try:
        brut = lock.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if re.fullmatch(r"\d+", brut) and not _pid_vivant(int(brut)):
        try:
            if lock.read_text(encoding="utf-8").strip() == brut:
                lock.unlink()
        except OSError:
            pass


def _rendre_verrou(p: Path, pid: int) -> None:
    """Retire `<profil>.lock` s'il porte encore ce pid (un worker a pu le reprendre depuis)."""
    lock = Path(f"{p}.lock")
    try:
        if lock.read_text(encoding="utf-8").strip() == str(pid):
            lock.unlink()
    except OSError:
        pass


def ouvrir(alias: str, *, lanceur: Callable[..., Any] = subprocess.Popen) -> dict:
    """Ouvre la fenêtre de connexion du compte `alias` (déclaré dans comptes.json). Rend
    {ok, code, alias, deja?} ; codes d'échec : inconnu | occupe | absent | url | erreur."""
    alias = str(alias or "").strip().lower()
    compte = next((c for c in ia_web.comptes() if c["alias"] == alias), None)
    if compte is None:
        return {"ok": False, "code": "inconnu", "alias": alias}
    if ouverte(alias):
        return {"ok": True, "code": "", "alias": alias, "deja": True}
    url = urls_du_fournisseur(compte["fournisseur"]) or url_du_fournisseur(compte["fournisseur"])
    if not url:
        return {"ok": False, "code": "url", "alias": alias}
    if not (os.path.isfile(CHROME) and os.access(CHROME, os.X_OK)):
        return {"ok": False, "code": "absent", "alias": alias}
    p = profil(alias)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    p.mkdir(exist_ok=True, mode=0o700)
    with _lock:
        if verrou_tenu(p) or chrome_ouvert(p):
            return {"ok": False, "code": "occupe", "alias": alias}
        # Verrou d'un pid mort : repris, comme le fait le worker, mais SEULEMENT s'il porte encore
        # ce pid mort au moment de l'effacer (relecture fiche 4 : un worker a pu le prendre entre-temps).
        _effacer_verrou_mort(p)
        fd = _prendre_verrou(p)
        if fd is None:
            return {"ok": False, "code": "occupe", "alias": alias}
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            proc = lanceur(argv(p, url), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, env=env(), start_new_session=True, close_fds=True)
        except OSError as exc:
            _rendre_verrou(p, os.getpid())
            log.warning("connexion du compte %s : Chrome impossible à lancer (%s)", alias, exc)
            return {"ok": False, "code": "erreur", "alias": alias}
        # Le verrou porte maintenant le pid du Chrome : un worker le verra vivant (« occupe »).
        Path(f"{p}.lock").write_text(str(proc.pid), encoding="utf-8")
        _ouvertes[alias] = proc
    log.info("connexion du compte %s : fenêtre Chrome ouverte (pid %s), à fermer par l'utilisateur", alias, proc.pid)
    threading.Thread(target=_surveiller, args=(alias, proc, p), name=f"connexion-{alias}", daemon=True).start()
    _signaler()
    return {"ok": True, "code": "", "alias": alias}


def _surveiller(alias: str, proc: Any, p: Path) -> None:
    try:
        proc.wait()
    except Exception:  # noqa: BLE001 — le verrou doit être rendu quoi qu'il arrive
        log.exception("surveillance de la connexion %s", alias)
    _rendre_verrou(p, proc.pid)
    _rendre_verrou(p, os.getpid())  # pid du Chrome jamais écrit (cas d'erreur) : rendu aussi
    with _lock:
        if _ouvertes.get(alias) is proc:
            _ouvertes.pop(alias, None)
    log.info("connexion du compte %s : fenêtre fermée", alias)
    compte = next((c for c in ia_web.comptes() if c["alias"] == alias), None)
    ouverte_ = session_ouverte(p, compte["fournisseur"]) if compte else None
    if ouverte_ is not None:
        log.info("connexion du compte %s : session %s", alias, "ouverte" if ouverte_ else "absente")
        fn = on_session
        if fn is not None:
            try:
                fn(alias, ouverte_)
            except Exception:  # noqa: BLE001
                log.exception("état du compte %s après sa connexion", alias)
    _signaler()


def _signaler() -> None:
    fn = on_change
    if fn is None:
        return
    try:
        fn()
    except Exception:  # noqa: BLE001
        log.exception("état des comptes après une connexion")
