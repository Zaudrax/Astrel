"""Questions à choix cliquables dans le HUD (15/10, idée 9 validée par l'utilisateur).

Claude pose une question avec 2 à 4 réponses (la première = sa recommandation)
par l'outil MCP `ask_choice`. L'outil rend la main AUSSITÔT : le canal de
contrôle ne traite qu'une commande à la fois, un outil qui attendrait le clic
gèlerait la bulle et tous les autres outils. Le HUD affiche des boutons (et
« Autre » pour taper) ; le clic revient par la commande `choice_answer`, et le
cœur envoie la réponse au cerveau comme un message de l'utilisateur.

Ici : l'état et la validation, sans HUD ni cerveau (testables seuls). Rien
n'est dit : l'utilisateur lit la question. Une seule question en attente par canal
(une nouvelle remplace l'ancienne, qui passe « annulée »). Une question en
attente est épinglée dans l'historique du bus : un HUD qui se reconnecte la
revoit même après 300 appels d'outils. En mémoire seulement : une relance de
Astrel oublie les questions.

Événements (contrat des lots du 15/10) : `choice` {id, question, options:
[{label, recommended}], allow_other, channel, answered} et `choice_answered`
{id, value} ; extension compatible : `cancelled: true` pour une question
remplacée (value vide). Plan P21 (21/10) : `choice_answered` porte aussi `libre`
(vrai si la réponse n'est aucun des libellés : texte tapé dans « Autre »), et
`ask` rend `remplace: <id>` quand la nouvelle question en chasse une autre.
"""
from __future__ import annotations

import re
import threading
import uuid
from collections import OrderedDict
from typing import Any

from .events import bus

MIN_OPTIONS = 2
MAX_OPTIONS = 4
QUESTION_MAX = 1000
OPTION_MAX = 120
VALUE_MAX = 500
KEEP = 50  # questions retenues (réponses comprises), pour ignorer un second clic

_CONTROLS = re.compile(r"[\x00-\x09\x0b-\x1f\x7f‪-‮⁦-⁩]")  # \n permis


def _clean(text: Any, limit: int, one_line: bool) -> str:
    if not isinstance(text, str):
        return ""
    text = _CONTROLS.sub(" ", text)
    if one_line:
        text = " ".join(text.split())
    else:
        text = "\n".join(" ".join(line.split()) for line in text.strip().splitlines()).strip()
    return text[:limit].strip()


def clean_options(options: Any) -> list[str]:
    """Libellés d'une ligne, non vides, sans doublon (casse ignorée), dans l'ordre."""
    if not isinstance(options, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for opt in options:
        label = _clean(opt, OPTION_MAX, one_line=True)
        if label and label.casefold() not in seen:
            seen.add(label.casefold())
            out.append(label)
    return out


def is_free(value: str, options: list[str]) -> bool:
    """Réponse hors des libellés proposés (casse et blancs ignorés) : texte libre de « Autre »."""
    key = " ".join(value.split()).casefold()
    return key not in {o.casefold() for o in options}


class Choices:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def ask(self, question: Any, options: Any, channel: str = "", kind: str = "",
            meta: dict | None = None, images: list | None = None) -> dict[str, Any]:
        """Pose une question. Rend {"ok": True, "id": …} ou {"ok": False, "error": …} ;
        `remplace` : id de la question encore en attente qu'elle remplace (plan P21).
        `kind` (19/10, plan P8) : vide = question de Claude ; « offre » = carte du cœur (offre de
        modèle), traitée par le cœur sans tour de Claude. Une question ne remplace que celles
        de MÊME genre dans le canal. `meta` : gardé pour le cœur, jamais publié.
        `images` (22/10, plan P26) : descripteurs de `medias.publish` alignés sur `options`
        (None = pas d'image) ; publiés dans l'option (`image`), galerie à choisir du HUD."""
        q = _clean(question, QUESTION_MAX, one_line=False)
        if not q:
            return {"ok": False, "error": "question vide"}
        opts = clean_options(options)
        if not MIN_OPTIONS <= len(opts) <= MAX_OPTIONS:
            return {"ok": False, "error": f"il faut de {MIN_OPTIONS} à {MAX_OPTIONS} réponses différentes "
                                          f"(reçu : {len(opts)})"}
        pics: dict[str, dict] = {}  # libellé gardé (casse ignorée) → image de SA réponse d'origine
        if isinstance(images, (list, tuple)) and isinstance(options, (list, tuple)):
            for opt, pic in zip(options, images):
                label = _clean(opt, OPTION_MAX, one_line=True).casefold()
                if label and label not in pics and isinstance(pic, dict):
                    pics[label] = pic
        cid = uuid.uuid4().hex[:8]
        item = {"id": cid, "question": q, "options": opts, "channel": channel, "status": "pending", "value": "",
                "kind": str(kind or ""), "meta": dict(meta or {})}
        with self._lock:
            replaced = [c for c in self._items.values() if c["status"] == "pending" and c["channel"] == channel
                        and c.get("kind", "") == item["kind"]]
            for old in replaced:
                old["status"] = "cancelled"
            self._items[cid] = item
            evicted = []
            while len(self._items) > KEEP:
                evicted.append(self._items.popitem(last=False)[0])
        for old_id in evicted:
            bus.unpin(f"choice:{old_id}")
        for old in replaced:
            self._closed(old["id"], "", cancelled=True)
        ev = bus.publish("choice", id=cid, question=q,
                         options=[{"label": o, "recommended": i == 0,
                                   **({"image": pics[o.casefold()]} if o.casefold() in pics else {})}
                                  for i, o in enumerate(opts)],
                         allow_other=True, channel=channel, answered=False,
                         **({"choice_kind": item["kind"]} if item["kind"] else {}))
        bus.pin(f"choice:{cid}", ev)
        out: dict[str, Any] = {"ok": True, "id": cid}
        if replaced:
            out["remplace"] = replaced[-1]["id"]
        return out

    def answer(self, cid: Any, value: Any) -> dict[str, Any]:
        """Réponse de l'utilisateur (un libellé ou un texte libre). Id inconnu, déjà
        répondu ou annulé : ignoré (un second clic, un HUD resté ouvert)."""
        cid = str(cid or "").strip() if isinstance(cid, str) else ""
        text = _clean(value, VALUE_MAX, one_line=False)
        if not text:
            return {"ok": False, "error": "réponse vide"}
        with self._lock:
            item = self._items.get(cid)
            if item is None:
                return {"ok": False, "error": "question inconnue"}
            if item["status"] != "pending":
                return {"ok": False, "error": "question déjà close", "status": item["status"]}
            item["status"], item["value"] = "answered", text
            libre = is_free(text, item["options"])
            result = {"ok": True, "id": cid, "value": text, "libre": libre, "question": item["question"],
                      "channel": item["channel"], "kind": item.get("kind", ""), "meta": dict(item.get("meta") or {})}
        self._closed(cid, text, libre=libre)
        return result

    def _closed(self, cid: str, value: str, cancelled: bool = False, libre: bool = False) -> None:
        extra: dict[str, Any] = {"cancelled": True} if cancelled else {"libre": libre}
        bus.unpin(f"choice:{cid}")
        bus.update_history(lambda ev: ev.get("type") == "choice" and ev.get("id") == cid,
                           answered=True, value=value, **extra)
        bus.publish("choice_answered", id=cid, value=value, **extra)

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(c) for c in self._items.values() if c["status"] == "pending"]

    def get(self, cid: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(cid)
            return dict(item) if item else None


choices = Choices()
