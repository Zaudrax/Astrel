"""Bus d'événements interne : tout ce qui se passe dans Astrel y est publié.

Le cœur publie (état, niveau sonore, transcriptions, appels d'outils,
confirmations, quota…). Les abonnés — le serveur du HUD, le fichier d'état de la
bulle, le journal — reçoivent chaque événement dans le thread de l'éditeur :
un abonné lent doit donc se contenter de mettre l'événement en file.

Un client qui se connecte en cours de route reçoit un INSTANTANÉ : le dernier
état connu de chaque sujet « persistant » (état, graphe, capteurs, quota…) et
l'historique récent du journal. Sans lui, un HUD ouvert après le démarrage
afficherait un graphe vide jusqu'au prochain redémarrage du cerveau.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable

log = logging.getLogger("astrel.events")

Event = dict[str, Any]
Subscriber = Callable[[Event], None]

# Sujets dont seule la DERNIÈRE valeur compte : rejoués tels quels à la connexion.
STICKY = {"state", "graph", "sensors", "quota", "session", "voice", "model", "mic", "confirm", "autonomy", "tasks",
          "channels", "reports", "context", "estop",
          # 14/10 : mode d'usage, fenêtre de suite, mission, autorisations 30 min, texte dicté
          "usage", "followup", "mission", "grants", "dictation",
          # 15/10 : mode « Autonome TOTAL » (en mémoire seulement), page « Réglages »
          "total", "settings",
          # 15/10 : mode plan, liste des plans (`plans.py`) ; le détail (`plan`) n'est pas rejoué
          "plans",
          # 17/10 (plan P1) : pont WhatsApp (connecté ou non, mode « absent »)
          "whatsapp",
          # 18/10 (plan P5) : appairage en cours (QR, code) ; en mémoire SEULEMENT, jamais
          # dans HISTORY ni le journal, effacé à la fin (`whatsapp.Linker._finish`)
          "whatsapp_link",
          # 18/10 (plan P7) : levée du garde-fou {on, since, changed} (en mémoire seulement)
          "levee",
          # 21/10 (plan P20) : pression mémoire du PC (RAM, swap, état ok/attention/blocage)
          "memory",
          # 23/10 (plan P28) : effort de réflexion du cerveau (sélecteur à côté du modèle)
          "effort",
          # 26/10 (plan P33 lot 3) : volet « Courrier » des agents (`courrier.etat`), Avancé seulement
          "courrier",
          # 28/10 (plan P40 lot A4) : comptes, moteurs, modèles proposés et réglages des images IA
          # (`images_ia.Images.status` sans `ok`) pour Réglages › Images et le panneau du HUD (A5)
          "ia_etat",
          # 28/10 (plan P41 lot B4) : crédits par compte, choix modèle+durée, file des vidéos IA
          # (`videos_ia.Videos.status` sans `ok`) pour l'onglet Vidéo du panneau et les Réglages
          "video_etat"}
# Sujets conservés en historique (journal de la conversation, activité).
# « report » : compte rendu posé par l'outil MCP `post_report` (voir `reports`).
# « choice » / « choice_answered » : question à choix du HUD et sa réponse (`choices`, 15/10).
# « image_job » : carte d'une image IA en cours ou finie (21/10, plan P14, `images_ia.py`) ; le HUD
# la retrouve par `job` et ne garde que son dernier état.
# « media » : carte de médias posée par l'outil MCP `show_media` (22/10, plan P26, `medias.py`).
# « video_job » : carte d'une vidéo IA, comme « image_job » (28/10, plan P41 B3, `videos_ia.py`).
HISTORY = {"transcript", "reply", "report", "tool", "tool_result", "log", "notice", "choice", "choice_answered",
           "image_job", "video_job", "media"}
HISTORY_MAX = 300


class Bus:
    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()
        self._sticky: dict[str, Event] = {}
        self._history: deque[Event] = deque(maxlen=HISTORY_MAX)
        self._pinned: dict[str, Event] = {}
        self._command_handler: Callable[[dict], Any] | None = None

    # --- publication ---

    def publish(self, kind: str, **data: Any) -> Event:
        ev: Event = {"type": kind, "ts": time.time(), **data}
        with self._lock:
            if kind in STICKY:
                self._sticky[kind] = ev
            if kind in HISTORY:
                self._history.append(ev)
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(ev)
            except Exception:  # un abonné en panne ne doit pas casser le cœur
                log.exception("abonné en échec sur %s", kind)
        return ev

    def clear_sticky(self, kind: str) -> None:
        with self._lock:
            self._sticky.pop(kind, None)

    def update_history(self, match: Callable[[Event], bool], **changes: Any) -> int:
        """Corrige des événements de l'historique (un message déplacé dans un
        autre canal du HUD) : un HUD qui se reconnecte les revoit à leur place.
        Les événements sont remplacés, pas modifiés : une copie déjà partie vers
        un abonné reste telle qu'elle était."""
        count = 0
        with self._lock:
            for i, ev in enumerate(self._history):
                if match(ev):
                    self._history[i] = {**ev, **changes}
                    count += 1
        return count

    def pin(self, key: str, ev: Event) -> None:
        """Garde un événement d'historique au-delà de `HISTORY_MAX` (question à
        choix encore sans réponse) : en plein travail, 300 appels d'outils
        chassent vite un événement du journal, et un HUD qui se reconnecte ne
        verrait plus la question. Rejoué en tête de l'historique s'il en est sorti."""
        with self._lock:
            self._pinned[key] = ev

    def unpin(self, key: str) -> None:
        with self._lock:
            self._pinned.pop(key, None)

    # --- abonnement ---

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)

        return unsubscribe

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            history = list(self._history)
            kept = {(ev.get("type"), ev.get("id")) for ev in history}
            # Sortis du journal : plus anciens que tout ce qui y reste, donc en tête.
            lost = sorted((ev for ev in self._pinned.values() if (ev.get("type"), ev.get("id")) not in kept),
                          key=lambda ev: ev.get("ts", 0))
            return {"sticky": list(self._sticky.values()), "history": lost + history}

    def last(self, kind: str) -> Event | None:
        with self._lock:
            return self._sticky.get(kind)

    # --- commandes venues des interfaces (HUD, bulle, raccourcis) ---

    def set_command_handler(self, handler: Callable[[dict], Any]) -> None:
        self._command_handler = handler

    def command(self, cmd: dict) -> Any:
        if self._command_handler is None:
            return {"ok": False, "error": "Astrel non prêt"}
        return self._command_handler(cmd)


bus = Bus()
