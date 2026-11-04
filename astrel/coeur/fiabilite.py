"""Fiabilité (01/11, idée « gardée pour plus tard » n° 5) : messages de l'utilisateur retrouvés après un
arrêt (`attente.py`), chien de garde des boucles figées (`gel.py`), tâche de fond muette.
ALERTES seules : rien n'est arrêté, rien n'est rejoué tout seul.

Mixin de `Astrel` (core.py), comme les autres de `astrel/coeur/`.
"""
from __future__ import annotations

import threading
import time

from .. import config
from .. import gel
from ..attente import attente
from ..attente import notice as attente_notice
from ..attente import phrase as attente_phrase
from ..channels import channels
from ..events import bus
from ..core import _UTILISATEUR, log  # aides du cœur (le paquet charge core d'abord)


class FiabiliteCoeur:
    """Messages en attente, boucles figées, tâches muettes."""

    # --- démarrage ---

    def _fiabilite_boot(self) -> None:
        """Après le démarrage du HUD : chien de garde en marche, messages d'avant l'arrêt proposés."""
        gel.chien.demarrer(self._gel_signal)
        try:
            self._attente_boot()
        except Exception:
            log.exception("messages en attente illisibles")

    def _attente_boot(self) -> None:
        items = attente.a_proposer()
        if not items:
            return
        log.warning("%d message(s) de l'utilisateur d'avant l'arrêt non traité(s) : proposé(s), pas rejoué(s)",
                    len(items))
        for it in items:
            # Comme un message « non envoyé » (16/10) : le HUD montre « Renvoyer », qui repart par la
            # commande `say` avec `resend_of` (un nouveau message de l'utilisateur, confirmations comprises).
            bus.publish("transcript", who="user", text=it["text"], source=it.get("source") or "clavier",
                        channel=it.get("channel") or channels.default, mid=it["mid"], unsent=True,
                        recovered=True)
        bus.publish("notice", level="warning", text=attente_notice(items))
        if any(it.get("source") == "voix" for it in items):
            self._announce(attente_phrase(items))

    # --- messages de l'utilisateur (appelés par core.py) ---

    def _attente_noter(self, mid: str, text: str, source: str, channel: str, pieces: int = 0) -> None:
        try:
            attente.noter(mid, text, source, channel, pieces)
        except Exception:
            log.exception("message non noté en attente")

    def _attente_fin_tour(self, source: str, garder: bool = False) -> None:
        """`garder` (04/11, T46) : le tour a fini sur un moteur tombé, déconnecté ou une limite : ce qui était
        noté pendant ce démarrage n'a pas abouti. Montré « non envoyé » avec « Renvoyer », gardé sur disque
        (et reproposé après une relance d'Astrel) ; JAMAIS rejoué."""
        if getattr(self, "_retry_job", None) is not None:
            return  # rejeu programmé (panne passagère) : le message n'est pas encore traité
        try:
            if not garder:
                attente.traites(utilisateur=source in _UTILISATEUR)
                return
            items = attente.proposer_courants()
        except Exception:
            log.exception("messages en attente")
            return
        if not items:
            return
        log.warning("%d demande(s) de l'utilisateur interrompue(s) : proposée(s), pas rejouée(s)", len(items))
        for it in items:
            mid = it["mid"]
            bus.update_history(lambda ev, m=mid: ev.get("type") == "transcript" and ev.get("mid") == m, unsent=True)
            bus.publish("unsent", mid=mid, error="demande interrompue")
        parts = ["Ta demande n'a pas abouti : « Renvoyer » dans le fil pour la relancer."]
        if any(it.get("acted") for it in items):
            parts.append("Des actions avaient peut-être déjà commencé : vérifie avant de renvoyer.")
        bus.publish("notice", level="warning", text="\n".join(parts))

    # --- boucles figées ---

    def _gel_signal(self, s: "gel.Signal") -> None:
        """Fil du chien de garde : journal d'abord (la pile du fil figé), HUD par un fil à part
        (si le gel tient un verrou de l'envoi, le chien ne doit pas se figer avec lui)."""
        text = gel.texte(s)
        if s.etat == "gel":
            log.error("GEL : %s (boucle « %s », %.0f s sans battement). Rien n'est arrêté. Pile du fil :\n%s",
                      text, s.nom, s.age, s.pile or "(fil introuvable)")
        else:
            log.warning("gel fini : boucle « %s » repartie après %.0f s", s.nom, s.age)
        level = "warning" if s.etat == "gel" else "info"
        threading.Thread(target=bus.publish, args=("notice",), kwargs={"level": level, "text": text},
                         name="gel-notice", daemon=True).start()

    # --- tâches de fond muettes ---

    def _muettes_tick(self, now: float | None = None) -> None:
        """Chaque seconde (`_mission_loop`) : un SOUS-AGENT sans aucun évènement (outil, message,
        progression) depuis `TACHE_MUETTE_S` (`TACHE_MUETTE_OUTIL_S` s'il a un outil en cours) est
        signalé UNE fois au journal et au HUD ; jamais arrêté. Pas pendant une carte de confirmation
        (il attend peut-être l'utilisateur) ni sous arrêt d'urgence. Une commande Bash de fond n'est pas
        suivie : elle ne donne aucune nouvelle avant sa fin (la durée a déjà son alerte)."""
        tasks_lock = getattr(self, "_tasks_lock", None)
        if tasks_lock is None or config.ESTOP_FILE.exists():
            return
        now = time.time() if now is None else now
        vus = getattr(getattr(self, "brain", None), "agent_vu", None) or {}
        attend = getattr(self, "_pending_confirm", None) is not None
        muettes, reprises = [], []
        with tasks_lock:
            for task in self._tasks.values():
                if task.get("kind") != "agent":
                    continue
                tuid = str(task.get("tool_use_id") or "")
                dernier = max(float(task.get("started") or 0.0), float(task.get("vu") or 0.0),
                              float(vus.get(tuid) or 0.0))
                if task.get("muette"):
                    if dernier > float(task["muette"]):
                        task.pop("muette", None)
                        reprises.append(dict(task))
                    continue
                if attend:
                    continue
                en_outil = bool((self._agent_log.get(tuid) or {}).get("pending"))
                delai = config.TACHE_MUETTE_OUTIL_S if en_outil else config.TACHE_MUETTE_S
                if delai > 0 and now - dernier > delai:
                    task["muette"] = dernier
                    muettes.append((dict(task), now - dernier, en_outil))
        for task, silence, en_outil in muettes:
            minutes = max(1, round(silence / 60))
            desc = task.get("desc") or "tâche"
            if en_outil:
                text = (f"Agent muet ? « {desc} » : aucune nouvelle depuis {minutes} min, un outil en cours. "
                        "Rien n'est arrêté : bouton Stop du panneau « en cours » si besoin.")
            else:
                text = (f"Agent muet ? « {desc} » : aucune nouvelle depuis {minutes} min. "
                        "Rien n'est arrêté : bouton Stop du panneau « en cours » si besoin.")
            log.warning("%s", text)
            bus.publish("notice", level="warning", channel=channels.attention(task.get("channel", "")), text=text,
                        task_id=task.get("id", ""))
        for task in reprises:
            desc = task.get("desc") or "tâche"
            log.info("agent de nouveau actif : %s", desc)
            bus.publish("notice", level="info", channel=channels.attention(task.get("channel", "")),
                        text=f"L'agent « {desc} » donne de nouveau des nouvelles.", task_id=task.get("id", ""))
