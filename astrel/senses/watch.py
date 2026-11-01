"""WATCH : Astrel voit ton écran à chaque demande.

Allumé, chaque requête part à Claude avec une capture de l'écran : « ça ne
compile pas », « résume cette page », « c'est quoi cette erreur » se
comprennent sans rien expliquer. Éteint, rien n'est capturé.

Coût honnête : une image d'écran pèse environ 1 500 jetons par requête sur
l'abonnement. C'est pour ça que WATCH est un interrupteur et pas un défaut.
La capture passe par le partage d'écran autorisé une fois (voir
`tools/screenshot.py`) ; rien n'est gardé sur le disque.
"""
from __future__ import annotations

import base64
import logging
import subprocess
from pathlib import Path

from .. import config
from ..platform import python_exe
from ..tools.camera import shrink_image

log = logging.getLogger("astrel.watch")


def capture_avec_legende(ecran: str = "") -> tuple[dict | None, str]:
    """(capture prête à joindre à un message Claude ou None, légende d'une ligne).

    Plusieurs écrans (22/10, plan P24) : sans `ecran`, `astrel.tools.screenshot` prend l'écran du
    réglage « Écran regardé par défaut », sinon le principal de GNOME, et s'il n'est pas partagé un
    écran partagé (remarque dans la légende) : WATCH ne tombe pas en panne pour autant. Le chemin
    du PNG est la 1re ligne de la sortie, la légende la suite."""
    cmd = [python_exe(), "-m", "astrel.tools.screenshot"]
    if ecran.strip():
        cmd.append(f"--ecran={ecran.strip()}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(config.ROOT))
        if proc.returncode != 0:
            log.warning("capture WATCH impossible : %s", proc.stderr.strip())
            return None, ""
        lignes = [ligne.strip() for ligne in proc.stdout.splitlines() if ligne.strip()]
        if not lignes:
            log.warning("capture WATCH impossible : aucun fichier rendu")
            return None, ""
        path = lignes[0]
        try:
            data = shrink_image(path, max_width=1600)
        finally:
            Path(path).unlink(missing_ok=True)
        image = {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(data).decode()}
        return image, " ".join(lignes[1:])
    except Exception as exc:
        log.warning("capture WATCH impossible : %s", exc)
        return None, ""


def capture_for_prompt() -> dict | None:
    """Une capture prête à joindre à un message Claude, ou None si impossible (contrat d'origine,
    lu par core.py ; la légende de l'écran vu est donnée par `capture_avec_legende`)."""
    return capture_avec_legende()[0]


class Watch:
    def start(self) -> None:
        if capture_for_prompt() is None:
            raise RuntimeError("partage d'écran non autorisé — lance une capture une fois pour l'accepter")

    def stop(self) -> None:
        pass
