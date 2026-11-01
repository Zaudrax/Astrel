"""Linux (GNOME, PipeWire, systemd) : actions sur le bureau — ouvrir, notifier,
volume, luminosité, thème, médias, presse-papiers, verrouillage, alimentation,
Wi-Fi.

Code d'origine de `astrel/tools/desktop.py`, extrait SANS changement de
comportement (P2, lot 4) : c'est la machine de référence.

Règles (communes à tous les systèmes) :
- jamais de `shell=True` : chaque commande est une liste d'arguments, un nom
  d'application ou une URL ne peut donc pas injecter une commande ;
- chaque fonction rend une phrase courte, lisible par Claude et prononçable ;
- ce qui n'existe pas sur la machine est dit franchement.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


def _run(cmd: list[str], timeout: float = 10.0, check: bool = True) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"{cmd[0]} a échoué").strip()[:300])
    return proc.stdout.strip()


def _spawn(cmd: list[str]) -> None:
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


# --- ouvrir ---

def open_url(url: str) -> str:
    if not re.match(r"^(https?|mailto):", url.strip(), re.I):
        raise ValueError("seules les adresses http(s) et mailto sont ouvertes")
    _spawn(["xdg-open", url.strip()])
    return f"Ouvert : {url}"


def open_path(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"introuvable : {p}")
    _spawn(["xdg-open", str(p)])
    return f"Ouvert : {p.name}"


def _desktop_entries() -> dict[str, tuple[str, str]]:
    """id .desktop → (nom affiché, id), pour trouver une app par son nom."""
    dirs = [Path("/usr/share/applications"), Path.home() / ".local/share/applications",
            Path("/var/lib/flatpak/exports/share/applications"),
            Path.home() / ".local/share/flatpak/exports/share/applications"]
    out: dict[str, tuple[str, str]] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for f in d.glob("*.desktop"):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "NoDisplay=true" in text:
                continue
            m = re.search(r"^Name(?:\[fr\])?=(.+)$", text, re.M)
            name = m.group(1).strip() if m else f.stem
            out[f.stem] = (name, f.stem)
    return out


def open_app(name: str) -> str:
    """Lance une application par son nom (« Firefox », « terminal », « VS Code »…)."""
    wanted = name.strip().lower()
    entries = _desktop_entries()
    scored = []
    for entry_id, (label, _) in entries.items():
        low_label, low_id = label.lower(), entry_id.lower()
        if wanted in (low_label, low_id):
            score = 0
        elif low_label.startswith(wanted) or wanted in low_id.split("."):
            score = 1
        elif wanted in low_label or wanted in low_id:
            score = 2
        else:
            continue
        scored.append((score, len(label), entry_id, label))
    if not scored:
        raise LookupError(f"aucune application ne correspond à « {name} »")
    _, _, entry_id, label = sorted(scored)[0]
    _spawn(["gtk-launch", entry_id])
    return f"{label} lancé."


def list_apps(filter_text: str = "") -> list[str]:
    f = filter_text.strip().lower()
    return sorted({label for label, _ in _desktop_entries().values() if f in label.lower()})[:80]


# --- notifications ---

def notify(title: str, body: str = "", urgent: bool = False) -> str:
    # 24/10, plan P31 lot D : nom affiché (Astrel par défaut) et icône du lanceur (`astrel-hud`,
    # posée par install.py). Import tardif : config importe ce paquet.
    from ..i18n import assistant_name
    cmd = ["notify-send", f"--app-name={assistant_name()}", "--icon=astrel-hud"]
    if urgent:
        cmd.append("--urgency=critical")
    _run(cmd + [title[:120], body[:500]])
    return "Notification affichée."


# --- son ---

def set_volume(level: int | None = None, mute: bool | None = None, delta: int | None = None) -> str:
    """Volume de la sortie par défaut, en pourcents (0-150)."""
    sink = "@DEFAULT_AUDIO_SINK@"
    if mute is not None:
        _run(["wpctl", "set-mute", sink, "1" if mute else "0"])
    if level is not None:
        level = max(0, min(150, int(level)))
        _run(["wpctl", "set-volume", sink, f"{level / 100:.2f}"])
    elif delta:
        sign = "+" if delta > 0 else "-"
        _run(["wpctl", "set-volume", "-l", "1.5", sink, f"{abs(int(delta)) / 100:.2f}{sign}"])
    return get_volume()


def get_volume() -> str:
    out = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])  # « Volume: 0.45 [MUTED] »
    m = re.search(r"([\d.]+)", out)
    pct = round(float(m.group(1)) * 100) if m else 0
    return f"Volume à {pct} %" + (", coupé" if "MUTED" in out else "") + "."


# --- écran ---

def set_brightness(level: int) -> str:
    """Luminosité de l'écran intégré, en pourcents, par le démon GNOME."""
    level = max(1, min(100, int(level)))
    _run(["gdbus", "call", "--session", "--dest", "org.gnome.SettingsDaemon.Power",
          "--object-path", "/org/gnome/SettingsDaemon/Power",
          "--method", "org.freedesktop.DBus.Properties.Set",
          "org.gnome.SettingsDaemon.Power.Screen", "Brightness", f"<int32 {level}>"])
    return f"Luminosité à {level} %."


def set_dark_mode(dark: bool) -> str:
    _run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme",
          "prefer-dark" if dark else "default"])
    return "Thème sombre activé." if dark else "Thème clair activé."


def lock_screen() -> str:
    _run(["loginctl", "lock-session"])
    return "Session verrouillée."


# --- médias (MPRIS) ---

def _players() -> list[str]:
    out = _run(["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
                "--object-path", "/org/freedesktop/DBus", "--method", "org.freedesktop.DBus.ListNames"])
    return re.findall(r"'(org\.mpris\.MediaPlayer2\.[^']+)'", out)


def media_control(action: str) -> str:
    """play, pause, toggle, next, previous, stop — sur le lecteur actif."""
    methods = {"play": "Play", "pause": "Pause", "toggle": "PlayPause", "next": "Next",
               "previous": "Previous", "stop": "Stop"}
    if action not in methods:
        raise ValueError(f"action inconnue : {action} (attendu : {', '.join(methods)})")
    players = _players()
    if not players:
        return "Aucun lecteur multimédia ouvert."
    target = players[0]
    _run(["gdbus", "call", "--session", "--dest", target, "--object-path", "/org/mpris/MediaPlayer2",
          "--method", f"org.mpris.MediaPlayer2.Player.{methods[action]}"])
    return f"{action} sur {target.rsplit('.', 1)[-1]}."


# --- presse-papiers ---

def clipboard_read() -> str:
    if not shutil.which("xclip"):
        raise RuntimeError("xclip absent")
    return _run(["xclip", "-selection", "clipboard", "-o"], check=False)[:20000]


def clipboard_write(text: str) -> str:
    proc = subprocess.run(["xclip", "-selection", "clipboard", "-i"], input=text, text=True, timeout=5)
    if proc.returncode != 0:
        raise RuntimeError("écriture du presse-papiers impossible")
    return "Copié dans le presse-papiers."


# --- réseau et alimentation (confirmés par le garde-fou) ---

def wifi_toggle(on: bool) -> str:
    _run(["nmcli", "radio", "wifi", "on" if on else "off"])
    return "Wi-Fi activé." if on else "Wi-Fi coupé."


def power_action(action: str) -> str:
    """suspend, poweroff, reboot — TOUJOURS confirmé par l'utilisateur (garde-fou)."""
    allowed = {"suspend": "suspend", "poweroff": "poweroff", "reboot": "reboot"}
    if action not in allowed:
        raise ValueError("action attendue : suspend, poweroff ou reboot")
    _spawn(["systemctl", allowed[action]])
    return f"{action} lancé."


def windows_summary() -> str:
    """Fenêtres ouvertes. Sous Wayland, GNOME ne les expose pas aux applications :
    on le dit plutôt que d'inventer."""
    return json.dumps({"disponible": False, "raison": "GNOME Wayland n'expose pas la liste des fenêtres"})


# --- démarrage automatique et raccourcis (17/10, plan P4, lot 8 : page « Réglages ») ---
# Service `astrel.service` de systemd --user (posé par install.py), bulle par son propre service
# `astrel-bubble.service` (29/10 : plus l'autostart GNOME `astrel-bubble.desktop`, qui en ouvrait une
# seconde ; celui-ci est désactivé dès qu'on règle la bulle), raccourcis GNOME par hotkeys.sh.
# Sans systemd (WSL2 par défaut) ou hors GNOME : `Indisponible`, jamais une erreur.

_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"


def _unavailable(what: str, why: str):
    from . import unavailable

    return unavailable(what, why, system="linux")


def _root() -> Path:
    from .. import config

    return Path(config.ROOT)


def _has_systemd() -> bool:
    return bool(shutil.which("systemctl")) and Path("/run/systemd/system").exists()


def _unit() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "astrel.service"


def _bubble_unit() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "astrel-bubble.service"


def _bubble_file() -> Path:
    """Ancien lanceur de la bulle (autostart GNOME) : seulement désactivé, jamais réécrit."""
    return Path.home() / ".config" / "autostart" / "astrel-bubble.desktop"


def _enabled(unit: str) -> bool:
    return subprocess.run(["systemctl", "--user", "is-enabled", unit], capture_output=True,
                          text=True, timeout=10).stdout.strip() == "enabled"


def _disable_bubble_file() -> None:
    path = _bubble_file()
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"(?m)^X-GNOME-Autostart-enabled=.*$", "X-GNOME-Autostart-enabled=false", text)
    if "X-GNOME-Autostart-enabled=false" not in text:
        text = text.rstrip("\n") + "\nX-GNOME-Autostart-enabled=false\n"
    path.write_text(text, encoding="utf-8")


def _gnome() -> bool:
    import os

    return bool(shutil.which("gsettings")) and "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()


def autostart_state() -> dict:
    """{"service": lancé à l'ouverture de session ?, "bubble": bulle aussi ?}."""
    if not _has_systemd():
        raise _unavailable("Démarrage automatique", "systemd absent (WSL2 sans systemd ?)")
    if not _unit().exists():
        raise _unavailable("Démarrage automatique", "service non installé : lance python3 install.py")
    return {"service": _enabled("astrel.service"),
            "bubble": _bubble_unit().exists() and _enabled("astrel-bubble.service")}


def set_autostart(on: bool, bubble: bool | None = None) -> str:
    """Service au démarrage de session (enable/disable, sans l'arrêter ni le lancer) ;
    `bubble` : la bulle aussi (service `astrel-bubble.service`, posé d'après systemd/ s'il manque ;
    enable/disable sans l'arrêter ni la lancer). L'ancien autostart GNOME est désactivé dans les deux cas."""
    autostart_state()  # lève Indisponible si rien à régler
    if bubble is None:
        _run(["systemctl", "--user", "enable" if on else "disable", "astrel.service"])
        return "Astrel démarrera avec ta session." if on else "Astrel ne démarrera plus avec ta session."
    _disable_bubble_file()
    unit = _bubble_unit()
    if bubble:
        if not unit.exists():
            template = (_root() / "systemd" / "astrel-bubble.service").read_text(encoding="utf-8")
            unit.write_text(template.replace("@ROOT@", str(_root())), encoding="utf-8")
            _run(["systemctl", "--user", "daemon-reload"])
        _run(["systemctl", "--user", "enable", "astrel-bubble.service"])
        return "La bulle s'ouvrira avec ta session."
    if unit.exists():
        _run(["systemctl", "--user", "disable", "astrel-bubble.service"])
    return "La bulle ne s'ouvrira plus avec ta session."


def hotkeys_state() -> bool:
    """Raccourcis de hotkeys.sh posés (Ctrl+Alt+J…) ?"""
    if not _gnome():
        raise _unavailable("Raccourcis clavier", "GNOME seulement")
    cur = _run(["gsettings", "get", _SCHEMA, "custom-keybindings"], check=False)
    return "/astrel-wake/" in cur


def set_hotkeys(on: bool) -> str:
    hotkeys_state()  # lève Indisponible hors GNOME
    script = _root() / "hotkeys.sh"
    _run([str(script)] if on else [str(script), "--remove"], timeout=30)
    return ("Raccourcis posés : Ctrl+Alt+J parler, Ctrl+Alt+M micro, Ctrl+Alt+X interrompre."
            if on else "Raccourcis retirés.")
