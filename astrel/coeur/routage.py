"""Routage des demandes et barrière des données sensibles (04/11/2026, lot 3 du moteur local,
docs/chantiers/HYBRIDE-QWEN.md).

- Le cœur décide (`routeur.decider`, `sensible.scan`), jamais un modèle, AVANT `brain.send`.
- Une demande sensible destinée à Claude, ou que le local ne sait pas traiter, est RETENUE : rien n'est
  envoyé, rien n'est noté dans `attente.py`, l'historique ou le journal en clair ; une carte (`choices`,
  genre « routage ») s'affiche à l'ÉCRAN. Réponse par le HUD seulement : refusée à Claude, à la bulle, à la
  voix et à WhatsApp, comme une question du garde-fou.
- « Envoyer à Claude » : la demande part telle quelle ; « Rester en local » : le moteur local ; « Annuler » :
  rien ne part, les pièces jointes reviennent à la barre.
- `logs/routage.jsonl` : catégorie, moteur, raisons, carte — JAMAIS le texte.

Mixin de `Astrel` (core.py), comme les autres de `astrel/coeur/`.
"""
from __future__ import annotations

import json
import threading
import time

from .. import config, langue, routeur, sensible
from ..channels import channels
from ..choices import choices
from ..events import bus
from ..settings import reglages
from ..core import log  # aides du cœur (le paquet charge core d'abord)

ROUTAGE_LOG = config.LOGS / "routage.jsonl"
ROUTAGE_LOG_MAX = 2 * 1024 * 1024  # puis une seule rotation (.1)
EXTRAIT = 600  # caractères de la demande montrés dans la carte
NOTICE_AUTO_S = 600.0  # « routé en local » rappelé au plus toutes les 10 min

ENVOYER, RESTER, RESTER_SANS, ANNULER = ("Envoyer à Claude", "Rester en local",
                                         "Rester en local (sans les pièces jointes)", "Annuler")
DIT_SENSIBLE = "Ta demande contient des données sensibles : je te demande à l'écran avant de l'envoyer à Claude."
DIT_CAPACITE = "Le modèle local ne sait pas faire ça : je te propose à l'écran de l'envoyer à Claude."
WHATSAPP_RETENU = ("Ta demande contient des données sensibles : elle attend ton accord sur le PC (HUD) avant de "
                   "partir chez Claude.")
NOTICE_LOCAL = "Répondu par le modèle local ({raison}). « avec Claude : » en tête pour l'envoyer à Claude."
RAISONS_AUTO = {"cybersecurite": "cybersécurité", "sensible": "données sensibles", "reglage": "réglage « Qui répond »",
                "hybride": "mode hybride"}


class RoutageCoeur:
    """Choix du moteur de chaque demande de l'utilisateur, et cartes de la barrière."""

    def _routage_retenus(self) -> dict:
        d = self.__dict__.get("_routage_retenus_d")
        if d is None:
            d = self.__dict__.setdefault("_routage_retenus_d", {})
        return d

    def _routage_decider(self, text: str, channel: str, source: str, joined) -> routeur.Decision:
        ch = channel or channels.active
        try:
            locaux = channels.local_ids()
        except Exception:
            locaux = frozenset()
        try:
            projet = bool(channels.workdir_of(ch))
        except Exception:
            projet = False
        try:
            mode = reglages.value("moteur_mode")
        except Exception:
            mode = "auto"
        # Ce que les pièces apportent au texte envoyé (fichiers texte) est scanné avec lui.
        scanne = text + ("\n" + "\n".join(joined.notes) if joined is not None and joined.notes else "")
        pieces = [str(s.get("name") or "") for s in (joined.shown if joined is not None else [])] or \
            (list(joined.ids) if joined is not None else [])
        etat = routeur.Etat(canaux_locaux=locaux, projet=projet, source=source,
                            scan=lambda _t, p: sensible.scan(scanne, p),
                            lecture_locale=not self._outils_locaux_raison(),  # 04/11 (lot 4)
                            actions_locales=not self._outils_locaux_actions_raison())  # 05/11 (lot 5)
        # Canal Local : celui DONNÉ avec le message (comme au lot 1), pas le canal ouvert par défaut.
        return routeur.decider(text, channel, pieces, {"moteur_mode": mode}, etat)

    @staticmethod
    def _routage_journal(dec: routeur.Decision, source: str) -> None:
        """Une ligne par décision, sans le texte."""
        ligne = {"at": round(time.time(), 1), "source": source, "moteur": dec.moteur, "categorie": dec.categorie,
                 "raisons": list(dec.raisons), "carte": dec.carte, "explicite": dec.explicite}
        try:
            ROUTAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
            if ROUTAGE_LOG.exists() and ROUTAGE_LOG.stat().st_size > ROUTAGE_LOG_MAX:
                ROUTAGE_LOG.replace(ROUTAGE_LOG.with_suffix(".jsonl.1"))
            with ROUTAGE_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("journal du routage non écrit : %s", exc)

    def _routage_notice(self, dec: routeur.Decision, channel: str) -> None:
        """Routage AUTOMATIQUE vers le local : dit une fois de temps en temps, pour qu'il ne surprenne pas."""
        if dec.explicite or dec.categorie == "hors_ligne":
            return
        now = time.monotonic()
        if now - float(self.__dict__.get("_routage_notice_at", -1e9)) < NOTICE_AUTO_S:
            return
        self.__dict__["_routage_notice_at"] = now
        bus.publish("notice", level="info", channel=channel or channels.active,
                    text=NOTICE_LOCAL.format(raison=RAISONS_AUTO.get(dec.categorie, dec.categorie)))

    def _routage(self, text: str, source: str, channel: str, shown: str | None, joined) -> bool:
        """Décide pour une demande de l'utilisateur. True : elle est traitée ici (local, ou retenue derrière une
        carte) ; False : elle part chez Claude par le chemin habituel."""
        dec = self._routage_decider(text, channel, source, joined)
        self._routage_journal(dec, source)
        if dec.carte:
            self._routage_retenir(dec, text, source, channel, shown, joined)
            return True
        if dec.moteur == routeur.LOCAL:
            self._routage_notice(dec, channel)
            self._tour_local(text, dec.texte, source, channel, shown, joined, outils=dec.outils)
            return True
        return False

    def _routage_retenir(self, dec: routeur.Decision, text: str, source: str, channel: str, shown: str | None,
                         joined, avec_rester: bool = True) -> None:
        """La demande attend la réponse de l'utilisateur à l'écran ; rien n'est parti. `avec_rester` : False pour une
        demande déjà traitée en local (lot 6, « Envoyer à Claude ») : Envoyer ou Annuler seulement."""
        ch, _routed = channels.route(text, requested=channel, follow=True)
        extrait = " ".join(text.split())
        if len(extrait) > EXTRAIT:
            extrait = extrait[:EXTRAIT - 1].rstrip() + f"… ({len(text)} caractères)"
        noms = [str(s.get("name") or "") for s in (joined.shown if joined is not None else []) if s.get("name")]
        raisons = ", ".join(dec.raisons)
        if dec.carte == routeur.CARTE_SENSIBLE:
            question = (f"Ta demande contient : {raisons}. L'envoyer à Claude (Anthropic, hors de cette machine) ?"
                        f"\n\n« {extrait} »")
        else:
            question = f"Le modèle local ne sait pas faire ça ({raisons}). L'envoyer à Claude ?\n\n« {extrait} »"
        if noms:
            question += "\nPièces jointes : " + ", ".join(noms)
        rester = RESTER_SANS if noms else RESTER
        options = [rester, ENVOYER] if dec.recommande == routeur.LOCAL else [ENVOYER, rester]
        if source == "whatsapp" or not avec_rester:
            options = [ENVOYER]  # la réponse locale ne repart pas par WhatsApp (lot 1)
        options.append(ANNULER)
        res = choices.ask(question, options, channel=ch, kind="routage",
                          meta={"actions": {ENVOYER: "claude", rester: "local", ANNULER: "annuler"}})
        if not res.get("ok"):
            log.warning("carte de routage impossible (%s) : demande non envoyée", res.get("error"))
            self._routage_rendre(joined)
            bus.publish("notice", level="warning", channel=ch, text="Demande non envoyée : carte impossible.")
            return
        retenus = self._routage_retenus()
        ancien = retenus.pop(res.get("remplace") or "", None)
        if ancien:  # une nouvelle carte du même canal a remplacé l'ancienne : l'ancienne demande ne part pas
            self._routage_rendre(ancien.get("joined"))
        retenus[res["id"]] = {"text": text, "demande": dec.texte, "source": source, "channel": ch, "shown": shown,
                              "joined": joined, "at": time.time()}
        while len(retenus) > 20:
            self._routage_rendre(retenus.pop(next(iter(retenus))).get("joined"))
        log.info("demande retenue (%s : %s), carte %s", dec.carte, raisons, res["id"])
        if source == "voix":
            self.say(langue.parle(DIT_SENSIBLE if dec.carte == routeur.CARTE_SENSIBLE else DIT_CAPACITE))
        elif source == "whatsapp":
            try:
                self.whatsapp.send_text("🔷 " + langue.parle(WHATSAPP_RETENU))
            except Exception:
                log.exception("avis WhatsApp non envoyé")

    def _routage_rendre(self, joined) -> None:
        ids = list(joined.ids) if joined is not None and joined.ids else []
        if ids:
            try:
                self._release_attachments(ids)
            except Exception:
                log.exception("pièces jointes non rendues")

    @staticmethod
    def _routage_refus(cmd: dict, cid: str) -> str:
        """Raison de refuser la réponse à une carte de routage : HUD de l'utilisateur seulement."""
        item = choices.get(cid) or {}
        if item.get("kind") != "routage":
            return ""
        if cmd.get("_from_claude") is True:
            return "réponse réservée à l'utilisateur (HUD), refusée à Claude"
        if cmd.get("_via") != "hud":
            return "réponse à l'écran seulement (HUD) : une donnée sensible ne part pas sur une autre réponse"
        return ""

    def _routage_reponse(self, result: dict) -> dict:
        """Clic de l'utilisateur sur la carte : la demande retenue part (Claude, local) ou est oubliée."""
        cid = str(result.get("id") or "")
        item = self._routage_retenus().pop(cid, None)
        action = str(((result.get("meta") or {}).get("actions") or {}).get(result.get("value"), "annuler"))
        if item is None:
            return {"ok": False, "error": "demande introuvable (Astrel a redémarré ?) : renvoie-la"}
        log.info("carte de routage %s : %s", cid, action)
        joined = item.get("joined")
        if action == "claude":
            threading.Thread(target=self.handle, args=(item["text"], item["source"], item["channel"]),
                             kwargs={"shown": item.get("shown"), "joined": joined, "route": routeur.CLAUDE},
                             name="routage-claude", daemon=True).start()
        elif action == "local" and item["source"] != "whatsapp":
            if joined is not None and (joined.ids or joined.shown):
                self._routage_rendre(joined)
                joined = None
            self._tour_local(item["text"], item["demande"], item["source"], item["channel"], item.get("shown"),
                             joined)
        else:
            self._routage_rendre(joined)
            bus.publish("notice", level="info", channel=item["channel"], text="Demande annulée : rien n'est parti.")
        return {"ok": True, "id": cid, "routage": action}
