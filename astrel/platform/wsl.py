"""WSL2 (03/11/2026, portage, décision de l'utilisateur : WSL2 « sans le bureau » + petit pont vers Windows).

Sous WSL2, Astrel fait tourner son code Linux, mais le bureau est celui de Windows : ouvrir une page,
un dossier ou une application, notifier, le presse-papiers, les touches multimédia, le verrouillage,
la liste des fenêtres et la mise en veille passent donc par les programmes de Windows, joignables
depuis WSL (`powershell.exe`, `explorer.exe`, `rundll32.exe`). Pièges propres à WSL : une variable
d'environnement n'atteint un programme Windows que si `WSLENV` la nomme ; un chemin se traduit par
`wslpath -w`. Le reste (démarrage automatique par systemd, raccourcis) reste celui de Linux ; ce
qui n'a pas de sens ici le dit (`unavailable`). Jamais lancé sur un vrai WSL2.
"""
from __future__ import annotations

from . import linux as _linux
from . import unavailable
from . import windows as _win
from ._commun import best_match, check_media, check_path, check_power, check_url, run, spawn

_PS = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]


def _ps(script: str, variables: dict | None = None, input: str | None = None, timeout: float = 15.0) -> str:
    """PowerShell de Windows ; `variables` lui sont transmises par WSLENV."""
    env = None
    if variables:
        env = {**variables, "WSLENV": ":".join(variables)}
    return run(_PS + [_win._UTF8 + script], env=env, input=input, timeout=timeout)


# --- ouvrir ---

def open_url(url: str) -> str:
    url = check_url(url)
    _ps("Start-Process $env:ASTREL_URL", {"ASTREL_URL": url})
    return f"Ouvert : {url}"


def open_path(path: str) -> str:
    p = check_path(path)
    spawn(["explorer.exe", run(["wslpath", "-w", str(p)])])
    return f"Ouvert : {p}"


def list_apps(filter_text: str = "") -> list[str]:
    noms = [n for n in _ps("Get-StartApps | ForEach-Object { $_.Name }", timeout=30).splitlines() if n.strip()]
    f = filter_text.lower().strip()
    return sorted({n for n in noms if f in n.lower()})[:200]


def open_app(name: str) -> str:
    lignes = _ps("Get-StartApps | ForEach-Object { $_.Name + \"`t\" + $_.AppID }", timeout=30).splitlines()
    apps = dict(line.split("\t", 1) for line in lignes if "\t" in line)
    trouve = best_match(name, {n: n for n in apps})
    if trouve is None:
        raise RuntimeError(f"application introuvable dans le menu Démarrer de Windows : {name}")
    nom = trouve[0]
    _ps("Start-Process (\"shell:AppsFolder\\\" + $env:ASTREL_APP)", {"ASTREL_APP": apps[nom]})
    return f"Lancé : {nom}"


# --- notifier, presse-papiers ---

def notify(title: str, body: str = "", urgent: bool = False) -> str:
    _ps(_win._TOAST, {"ASTREL_TITRE": title[:120], "ASTREL_TEXTE": body[:500]})
    return "Notification affichée."


def clipboard_read() -> str:
    return _ps("Get-Clipboard -Raw")[:20000]


def clipboard_write(text: str) -> str:
    _ps("Set-Clipboard -Value ([Console]::In.ReadToEnd())", input=text, timeout=10)
    return "Copié dans le presse-papiers."


# --- son et médias : touches virtuelles envoyées par Windows (ctypes de Windows inaccessible ici) ---

def _touches(code: int, fois: int = 1) -> None:
    _ps("$s = New-Object -ComObject WScript.Shell; 1..[int]$env:ASTREL_FOIS | ForEach-Object "
        "{ $s.SendKeys([string][char][int]$env:ASTREL_TOUCHE) }",
        {"ASTREL_TOUCHE": str(code), "ASTREL_FOIS": str(max(1, fois))})


def set_volume(level: int | None = None, mute: bool | None = None, delta: int | None = None) -> str:
    """Par les touches de volume (pas de 2 %), comme sous Windows : approché, et non relisible."""
    if mute is not None:
        _touches(_win.VK_VOLUME_MUTE)
    if level is not None:
        _touches(_win.VK_VOLUME_DOWN, 50)
        _touches(_win.VK_VOLUME_UP, max(0, min(100, int(level))) // 2 or 1)
    elif delta:
        _touches(_win.VK_VOLUME_UP if delta > 0 else _win.VK_VOLUME_DOWN, max(1, abs(int(delta)) // 2))
    return "Volume réglé (touches de volume de Windows)."


def get_volume() -> str:
    raise unavailable("Lecture du volume", "Windows ne l'expose pas sans bibliothèque en plus", system="windows")


def media_control(action: str) -> str:
    check_media(action)
    _touches(_win._MEDIA_KEYS[action])
    return f"{action} envoyé (touche multimédia de Windows)."


# --- écran, session ---

def set_brightness(level: int) -> str:
    level = max(1, min(100, int(level)))
    _ps("Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | "
        "Invoke-CimMethod -MethodName WmiSetBrightness "
        "-Arguments @{Timeout=[uint32]1; Brightness=[byte]$env:ASTREL_NIVEAU} | Out-Null", {"ASTREL_NIVEAU": str(level)})
    return f"Luminosité à {level} %."


def set_dark_mode(dark: bool) -> str:
    valeur = "0" if dark else "1"
    _ps("$k = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize'; "
        "'AppsUseLightTheme','SystemUsesLightTheme' | ForEach-Object "
        "{ Set-ItemProperty -Path $k -Name $_ -Value ([int]$env:ASTREL_CLAIR) -Type DWord }", {"ASTREL_CLAIR": valeur})
    return "Thème sombre activé." if dark else "Thème clair activé."


def lock_screen() -> str:
    spawn(["rundll32.exe", "user32.dll,LockWorkStation"])
    return "Session verrouillée."


def wifi_toggle(on: bool) -> str:
    raise unavailable("Allumer ou couper le Wi-Fi", "Windows exige les droits administrateur", system="windows")


def power_action(action: str) -> str:
    """suspend, poweroff, reboot de WINDOWS — TOUJOURS confirmé (garde-fou)."""
    check_power(action)
    if action == "suspend":
        spawn(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
    else:
        spawn(["shutdown.exe", "/s" if action == "poweroff" else "/r", "/t", "0"])
    return f"{action} lancé."


def windows_summary() -> str:
    import json
    out = _ps("Get-Process | Where-Object { $_.MainWindowTitle } | Select-Object ProcessName, MainWindowTitle | "
              "ConvertTo-Json -Compress", timeout=20)
    try:
        items = json.loads(out or "[]")
    except ValueError:
        items = []
    if isinstance(items, dict):
        items = [items]
    return json.dumps({"disponible": True, "fenetres": [
        {"app": i.get("ProcessName", ""), "titre": i.get("MainWindowTitle", "")} for i in items]}, ensure_ascii=False)


# --- démarrage automatique, raccourcis : ceux de Linux (systemd dans WSL ; raccourcis : GNOME absent) ---

autostart_state = _linux.autostart_state
set_autostart = _linux.set_autostart


def hotkeys_state() -> bool:
    raise unavailable("Raccourcis clavier", "pas sous WSL2 : ils viendront avec Windows natif", system="linux")


def set_hotkeys(on: bool) -> str:
    return hotkeys_state()  # lève Indisponible
