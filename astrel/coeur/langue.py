"""Langue de la conversation côté cœur (02/11, demande de l'utilisateur : conversation anglaise complète).

Mixin de `Astrel` (core.py). Le réglage `conversation_language` (`astrel/langue.py`) s'applique :
- TOUT DE SUITE à l'écoute (Whisper), à la voix (voix retenue pour cette langue, secours local de la
  langue) et aux phrases dites par le code ;
- au message SUIVANT de l'utilisateur pour les consignes de Claude : elles sont figées à la création de la
  session (CLAUDE.md), donc session neuve, mais seulement sans tâche de fond ni tour en cours (une remise à
  zéro les tuerait) ; en attendant, une note dans chaque message demande la nouvelle langue.
"""
from __future__ import annotations

import threading

from .. import langue, sources
from ..events import bus
from ..graph import graph
from ..core import log  # aides du cœur (le paquet charge core d'abord)

# Notes (dans le MESSAGE, jamais dans le prompt système) le temps que la session neuve soit possible.
NOTE_VERS_FR = ("[l'utilisateur est repassé en français : réponds désormais en français. Une session neuve démarre "
                "quand les tâches de fond seront finies.]")
NOTICES_CHANGEMENT = {
    "en": "Conversation en anglais : écoute et voix en anglais tout de suite, consignes en anglais à ton "
          "prochain message (session neuve).",
    "fr": "Conversation en français : écoute et voix en français tout de suite, consignes en français à ton "
          "prochain message (session neuve).",
}
NOTICES_SESSION = {
    "en": "Conversation en anglais : nouvelle session avec les consignes en anglais.",
    "fr": "Conversation en français : nouvelle session avec les consignes en français.",
}
_UTILISATEUR = sources.UTILISATEUR  # 04/11 (T58) : `astrel/sources.py`


class LangueCoeur:
    """Langue de la conversation : voix, session, notes du tour."""

    def _langue_suivre(self, source: str) -> str:
        """Avant d'envoyer un message de l'utilisateur : la session parle-t-elle la langue choisie ? Sinon
        session neuve si rien ne tourne (ni tâche de fond, ni tour, ni mission), ou note à mettre devant le message
        (rendue ici)."""
        if source not in _UTILISATEUR:
            return ""
        have = getattr(self.brain, "langue", None)
        want = langue.conversation()
        if have is None or have == want:
            return ""
        with self._tasks_lock:
            busy = bool(self._tasks)
        # Relecture du 02/11 : une mission en cours (ou en pause) vit dans cette session : pas de remise à zéro.
        from .. import mission as mission_mod

        busy = busy or mission_mod.mission.status in (mission_mod.ACTIVE, mission_mod.PAUSED)
        if not busy and not self._turn_active:
            log.info("langue de la conversation : %s → %s, session neuve", have, want)
            self.reset_session(notice=NOTICES_SESSION[want])
            return ""
        log.info("langue de la conversation : %s → %s, session gardée (tâches de fond en cours)", have, want)
        return (langue.SWITCH_NOTE_EN if want == "en" else NOTE_VERS_FR) + " "

    def _langue_changee(self) -> None:
        """Réglage changé (HUD) : voix et secours de la nouvelle langue (dans un fil : charger une voix
        peut prendre une seconde), notice ; la session suit au prochain message (`_langue_suivre`)."""
        want = langue.conversation()
        bus.publish("notice", level="info", text=NOTICES_CHANGEMENT[want])

        def voix() -> None:
            spec = langue.voix(want)
            try:
                active = self.speaker.set_voice(spec)
                graph.set_voice(active)
            except Exception as exc:
                log.warning("voix %s indisponible pour la conversation (%s)", spec, exc)
                bus.publish("notice", level="warning", text=f"Voix {spec} indisponible : {exc}")
            setter = getattr(self.speaker, "set_fallback", None)
            if setter is not None:
                setter(langue.voix_secours(want))
            from ..settings import reglages

            reglages.publish()  # la liste des voix suit la langue

        threading.Thread(target=voix, name="langue-voix", daemon=True).start()
