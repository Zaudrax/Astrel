"""La parole est à l'utilisateur (13/10, demande de l'utilisateur).

Le problème : quand une tâche de fond finit, le CLI relance seul un tour et
Astrel lisait le compte rendu aussitôt (réglage « direct »), sans regarder si
l'utilisateur était en train de parler. Or le micro devient SOURD dès qu'Astrel
parle (sinon il s'entendrait) : tout ce que l'utilisateur disait pendant la lecture
était perdu, sa phrase coupée (« … créer des… Oui. », 13/10 à 15 h 07).

Ici :
- `Floor` sait si l'utilisateur a la parole : une écoute est ouverte, sa phrase est
  en cours de transcription, ou il a parlé il y a moins de
  `config.SPEECH_GRACE_S` (parole vue par le VAD pendant une écoute, ou dans la
  boucle de veille, mot de réveil compris) ;
- `Backlog` garde ce qui ne peut pas être dit tout de suite. Le cœur le vide
  d'un bloc (un seul carillon) quand le silence est installé et que plus rien
  n'est en cours : ce que l'utilisateur a dit entre-temps passe d'abord.

Ce qui n'est PAS retenu ici : les réponses à l'utilisateur lui-même, les questions
de confirmation (il faut y répondre), un compte rendu qu'il demande en cliquant
sur 🔊 Lire.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator

from . import config

log = logging.getLogger("astrel.floor")


class Floor:
    """l'utilisateur a-t-il la parole ? `busy()` dit pourquoi (vide : non)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._listening = 0
        self._transcribing = 0
        self._last_heard = float("-inf")

    def heard(self) -> None:
        """De la parole vient d'être entendue (VAD) : le délai de grâce repart."""
        now = self._clock()
        with self._lock:
            self._last_heard = now

    @contextmanager
    def listening(self) -> Iterator[None]:
        """Une écoute de l'utilisateur est ouverte (il peut commencer à tout instant)."""
        with self._lock:
            self._listening += 1
        try:
            yield
        finally:
            with self._lock:
                self._listening -= 1

    @contextmanager
    def transcribing(self) -> Iterator[None]:
        """Sa phrase est en train de devenir du texte : elle n'est pas encore traitée."""
        with self._lock:
            self._transcribing += 1
        try:
            yield
        finally:
            with self._lock:
                self._transcribing -= 1

    def since_heard(self) -> float:
        with self._lock:
            return self._clock() - self._last_heard

    def busy(self) -> str:
        with self._lock:
            if self._transcribing:
                return "transcription en cours"
            if self._listening:
                return "écoute en cours"
            since = self._clock() - self._last_heard
        if since < config.SPEECH_GRACE_S:
            return f"L'utilisateur a parlé il y a {since:.1f} s"
        return ""

    def speaking(self) -> str:
        """l'utilisateur PARLE (pas seulement : une écoute est ouverte) : sa phrase
        est transcrite, ou de la voix a été entendue il y a moins de
        SPEECH_GRACE_S. Une question de confirmation attend que ce soit vide
        (14/10 : elle lui coupait la parole en prenant le micro)."""
        with self._lock:
            if self._transcribing:
                return "transcription en cours"
            since = self._clock() - self._last_heard
        if since < config.SPEECH_GRACE_S:
            return f"L'utilisateur parle (voix il y a {since:.1f} s)"
        return ""

    def reset(self) -> None:
        with self._lock:
            self._listening = self._transcribing = 0
            self._last_heard = float("-inf")


@dataclass
class Held:
    """Un message mis en attente : les phrases d'un tour de fond, d'un réflexe,
    un rappel ou un compte rendu posé par `post_report`."""
    source: str                       # « fond », « réflexe », « annonce », « rapport »
    sentences: list[str] = field(default_factory=list)
    raw: bool = False                 # dit tel quel (phrase fixe), sans nettoyage
    report: bool = False              # compte rendu : « à lire » dans le HUD tant qu'il n'est pas dit
    mids: list[str] = field(default_factory=list)  # ses messages dans le HUD (bouton 🔊)
    since: float = field(default_factory=time.monotonic)


class Backlog:
    """File des messages non demandés, dans l'ordre d'arrivée."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[Held] = []
        self._ready = threading.Event()

    def __bool__(self) -> bool:
        with self._lock:
            return bool(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def add(self, item: Held) -> Held:
        with self._lock:
            self._items.append(item)
            self._ready.set()
        return item

    def take(self) -> list[Held]:
        """Tout ce qui attend, d'un coup : plusieurs messages sont dits ensemble."""
        with self._lock:
            items, self._items = self._items, []
            self._ready.clear()
        return [it for it in items if it.sentences]

    def clear(self) -> int:
        """🔇 / ■ : l'utilisateur veut le silence. Les comptes rendus restent « à lire »."""
        with self._lock:
            n = len(self._items)
            self._items = []
            self._ready.clear()
        return n

    def discard_mid(self, mid: str) -> None:
        """Lu au clic (🔊) : inutile de le redire après."""
        if not mid:
            return
        with self._lock:
            self._items = [it for it in self._items if mid not in it.mids]
            if not self._items:
                self._ready.clear()

    def wait(self, timeout: float) -> bool:
        return self._ready.wait(timeout)


_NUMBERS = {2: "deux", 3: "trois", 4: "quatre", 5: "cinq", 6: "six", 7: "sept", 8: "huit", 9: "neuf"}
_NUMBERS_EN = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


def intro(n: int, lang: str | None = None) -> str:
    """Annonce d'un groupe de messages (plusieurs tâches finies pendant qu'il parlait). 02/11 : en anglais
    pour une conversation anglaise (`langue.py`)."""
    if lang is None:
        from . import langue

        lang = langue.conversation()
    if lang == "en":
        return f"{_NUMBERS_EN.get(n, str(n)).capitalize()} messages came in while you were talking."
    return f"{_NUMBERS.get(n, str(n)).capitalize()} messages sont arrivés pendant que tu parlais."


floor = Floor()
backlog = Backlog()
