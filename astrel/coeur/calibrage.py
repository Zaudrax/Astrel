"""Assistant de calibrage du micro (02/11, demande de l'utilisateur) : commandes du HUD, écoute suspendue
pendant les mesures, profil écrit seulement après « Enregistrer ».

Mixin de `Astrel` (core.py). Mesure : `astrel/calibrage.py` (la même que `./run.sh --calibrate`).
- `calibrage_start` / `calibrage_cancel` / `calibrage_save` : HUD non-Claude SEULEMENT (refusées si
  `_from_claude`), car elles changent les seuils d'écoute ;
- pendant les mesures (≈ 15 s) : mot d'éveil non écouté, voix d'Astrel en pause (rien n'est dit, ce qui
  n'a pas été entendu est GARDÉ et rejoué ensuite), messages non demandés en file (`floor`) ; tout est
  rétabli à la fin des mesures, même sur erreur ou annulation ;
- Stop tout annule la séance ; rien n'est écrit sans le clic « Enregistrer » ; le seuil de l'ancien mot
  d'éveil (`wake`) n'est jamais réécrit ;
- un micro choisi SANS profil : évènement `calibrage_propose` (le HUD propose l'assistant, jamais lancé seul).
"""
from __future__ import annotations

import os
import threading
import time

from .. import audio
from .. import calibrage
from .. import config
from .. import micprofile
from ..events import bus
from ..floor import floor
from ..core import log  # aides du cœur (le paquet charge core d'abord)

CALIBRAGE_COMMANDS = ("calibrage_start", "calibrage_cancel", "calibrage_save")
_PRIORITAIRES = (("ASTREL_MIC_GAIN", "gain"), ("ASTREL_VAD_THRESHOLD", "vad"))


class CalibrageCoeur:
    """Assistant de calibrage du micro (HUD)."""

    _calibrage: "calibrage.Seance | None" = None   # séance que l'utilisateur peut encore enregistrer
    # Relecture du 02/11 : dernière séance lancée, gardée même annulée jusqu'à la fin de son fil (`fin` appelé) ;
    # un nouveau calibrage l'attend. L'état de pause et de `floor` est PAR séance (dict passé à suspendre/rétablir).
    _calib_derniere: "calibrage.Seance | None" = None
    _calib_suspensions = 0       # mesures en cours (mot d'éveil suspendu tant qu'il en reste une)
    _calib_lock = threading.Lock()

    @property
    def _calibrating(self) -> bool:
        """Une mesure de calibrage écoute le micro : la veille n'écoute pas le mot d'éveil."""
        return self._calib_suspensions > 0

    def _calibrage_command(self, name: str, cmd: dict) -> dict:
        if cmd.get("_from_claude") is True:
            log.warning("commande %r refusée : elle vient de Claude", name)
            bus.publish("notice", level="warning",
                        text=f"Claude a tenté « {name} » : refusé, le calibrage du micro est à toi seul.")
            return {"ok": False, "error": "calibrage du micro réservé à l'utilisateur (HUD), refusé à Claude"}
        if name == "calibrage_start":
            return self._calibrage_start()
        if name == "calibrage_cancel":
            return self._calibrage_annuler("annulé par l'utilisateur")
        return self._calibrage_save()

    def _calibrage_refus(self) -> str:
        if config.ESTOP_FILE.exists():
            return "arrêt d'urgence en cours : « Reprendre tout » d'abord"
        if self._off:
            return "micro coupé : rallume-le d'abord (bouton du micro)"
        if self.recorder is None or self.wake is None:
            return "écoute non chargée (Astrel démarre, ou tourne au clavier seulement)"
        if self._in_conversation or self._recording or self._confirm_evt.is_set():
            return "une écoute est en cours : réessaie quand elle est finie"
        return ""

    def _calibrage_start(self) -> dict:
        why = self._calibrage_refus()
        if why:
            return {"ok": False, "error": why}
        for old in {id(x): x for x in (self._calibrage, self._calib_derniere) if x is not None}.values():
            old.annuler("nouveau calibrage")
            old.join(5.0)
            if old.vivante:
                return {"ok": False, "error": "le calibrage précédent se termine encore : réessaie dans un instant"}
        self._calibrage = None
        name, label = self.mic.identity()
        n = audio.source_channels(name)
        text = getattr(self.wake, "_text", None)
        transcribe = getattr(text, "_transcribe", None) if text is not None and not text._dead else None
        etat: dict = {}
        seance = calibrage.Seance(
            name, label, n, self.mic.profile, lambda **d: bus.publish("calibrage", **d),
            vad=self._calibrage_vad, transcribe=transcribe,
            debut=lambda: self._calibrage_suspendre(etat), fin=lambda: self._calibrage_retablir(etat),
            bruit_s=calibrage.BRUIT_S, parole_s=calibrage.PAROLE_S)
        self._calibrage = self._calib_derniere = seance
        log.info("calibrage du micro %s lancé (%d canal(aux))", label or name or "du système", n)
        seance.start()
        return {"ok": True, "micro": label or name, "canaux": n}

    @staticmethod
    def _calibrage_vad():
        """Un VAD neuf pour la séance (celui de l'écoute garde son état)."""
        from ..listener import VoiceRecorder

        return VoiceRecorder()._vad

    def _calibrage_suspendre(self, etat: dict) -> None:
        """Début des mesures : plus de mot d'éveil, voix en pause (rien perdu), file des non-demandés.
        `etat` : propre à la séance (ce qu'elle a mis en pause, son `floor`), rendu par `_calibrage_retablir`."""
        busy = self.speaker.busy
        with self._calib_lock:
            self._calib_suspensions += 1
        self._followup = False
        etat["pause"] = bool(self.speaker.pause())
        etat["floor"] = floor.listening()
        etat["floor"].__enter__()
        if busy:
            time.sleep(config.ECHO_TAIL_S)  # la fin de sa phrase ne doit pas compter comme du bruit de fond

    def _calibrage_retablir(self, etat: dict) -> None:
        """Fin des mesures (toujours appelée) : la veille reprend comme avant."""
        try:
            if self.wake is not None:
                self.wake.reset()  # aucun son de la mesure dans la voie texte
        finally:
            with self._calib_lock:
                self._calib_suspensions = max(0, self._calib_suspensions - 1)
            ctx = etat.pop("floor", None)
            if ctx is not None:
                ctx.__exit__(None, None, None)
            if etat.pop("pause", False):
                self.speaker.resume()

    def _calibrage_annuler(self, raison: str) -> dict:
        seance = self._calibrage
        self._calibrage = None  # plus rien à enregistrer ; `_calib_derniere` garde le fil jusqu'à sa fin
        if seance is None:
            return {"ok": True, "cancelled": False}
        seance.annuler(raison)
        return {"ok": True, "cancelled": True}

    def _calibrage_micro_coupe(self) -> None:
        """Micro coupé (vrai mute) pendant une séance : elle est annulée, sa capture fermée."""
        if self._calibrage is not None:
            self._calibrage_annuler("micro coupé")

    def _calibrage_estop(self) -> None:
        """Stop tout : la séance s'arrête (mesures comprises), rien n'est écrit."""
        if self._calibrage is not None:
            self._calibrage_annuler("Stop tout")

    def _calibrage_save(self) -> dict:
        why = self._calibrage_refus()  # relecture du 02/11 : ESTOP, micro coupé, écoute en cours
        if why:
            return {"ok": False, "error": why}
        seance = self._calibrage
        prop = seance.proposition if seance is not None and seance.etat == "resultat" else None
        if prop is None:
            return {"ok": False, "error": "aucun calibrage à enregistrer : lance d'abord les mesures"}
        name, label = self.mic.identity()
        if name != seance.source:
            return {"ok": False, "error": "le micro a changé depuis la mesure : refais le calibrage"}
        canaux = prop["canaux"] if seance.n > 1 else None
        prof = micprofile.save(name, label, channels=canaux, gain=prop["gain"], vad=prop["vad"])
        self._calibrage = None
        seance.etat = "enregistre"
        if seance.n > 1 and tuple(canaux or ()) != tuple(self.mic.profile.channels):
            self.mic.switch(self.mic.source)  # autres canaux : le flux est rouvert avec eux
        else:
            self.mic.load_profile()
        prioritaires = [v for v, _k in _PRIORITAIRES if v in os.environ]
        log.info("calibrage enregistré pour %s : %s", label or name, prof.describe())
        bus.publish("calibrage", etat="enregistre", micro=label or name, profil=prof.describe(),
                    prioritaires=prioritaires)
        bus.publish("notice", level="info", text=f"Profil du micro enregistré : {prof.describe()}.")
        if prioritaires:
            bus.publish("notice", level="warning",
                        text=f"Attention : {', '.join(prioritaires)} reste prioritaire sur ce profil.")
        from ..settings import reglages

        threading.Thread(target=reglages.publish, name="settings-calibrage", daemon=True).start()
        return {"ok": True, "profil": prof.describe(), "gain": prof.gain, "vad": prof.vad,
                "channels": list(prof.channels)}

    def _calibrage_view(self) -> dict:
        """Ligne « Calibrage du micro » des Réglages : état du profil du micro en cours, bouton de l'assistant."""
        p = self.mic.profile
        if p.key:
            line = {"label": "Ce micro", "level": "ok", "text": f"calibré : {p.describe()}"}
        else:
            line = {"label": "Ce micro", "level": "warn", "text": f"pas encore calibré : réglages par défaut ({p.describe()})"}
        return {"value": {"lines": [line]},
                "actions": [{"id": "ouvrir", "label": "Calibrer ce micro", "hud": "calibrage"}]}

    def _calibrage_propose_si_besoin(self) -> None:
        """Micro choisi sans profil : le HUD propose l'assistant (notice non bloquante)."""
        if self.mic.profile.key:
            return
        name, label = self.mic.identity()
        bus.publish("calibrage_propose", source=name, micro=label or name or "micro du système")
