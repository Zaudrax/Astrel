#!/usr/bin/env python3
"""Télécharge les modèles locaux (exclus de Git : ~550 Mo). Ne retélécharge pas
ce qui est déjà là. Même liste que l'ancien `fetch-models.sh`, en Python pour
marcher aussi sous Windows (P2, lot 2).

À lancer avec le Python de la venv : le mot de réveil et le VAD viennent du
paquet openwakeword.
"""
from __future__ import annotations

import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
M = ROOT / "models"

HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR"
# 02/11 (conversation anglaise) : voix de secours locale en anglais, « joe » (jeu de données CC0 ;
# modèle affiné depuis la voix « lessac », sous licence de recherche non commerciale : voir NOTICE).
HF_EN = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US"
MP = "https://storage.googleapis.com/mediapipe-models"

FILES = [
    (f"{HF}/upmc/medium/fr_FR-upmc-medium.onnx", "piper/fr_FR-upmc-medium.onnx"),
    (f"{HF}/upmc/medium/fr_FR-upmc-medium.onnx.json", "piper/fr_FR-upmc-medium.onnx.json"),
    (f"{HF_EN}/joe/medium/en_US-joe-medium.onnx", "piper/en_US-joe-medium.onnx"),
    (f"{HF_EN}/joe/medium/en_US-joe-medium.onnx.json", "piper/en_US-joe-medium.onnx.json"),
    (f"{MP}/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite",
     "mediapipe/blaze_face_short_range.tflite"),
    (f"{MP}/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task",
     "mediapipe/gesture_recognizer.task"),
]


def get(url: str, dest: Path, tries: int = 3) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"   ↓ {dest.name}", flush=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp, open(part, "wb") as f:
                shutil.copyfileobj(resp, f)
            part.replace(dest)
            return
        except OSError as exc:
            if attempt == tries:
                part.unlink(missing_ok=True)
                raise RuntimeError(f"téléchargement de {dest.name} impossible : {exc}") from exc


def main() -> int:
    for url, rel in FILES:
        get(url, M / rel)
    # Mot de réveil et VAD : livrés DANS le paquet openwakeword 0.4.0 (rien à
    # télécharger ; l'ancien script appelait un `download_models` qui n'existe pas
    # dans cette version et échouait en silence).
    try:
        import openwakeword

        bundled = Path(openwakeword.__file__).parent / "resources" / "models"
        missing = [n for n in ("hey_jarvis_v0.1.onnx", "silero_vad.onnx") if not (bundled / n).exists()]
    except Exception as exc:
        missing = [f"openwakeword ({exc})"]
    if missing:
        print("   ⚠ modèles du mot de réveil absents : " + ", ".join(missing))
        return 1
    print("   modèles prêts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
