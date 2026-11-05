"""Commandes « / » de la barre de chat, côté cœur (05/11/2026, demande de l'utilisateur ; table et règles :
`astrel/commandes_chat.py`).

- HUD : `chat_commande {nom, arg, channel}`, HUD de l'utilisateur SEUL (refusée à Claude ET hors du HUD : terminal,
  bulle, WhatsApp), validée de nouveau ici (`commandes_chat.analyser`). /aide et /tout-arreter restent dans le HUD
  (liste dans le fil ; boîte de confirmation puis `estop`), /stop y est la commande `stop` du bouton Arrêter, /local et /claude
  y partent en `say` avec le préfixe du routeur.
- Bulle et terminal : un `say` tapé (source clavier, pas Claude, pas le HUD) qui est une commande est traité par
  `_chat_commande_tapee` : jamais envoyé à un modèle ; /local et /claude y deviennent « en local : … » / « avec
  Claude : … » et suivent le chemin normal (routeur, barrière du lot 3) ; /tout-arreter y est refusée (sa
  confirmation est celle du HUD). Le résultat part en notice (la bulle n'a pas de fil).
Chaque commande appelle le chemin qui existait déjà : `reset_session`, réglage `moteur_mode` (`settings_set`),
`_hors_ligne_switch`, `_set_model`, `set_usage`, `_cmd_stop`, et (05/11) `_tour_local(..., reflexion=True)` pour
/reflechir. Aucune n'ouvre de droit nouveau.

Mixin de `Astrel` (core.py), comme les autres de `astrel/coeur/`.
"""
from __future__ import annotations

import threading

from .. import commandes_chat, hors_ligne, mission as mission_mod
from . import local_tour
from ..events import bus
from ..core import log  # aides du cœur (le paquet charge core d'abord)

CHAT_COMMANDS = ("chat_commande",)
REFUS_HUD = "{name} est réservé au HUD de l'utilisateur".format(name="chat_commande")  # comme history_query
NOTICE_CLAUDE = "Claude a tenté « chat_commande » : refusé, les commandes du chat sont à toi seul."

# /clear refusée tant que quelque chose vit dans la session (une nouvelle conversation le tuerait)
CLEAR_TOUR = "Pas de nouvelle conversation : Claude répond encore. Arrête-le d'abord (bouton Arrêter ou Échap), puis /clear."
CLEAR_TACHE = ("Pas de nouvelle conversation : une tâche de fond tourne, elle serait tuée. Attends qu'elle finisse, "
               "ou « ⛔ Stop tout ».")
CLEAR_MISSION = "Pas de nouvelle conversation : une mission tourne dans cette session."
CLEAR_LOCAL = "Pas de nouvelle conversation : le modèle local répond encore. Arrête-le d'abord (bouton Arrêter ou Échap)."
MODE_HORS_LIGNE = "Mode « rien ne sort » : le modèle local répond à tout. /enligne d'abord pour choisir qui répond."
TOUT_ARRETER_HUD = "« Stop tout » se confirme dans le HUD : bouton ⛔ en haut, ou /tout-arreter dans sa barre."
MODES_USAGE = {"silence": "silencieux", "vocal": "conversation", "dictee": "dictee"}


class CommandesChatCoeur:
    """Commandes « / » tapées dans la barre du HUD ou dans le mini-chat de la bulle."""

    def _cmd_chat_commande(self, name: str, cmd: dict) -> dict:
        if cmd.get("_from_claude") is True:
            log.warning("chat_commande refusée : elle vient de Claude (%s)", cmd.get("nom"))
            bus.publish("notice", level="warning", text=NOTICE_CLAUDE)
            return {"ok": False, "error": REFUS_HUD}
        if cmd.get("_via") != "hud":
            log.warning("chat_commande refusée hors du HUD (%s)", cmd.get("_via") or "?")
            return {"ok": False, "error": REFUS_HUD}
        nom, arg = str(cmd.get("nom") or "").strip(), str(cmd.get("arg") or "").strip()
        # Revalidée ici : le cœur ne se fie pas à l'analyse du HUD (nom inconnu, argument manquant ou en trop).
        a = commandes_chat.analyser(f"/{nom} {arg}" if arg else f"/{nom}")
        if a is None or not a.ok:
            return {"ok": False, "error": a.erreur if a else commandes_chat.INCONNUE.format(nom=nom[:30])}
        return self._chat_commande(a, cmd)

    def _chat_commande(self, a: commandes_chat.Analyse, cmd: dict) -> dict:
        """Exécute une commande valide (`a.ok`) par son chemin habituel. `cmd` : la commande d'origine (canal,
        `_via`), jamais venue de Claude (les appelants l'ont refusée)."""
        log.info("commande du chat /%s (%s)", a.nom, cmd.get("_via") or "interne")
        channel = str(cmd.get("channel") or "")
        if a.nom == "aide":
            return {"ok": True, "text": commandes_chat.aide()}
        if a.nom == "clear":
            return self._chat_clear()
        if a.nom in commandes_chat.PREFIXES:
            # /local et /claude : le message part comme s'il avait été tapé avec son préfixe (routeur, lot 3).
            return self._cmd_say("say", {**cmd, "cmd": "say", "text": commandes_chat.texte_routage(a),
                                         "channel": channel, "source": "clavier", "_chat_commande": True})
        if a.nom == "reflechir":
            # 05/11 (lot 8 du moteur local) : la question part au modèle LOCAL avec la réflexion activée pour elle
            # seule (coupée par défaut, décision de l'utilisateur) ; jamais à Claude, rien de routé. Sans outils : la
            # réflexion du CLI avec outils est celle du CLI (`thinking: adaptive`).
            if self._local_actif():
                return {"ok": False, "error": local_tour.OCCUPE}
            self._tour_local(f"/{a.nom} {a.arg}", a.arg, "clavier", channel, reflexion=True)
            return {"ok": True, "local": True}
        if a.nom == "mode":
            if hors_ligne.actif():
                return {"ok": False, "error": MODE_HORS_LIGNE}
            r = self._settings_command("settings_set", {"key": "moteur_mode", "value": a.arg,
                                                        "_via": cmd.get("_via"), "_from_claude": False})
            return {"ok": r.get("ok") is not False, "mode": a.arg,
                    **({"error": r.get("error")} if r.get("ok") is False else {})}
        if a.nom in ("horsligne", "enligne"):
            on = a.nom == "horsligne"
            deja = on == hors_ligne.actif()  # rien ne change : pas de notice du cœur, le HUD dit `text`
            r = self._hors_ligne_switch(on, by="HUD" if cmd.get("_via") == "hud" else "terminal")
            return {**{k: r[k] for k in ("ok", "error", "text", "hors_ligne", "stop_tout") if k in r},
                    **({"deja": True} if deja and r.get("ok") else {})}
        if a.nom == "modele":
            return self._set_model(a.arg)  # jamais de relance pendant une tâche (`_set_model`)
        if a.nom in MODES_USAGE:
            return self.set_usage(MODES_USAGE[a.nom])
        if a.nom == "stop":
            return self._cmd_stop("stop", cmd)
        if a.nom == "tout-arreter":
            # Le HUD la confirme puis envoie `estop` (comme le bouton ⛔) ; ailleurs, pas de boîte : refusée.
            return {"ok": False, "error": TOUT_ARRETER_HUD}
        return {"ok": False, "error": commandes_chat.INCONNUE.format(nom=a.nom)}

    def _chat_clear_occupe(self) -> str:
        """Pourquoi /clear est refusée (vide : rien ne vit dans la session). Mêmes raisons que la session gardée
        au changement de langue (`_langue_suivre`), plus le tour en cours et la réponse locale."""
        with self._tasks_lock:
            if self._turn_active:
                return CLEAR_TOUR
            if self._tasks:
                return CLEAR_TACHE
        if mission_mod.mission.status in (mission_mod.ACTIVE, mission_mod.PAUSED):
            return CLEAR_MISSION
        if self._local_actif():
            return CLEAR_LOCAL
        return ""

    def _chat_clear(self) -> dict:
        """/clear, /nouveau : la remise à zéro de session existante (« Nouvelle conversation », `reset`), et
        l'historique EN MÉMOIRE du modèle local oublié aussi. Le HUD vide l'affichage du canal ouvert."""
        pourquoi = self._chat_clear_occupe()
        if pourquoi:
            return {"ok": False, "error": pourquoi}
        local = self.__dict__.get("_moteur_local_obj")
        if local is not None:
            local.oublier()
        threading.Thread(target=self.reset_session, name="reset", daemon=True).start()
        return {"ok": True, "vider": True}

    def _chat_commande_tapee(self, a: commandes_chat.Analyse, cmd: dict) -> dict:
        """Commande tapée dans la bulle ou au terminal (`say`) : jamais envoyée à un modèle ; le résultat en notice.
        Réponse `ok` (le message a été reçu et traité : la bulle vide sa ligne), l'issue dans `resultat`."""
        if not a.ok:
            r = {"ok": False, "error": a.erreur}
        else:
            r = self._chat_commande(a, cmd)
        texte = str(r.get("text") or "") if r.get("ok") is not False else str(r.get("error") or "")
        if texte:
            bus.publish("notice", level="info" if r.get("ok") is not False else "warning",
                        channel=str(cmd.get("channel") or ""), text=texte)
        return {"ok": True, "commande": a.nom or a.inconnue,
                "resultat": {k: r[k] for k in ("ok", "error", "text") if k in r}}
