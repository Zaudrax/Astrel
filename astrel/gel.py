"""Chien de garde des boucles du cœur (01/11, fiabilité ; HERMES-cron-voix-telegram.md F2,
HERMES-agent-coeur.md idée 32). ALERTE SEULE, comme `boucles.py` : rien n'est jamais arrêté.

Un Astrel figé (micro mort mais process vivant, fil bloqué sur un verrou) ne se voyait pas :
le HUD restait connecté, la bulle aussi. Ici, chaque boucle surveillée BAT (`battre`) :
- « coeur » : la boucle principale (mot d'éveil, écoute), à chaque tour, et à chaque échange
  d'une conversation (qui a son propre délai, `attente`, compté depuis le dernier battement) ;
- « cerveau » : la lecture du cerveau (`Astrel._pump`, un évènement `idle` par seconde) ;
- « taches » : la boucle de la mission et des tâches (chaque seconde).
Un fil à part (`chien-de-garde`) regarde toutes les `GEL_PERIODE_S` : une boucle sans battement
depuis son délai est signalée UNE fois (journal avec la pile du fil figé, puis le HUD), et son
retour aussi. Une boucle n'est surveillée qu'après son premier battement (mode texte : pas de
boucle principale). Horloge `monotonic` : la mise en veille du PC ne compte pas (CLOCK_MONOTONIC
s'arrête pendant la veille sous Linux).

Filet : si même le chien ne tourne plus (fil qui tient le GIL), `faulthandler` écrit les piles
de tous les fils dans `logs/gel.log` après `GEL_GIL_S`, sans arrêter le process.
"""
from __future__ import annotations

import faulthandler
import logging
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator

from . import config

log = logging.getLogger("astrel.gel")

# Noms affichés (texte d'écran : catalogue `astrel/locales/en.json`).
NOMS = {"coeur": "la boucle d'écoute", "cerveau": "la lecture du cerveau", "taches": "la veille des tâches"}


def seuils() -> dict[str, float]:
    return {"coeur": config.GEL_COEUR_S, "cerveau": config.GEL_CERVEAU_S, "taches": config.GEL_TACHES_S}


@dataclass
class _Boucle:
    seuil: float
    vu: float = 0.0
    fil: int = 0
    baux: list[float] = field(default_factory=list)  # délais des attentes en cours (piles)
    gelee: bool = False
    gel_depuis: float = 0.0  # dernier battement avant le gel


@dataclass
class Signal:
    nom: str
    etat: str          # « gel » ou « repart »
    age: float         # secondes sans battement (au gel), ou durée du gel (au retour)
    pile: str = ""


class Chien:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._boucles: dict[str, _Boucle] = {}
        self._fil: threading.Thread | None = None
        self._stop = threading.Event()
        self._faulthandler = None

    # --- côté boucles ---

    def battre(self, nom: str) -> None:
        now = self._clock()
        with self._lock:
            b = self._boucles.get(nom)
            if b is None:
                b = self._boucles[nom] = _Boucle(seuils().get(nom, config.GEL_COEUR_S))
            b.vu, b.fil = now, threading.get_ident()

    @contextmanager
    def attente(self, nom: str, max_s: float) -> Iterator[None]:
        """Attente longue et normale (conversation) : délai `max_s` depuis le dernier battement."""
        self.battre(nom)
        with self._lock:
            self._boucles[nom].baux.append(float(max_s))
        try:
            yield
        finally:
            with self._lock:
                b = self._boucles.get(nom)
                if b is not None and b.baux:
                    b.baux.pop()
            self.battre(nom)

    # --- côté chien ---

    def verifier(self) -> list[Signal]:
        """Rend les changements depuis le dernier passage (gel, retour)."""
        now = self._clock()
        out: list[Signal] = []
        frames = None
        with self._lock:
            for nom, b in self._boucles.items():
                age = now - b.vu
                delai = max([b.seuil, *b.baux])
                if not b.gelee and age > delai:
                    b.gelee, b.gel_depuis = True, b.vu
                    if frames is None:
                        frames = sys._current_frames()
                    frame = frames.get(b.fil)
                    pile = "".join(traceback.format_stack(frame)[-25:]) if frame is not None else ""
                    out.append(Signal(nom, "gel", age, pile))
                elif b.gelee and age <= delai:
                    # Elle a rebattu : durée du gel = écart entre le battement d'avant et celui-ci.
                    b.gelee = False
                    out.append(Signal(nom, "repart", b.vu - b.gel_depuis))
        return out

    def demarrer(self, on_signal: Callable[[Signal], None]) -> None:
        if not config.GEL_ON or (self._fil is not None and self._fil.is_alive()):
            return
        self._stop.clear()
        self._armer_filet()
        self._fil = threading.Thread(target=self._boucle, args=(on_signal,), name="chien-de-garde", daemon=True)
        self._fil.start()
        log.info("chien de garde des boucles en marche (alerte seule)")

    def arreter(self) -> None:
        self._stop.set()
        if self._faulthandler is not None:
            try:
                faulthandler.cancel_dump_traceback_later()
            except Exception:  # noqa: BLE001
                pass

    def _armer_filet(self) -> None:
        if config.GEL_GIL_S <= 0:
            return
        try:
            config.GEL_LOG.parent.mkdir(parents=True, exist_ok=True)
            self._faulthandler = open(config.GEL_LOG, "a", encoding="utf-8")  # noqa: SIM115 — gardé ouvert
        except OSError as exc:
            log.warning("journal des gels indisponible : %s", exc)
            self._faulthandler = None

    def _rearmer(self) -> None:
        if self._faulthandler is None:
            return
        try:
            # Chaque passage repousse l'échéance : elle ne tombe que si ce fil ne tourne plus.
            faulthandler.dump_traceback_later(config.GEL_GIL_S, repeat=False, file=self._faulthandler)
        except Exception as exc:  # noqa: BLE001
            log.warning("filet des gels coupé : %s", exc)
            self._faulthandler = None

    def _boucle(self, on_signal: Callable[[Signal], None]) -> None:
        while not self._stop.wait(config.GEL_PERIODE_S):
            self._rearmer()
            try:
                signaux = self.verifier()
            except Exception:  # noqa: BLE001
                log.exception("chien de garde")
                continue
            for s in signaux:
                try:
                    on_signal(s)
                except Exception:  # noqa: BLE001
                    log.exception("alerte de gel")


def texte(s: Signal) -> str:
    """Texte du HUD (notice)."""
    nom = NOMS.get(s.nom, s.nom)
    if s.etat == "gel":
        return (f"Astrel semble figé : {nom} ne répond plus depuis {round(s.age)} s. Rien n'est arrêté ; "
                f"les détails sont dans le journal.")
    return f"Astrel repart : {nom} répond de nouveau, après {round(s.age)} s."


chien = Chien()
