"""SearXNG du moteur local, lancé par le CŒUR s'il ne tourne pas (05/11/2026, lot 7 partie B,
docs/chantiers/HYBRIDE-QWEN.md).

Choix (point (7) de la partie A) : le service utilisateur `astrel-searxng.service` reste PRIORITAIRE (activé par
l'utilisateur, il démarre avec Astrel et survit à ses relances) ; s'il n'est pas activé, le cœur lance
`tools/searxng.sh --lancer` lui-même au PREMIER tour web, par `platform.enfants.popen` (meurt avec le cœur,
PR_SET_PDEATHSIG), et le garde pour les tours suivants (~3 s de chargement, 281 Mo). Pourquoi pas à chaque tour :
le serveur MCP `web_local` meurt avec chaque CLI local, SearXNG doit vivre plus longtemps. Pourquoi pas au
démarrage : la mémoire du PC est comptée (16 Go) et beaucoup de sessions n'ont aucun tour web.
Jamais lancé en mode « rien ne sort » ; écoute 127.0.0.1 seulement (le script le fixe) ; un port déjà pris n'est
pas partagé (le script refuse). Journal : `logs/searxng.log` (0600), recherches masquées par le script.
SearXNG (AGPL-3.0) est un PROGRAMME séparé : jamais importé."""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import threading
import time

from . import config, hors_ligne, web_local

log = logging.getLogger("astrel.searxng_local")

SCRIPT = config.ROOT / "tools" / "searxng.sh"
VENV = config.ROOT / ".venv-searxng"
JOURNAL = "searxng.log"
ATTENTE_S = 20.0

_verrou = threading.Lock()
_proc: subprocess.Popen | None = None


def repond(delai: float = 1.5) -> bool:
    """SearXNG répond-il sur 127.0.0.1 (service, lancement à la main ou par le cœur) ?"""
    import httpx

    try:
        with httpx.Client(trust_env=False, timeout=delai) as c:
            return c.get(f"{web_local.SEARXNG_URL}/healthz").status_code == 200
    except httpx.HTTPError:
        return False


def installe() -> bool:
    return (VENV / "bin" / "granian").exists() and SCRIPT.exists()


def _port_pris() -> bool:
    with socket.socket() as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", web_local.SEARXNG_PORT)) == 0


def _environnement() -> dict:
    """Celui du cœur, sans aucune variable de Claude ni du cerveau (SearXNG n'en a rien à faire)."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("ANTHROPIC_", "CLAUDE", "ASTREL_BRAIN", "CLE_API_"))}


def assurer(attente_s: float = ATTENTE_S) -> str:
    """"" si SearXNG répond (au besoin lancé ici), sinon la raison (courte, en français)."""
    global _proc
    if repond():
        return ""
    if hors_ligne.actif():
        return "mode « rien ne sort »"
    if not installe():
        return "SearXNG n'est pas installé (tools/searxng.sh --installer)"
    if not shutil.which("bash"):
        return "SearXNG local : lancé par tools/searxng.sh, qui demande bash"
    from .platform import enfants

    with _verrou:
        if _proc is None or _proc.poll() is not None:
            if _port_pris():
                return f"le port 127.0.0.1:{web_local.SEARXNG_PORT} est pris par un autre programme"
            config.LOGS.mkdir(parents=True, exist_ok=True)
            chemin = config.LOGS / JOURNAL
            fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                # 05/11 : chemin en « / » : sous Windows, `dirname "$0"` de Git Bash ne coupe pas aux « \ »
                _proc = enfants.popen(["bash", SCRIPT.as_posix(), "--lancer"], cwd=str(config.ROOT), env=_environnement(),
                                      stdin=subprocess.DEVNULL, stdout=fd, stderr=subprocess.STDOUT)
            except OSError as exc:
                return f"SearXNG non lancé ({exc})"
            finally:
                os.close(fd)
            log.info("SearXNG lancé par le cœur (pid %s, 127.0.0.1:%s)", _proc.pid, web_local.SEARXNG_PORT)
        proc = _proc
    fin = time.monotonic() + attente_s
    while time.monotonic() < fin:
        if repond(1.0):
            return ""
        if proc.poll() is not None:
            return f"SearXNG s'est arrêté au démarrage (code {proc.returncode}, logs/{JOURNAL})"
        time.sleep(0.3)
    return "SearXNG ne répond pas encore (démarrage lent)"


def arreter() -> None:
    """Arrête le SearXNG lancé par le cœur (jamais celui du service)."""
    global _proc
    with _verrou:
        p, _proc = _proc, None
    if p is not None and p.poll() is None:
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()


def lance_par_le_coeur() -> bool:
    p = _proc
    return p is not None and p.poll() is None

