#!/usr/bin/env python3
"""Vérifie qu'une machine peut faire tourner Astrel — AVANT toute installation.

Bibliothèque standard SEULEMENT (aucune dépendance, aucune venv requise) :
`python3 tools/check_install.py` sur Linux et macOS, `py tools\\check_install.py`
sous Windows. Lecture seule : rien n'est installé ni modifié.

Chaque ligne : OK, MANQUE (bloquant : l'installation échouera), ALERTE (Astrel
marchera, mais sans cette fonction) ou INFO. En bas, un « rapport à coller »
sans aucune donnée personnelle (ni e-mail, ni chemin du dossier personnel) :
c'est ce qu'on envoie quand on demande de l'aide.

Code de sortie : 1 s'il manque quelque chose de bloquant, 0 sinon.
`--json` pour une sortie machine, `--ci` en intégration continue (Claude Code
et l'audio ne sont pas exigés).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_MIN = (3, 11)
PY_TESTED = ((3, 11), (3, 12), (3, 13))
DISK_MIN_GB = 6

OK, MANQUE, ALERTE, INFO = "OK", "MANQUE", "ALERTE", "INFO"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""


def system() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform in ("win32", "cygwin"):
        return "windows"
    return "autre"


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False


def linux_distro() -> str:
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError:
        return "Linux"
    m = re.search(r'^PRETTY_NAME="?([^"\n]+)', text, re.M)
    return m.group(1) if m else "Linux"


def _out(cmd: list[str], timeout: float = 15.0) -> tuple[int, str]:
    exe = shutil.which(cmd[0])
    if exe is None:
        return 127, ""
    try:
        proc = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or proc.stderr or "").strip()


def _hint(sys_name: str, linux: str = "", macos: str = "", windows: str = "") -> str:
    return {"linux": linux, "macos": macos, "windows": windows}.get(sys_name, "")


# --- contrôles ---

def check_system(sys_name: str) -> Check:
    if sys_name == "linux":
        name = linux_distro() + (" (WSL2)" if is_wsl() else "")
        status = INFO
        if is_wsl():
            return Check("système", ALERTE, name,
                         "WSL2 : son et fenêtres passent par WSLg (Windows 11) ; micro NON vérifié. Voir le README.")
        if not re.search(r"debian|ubuntu|mint|pop!_os", name, re.I):
            return Check("système", ALERTE, name, "seuls Debian et Ubuntu sont documentés ; les noms de paquets peuvent différer")
        return Check("système", status, name)
    if sys_name == "macos":
        return Check("système", ALERTE, f"macOS {platform.mac_ver()[0]} ({platform.machine()})",
                     "portage macOS EXPÉRIMENTAL : voir l'état dans le README")
    if sys_name == "windows":
        return Check("système", ALERTE, f"Windows {platform.release()} ({platform.machine()})",
                     "portage Windows EXPÉRIMENTAL : voir l'état dans le README (WSL2 conseillé)")
    return Check("système", MANQUE, sys.platform, "système non pris en charge")


def check_python(sys_name: str) -> Check:
    v = sys.version_info[:2]
    label = platform.python_version()
    fix = _hint(sys_name, linux="sudo apt install python3 python3-venv",
                macos="installe Python 3.12 depuis python.org ou « brew install python@3.12 »",
                windows="winget install Python.Python.3.12 (ou python.org), case « Add to PATH » cochée")
    if v < PY_MIN:
        return Check("Python", MANQUE, f"{label} (il faut {PY_MIN[0]}.{PY_MIN[1]} ou plus)", fix)
    if v not in PY_TESTED:
        return Check("Python", ALERTE, f"{label} (versions essayées : 3.11 à 3.13)",
                     "certaines dépendances n'ont peut-être pas encore de version pour ce Python")
    return Check("Python", OK, label)


def check_venv(sys_name: str) -> Check:
    if importlib.util.find_spec("venv") is None or importlib.util.find_spec("ensurepip") is None:
        return Check("module venv", MANQUE, "venv/ensurepip absent",
                     _hint(sys_name, linux="sudo apt install python3-venv"))
    return Check("module venv", OK)


def check_git(sys_name: str) -> Check:
    code, out = _out(["git", "--version"])
    if code != 0:
        return Check("git", MANQUE, "absent (sert à recevoir les mises à jour)",
                     _hint(sys_name, linux="sudo apt install git", macos="xcode-select --install",
                           windows="winget install Git.Git"))
    return Check("git", OK, out.replace("git version ", ""))


def check_claude(sys_name: str, ci: bool) -> list[Check]:
    code, out = _out(["claude", "--version"])
    if code != 0:
        status = INFO if ci else MANQUE
        return [Check("Claude Code", status, "commande « claude » introuvable",
                      "installe Claude Code (https://docs.claude.com/claude-code), puis « claude » pour te connecter")]
    version = out.split()[0] if out else "?"
    res = [Check("Claude Code", OK, version)]
    code, out = _out(["claude", "auth", "status", "--json"], timeout=30)
    try:
        data = json.loads(out) if code == 0 else {}
    except ValueError:
        data = {}
    if data.get("loggedIn"):
        # Ni e-mail ni identifiant d'organisation : seulement le type de connexion.
        kind = data.get("subscriptionType") or data.get("authMethod") or "connecté"
        if data.get("authMethod") not in (None, "claude.ai"):
            # 20/10 (plan P11) : une clé API est un état normal (facturée à l'usage).
            res.append(Check("connexion Claude", OK, f"connecté par {data.get('authMethod')} (clé API, facturée à l'usage)"))
        else:
            res.append(Check("connexion Claude", OK, f"abonnement {kind}"))
    elif data and _fournisseur_choisi() != "abonnement":
        # Astrel tourne sur une clé API (Réglages › Fournisseur) : le compte n'est pas nécessaire.
        res.append(Check("connexion Claude", INFO, "non connecté",
                         f"Astrel utilise « {_fournisseur_choisi()} » (Réglages › Fournisseur)"))
    elif data:
        res.append(Check("connexion Claude", INFO if ci else MANQUE, "non connecté",
                         "lance « claude » une fois dans un terminal et connecte ton compte, "
                         "ou enregistre une clé API dans Réglages › Fournisseur du HUD"))
    else:
        res.append(Check("connexion Claude", ALERTE, "état inconnu (version trop ancienne ?)",
                         "mets Claude Code à jour : « claude update »"))
    return res


def _fournisseur_choisi() -> str:
    """Réglage `fournisseur` d'Astrel (runtime/settings.json, plan P11), lu sans importer astrel."""
    try:
        data = json.loads((ROOT / "runtime" / "settings.json").read_text(encoding="utf-8"))
        value = (data.get("values") or {}).get("fournisseur")
    except (OSError, ValueError, AttributeError):
        value = None
    return str(os.environ.get("ASTREL_FOURNISSEUR") or value or "abonnement")


def check_node(sys_name: str) -> Check:
    code, out = _out(["node", "--version"])
    fix = _hint(sys_name, linux="sudo apt install nodejs npm (ou nvm)", macos="brew install node",
                windows="winget install OpenJS.NodeJS.LTS")
    if code != 0:
        return Check("Node.js", ALERTE, "absent : pas de navigateur piloté (Playwright)", fix)
    m = re.match(r"v(\d+)", out)
    if m and int(m.group(1)) < 18:
        return Check("Node.js", ALERTE, f"{out} (il faut 18 ou plus)", fix)
    if shutil.which("npm") is None:
        return Check("Node.js", ALERTE, f"{out}, mais npm absent", fix)
    return Check("Node.js", OK, out)


def check_audio(sys_name: str, ci: bool) -> list[Check]:
    if sys_name != "linux":
        return [Check("audio", INFO, "micro et haut-parleur par sounddevice (installé avec Astrel)",
                      "macOS demandera l'autorisation du micro au premier lancement" if sys_name == "macos" else "")]
    missing = [t for t in ("parecord", "paplay", "pactl") if shutil.which(t) is None]
    if missing:
        return [Check("audio (PipeWire/PulseAudio)", INFO if ci else MANQUE, "absent : " + ", ".join(missing),
                      "sudo apt install pulseaudio-utils pipewire-pulse")]
    code, out = _out(["pactl", "info"])
    if code != 0:
        return [Check("audio (PipeWire/PulseAudio)", INFO if ci else ALERTE, "serveur de son injoignable",
                      "lance-le depuis ta session graphique, pas en SSH")]
    server = re.search(r"Server Name: (.+)", out)
    res = [Check("audio (PipeWire/PulseAudio)", OK, server.group(1).strip() if server else "")]
    code, out = _out(["pactl", "list", "short", "sources"])
    mics = [line for line in out.splitlines() if line.strip() and ".monitor" not in line]
    res.append(Check("micro", OK if mics else (INFO if ci else ALERTE),
                     f"{len(mics)} source(s)" if mics else "aucun micro vu",
                     "" if mics else "branche un micro, ou vérifie les réglages de son"))
    # 17/10 (plan P4, lot 4) : la sortie aussi, pour la liste du premier lancement du HUD.
    code, out = _out(["pactl", "list", "short", "sinks"])
    sinks = [line for line in out.splitlines() if line.strip()] if code == 0 else []
    res.append(Check("haut-parleur", OK if sinks else (INFO if ci else ALERTE),
                     f"{len(sinks)} sortie(s)" if sinks else "aucune sortie de son vue",
                     "" if sinks else "branche un haut-parleur ou un casque, ou vérifie les réglages de son"))
    return res


def check_linux_desktop(ci: bool) -> list[Check]:
    """Paquets système de la bulle, de la capture d'écran et des outils du bureau."""
    res = []
    sys_py = Path("/usr/bin/python3")  # la venv voit les paquets du Python SYSTÈME (--system-site-packages)

    def sys_python_ok(code: str) -> bool:
        if not sys_py.exists():
            return False
        try:
            return subprocess.run([str(sys_py), "-c", code], capture_output=True, timeout=20).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    gi_ok = sys_python_ok("import gi; gi.require_version('Gtk', '3.0'); gi.require_version('Gdk', '3.0')")
    res.append(Check("GTK pour Python (bulle)", OK if gi_ok else ALERTE, "" if gi_ok else "absent : pas de bulle flottante",
                     "" if gi_ok else "sudo apt install python3-gi gir1.2-gtk-3.0"))
    gst = sys_python_ok("import gi; gi.require_version('Gst', '1.0'); from gi.repository import Gst; "
                        "Gst.init(None); assert Gst.ElementFactory.find('pipewiresrc')")
    res.append(Check("GStreamer PipeWire (capture d'écran)", OK if gst else ALERTE,
                     "" if gst else "absent : pas de capture d'écran",
                     "" if gst else "sudo apt install gstreamer1.0-pipewire gir1.2-gstreamer-1.0 gstreamer1.0-plugins-good"))
    tools = {"notify-send": "libnotify-bin", "xclip": "xclip", "wpctl": "wireplumber", "gtk-launch": "libgtk-3-bin"}
    missing = [t for t in tools if shutil.which(t) is None]
    res.append(Check("outils du bureau", OK if not missing else ALERTE,
                     "" if not missing else "absents : " + ", ".join(missing),
                     "" if not missing else "sudo apt install " + " ".join(sorted({tools[t] for t in missing}))))
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if desktop and "GNOME" not in desktop.upper():
        res.append(Check("bureau", ALERTE, desktop,
                         "pensé pour GNOME : luminosité, thème, raccourcis et capture peuvent ne pas marcher"))
    elif not desktop and not ci:
        res.append(Check("bureau", INFO, "aucune session graphique vue (SSH ?)"))
    else:
        res.append(Check("bureau", OK, desktop or "?"))
    return res


def check_gpu(sys_name: str) -> Check:
    code, out = _out(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    if code == 0 and out:
        if sys_name == "linux":
            return Check("carte graphique", OK, out.splitlines()[0] + " : transcription rapide (CUDA)")
        return Check("carte graphique", INFO, out.splitlines()[0] + " : transcription sur le processeur "
                     "(CUDA n'est branché que sous Linux pour l'instant)")
    return Check("carte graphique", INFO, "pas de carte NVIDIA : transcription sur le processeur "
                 "(plus lente, vitesse non mesurée)")


def check_browser(sys_name: str) -> Check:
    if sys_name == "linux":
        found = next((b for b in ("google-chrome", "chromium", "chromium-browser") if shutil.which(b)), "")
        if found:
            return Check("navigateur du HUD", OK, found)
        return Check("navigateur du HUD", ALERTE, "ni Chrome ni Chromium : le HUD s'ouvrira dans le navigateur par défaut",
                     "sudo apt install chromium")
    return Check("navigateur du HUD", INFO, "le HUD s'ouvre dans le navigateur par défaut")


def check_disk() -> Check:
    try:
        free = shutil.disk_usage(ROOT).free / 2**30
    except OSError:
        return Check("espace disque", INFO, "inconnu")
    if free < DISK_MIN_GB:
        return Check("espace disque", MANQUE, f"{free:.1f} Go libres (il en faut environ {DISK_MIN_GB})")
    return Check("espace disque", OK, f"{free:.0f} Go libres")


def venv_python() -> Path | None:
    for rel in (".venv/bin/python", ".venv/Scripts/python.exe"):
        p = ROOT / rel
        if p.exists():
            return p
    return None


def check_installed() -> list[Check]:
    """Après l'installation : la venv, les modèles."""
    py = venv_python()
    if py is None:
        return [Check("installation", INFO, "pas encore installé (normal avant install.py)")]
    res = [Check("environnement Python", OK, ".venv")]
    piper = list((ROOT / "models" / "piper").glob("*.onnx"))
    mp = list((ROOT / "models" / "mediapipe").glob("*"))
    res.append(Check("modèles (voix locale, gestes, visage)", OK if piper and len(mp) >= 2 else ALERTE,
                     f"{len(piper)} voix, {len(mp)} modèle(s) d'image",
                     "" if piper and len(mp) >= 2 else "relance : python install.py (étape des modèles)"))
    code = subprocess.run([str(py), "-c", "import openwakeword, faster_whisper, aiohttp, mcp"],
                          capture_output=True, cwd=str(ROOT)).returncode
    res.append(Check("dépendances Python", OK if code == 0 else MANQUE, "" if code == 0 else "incomplètes",
                     "" if code == 0 else "relance : python install.py"))
    res.append(check_imagegen())
    return res


def _version_npm(dossier: Path, paquet: str) -> tuple[str, str]:
    """(version FIXÉE par package-lock.json, version installée dans node_modules), "" si illisible."""
    def lire(f: Path, *cles: str) -> str:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for cle in cles:
                data = data[cle]
            return str(data)
        except (OSError, ValueError, KeyError, TypeError):
            return ""
    return (lire(dossier / "package-lock.json", "packages", f"node_modules/{paquet}", "version"),
            lire(dossier / "node_modules" / paquet / "package.json", "version"))


def check_imagegen(root: Path | None = None) -> Check:
    """05/11 (T45) : worker des images et vidéos IA (imagegen/, `npm ci` d'install.py), playwright-core à la
    version du package-lock. Facultatif : ALERTE seulement."""
    dossier = (root or ROOT) / "imagegen"
    attendu, installe = _version_npm(dossier, "playwright-core")
    nom = "images et vidéos IA (Node)"
    if not attendu:
        return Check(nom, INFO, "package-lock.json absent")
    if not installe:
        return Check(nom, ALERTE, "worker non installé : pas d'images ni de vidéos IA", "cd imagegen && npm ci")
    if installe != attendu:
        return Check(nom, ALERTE, f"playwright-core {installe} installé, {attendu} attendu", "cd imagegen && npm ci")
    return Check(nom, OK, f"playwright-core {installe}")


def check_cairo_macos() -> Check:
    """03/11 (portage) : la bulle de macOS dessine avec cairo ; pycairo n'a pas de roue toute prête
    sous macOS et se compile à l'installation, avec cairo et pkg-config de Homebrew."""
    if shutil.which("pkg-config") is None:
        return Check("cairo (bulle)", MANQUE, "pkg-config absent : pycairo ne se compilera pas",
                     "brew install cairo pkg-config")
    code, _out_ = _out(["pkg-config", "--modversion", "cairo"])
    if code != 0:
        return Check("cairo (bulle)", MANQUE, "bibliothèque cairo absente", "brew install cairo pkg-config")
    return Check("cairo (bulle)", OK, _out_.strip())


def run_checks(ci: bool = False) -> list[Check]:
    s = system()
    res = [check_system(s), check_python(s), check_venv(s), check_git(s)]
    res += check_claude(s, ci)
    res.append(check_node(s))
    res += check_audio(s, ci)
    if s == "macos":
        res.append(check_cairo_macos())
    if s == "linux" and not is_wsl():
        res += check_linux_desktop(ci)
    res += [check_gpu(s), check_browser(s), check_disk()]
    res += check_installed()
    return res


def anonymize(text: str) -> str:
    """Ni dossier personnel ni nom d'utilisateur dans le rapport à coller."""
    home = str(Path.home())
    if home and home not in ("/", "\\"):
        text = text.replace(home, "~")
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    if len(user) >= 3:
        text = re.sub(rf"\b{re.escape(user)}\b", "<utilisateur>", text)
    return re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<e-mail>", text)


def report(results: list[Check]) -> str:
    lines = ["===== Rapport Astrel (à coller) =====",
             f"machine : {platform.system()} {platform.release()} {platform.machine()}, Python {platform.python_version()}"]
    for c in results:
        lines.append(f"[{c.status}] {c.name}" + (f" : {c.detail}" if c.detail else ""))
    lines.append("=====================================")
    return anonymize("\n".join(lines))



def _console_utf8() -> None:
    """03/11 (CI Windows) : sortie redirigée = encodage du système (cp1252), qui plante sur « → » ou
    « ✓ ». Toujours UTF-8, et un caractère impossible devient « ? » au lieu d'arrêter le programme."""
    for flux in (sys.stdout, sys.stderr):
        try:
            if flux is not None and (flux.encoding or "").lower().replace("-", "") != "utf8":
                flux.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

def main(argv: list[str] | None = None) -> int:
    _console_utf8()
    ap = argparse.ArgumentParser(description="Vérifie qu'une machine peut faire tourner Astrel (lecture seule).")
    ap.add_argument("--json", action="store_true", help="sortie JSON")
    ap.add_argument("--ci", action="store_true", help="intégration continue : Claude Code et l'audio ne bloquent pas")
    args = ap.parse_args(argv)
    results = run_checks(ci=args.ci)
    blocking = [c for c in results if c.status == MANQUE]
    if args.json:
        # 17/10 (plan P4) : « rapport » = le rapport à coller, déjà anonymisé (page Réglages du HUD).
        print(anonymize(json.dumps({"checks": [asdict(c) for c in results], "bloquant": len(blocking),
                                    "rapport": report(results)}, ensure_ascii=False, indent=1)))
        return 1 if blocking else 0
    width = max(len(c.name) for c in results)
    for c in results:
        print(f"  {c.status:<7} {c.name:<{width}}  {c.detail}")
        if c.fix and c.status != OK:
            print(f"  {'':<7} {'':<{width}}  → {c.fix}")
    print()
    if blocking:
        print(f"{len(blocking)} élément(s) bloquant(s) : corrige les lignes MANQUE, puis relance ce contrôle.")
    else:
        print("Rien de bloquant. Les ALERTE désignent des fonctions qui manqueront, pas des pannes.")
    print()
    print(report(results))
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
