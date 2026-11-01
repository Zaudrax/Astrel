"""Comptes rendus à la demande (30/09, demande de l'utilisateur).

Quand une tâche de fond finit, le CLI relance seul un tour et Claude en rend
compte. Avant, ce compte rendu était dit aussitôt : il coupait l'utilisateur en
pleine explication, et plusieurs agents qui finissaient ensemble parlaient
l'un après l'autre sans qu'il ait rien demandé.

En mode « demande » (par défaut), le compte rendu arrive EN SILENCE : il
s'affiche dans le canal du projet, avec un bouton 🔊 à côté du message.
L'utilisateur clique quand il veut l'entendre : le texte déjà affiché est dit tel
quel par l'orateur, sans repasser par Claude (rien de consommé sur
l'abonnement). En mode « direct », le compte rendu est dit aussitôt, comme avant.

Deux sources :
- tout texte d'un tour lancé par le CLI (fin de tâche de fond) : `core` le garde ;
- l'outil MCP `post_report` : Claude le choisit explicitement, dans n'importe
  quel tour (tâche finie pendant que l'utilisateur lui parlait, par exemple).

Le texte des comptes rendus est gardé EN MÉMOIRE (les derniers `KEEP`). Après
une relance d'Astrel, un HUD resté ouvert garde ses messages : son bouton
renvoie alors le texte affiché, que le cœur lit à la place (un HUD authentifié
peut déjà faire parler Astrel, par « announce »).

Comptes rendus TYPÉS (15/10, idée 3 validée par l'utilisateur) : `post_report` porte
un `kind` (decision : l'utilisateur doit trancher, resultat, blocage, info par
défaut) et la liste des fichiers produits ; le HUD en fait un badge et une liste
sous le texte (champs `report_kind`, `report_files`, contrat des lots du 15/10).
"""
from __future__ import annotations

import logging
import os
import re
import threading
from collections import OrderedDict
from typing import Any

from . import config
from .events import bus

log = logging.getLogger("astrel.reports")

ON_DEMAND = "demande"
DIRECT = "direct"
KEEP = 100

# Types de compte rendu (contrat des lots du 15/10) ; inconnu = info.
KINDS = ("decision", "resultat", "blocage", "info")
INFO = "info"
_KIND_ALIASES = {"décision": "decision", "résultat": "resultat", "result": "resultat", "blocked": "blocage",
                 "bloque": "blocage", "bloqué": "blocage"}
MAX_FILES = 10
MAX_PATH = 300
_CONTROLS = re.compile(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]")

_ALIASES = {
    ON_DEMAND: ON_DEMAND, "a-la-demande": ON_DEMAND, "à-la-demande": ON_DEMAND, "clic": ON_DEMAND,
    "ondemand": ON_DEMAND, "on-demand": ON_DEMAND, "silence": ON_DEMAND, "on": ON_DEMAND,
    DIRECT: DIRECT, "auto": DIRECT, "aussitot": DIRECT, "aussitôt": DIRECT, "immediat": DIRECT,
    "immédiat": DIRECT, "voix": DIRECT, "off": DIRECT,
}


def parse_mode(word: str) -> str | None:
    """« demande », « direct » et leurs variantes ; None si inconnu."""
    return _ALIASES.get("-".join(str(word or "").strip().lower().split()))


def parse_kind(word: Any) -> str:
    """decision, resultat, blocage ou info (valeur inconnue ou vide = info)."""
    w = str(word or "").strip().lower()
    w = _KIND_ALIASES.get(w, w)
    return w if w in KINDS else INFO


def clean_files(files: Any) -> list[str]:
    """Chemins de fichiers produits : texte d'une ligne, sans doublon, 10 au plus."""
    if isinstance(files, str):
        files = [files]
    if not isinstance(files, (list, tuple)):
        return []
    out: list[str] = []
    for f in files:
        if not isinstance(f, str):
            continue
        path = _CONTROLS.sub("", f).strip()[:MAX_PATH]
        if path and path not in out:
            out.append(path)
        if len(out) >= MAX_FILES:
            break
    return out


def _saved_mode() -> str:
    env = parse_mode(os.environ.get("ASTREL_REPORTS", ""))
    if env:
        return env
    try:
        return parse_mode(config.REPORTS_FILE.read_text(encoding="utf-8")) or ON_DEMAND
    except OSError:
        return ON_DEMAND


class Reports:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode: str | None = None  # lu au premier usage (les tests changent le fichier)
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()

    # --- le réglage ---

    @property
    def mode(self) -> str:
        with self._lock:
            if self._mode is None:
                self._mode = _saved_mode()
            return self._mode

    @property
    def on_demand(self) -> bool:
        return self.mode == ON_DEMAND

    def set_mode(self, word: str) -> dict[str, Any]:
        mode = DIRECT if word == "toggle" and self.on_demand else ON_DEMAND if word == "toggle" else parse_mode(word)
        if mode is None:
            return {"ok": False, "error": f"réglage inconnu : {word} (demande, direct ou toggle)"}
        with self._lock:
            self._mode = mode
        try:
            config.REPORTS_FILE.write_text(mode + "\n", encoding="utf-8")
        except OSError:
            log.warning("réglage des comptes rendus non retenu : %s", config.REPORTS_FILE)
        log.info("comptes rendus : %s", mode)
        self.publish()
        return {"ok": True, "mode": mode}

    def publish(self) -> None:
        bus.publish("reports", mode=self.mode)

    def describe(self) -> dict[str, Any]:
        with self._lock:
            waiting = sum(1 for r in self._items.values() if not r["read"])
        return {"ok": True, "mode": self.mode, "en_attente": waiting}

    # --- les comptes rendus ---

    def add(self, mid: str, text: str, channel: str, pending: bool, kind: str = INFO,
            files: list[str] | None = None) -> None:
        """Retient un compte rendu affiché (pour le bouton 🔊)."""
        if not mid or not text.strip():
            return
        with self._lock:
            self._items[mid] = {"text": text, "channel": channel, "read": not pending,
                                "kind": parse_kind(kind), "files": list(files or [])}
            while len(self._items) > KEEP:
                self._items.popitem(last=False)

    def text(self, mid: str) -> str:
        with self._lock:
            item = self._items.get(mid)
            return item["text"] if item else ""

    def get(self, mid: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(mid)
            return dict(item) if item else None

    def mark_read(self, mid: str) -> None:
        """Le bouton passe de « Lire » à « Relire » dans tous les HUD, y compris
        dans l'historique rejoué à un HUD qui se reconnecte."""
        with self._lock:
            item = self._items.get(mid)
            if item is None or item["read"]:
                return
            item["read"] = True
        bus.update_history(lambda ev: ev.get("mid") == mid, pending_read=False)
        bus.publish("report_read", mid=mid)


reports = Reports()
