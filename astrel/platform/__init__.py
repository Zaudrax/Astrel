"""Ce qui dépend du système d'exploitation (P2, portage Mac/Windows).

Un seul endroit décide du système (`SYSTEM`) ; chaque moteur expose les MÊMES
fonctions :

- `linux.py`, `linux_capture.py` : le code d'origine (GNOME, PipeWire, portail
  de partage d'écran), extrait tel quel. C'est la machine de référence ;
- `macos.py` : osascript, open, pbcopy, pmset, screencapture ;
- `windows.py` : API Windows (ctypes, winreg), PowerShell, os.startfile ;
- `degrade.py` : tout autre système — chaque action dit qu'elle est indisponible.

Règle : ce qui n'a pas d'équivalent lève `Indisponible` avec une phrase claire,
que Claude répète telle quelle. Jamais de faux succès.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

LABELS = {"linux": "Linux", "macos": "macOS", "windows": "Windows", "autre": "ce système"}


def detect(name: str | None = None) -> str:
    """`sys.platform` → linux, macos, windows ou autre."""
    name = sys.platform if name is None else name
    if name.startswith("linux"):
        return "linux"
    if name == "darwin":
        return "macos"
    if name in ("win32", "cygwin"):
        return "windows"
    return "autre"


SYSTEM = detect()
LABEL = LABELS[SYSTEM]


class Indisponible(RuntimeError):
    """Action sans équivalent sur ce système : le message le dit en clair."""


def unavailable(what: str, why: str = "", system: str | None = None) -> Indisponible:
    label = LABELS[system or SYSTEM]
    return Indisponible(f"{what} : indisponible sous {label}" + (f" ({why})" if why else "") + ".")


_DESKTOP = {"linux": "linux", "macos": "macos", "windows": "windows", "autre": "degrade"}


_WSL: list[bool] = []


def sous_wsl() -> bool:
    """Linux de WSL2 (03/11) : le bureau est celui de Windows. Lu une fois (/proc/version)."""
    if not _WSL:
        from .materiel import is_wsl
        _WSL.append(SYSTEM == "linux" and is_wsl())
    return _WSL[0]


def desktop_backend(system: str | None = None) -> ModuleType:
    """Module des actions du bureau pour ce système (ou celui demandé). Sous WSL2 (03/11) : `wsl.py`,
    le pont vers le bureau de Windows."""
    nom = _DESKTOP[system or SYSTEM]
    if system is None and nom == "linux" and sous_wsl():
        nom = "wsl"
    return importlib.import_module(f"{__name__}.{nom}")


def python_exe() -> str:
    """Python de la venv pour lancer un outil à part (capture d'écran).

    Sous Linux et macOS, `.venv/bin/python` (config.VENV_PYTHON, inchangé) ;
    sous Windows la venv range Python dans `Scripts\\python.exe` : à défaut du
    chemin de config, le Python en cours (celui de la venv, qui fait tourner
    Astrel ou le serveur MCP)."""
    from .. import config

    venv = Path(config.VENV_PYTHON)
    return str(venv) if venv.exists() else sys.executable


def camera_api(system: str | None = None) -> int:
    """Pilote vidéo d'OpenCV : V4L2 sous Linux (d'origine), AVFoundation sous
    macOS, DirectShow sous Windows, sinon le choix d'OpenCV."""
    import cv2

    system = system or SYSTEM
    name = {"linux": "CAP_V4L2", "macos": "CAP_AVFOUNDATION", "windows": "CAP_DSHOW"}.get(system, "CAP_ANY")
    return int(getattr(cv2, name, getattr(cv2, "CAP_ANY", 0)))


def console_utf8() -> None:
    """03/11 (CI Windows) : sortie redirigée = encodage du système (cp1252 sous Windows), qui plante sur
    « → » ou « ✓ ». Toujours UTF-8 ; un caractère impossible devient « ? » au lieu d'arrêter Astrel."""
    for flux in (sys.stdout, sys.stderr):
        try:
            if flux is not None and (flux.encoding or "").lower().replace("-", "") != "utf8":
                flux.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def lancer_detache(cmd: list[str]) -> None:
    """Programme d'Astrel lancé à part, qui survit à celui qui le lance (bulle, raccourcis)."""
    import subprocess

    from .. import config

    kw: dict = {"cwd": str(config.ROOT), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL}
    if SYSTEM == "windows":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED, NEW_PROCESS_GROUP, NO_WINDOW
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, **kw)


def ouvrir_hud() -> None:
    """Le HUD, comme `./hud.sh` sous Linux : `python -m astrel --hud`, détaché."""
    lancer_detache([python_exe(), "-m", "astrel", "--hud"])


def allumer_astrel() -> None:
    lancer_detache([python_exe(), "-m", "astrel"])
