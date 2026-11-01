"""Les sens d'Astrel : EYES, WATCH, HOLO, FOCUS, REFLEX.

`setup(core)` enregistre un moteur pour chaque capteur auprès du registre
(`astrel.sensors`). Un moteur dont les dépendances manquent (pas de webcam,
modèle absent) n'empêche pas les autres de fonctionner : son interrupteur dit
simplement « indisponible », avec la raison.
"""
from __future__ import annotations

import logging
import time

from ..sensors import sensors

log = logging.getLogger("astrel.senses")


def setup(core) -> dict:
    engines: dict = {}

    from .focus import Focus
    engines["FOCUS"] = Focus()

    from .watch import Watch
    engines["WATCH"] = Watch()

    try:
        from .eyes import Eyes
        engines["EYES"] = Eyes(on_return=core.greet_return)
    except Exception as exc:  # MediaPipe ou OpenCV absent
        log.warning("EYES indisponible : %s", exc)

    try:
        from .holo import Holo
        engines["HOLO"] = Holo(on_gesture=core.on_gesture)
    except Exception as exc:
        log.warning("HOLO indisponible : %s", exc)

    from .reflex import Reflex

    def presence() -> tuple[bool, bool, float]:
        eyes = engines.get("EYES")
        on = sensors.is_on("EYES")
        present = bool(eyes and eyes.present)
        since = time.time() - eyes.present_since if eyes and present else 0.0
        return on, present, since

    engines["REFLEX"] = Reflex(command=core.on_command, presence=presence, quota=lambda: core.brain.quota)

    for name, engine in engines.items():
        sensors.register(name, engine)
    return engines
