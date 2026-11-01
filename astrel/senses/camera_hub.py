"""La webcam, partagée entre les capteurs visuels (EYES, HOLO) et les photos.

Un périphérique V4L2 n'a qu'un lecteur à la fois : si EYES tient la caméra, une
photo demandée par Claude échouerait « caméra occupée ». Le hub est donc le
SEUL propriétaire : il ouvre la caméra dès qu'un abonné en a besoin, distribue
chaque image, et la relâche quand plus personne n'écoute — le voyant s'éteint.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

import cv2

from ..platform import camera_api

log = logging.getLogger("astrel.camera")

Frame = "numpy.ndarray"
Listener = Callable[[object], None]


class CameraHub:
    def __init__(self, device: int | None = None, fps: float = 12.0, width: int = 640) -> None:
        self.device = int(os.environ.get("ASTREL_CAMERA", "0")) if device is None else device
        self.period = 1.0 / fps
        self.width = width
        self._listeners: dict[str, Listener] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._latest = None
        self._latest_at = 0.0
        self.error = ""

    # --- abonnements ---

    def subscribe(self, name: str, fn: Listener) -> None:
        with self._lock:
            self._listeners[name] = fn
            start = self._thread is None
        if start:
            self._start()

    def unsubscribe(self, name: str) -> None:
        with self._lock:
            self._listeners.pop(name, None)
            idle = not self._listeners
        if idle:
            self._halt()

    @property
    def active(self) -> bool:
        return self._thread is not None

    def latest(self, max_age_s: float = 2.0):
        """Dernière image, si la caméra tourne déjà et qu'elle est fraîche."""
        if self._latest is not None and time.monotonic() - self._latest_at <= max_age_s:
            return self._latest.copy()
        return None

    # --- boucle ---

    def _start(self) -> None:
        self._stop.clear()
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="camera", daemon=True)
        self._thread.start()
        ready.wait(5.0)
        if self.error:
            self._thread = None
            raise RuntimeError(self.error)

    def _halt(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=3.0)
        self._latest = None

    def _run(self, ready: threading.Event) -> None:
        # V4L2 sous Linux (d'origine), AVFoundation sous macOS, DirectShow sous Windows.
        cap = cv2.VideoCapture(self.device, camera_api())
        if not cap.isOpened():
            self.error = f"webcam {self.device} inaccessible (occupée par une autre application ?)"
            ready.set()
            return
        self.error = ""
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.width * 3 / 4))
        ready.set()
        log.info("webcam ouverte")
        try:
            while not self._stop.is_set():
                t0 = time.monotonic()
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.2)
                    continue
                frame = cv2.flip(frame, 1)  # effet miroir : la gauche de l'utilisateur est à gauche
                self._latest, self._latest_at = frame, time.monotonic()
                with self._lock:
                    listeners = list(self._listeners.values())
                for fn in listeners:
                    try:
                        fn(frame)
                    except Exception:
                        log.exception("abonné caméra en échec")
                time.sleep(max(0.0, self.period - (time.monotonic() - t0)))
        finally:
            cap.release()
            log.info("webcam relâchée")


hub = CameraHub()
