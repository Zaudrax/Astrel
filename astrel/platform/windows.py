"""Windows : actions sur le bureau par l'API Windows (ctypes, winreg),
`os.startfile` et Windows PowerShell 5.1 (livré avec Windows 10 et 11).

⚠️ NON VÉRIFIÉ sur un vrai Windows au 17/10/2026 (écrit d'après la
documentation de Microsoft, testé seulement avec des appels simulés).

Le texte venu de Claude n'est JAMAIS collé dans un script PowerShell : il passe
par une variable d'environnement ou l'entrée standard, donc il ne peut pas
devenir du code.

Limites dites telles quelles :
- volume : Windows n'a pas d'outil en ligne de commande ; on simule les touches
  de volume du clavier (pas de 2 %), le niveau n'est donc qu'approché et ne peut
  pas être relu ; « muet » BASCULE (Windows ne dit pas l'état) ;
- médias : touches multimédia, « play » et « pause » basculent tous deux ;
- Wi-Fi : couper la carte exige les droits administrateur, refusé ici.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from . import unavailable
from ._commun import best_match, check_media, check_path, check_power, check_url, run, spawn

_PS = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]
_UTF8 = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; [Console]::InputEncoding = [Text.Encoding]::UTF8; "


def _ps(script: str, env: dict | None = None, input: str | None = None, timeout: float = 15.0) -> str:
    return run(_PS + [_UTF8 + script], env=env, input=input, timeout=timeout)


# Touches virtuelles (winuser.h)
VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF
VK_MEDIA_NEXT, VK_MEDIA_PREV, VK_MEDIA_STOP, VK_MEDIA_PLAY_PAUSE = 0xB0, 0xB1, 0xB2, 0xB3
_KEYUP = 0x0002


def _press(vk: int, times: int = 1) -> None:
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    for _ in range(max(0, times)):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, _KEYUP, 0)


# --- ouvrir ---

def open_url(url: str) -> str:
    url = check_url(url)
    os.startfile(url)  # type: ignore[attr-defined]
    return f"Ouvert : {url}"


def open_path(path: str) -> str:
    p = check_path(path)
    os.startfile(str(p))  # type: ignore[attr-defined]
    return f"Ouvert : {p.name}"


def _start_menu() -> list[Path]:
    dirs = []
    for var in ("ProgramData", "APPDATA"):
        base = os.environ.get(var)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return dirs


def _apps() -> dict[str, str]:
    """raccourci .lnk du menu Démarrer → nom affiché."""
    out: dict[str, str] = {}
    for d in _start_menu():
        if d.is_dir():
            for lnk in d.rglob("*.lnk"):
                if "uninstall" not in lnk.stem.lower() and "désinstaller" not in lnk.stem.lower():
                    out[str(lnk)] = lnk.stem
    return out


def open_app(name: str) -> str:
    found = best_match(name, _apps())
    if found is None:
        raise LookupError(f"aucune application ne correspond à « {name} »")
    lnk, label = found
    os.startfile(lnk)  # type: ignore[attr-defined]
    return f"{label} lancé."


def list_apps(filter_text: str = "") -> list[str]:
    f = filter_text.strip().lower()
    return sorted({label for label in _apps().values() if f in label.lower()})[:80]


# --- notifications ---

_TOAST = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
    "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
    "$n = $t.GetElementsByTagName('text'); "
    "$n.Item(0).AppendChild($t.CreateTextNode($env:ASTREL_TITRE)) | Out-Null; "
    "$n.Item(1).AppendChild($t.CreateTextNode($env:ASTREL_TEXTE)) | Out-Null; "
    "$toast = [Windows.UI.Notifications.ToastNotification]::new($t); "
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($toast)"
)


def notify(title: str, body: str = "", urgent: bool = False) -> str:
    _ps(_TOAST, env={"ASTREL_TITRE": title[:120], "ASTREL_TEXTE": body[:500]})
    return "Notification affichée."


# --- son ---

def set_volume(level: int | None = None, mute: bool | None = None, delta: int | None = None) -> str:
    """Par les touches de volume (pas de 2 %) : approché, et non relisible."""
    said = []
    if mute is not None:
        _press(VK_VOLUME_MUTE)
        said.append("son muet basculé (Windows ne dit pas s'il est coupé)")
    if level is not None:
        level = max(0, min(100, int(level)))
        _press(VK_VOLUME_DOWN, 50)
        _press(VK_VOLUME_UP, round(level / 2))
        said.append(f"volume réglé à environ {level} %")
    elif delta:
        _press(VK_VOLUME_UP if delta > 0 else VK_VOLUME_DOWN, max(1, round(abs(int(delta)) / 2)))
        said.append(f"volume {'monté' if delta > 0 else 'baissé'} d'environ {abs(int(delta))} %")
    if not said:
        return get_volume()
    return said[0][0].upper() + "; ".join(said)[1:] + "."


def get_volume() -> str:
    raise unavailable("Lecture du volume", "Windows ne l'expose pas sans bibliothèque en plus", system="windows")


# --- écran ---

def set_brightness(level: int) -> str:
    level = max(1, min(100, int(level)))
    try:
        _ps("Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | "
            "Invoke-CimMethod -MethodName WmiSetBrightness "
            "-Arguments @{Timeout=[uint32]1; Brightness=[byte]$env:ASTREL_NIVEAU} | Out-Null",
            env={"ASTREL_NIVEAU": str(level)})
    except RuntimeError as exc:
        raise RuntimeError(f"luminosité non réglable (seulement sur un écran intégré) : {exc}") from exc
    return f"Luminosité à {level} %."


def set_dark_mode(dark: bool) -> str:
    import winreg  # type: ignore[import-not-found]

    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", 0, winreg.KEY_SET_VALUE)
    try:
        for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0 if dark else 1)
    finally:
        winreg.CloseKey(key)
    return "Thème sombre activé." if dark else "Thème clair activé."


def lock_screen() -> str:
    import ctypes

    if not ctypes.windll.user32.LockWorkStation():  # type: ignore[attr-defined]
        raise RuntimeError("verrouillage refusé par Windows")
    return "Session verrouillée."


# --- médias ---

_MEDIA_KEYS = {"play": VK_MEDIA_PLAY_PAUSE, "pause": VK_MEDIA_PLAY_PAUSE, "toggle": VK_MEDIA_PLAY_PAUSE,
               "next": VK_MEDIA_NEXT, "previous": VK_MEDIA_PREV, "stop": VK_MEDIA_STOP}


def media_control(action: str) -> str:
    check_media(action)
    _press(_MEDIA_KEYS[action])
    if action in ("play", "pause"):
        return "Lecture / pause basculée (touche multimédia)."
    return f"{action} envoyé (touche multimédia)."


# --- presse-papiers ---

def clipboard_read() -> str:
    return _ps("Get-Clipboard -Raw")[:20000]


def clipboard_write(text: str) -> str:
    _ps("Set-Clipboard -Value ([Console]::In.ReadToEnd())", input=text, timeout=10)
    return "Copié dans le presse-papiers."


# --- réseau et alimentation (confirmés par le garde-fou) ---

def wifi_toggle(on: bool) -> str:
    raise unavailable("Allumer ou couper le Wi-Fi", "Windows exige les droits administrateur", system="windows")


def power_action(action: str) -> str:
    """suspend, poweroff, reboot — TOUJOURS confirmé (garde-fou)."""
    check_power(action)
    if action == "suspend":
        # Si la mise en veille prolongée est activée, Windows hiberne au lieu de suspendre.
        spawn(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
    else:
        spawn(["shutdown", "/s" if action == "poweroff" else "/r", "/t", "0"])
    return f"{action} lancé."


def windows_summary() -> str:
    out = _ps("Get-Process | Where-Object { $_.MainWindowTitle } | "
              "Select-Object -First 40 ProcessName, MainWindowTitle | ConvertTo-Json -Compress")
    try:
        items = json.loads(out or "[]")
    except ValueError:
        items = []
    if isinstance(items, dict):
        items = [items]
    return json.dumps({"disponible": True, "fenetres": [
        {"app": i.get("ProcessName", ""), "titre": i.get("MainWindowTitle", "")} for i in items]},
        ensure_ascii=False)


# --- démarrage automatique et raccourcis (03/11/2026, portage) ---
# Démarrage : deux raccourcis dans le dossier « Démarrage » du menu Démarrer de l'utilisateur (aucun droit
# d'administrateur), qui lancent `pythonw` (sans fenêtre de console) : Astrel, et la bulle Qt.
# Raccourcis clavier : écoutés par le cœur (`platform/raccourcis.py`, pynput).

_LIENS = {"service": ("Astrel.lnk", "-X utf8 -m astrel"), "bubble": ("Astrel bulle.lnk", "-X utf8 -m astrel.bulle_qt")}


def _demarrage() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / \
        "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _pythonw() -> Path:
    from .. import config
    return Path(config.VENV_PYTHON).with_name("pythonw.exe")


def autostart_state() -> dict:
    """{"service": Astrel lancé à l'ouverture de session ?, "bubble": bulle aussi ?}."""
    if not _pythonw().exists():
        raise unavailable("Démarrage automatique", "Astrel n'est pas installé : lance install.ps1", system="windows")
    return {k: (_demarrage() / nom).exists() for k, (nom, _a) in _LIENS.items()}


def _lien(cle: str, on: bool) -> None:
    from .. import config
    nom, args = _LIENS[cle]
    chemin = _demarrage() / nom
    if not on:
        chemin.unlink(missing_ok=True)
        return
    chemin.parent.mkdir(parents=True, exist_ok=True)
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:ASTREL_LIEN); "
              "$s.TargetPath = $env:ASTREL_CIBLE; $s.Arguments = $env:ASTREL_ARGS; "
              "$s.WorkingDirectory = $env:ASTREL_DOSSIER; $s.WindowStyle = 7; $s.Save()")
    env = {**os.environ, "ASTREL_LIEN": str(chemin), "ASTREL_CIBLE": str(_pythonw()), "ASTREL_ARGS": args,
           "ASTREL_DOSSIER": str(config.ROOT)}
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env, check=True,
                   capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def set_autostart(on: bool, bubble: bool | None = None) -> str:
    """Comme sous Linux : `bubble` None = Astrel seul ; sinon la bulle seule (Astrel inchangé)."""
    autostart_state()  # lève Indisponible si rien à régler
    if bubble is None:
        _lien("service", on)
        return "Astrel démarrera avec ta session." if on else "Astrel ne démarrera plus avec ta session."
    _lien("bubble", bubble)
    return "La bulle s'ouvrira avec ta session." if bubble else "La bulle ne s'ouvrira plus avec ta session."


def hotkeys_state() -> bool:
    from . import raccourcis
    return raccourcis.actif()


def set_hotkeys(on: bool) -> str:
    from . import raccourcis
    return raccourcis.regler(on)
