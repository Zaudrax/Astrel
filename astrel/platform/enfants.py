"""Enfants qui meurent avec le cœur (01/11, fiabilité ; HERMES-outils.md, idée 27).

Si le cœur est tué net (`kill -9`, manque de mémoire, plantage hors systemd), ses enfants
devenaient orphelins : le CLI `claude` (et ses serveurs MCP), la passerelle litellm, le worker
Node des images et vidéos (qui ferme son entrée tout de suite : la fin du tube ne le prévient
pas) et son Chrome. Sous Linux, `prctl(PR_SET_PDEATHSIG, SIGTERM)` posé dans l'enfant, entre
`fork` et `exec`, demande au noyau de lui envoyer SIGTERM dès que son parent meurt (SIGTERM :
le CLI range sa session, Playwright ferme Chrome).

Piège (prctl(2)) : le « parent » est le FIL qui a lancé l'enfant, pas le process. Un enfant
lancé depuis un fil de commande, de démarrage ou de relance du cerveau serait tué dès la fin de
ce fil (le CLI tué juste après un changement de modèle). Ici, l'enfant est donc toujours lancé
par UN fil permanent (`_Lanceur`), qui ne finit qu'avec le process.

Hors Linux (macOS, Windows, autre) : rien de changé, `subprocess.Popen` tel quel. Réglage
`config.PDEATHSIG` (`ASTREL_PDEATHSIG=0` coupe tout). Seuls les enfants qui doivent mourir avec le
cœur passent par `popen` ; jamais ce qui doit lui survivre (mise à jour, minuteurs des rappels,
Chrome de connexion d'un compte, `xdg-open`). `parecord`/`paplay` n'en ont pas besoin : un tube
fermé les arrête déjà (SIGPIPE, fin de l'entrée), et `fork` à chaque phrase coûterait du temps.
"""
from __future__ import annotations

import ctypes
import logging
import os
import queue
import signal
import subprocess
import threading
from typing import Any, Callable

from . import SYSTEM

log = logging.getLogger("astrel.enfants")

PR_SET_PDEATHSIG = 1
SIGNAL = signal.SIGTERM
LANCEMENT_MAX_S = 30.0  # au-delà, le fil lanceur est tenu pour bloqué : l'appelant reçoit une erreur

_prctl: Any = None
_prctl_lock = threading.Lock()
_prctl_essaye = False


def _fonction_prctl() -> Any:
    """`prctl` de la libc, résolu UNE fois dans le parent (rien à charger entre fork et exec)."""
    global _prctl, _prctl_essaye
    with _prctl_lock:
        if not _prctl_essaye:
            _prctl_essaye = True
            try:
                fn = ctypes.CDLL(None, use_errno=True).prctl
                fn.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
                fn.restype = ctypes.c_int
                _prctl = fn
            except (OSError, AttributeError) as exc:
                log.warning("prctl indisponible, les enfants ne mourront pas avec le cœur : %s", exc)
        return _prctl


def actif(system: str | None = None) -> bool:
    """Linux seulement, et réglage allumé."""
    from .. import config

    return (system or SYSTEM) == "linux" and bool(config.PDEATHSIG) and _fonction_prctl() is not None


def _dans_l_enfant(parent: int, prctl: Any) -> Callable[[], None]:
    """Code lancé dans l'enfant avant `exec` : le moins de Python possible (pas d'import, pas de
    journal). Parent déjà mort entre `fork` et `prctl` : le signal tout de suite."""
    sig = int(SIGNAL)

    def avant_exec() -> None:
        prctl(PR_SET_PDEATHSIG, sig, 0, 0, 0)
        if os.getppid() != parent:
            os.kill(os.getpid(), sig)

    return avant_exec


class _Lanceur:
    """Un fil permanent qui fait les `Popen` : il ne meurt qu'avec le process."""

    def __init__(self) -> None:
        self._q: "queue.Queue[tuple[Callable[[], Any], dict]]" = queue.Queue()
        self._lock = threading.Lock()
        self._fil: threading.Thread | None = None

    def _boucle(self) -> None:
        while True:
            fn, boite = self._q.get()
            with self._lock:
                if boite.get("abandon"):
                    continue  # l'appelant ne l'attend plus : surtout ne rien lancer
                boite["parti"] = True
            try:
                boite["proc"] = fn()
            except BaseException as exc:  # noqa: BLE001 — rendu à l'appelant, tel quel
                boite["erreur"] = exc
            boite["fait"].set()

    def _demarrer(self) -> threading.Thread:
        with self._lock:
            if self._fil is None or not self._fil.is_alive():
                self._fil = threading.Thread(target=self._boucle, name="lanceur-enfants", daemon=True)
                self._fil.start()
            return self._fil

    def lancer(self, fn: Callable[[], Any]) -> Any:
        fil = self._demarrer()
        if threading.current_thread() is fil:
            return fn()  # déjà dans le fil lanceur
        boite: dict = {"fait": threading.Event()}
        self._q.put((fn, boite))
        if not boite["fait"].wait(LANCEMENT_MAX_S):
            with self._lock:
                if not boite.get("parti"):
                    boite["abandon"] = True
                    raise OSError("lancement du process impossible : le fil lanceur ne répond plus")
            boite["fait"].wait()  # parti : il rendra la main (un exec ne bloque pas)
        if "erreur" in boite:
            raise boite["erreur"]
        return boite["proc"]


_lanceur = _Lanceur()


def popen(args: Any, **kwargs: Any) -> subprocess.Popen:
    """`subprocess.Popen(args, **kwargs)`, et sous Linux l'enfant meurt avec le cœur. Un
    `preexec_fn` déjà donné est respecté (rien d'ajouté)."""
    if kwargs.get("preexec_fn") is not None or not actif():
        return subprocess.Popen(args, **kwargs)
    kwargs["preexec_fn"] = _dans_l_enfant(os.getpid(), _fonction_prctl())
    return _lanceur.lancer(lambda: subprocess.Popen(args, **kwargs))
