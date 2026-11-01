"""macOS : actions sur le bureau par les outils livrés avec le système
(`open`, `osascript`, `pbcopy`/`pbpaste`, `pmset`, `networksetup`).

⚠️ NON VÉRIFIÉ sur un vrai Mac au 17/10/2026 (écrit d'après la documentation
d'Apple, testé seulement avec des commandes simulées). Certaines actions
demandent une autorisation la première fois (Réglages Système → Confidentialité
et sécurité → Automatisation) : macOS affiche alors sa propre fenêtre.

Le texte venu de Claude n'est JAMAIS collé dans un script AppleScript : il passe
en argument (`on run argv`), donc il ne peut pas devenir du code.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import unavailable
from ._commun import best_match, check_media, check_path, check_power, check_url, run, spawn


def _osascript(lines: list[str], *args: str, timeout: float = 10.0) -> str:
    cmd = ["osascript"]
    for line in lines:
        cmd += ["-e", line]
    return run(cmd + list(args), timeout=timeout)


# --- ouvrir ---

def open_url(url: str) -> str:
    url = check_url(url)
    spawn(["open", url])
    return f"Ouvert : {url}"


def open_path(path: str) -> str:
    p = check_path(path)
    spawn(["open", str(p)])
    return f"Ouvert : {p.name}"


def _app_dirs() -> list[Path]:
    return [Path("/Applications"), Path("/Applications/Utilities"), Path("/System/Applications"),
            Path("/System/Applications/Utilities"), Path.home() / "Applications"]


def _apps() -> dict[str, str]:
    """chemin du .app → nom affiché."""
    out: dict[str, str] = {}
    for d in _app_dirs():
        if d.is_dir():
            for app in d.glob("*.app"):
                out[str(app)] = app.stem
    return out


def open_app(name: str) -> str:
    found = best_match(name, _apps())
    if found is None:
        raise LookupError(f"aucune application ne correspond à « {name} »")
    path, label = found
    spawn(["open", path])
    return f"{label} lancé."


def list_apps(filter_text: str = "") -> list[str]:
    f = filter_text.strip().lower()
    return sorted({label for label in _apps().values() if f in label.lower()})[:80]


# --- notifications ---

def notify(title: str, body: str = "", urgent: bool = False) -> str:
    lines = ["on run argv", "display notification (item 2 of argv) with title (item 1 of argv)"]
    if urgent:
        lines[1] += ' sound name "Glass"'
    _osascript(lines + ["end run"], title[:120], body[:500])
    return "Notification affichée."


# --- son ---

def _volume_settings() -> tuple[int, bool]:
    out = _osascript(["set s to get volume settings",
                      'return ((output volume of s) as text) & "," & ((output muted of s) as text)'])
    level, _, muted = out.partition(",")
    try:
        pct = int(float(level))
    except ValueError:
        pct = 0
    return pct, muted.strip() == "true"


def set_volume(level: int | None = None, mute: bool | None = None, delta: int | None = None) -> str:
    """Volume de la sortie, en pourcents. macOS plafonne à 100 (Linux va à 150)."""
    if mute is not None:
        _osascript([f"set volume output muted {'true' if mute else 'false'}"])
    if level is not None:
        level = max(0, min(100, int(level)))
        _osascript([f"set volume output volume {level}"])
    elif delta:
        current, _ = _volume_settings()
        _osascript([f"set volume output volume {max(0, min(100, current + int(delta)))}"])
    return get_volume()


def get_volume() -> str:
    pct, muted = _volume_settings()
    return f"Volume à {pct} %" + (", coupé" if muted else "") + "."


# --- écran ---

def set_brightness(level: int) -> str:
    level = max(1, min(100, int(level)))
    import shutil

    # macOS n'a pas d'outil livré pour la luminosité ; `brightness` (Homebrew) si présent.
    if not shutil.which("brightness"):
        raise unavailable("Réglage de la luminosité",
                          "aucun outil livré avec macOS ; installe « brightness » avec Homebrew",
                          system="macos")
    run(["brightness", f"{level / 100:.2f}"])
    return f"Luminosité à {level} %."


def set_dark_mode(dark: bool) -> str:
    _osascript(['tell application "System Events" to tell appearance preferences to '
                f"set dark mode to {'true' if dark else 'false'}"])
    return "Thème sombre activé." if dark else "Thème clair activé."


def lock_screen() -> str:
    # Met l'écran en veille ; il est verrouillé si « exiger le mot de passe
    # immédiatement » est réglé (défaut de macOS récents).
    run(["pmset", "displaysleepnow"])
    return "Écran mis en veille (verrouillé si le mot de passe est exigé au réveil)."


# --- médias ---

_MEDIA_APPS = ("Spotify", "Music")
_MEDIA_VERBS = {"play": "play", "pause": "pause", "toggle": "playpause", "next": "next track",
                "previous": "previous track", "stop": "pause"}


def media_control(action: str) -> str:
    """play, pause, toggle, next, previous, stop — sur Spotify ou Musique s'ils tournent."""
    check_media(action)
    for app in _MEDIA_APPS:
        running = _osascript([f'return application "{app}" is running']) == "true"
        if running:
            _osascript([f'tell application "{app}" to {_MEDIA_VERBS[action]}'])
            return f"{action} sur {app}."
    return "Aucun lecteur multimédia ouvert (Spotify ou Musique)."


# --- presse-papiers ---

def clipboard_read() -> str:
    return run(["pbpaste"], check=False)[:20000]


def clipboard_write(text: str) -> str:
    run(["pbcopy"], input=text, timeout=5, env={"LANG": "en_US.UTF-8"})
    return "Copié dans le presse-papiers."


# --- réseau et alimentation (confirmés par le garde-fou) ---

def _wifi_device() -> str:
    out = run(["networksetup", "-listallhardwareports"])
    m = re.search(r"Hardware Port: (?:Wi-Fi|AirPort)\s*\nDevice: (\S+)", out)
    if not m:
        raise RuntimeError("aucune carte Wi-Fi trouvée")
    return m.group(1)


def wifi_toggle(on: bool) -> str:
    run(["networksetup", "-setairportpower", _wifi_device(), "on" if on else "off"])
    return "Wi-Fi activé." if on else "Wi-Fi coupé."


def power_action(action: str) -> str:
    """suspend, poweroff, reboot — TOUJOURS confirmé (garde-fou)."""
    check_power(action)
    if action == "suspend":
        spawn(["pmset", "sleepnow"])
    else:
        verb = "shut down" if action == "poweroff" else "restart"
        spawn(["osascript", "-e", f'tell application "System Events" to {verb}'])
    return f"{action} lancé."


def windows_summary() -> str:
    return json.dumps({"disponible": False,
                       "raison": "macOS ne donne la liste des fenêtres qu'avec l'autorisation d'accessibilité"})


# --- démarrage automatique et raccourcis (17/10, plan P4, lot 8) : pas encore portés ---

def autostart_state() -> dict:
    raise unavailable("Démarrage automatique", "pas encore porté", system="macos")


def set_autostart(on: bool, bubble: bool | None = None) -> str:
    raise unavailable("Démarrage automatique", "pas encore porté", system="macos")


def hotkeys_state() -> bool:
    raise unavailable("Raccourcis clavier", "pas encore portés", system="macos")


def set_hotkeys(on: bool) -> str:
    raise unavailable("Raccourcis clavier", "pas encore portés", system="macos")
