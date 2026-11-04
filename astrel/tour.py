"""État du tour en cours (04/11/2026, carte T58 pas 2a) : les 17 attributs du tour de Claude, rangés dans UN
objet (`Astrel.tour`) au lieu d'être éparpillés dans `Astrel`.

Les anciens noms (`self._turn_active`, `self._hushed`, `self._splitter`…) restent : ce sont des descripteurs
(`DuTour`) qui lisent et écrivent dans `self.tour`. Ni les mixins de `astrel/coeur/`, ni la relève, ni les tests
ne changent. Le Tour est créé au premier accès : les tests montent `Astrel` par `object.__new__` (pas de
`__init__`). `_last_turn` et `_turn_lock` restent hors du Tour : ils valent ENTRE deux tours."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .voice import SentenceSplitter


def _evenement_leve() -> threading.Event:
    e = threading.Event()
    e.set()  # aucun tour au départ : « au repos » est vrai
    return e


@dataclass
class Tour:
    active: bool = False          # un tour de Claude est en cours
    idle: threading.Event = field(default_factory=_evenement_leve)
    source: str = ""              # `astrel/sources.py` : voix, clavier, fond, réflexe…
    hushed: bool = False          # « chut » : le reste du tour n'est plus dit, le travail continue
    last_event: float = 0.0       # pour repérer un tour qui ne répond plus
    splitter: SentenceSplitter = field(default_factory=SentenceSplitter)
    spoken_any: bool = False
    reply: list[str] = field(default_factory=list)
    silent_hold: str = ""         # début de texte retenu tant qu'il peut devenir « [SILENT] » (`silence.py`)
    silenced: bool = False
    auto_joined: bool = False     # un message automatique a rejoint un tour de l'utilisateur
    report_chunk: bool = False    # le morceau en cours vient d'un tour de fond (compte rendu)
    report_spoken: bool = False
    held_group: Any = None        # message non demandé mis en attente (`floor.Held`)
    sent: list | None = None      # (prompt, images) envoyés pendant le tour (`reprise.py`)
    acted: bool = False           # un outil du cerveau principal a démarré
    retry: int = 0                # numéro du rejeu en cours (0 : premier envoi)

    def nouveau(self, source: str) -> None:
        """Remise à zéro au début d'un tour neuf (ce que faisait `_begin_turn`, champ pour champ) ;
        `active`, `idle` et `last_event` sont posés ensuite par `_begin_turn`."""
        self.splitter = SentenceSplitter()
        self.spoken_any = False
        self.reply = []
        self.silent_hold, self.silenced, self.auto_joined = "", False, False
        self.source = source
        self.hushed = False
        self.report_chunk = self.report_spoken = False
        self.held_group = None
        self.sent, self.acted, self.retry = [], False, 0


def tour_de(obj: Any) -> Tour:
    t = obj.__dict__.get("tour")
    if t is None:
        t = obj.__dict__.setdefault("tour", Tour())
    return t


class DuTour:
    """Descripteur de données : `self._turn_active` ↔ `self.tour.active`."""

    def __init__(self, champ: str) -> None:
        self.champ = champ

    def __get__(self, obj: Any, typ: Any = None) -> Any:
        return self if obj is None else getattr(tour_de(obj), self.champ)

    def __set__(self, obj: Any, valeur: Any) -> None:
        setattr(tour_de(obj), self.champ, valeur)


# Ancien nom dans `Astrel` → champ du Tour.
NOMS = {
    "_turn_active": "active", "_turn_idle": "idle", "_turn_source": "source", "_hushed": "hushed",
    "_last_event": "last_event", "_splitter": "splitter", "_spoken_any": "spoken_any", "_reply": "reply",
    "_silent_hold": "silent_hold", "_silenced": "silenced", "_auto_joined": "auto_joined",
    "_report_chunk": "report_chunk", "_report_spoken": "report_spoken", "_held_group": "held_group",
    "_turn_sent": "sent", "_turn_acted": "acted", "_turn_retry": "retry",
}
