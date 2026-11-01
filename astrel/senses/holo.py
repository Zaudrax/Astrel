"""HOLO : piloter Astrel d'un geste de la main, devant la webcam.

Reconnaissance locale (MediaPipe GestureRecognizer). Gestes retenus — des
gestes NETS, tenus un instant, pour qu'un mouvement de la main en parlant ne
déclenche rien :

| Geste                 | Effet                                              |
|-----------------------|----------------------------------------------------|
| pouce levé 👍         | « oui » à une confirmation en attente              |
| pouce baissé 👎       | « non » à une confirmation en attente              |
| main ouverte ✋       | interrompt Astrel (voix et tâche)                  |
| index levé ☝         | parler maintenant, sans dire « Astrel »            |

23/10 (plan P28) : le geste « poing fermé + bouger » (tourner le graphe 3D du HUD) est retiré
avec le graphe.

Un geste doit être vu sur `HOLD_FRAMES` images d'affilée, puis on attend
`COOLDOWN_S` avant d'en accepter un autre : un « oui » ne doit jamais partir
deux fois, ni par accident.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

import mediapipe as mp

from .. import config
from ..events import bus
from .camera_hub import hub

log = logging.getLogger("astrel.holo")

MODEL = config.MODELS / "mediapipe" / "gesture_recognizer.task"
RUN_EVERY_S = 0.08
HOLD_FRAMES = 4
COOLDOWN_S = 1.5
ACTIONS = {
    "Thumb_Up": "yes",
    "Thumb_Down": "no",
    "Open_Palm": "stop",
    "Pointing_Up": "wake",
}


class Holo:
    def __init__(self, on_gesture: Callable[[str], None]) -> None:
        self._on_gesture = on_gesture
        self._recognizer = None
        self._last_run = 0.0
        self._streak: tuple[str, int] = ("", 0)
        self._cooldown_until = 0.0

    def start(self) -> None:
        if not MODEL.exists():
            raise RuntimeError("modèle de gestes absent (models/mediapipe)")
        if self._recognizer is None:
            opts = mp.tasks.vision.GestureRecognizerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL)),
                num_hands=1,
                min_hand_detection_confidence=0.6,
                min_tracking_confidence=0.5,
            )
            self._recognizer = mp.tasks.vision.GestureRecognizer.create_from_options(opts)
        hub.subscribe("holo", self._on_frame)

    def stop(self) -> None:
        hub.unsubscribe("holo")

    def _on_frame(self, frame) -> None:
        now = time.monotonic()
        if now - self._last_run < RUN_EVERY_S:
            return
        self._last_run = now
        import cv2

        rgb = cv2.cvtColor(cv2.resize(frame, (480, 360)), cv2.COLOR_BGR2RGB)
        result = self._recognizer.recognize(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        if not result.gestures:
            self._streak = ("", 0)
            return
        top = result.gestures[0][0]
        name, score = top.category_name, top.score

        if name not in ACTIONS or score < 0.65:
            self._streak = ("", 0)
            return
        prev, count = self._streak
        count = count + 1 if prev == name else 1
        self._streak = (name, count)
        if count >= HOLD_FRAMES and now >= self._cooldown_until:
            self._cooldown_until = now + COOLDOWN_S
            self._streak = ("", 0)
            action = ACTIONS[name]
            log.info("geste : %s → %s", name, action)
            bus.publish("gesture", name=name, action=action)
            self._on_gesture(action)
