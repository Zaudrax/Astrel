"""Navigateur du HUD hors Linux (04/11/2026, lot de sécurité B, H5, accord de l'utilisateur).

Sous Linux, `cli.open_hud` lance Chrome ou Chromium trouvé dans le PATH, en mode application, avec le
profil DÉDIÉ `runtime/hud-profile` (le cookie du HUD n'y côtoie ni les extensions ni les sessions de tous
les jours). Sous Windows et macOS, ces navigateurs ne sont presque jamais dans le PATH : on passait au
navigateur PAR DÉFAUT, dans le profil de tous les jours, où une extension qui pilote le navigateur agirait
comme l'utilisateur. Ici : Chrome, Edge ou Chromium cherchés à leurs emplacements habituels, lancés de la même
façon (`--app`, `--user-data-dir`). Aucun trouvé : l'appelant ouvre le navigateur par défaut et
l'écrit au journal.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import SYSTEM

# Windows : (variable d'environnement de la racine, chemin dessous), dans l'ordre de préférence.
_WINDOWS = (
    ("PROGRAMFILES", ("Google", "Chrome", "Application", "chrome.exe")),
    ("PROGRAMFILES(X86)", ("Google", "Chrome", "Application", "chrome.exe")),
    ("LOCALAPPDATA", ("Google", "Chrome", "Application", "chrome.exe")),
    ("PROGRAMFILES(X86)", ("Microsoft", "Edge", "Application", "msedge.exe")),
    ("PROGRAMFILES", ("Microsoft", "Edge", "Application", "msedge.exe")),
    ("LOCALAPPDATA", ("Chromium", "Application", "chrome.exe")),
    ("PROGRAMFILES", ("Chromium", "Application", "chrome.exe")),
)
# macOS : applications, pour tous puis pour l'utilisateur.
_MACOS = (
    ("Google Chrome.app", "Google Chrome"),
    ("Microsoft Edge.app", "Microsoft Edge"),
    ("Chromium.app", "Chromium"),
)
_PATH = {"windows": ("chrome", "msedge", "chromium"), "macos": ("google-chrome", "chromium")}


def candidats(system: str | None = None, env: dict | None = None, home: Path | None = None) -> list[str]:
    """Chemins possibles du navigateur du HUD, dans l'ordre de préférence (existants ou non)."""
    system = system or SYSTEM
    env = os.environ if env is None else env
    out: list[str] = []
    if system == "windows":
        for var, parts in _WINDOWS:
            base = env.get(var) or ""  # os.environ de Windows ignore la casse des noms
            if base:
                out.append(os.path.join(base, *parts))
    elif system == "macos":
        home = home or Path.home()
        for root in (Path("/Applications"), home / "Applications"):
            for app, exe in _MACOS:
                out.append(str(root / app / "Contents" / "MacOS" / exe))
    return list(dict.fromkeys(out))


def trouver(system: str | None = None, env: dict | None = None, home: Path | None = None) -> str | None:
    """Le navigateur à profil dédié du HUD sous Windows ou macOS (Chrome, Edge, Chromium), ou None."""
    system = system or SYSTEM
    if system not in ("windows", "macos"):
        return None
    for path in candidats(system, env, home):
        if os.path.isfile(path):
            return path
    for name in _PATH[system]:
        found = shutil.which(name)
        if found:
            return found
    return None


def commande(exe: str, url: str, profil: Path) -> list[str]:
    """Fenêtre d'application, maximisée, dans le profil DÉDIÉ du HUD (jamais celui de tous les jours)."""
    return [exe, f"--app={url}", "--start-maximized", "--window-size=1600,1000", f"--user-data-dir={profil}"]
