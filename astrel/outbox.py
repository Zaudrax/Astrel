"""File des envois en attente (outbox) et brouillons de messages SORTANTS (09/11, carte T88).

Un message qu'Astrel veut envoyer (WhatsApp aujourd'hui, courriel plus tard) n'est jamais expédié
d'office : il est d'abord DÉPOSÉ ici comme brouillon « en attente », montré à l'utilisateur, qui peut
l'accepter (envoi), le refuser (jeté), l'éditer en ligne (change le texte envoyé), ou le réviser
(consigne libre : le moteur normal regénère le brouillon, aperçu avant envoi). L'ENVOI reste une
action CRITIQUE : seul l'« Accepter » explicite de l'utilisateur (HUD) le déclenche. Claude ne peut que
PROPOSER un brouillon ou en fournir une révision (outil MCP `draft_message`), jamais l'accepter.

Générique par CANAL : chaque canal enregistre son libellé et sa fonction d'envoi
(`register_channel(cid, label, send)`, `send(to, text) -> {"ok": bool, "error"?: str}`). Le premier
canal branché est WhatsApp (chat « message à soi-même ») ; le courriel viendra se brancher ici sans
toucher ce module ni la carte du HUD.

Rien ne quitte la machine depuis ce module : `accept` appelle la fonction d'envoi du canal, qui
reste la SEULE voie de sortie (et le cœur la refuse en mode « rien ne sort » et sous Stop tout).
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable

from .events import bus

log = logging.getLogger("astrel.outbox")

# États d'un brouillon.
PENDING = "pending"      # en attente de la décision de l'utilisateur
REVISING = "revising"    # le moteur regénère le texte (consigne de l'utilisateur) ; aperçu à venir
SENT = "sent"            # accepté puis remis au canal
REFUSED = "refused"      # jeté par l'utilisateur

KEEP = 50                # brouillons gardés en mémoire (les plus anciens tombent)
MAX_TEXT = 20_000        # un message plus long est tronqué (le canal a ses propres limites)
MAX_INSTRUCTION = 2_000  # consigne de révision
_SOCKET_MAX = 10         # réponse du canal de contrôle ≤ 4 096 octets
_SOCKET_TEXT = 120       # texte tronqué sur le canal de contrôle

# Fonction d'envoi d'un canal : (destinataire, texte) -> {"ok": bool, "error"?: str}.
Sender = Callable[[str, str], dict]
# Fonction de régénération : (brouillon, consigne) -> None (lance un tour du moteur normal).
Regen = Callable[[dict, str], None]


class Outbox:
    """File des brouillons de messages sortants, un canal d'envoi par identifiant."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._drafts: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self._channels: dict[str, dict[str, Any]] = {}

    # --- canaux ---

    def register_channel(self, cid: str, label: str, send: Sender) -> None:
        """Branche un canal d'envoi (WhatsApp, plus tard courriel). Rebrancher remplace."""
        with self._lock:
            self._channels[cid] = {"label": label, "send": send}

    def channel_label(self, cid: str) -> str:
        with self._lock:
            ch = self._channels.get(cid)
        return str(ch["label"]) if ch else cid

    def known_channel(self, cid: str) -> bool:
        with self._lock:
            return cid in self._channels

    # --- dépôt et révision (côté Claude, via le cœur) ---

    def stage(self, channel: str, text: str, to: str = "", source: str = "claude") -> dict:
        """Dépose un brouillon « en attente ». Rien n'est envoyé : la carte s'affiche, l'utilisateur
        tranche. Canal inconnu ou message vide : refus clair, rien n'est déposé."""
        channel = str(channel or "").strip()
        body = str(text or "").strip()[:MAX_TEXT]
        if not channel or not self.known_channel(channel):
            return {"ok": False, "error": _unknown_channel(channel)}
        if not body:
            return {"ok": False, "error": "message vide : rien à préparer"}
        did = uuid.uuid4().hex[:10]
        now = time.time()
        with self._lock:
            self._drafts[did] = {
                "id": did, "channel": channel, "label": self.channel_label(channel),
                "to": str(to or "").strip()[:200], "text": body, "original": body,
                "state": PENDING, "source": str(source or "claude"), "instruction": "",
                "revisions": 0, "created": now, "updated": now,
            }
            while len(self._drafts) > KEEP:
                self._drafts.popitem(last=False)
        log.info("brouillon sortant déposé (%s, %s, %d caractères)", did, channel, len(body))
        self.publish()
        return {"ok": True, "id": did}

    def apply_revision(self, draft_id: str, text: str) -> dict:
        """Nouveau texte d'une RÉVISION en cours (fourni par le moteur via `draft_message`
        `replace=`). N'agit que sur un brouillon en cours de révision : il revient « en
        attente » avec le nouveau texte (aperçu avant envoi). Hors révision : refusé, pour que
        Claude ne réécrive pas en douce un brouillon que l'utilisateur regarde déjà."""
        body = str(text or "").strip()[:MAX_TEXT]
        if not body:
            return {"ok": False, "error": "message vide : rien à préparer"}
        with self._lock:
            d = self._drafts.get(str(draft_id or ""))
            if d is None:
                return {"ok": False, "error": "brouillon introuvable"}
            if d["state"] != REVISING:
                return {"ok": False, "error": "aucune révision en attente pour ce brouillon"}
            d.update(text=body, state=PENDING, revisions=d["revisions"] + 1, updated=time.time())
        log.info("brouillon %s révisé par le moteur (%d caractères)", draft_id, len(body))
        self.publish()
        return {"ok": True, "id": d["id"]}

    # --- décisions de l'utilisateur (HUD, refusées à Claude par le cœur) ---

    def edit(self, draft_id: str, text: str) -> dict:
        """Édition EN LIGNE par l'utilisateur : change le texte qui sera envoyé. Le brouillon reste
        en attente ; seul un « Accepter » l'envoie."""
        body = str(text or "").strip()[:MAX_TEXT]
        if not body:
            return {"ok": False, "error": "texte vide : le brouillon n'est pas changé"}
        with self._lock:
            d = self._drafts.get(str(draft_id or ""))
            if d is None:
                return {"ok": False, "error": "brouillon introuvable"}
            if d["state"] not in (PENDING, REVISING):
                return {"ok": False, "error": "ce brouillon n'attend plus d'envoi"}
            d.update(text=body, state=PENDING, updated=time.time())
        self.publish()
        return {"ok": True, "id": d["id"], "text": body}

    def revise(self, draft_id: str, instruction: str, regen: Regen) -> dict:
        """Révision par l'utilisateur : consigne libre (« rends-le plus formel »). Le brouillon passe
        « en révision » et `regen` lance un tour du MOTEUR NORMAL (routage et contrôle des données
        sensibles compris) qui rappellera `apply_revision` avec le texte réécrit. Rien n'est
        envoyé. L'appelant (le cœur) coupe la révision sous Stop tout / « rien ne sort »."""
        consigne = str(instruction or "").strip()[:MAX_INSTRUCTION]
        if not consigne:
            return {"ok": False, "error": "dis comment réviser le message (ex. « rends-le plus court »)"}
        with self._lock:
            d = self._drafts.get(str(draft_id or ""))
            if d is None:
                return {"ok": False, "error": "brouillon introuvable"}
            if d["state"] not in (PENDING, REVISING):
                return {"ok": False, "error": "ce brouillon n'attend plus d'envoi"}
            d.update(state=REVISING, instruction=consigne, updated=time.time())
            snapshot = dict(d)
        self.publish()
        try:
            regen(snapshot, consigne)
        except Exception:
            log.exception("révision du brouillon %s : le moteur n'a pas pu être lancé", draft_id)
            with self._lock:
                cur = self._drafts.get(d["id"])
                if cur is not None and cur["state"] == REVISING:
                    cur.update(state=PENDING, updated=time.time())
            self.publish()
            return {"ok": False, "error": "révision impossible : le moteur n'a pas pu être lancé"}
        return {"ok": True, "id": d["id"]}

    def accept(self, draft_id: str) -> dict:
        """ACCEPTE l'envoi (action critique, l'utilisateur seul) : remet le texte à la fonction d'envoi
        du canal. Succès : le brouillon passe « envoyé ». Échec du canal : le brouillon reste en
        attente, avec la raison."""
        with self._lock:
            d = self._drafts.get(str(draft_id or ""))
            if d is None:
                return {"ok": False, "error": "brouillon introuvable"}
            if d["state"] != PENDING:
                return {"ok": False, "error": "ce brouillon n'attend plus d'envoi"}
            ch = self._channels.get(d["channel"])
            if ch is None:
                return {"ok": False, "error": _unknown_channel(d["channel"])}
            to, text, send = d["to"], d["text"], ch["send"]
        try:
            res = send(to, text)
        except Exception as exc:
            log.exception("envoi du brouillon %s refusé par le canal", draft_id)
            res = {"ok": False, "error": f"envoi impossible : {exc}"}
        if not isinstance(res, dict) or res.get("ok") is not True:
            err = (res or {}).get("error") if isinstance(res, dict) else None
            return {"ok": False, "error": str(err or "envoi impossible")}
        with self._lock:
            cur = self._drafts.get(d["id"])
            if cur is not None:
                cur.update(state=SENT, updated=time.time())
        log.info("brouillon %s accepté et remis au canal %s", draft_id, d["channel"])
        self.publish()
        return {"ok": True, "id": d["id"]}

    def refuse(self, draft_id: str, reason: str = "") -> dict:
        """Jette le brouillon (l'utilisateur seul) : il ne partira pas."""
        with self._lock:
            d = self._drafts.get(str(draft_id or ""))
            if d is None:
                return {"ok": False, "error": "brouillon introuvable"}
            if d["state"] in (SENT, REFUSED):
                return {"ok": False, "error": "ce brouillon n'attend plus d'envoi"}
            d.update(state=REFUSED, reason=str(reason or "").strip()[:500], updated=time.time())
        log.info("brouillon %s refusé par l'utilisateur", draft_id)
        self.publish()
        return {"ok": True, "id": d["id"]}

    # --- lecture ---

    def get(self, draft_id: str) -> dict | None:
        with self._lock:
            d = self._drafts.get(str(draft_id or ""))
            return dict(d) if d else None

    def pending(self) -> list[dict]:
        with self._lock:
            return [dict(d) for d in self._drafts.values() if d["state"] in (PENDING, REVISING)]

    def count_pending(self) -> int:
        with self._lock:
            return sum(1 for d in self._drafts.values() if d["state"] in (PENDING, REVISING))

    def view(self, socket: bool = False) -> list[dict]:
        """Brouillons pour le HUD (en attente puis les récents réglés), du plus récent au plus
        ancien. `socket` : forme réduite pour le canal de contrôle (≤ 4 096 octets)."""
        with self._lock:
            items = list(self._drafts.values())
        items.sort(key=lambda d: (d["state"] in (SENT, REFUSED), -d["updated"]))
        out: list[dict] = []
        for d in items:
            e = {"id": d["id"], "channel": d["channel"], "label": d["label"], "to": d["to"],
                 "text": d["text"], "state": d["state"], "source": d["source"],
                 "revisions": d["revisions"], "updated": d["updated"]}
            if d.get("reason"):
                e["reason"] = d["reason"]
            if socket:
                e = {"id": e["id"], "channel": e["channel"], "state": e["state"],
                     "text": e["text"][:_SOCKET_TEXT]}
            out.append(e)
        return out[:_SOCKET_MAX] if socket else out

    def describe(self) -> dict:
        return {"ok": True, "en_attente": self.count_pending()}

    def publish(self) -> None:
        bus.publish("outbox", en_attente=self.count_pending())


def _unknown_channel(channel: str) -> str:
    return f"canal d'envoi inconnu : {channel}"


outbox = Outbox()
