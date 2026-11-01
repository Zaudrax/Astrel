"""Une image de la webcam, à la demande.

La caméra n'est ouverte que le temps de la prise (quelques dixièmes de seconde),
puis relâchée : le voyant s'éteint aussitôt. Les premières images d'un capteur
qui s'allume sont sombres ou floues, on les jette.
"""
from __future__ import annotations

import os
import time

import cv2

from ..platform import camera_api

WARMUP_FRAMES = 8


def grab(device: int | None = None, max_width: int = 1280) -> bytes:
    """Rend une image JPEG de la webcam."""
    index = int(os.environ.get("ASTREL_CAMERA", "0")) if device is None else device
    cap = cv2.VideoCapture(index, camera_api())
    try:
        if not cap.isOpened():
            raise RuntimeError(f"webcam {index} inaccessible (occupée par une autre application ?)")
        frame = None
        deadline = time.monotonic() + 4.0
        for _ in range(WARMUP_FRAMES):
            ok, frame = cap.read()
            if not ok and time.monotonic() > deadline:
                break
        if frame is None:
            raise RuntimeError("la webcam n'a rendu aucune image")
    finally:
        cap.release()
    return encode(frame, max_width)


def encode(frame, max_width: int = 1280, quality: int = 82) -> bytes:
    h, w = frame.shape[:2]
    if w > max_width:
        frame = cv2.resize(frame, (max_width, int(h * max_width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("encodage JPEG impossible")
    return buf.tobytes()


def shrink_image(path: str, max_width: int = 1600) -> bytes:
    """Réduit une capture d'écran : une image de 4K coûte cher en contexte pour
    rien — 1600 px suffisent à lire un message d'erreur."""
    img = cv2.imread(path)
    if img is None:
        raise RuntimeError("image illisible")
    return encode(img, max_width, quality=85)
