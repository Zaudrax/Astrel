"""EYES : Astrel sait si l'utilisateur est devant l'écran.

Détection de visage locale (MediaPipe BlazeFace, ~3 ms par image sur le
processeur) sur le flux de la webcam. Aucune image n'est gardée ni envoyée :
seul un booléen « présent / absent » sort du capteur.

- Présent : un visage vu dans les dernières secondes.
- Absent : aucun visage depuis `AWAY_S`.
- Retour après une longue absence : Astrel salue (sauf en FOCUS) et le dit au
  HUD. C'est aussi ce qui permet aux réflexes de ne parler qu'à quelqu'un qui
  est là.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import mediapipe as mp

from .. import config
from ..events import bus
from .camera_hub import hub

log = logging.getLogger("astrel.eyes")

MODEL = config.MODELS / "mediapipe" / "blaze_face_short_range.tflite"
DETECT_EVERY_S = 0.4
PRESENT_GRACE_S = 4.0
AWAY_S = 45.0
GREET_AFTER_S = 20 * 60


class Eyes:
    def __init__(self, on_return: Callable[[float], None] | None = None) -> None:
        self._on_return = on_return
        self._detector = None
        self._last_seen = 0.0
        self._last_run = 0.0
        self.present = False
        self.present_since = 0.0
        self._away_since = time.time()
        self._lock = threading.Lock()

    def start(self) -> None:
        if not MODEL.exists():
            raise RuntimeError("modèle de visage absent (models/mediapipe)")
        if self._detector is None:
            opts = mp.tasks.vision.FaceDetectorOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL)),
                min_detection_confidence=0.55,
            )
            self._detector = mp.tasks.vision.FaceDetector.create_from_options(opts)
        hub.subscribe("eyes", self._on_frame)

    def stop(self) -> None:
        hub.unsubscribe("eyes")
        self._set_present(False, time.time())

    def _on_frame(self, frame) -> None:
        now = time.time()
        if now - self._last_run < DETECT_EVERY_S:
            return
        self._last_run = now
        import cv2

        small = cv2.cvtColor(cv2.resize(frame, (320, 240)), cv2.COLOR_BGR2RGB)
        result = self._detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=small))
        if result.detections:
            self._last_seen = now
            if not self.present:
                self._set_present(True, now)
        elif self.present and now - self._last_seen > AWAY_S:
            self._set_present(False, now)

    def _set_present(self, present: bool, now: float) -> None:
        with self._lock:
            if present == self.present:
                return
            self.present = present
            away_for = now - self._away_since if present else 0.0
            if present:
                self.present_since = now
            else:
                self._away_since = now
        log.info("présence : %s", "là" if present else "parti")
        bus.publish("presence", present=present, away_s=round(away_for))
        if present and away_for > GREET_AFTER_S and self._on_return:
            self._on_return(away_for)
