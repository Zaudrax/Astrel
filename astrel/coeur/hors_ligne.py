"""Mode « rien ne sort » côté cœur (04/11/2026, lot 2 du moteur local, docs/chantiers/HYBRIDE-QWEN.md).

Le cœur décide, jamais un modèle :
- entrer : REFUSÉ tant qu'une tâche de Claude tourne (tour, agent de fond, mission, image ou vidéo IA) —
  décision du 04/11 : « Stop tout » d'abord ; puis réglage écrit, cerveau Claude ARRÊTÉ (cycle
  T46 en `arret` : aucune relance), mode léger quitté, pont WhatsApp arrêté ;
- pendant : les demandes de l'utilisateur vont au moteur local (`_tour_local`), les messages automatiques
  attendent (`_local_freeze`), la voix est Piper (`speaker.say`), chaque module qui sort se coupe seul ;
- sortir : réglage effacé, cerveau relancé (reprise de la session), pont WhatsApp relancé, messages gelés
  repartis au calme (`_local_tick`).
Entrer comme sortir : refusé à Claude (`_settings_command`), dit ou tapé par l'utilisateur (« passe hors ligne »).

Mixin de `Astrel` (core.py), comme les autres de `astrel/coeur/`.
"""
from __future__ import annotations

import contextlib
import threading
import uuid

from .. import hors_ligne, mission as mission_mod, modeles_locaux
from ..channels import channels
from ..events import bus
from ..settings import SettingError, reglages
from ..core import log  # aides du cœur (le paquet charge core d'abord)

OCCUPE = ("{pourquoi} : rien n'est coupé. Arrête-la d'abord (« ⛔ Stop tout » en haut du HUD), puis réessaie.")
ENTREE = ("Mode « rien ne sort » : Claude est arrêté, je réponds avec le modèle local et rien ne part de la machine. "
          "« Repasse en ligne » pour revenir.")
SORTIE = "De retour en ligne : Claude repart."
DEJA_ON = "Le mode « rien ne sort » est déjà actif."
DEJA_OFF = "Astrel est déjà en ligne."


class HorsLigneCoeur:
    """Entrée et sortie du mode « rien ne sort »."""

    def _hors_ligne_occupe(self) -> str:
        """Ce qui empêche d'entrer dans le mode (vide : rien)."""
        with self._tasks_lock:
            if self._turn_active:
                return "Claude répond encore"
            if self._tasks:
                return "une tâche de fond de Claude tourne"
        if mission_mod.mission.status in (mission_mod.ACTIVE, mission_mod.PAUSED):
            return "une mission tourne"
        from ..images_ia import images
        from ..videos_ia import videos

        if images.en_cours is not None or videos.en_cours is not None:
            return "une image ou une vidéo IA est en cours"
        return ""

    def _hors_ligne_switch(self, on: bool, by: str = "HUD") -> dict:
        """Entre dans le mode ou en sort. Les appelants refusent ce qui vient de Claude."""
        if on == hors_ligne.actif():
            self._publish_model()
            return {"ok": True, "hors_ligne": on, "text": DEJA_ON if on else DEJA_OFF}
        return self._hors_ligne_entrer(by) if on else self._hors_ligne_sortir(by)

    def _verrous(self):
        return (getattr(self, "_turn_lock", None) or contextlib.nullcontext(),
                getattr(self, "_send_lock", None) or contextlib.nullcontext())

    def _hors_ligne_entrer(self, by: str) -> dict:
        pourquoi = self._hors_ligne_occupe()
        if pourquoi:
            return {"ok": False, "error": OCCUPE.format(pourquoi=pourquoi), "stop_tout": True}
        tour, envoi = self._verrous()
        with tour, envoi:
            pourquoi = self._hors_ligne_occupe()  # rien n'a pu démarrer depuis : revérifié sous les verrous
            if pourquoi:
                return {"ok": False, "error": OCCUPE.format(pourquoi=pourquoi), "stop_tout": True}
            try:
                reglages.set(hors_ligne.CLE, True)
            except SettingError as exc:
                return {"ok": False, "error": exc.message}
            self._cerveau_arreter()  # cycle T46 : plus aucune relance programmée
            self._local_web_couper(hors_ligne.REFUS)  # 05/11 (lot 7B) : navigateur du local coupé tout de suite
            if self._local_active():  # le mode léger lance aussi un CLI : quitté, sans retour automatique
                self.brain.local = ""
                modeles_locaux.local.set_active(False)
            try:
                self.brain.stop()
            except Exception:
                log.exception("arrêt du cerveau pour le mode « rien ne sort »")
        try:
            self.whatsapp.stop()
        except Exception:
            log.exception("pont WhatsApp non arrêté")
        log.warning("mode « rien ne sort » ACTIVÉ (%s)", by)
        self._hors_ligne_annoncer(ENTREE)
        return {"ok": True, "hors_ligne": True, "text": ENTREE}

    def _hors_ligne_sortir(self, by: str) -> dict:
        tour, envoi = self._verrous()
        with tour, envoi:
            try:
                reglages.set(hors_ligne.CLE, False)
            except SettingError as exc:
                return {"ok": False, "error": exc.message}
            self._cycle.rouvrir()
            self._cerveau_demarrer()  # un lancement raté passe par le cycle (recul), jamais d'exception
        try:
            self.whatsapp.start()
        except Exception:
            log.exception("pont WhatsApp non relancé")
        log.warning("mode « rien ne sort » coupé (%s)", by)
        self._hors_ligne_annoncer(SORTIE)
        return {"ok": True, "hors_ligne": False, "text": SORTIE}

    def _hors_ligne_annoncer(self, texte: str) -> None:
        self._publish_model()
        bus.publish("notice", level="info", text=texte)
        threading.Thread(target=reglages.publish, name="settings-hors-ligne", daemon=True).start()

    def _hors_ligne_dit(self, quoi: str, text: str, source: str, channel: str = "") -> None:
        """« passe hors ligne » / « repasse en ligne », dit ou tapé par l'utilisateur : affiché, fait, réponse dite."""
        ch = channel or channels.active
        bus.publish("transcript", who="user", text=text, source=source, channel=ch, mid=uuid.uuid4().hex[:12])
        res = self._hors_ligne_switch(quoi == "on", by=source)
        texte = res.get("text") or res.get("error") or ""
        if not res.get("ok"):
            bus.publish("notice", level="warning", channel=ch, text=texte)
        if source == "voix" and texte:
            self.say(texte, raw=True)

    def _hors_ligne_boot(self) -> bool:
        """Au démarrage : mode retenu → le cerveau n'est pas lancé. Vrai si Astrel démarre hors ligne."""
        if not hors_ligne.actif():
            return False
        self._cerveau_arreter()
        log.warning("démarrage en mode « rien ne sort » : cerveau Claude non lancé")
        bus.publish("notice", level="info", text="Démarrage en mode « rien ne sort » : Claude n'est pas lancé, je "
                                                 "réponds avec le modèle local. « Repasse en ligne » pour revenir.")
        self._publish_model()
        return True
