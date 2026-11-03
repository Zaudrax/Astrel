#!/usr/bin/env python3
"""Installe Astrel pour l'utilisateur courant — Linux, macOS, Windows.

    python3 install.py              (Linux, macOS ; ou ./install.sh)
    py install.py                   (Windows ; ou .\\install.ps1)

Aucun droit administrateur. Relançable sans risque : ce qui est déjà fait est
gardé (venv, modèles, réglages, mode de sécurité, empreinte du garde-fou).

Étapes : contrôle de la machine (tools/check_install.py) → environnement Python
→ réglages d'exemple → modèles → serveurs MCP (Node) → mode PRUDENT si c'est
une première installation → démarrage automatique (Linux : service systemd,
bulle, raccourcis GNOME) → empreinte du garde-fou (Linux, première fois).

Le HUD précompilé (hud/dist) est fourni : il n'est recompilé qu'avec
`--rebuild-hud` (il faut alors Node.js).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXAMPLES = ("channels", "reflexes", "mic_profiles")


def system() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform in ("win32", "cygwin"):
        return "windows"
    return "autre"


def venv_python(root: Path = ROOT, sys_name: str | None = None) -> Path:
    sys_name = sys_name or system()
    if sys_name == "windows":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def step(n: int, total: int, text: str) -> None:
    print(f"\n{n}/{total} {text}", flush=True)


def run(cmd: list, cwd: Path = ROOT, check: bool = True) -> int:
    exe = shutil.which(str(cmd[0])) or str(cmd[0])
    proc = subprocess.run([exe, *map(str, cmd[1:])], cwd=str(cwd))
    if check and proc.returncode != 0:
        raise SystemExit(f"   échec : {' '.join(map(str, cmd))} (code {proc.returncode})")
    return proc.returncode


def load_check_install(root: Path = ROOT):
    spec = importlib.util.spec_from_file_location("check_install", root / "tools" / "check_install.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["check_install"] = mod  # ses dataclasses retrouvent leur module
    spec.loader.exec_module(mod)
    return mod


# --- réglages ---

def copy_examples(root: Path = ROOT) -> list[str]:
    """config/X.example.json → config/X.json, seulement s'il n'existe pas : les
    réglages de l'utilisateur ne sont jamais écrasés, et ne sont pas suivis par
    Git (un `git pull` ne crée donc jamais de conflit)."""
    done = []
    for name in EXAMPLES:
        src = root / "config" / f"{name}.example.json"
        dst = root / "config" / f"{name}.json"
        if src.exists() and not dst.exists():
            shutil.copyfile(src, dst)
            done.append(dst.name)
    return done


def initial_autonomy(root: Path = ROOT, fresh: bool = True) -> bool:
    """Première installation : mode PRUDENT (tout ce qui touche à tes comptes,
    commits et dossier personnel est demandé). Jamais changé ensuite : le mode se
    règle dans le HUD (⚡/🛡) ou par `--send "autonomy autonome"`."""
    target = root / "runtime" / "autonomy.json"
    if not fresh or target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"mode": "prudent"}), encoding="utf-8")
    return True


def initial_affichage(root: Path = ROOT, fresh: bool = True) -> bool:
    """Première installation : HUD en affichage SIMPLE (22/10, plan P23, choix de l'utilisateur :
    Simple chez un autre, Avancé chez lui). Seulement si runtime/settings.json n'a pas encore
    de valeur `affichage` ; jamais changé ensuite (Réglages › Apparence, interrupteur de la barre)."""
    target = root / "runtime" / "settings.json"
    if not fresh:
        return False
    try:
        data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (OSError, ValueError):
        return False  # fichier illisible : on n'y touche pas
    values = data.get("values") if isinstance(data, dict) else None
    if not isinstance(data, dict) or (values is not None and not isinstance(values, dict)):
        return False
    values = dict(values or {})
    if "affichage" in values:
        return False
    values["affichage"] = "simple"
    data["values"] = values
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(target)
    return True


# --- démarrage automatique (Linux) ---

def current_workdir(unit: Path) -> str:
    """ASTREL_WORKDIR du service déjà installé : une réinstallation le garde."""
    try:
        m = re.search(r"^Environment=ASTREL_WORKDIR=(.+)$", unit.read_text(encoding="utf-8"), re.M)
    except OSError:
        return ""
    value = m.group(1).strip() if m else ""
    return "" if value == "@WORKDIR@" else value


def render(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace(f"@{key}@", value)
    return template


def service_path() -> str:
    """Le service n'hérite pas du PATH du shell : claude et node doivent y être."""
    dirs = []
    for tool in ("claude", "node"):
        found = shutil.which(tool)
        if found:
            dirs.append(str(Path(found).parent))
    dirs += ["/usr/local/bin", "/usr/bin", "/bin"]
    return ":".join(dict.fromkeys(dirs))


# --- icône du lanceur (Linux, 22/10, plan P25) ---

ICON_NAME = "astrel-hud"  # champ Icon= des deux .desktop de systemd/
ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)  # dossiers fixes de hicolor (il n'y a pas de 1024)


def launcher_icons(root: Path = ROOT) -> list[tuple[str, Path]]:
    """(dossier de hicolor, fichier source) de l'icône `astrel-hud` : la variante du logo retenue
    (`hud/assets/logo/choix.txt`, écrit par `tools/logo.py --choix`), un PNG par taille + le SVG dans
    `scalable`. Pas encore de choix : l'icône du HUD (`hud/assets/astrel-icon.png`) dans `scalable`,
    plutôt que l'icône générique."""
    logo = root / "hud" / "assets" / "logo"
    try:
        choice = (logo / "choix.txt").read_text(encoding="utf-8").strip().lower()
    except OSError:
        choice = ""
    out: list[tuple[str, Path]] = []
    if re.fullmatch(r"[a-e]", choice):  # e = étoile Astrel (24/10, plan P31 lot D)
        for size in ICON_SIZES:
            src = logo / "png" / f"astrel-logo-{choice}-{size}.png"
            if src.is_file():
                out.append((f"{size}x{size}", src))
        if (logo / f"astrel-logo-{choice}.svg").is_file():
            out.append(("scalable", logo / f"astrel-logo-{choice}.svg"))
    if not out and (root / "hud" / "assets" / "astrel-icon.png").is_file():
        out.append(("scalable", root / "hud" / "assets" / "astrel-icon.png"))
    return out


def install_icons(root: Path, home: Path) -> list[Path]:
    """Copie l'icône dans ~/.local/share/icons/hicolor/<taille>/apps/ (+ scalable), puis met à jour
    le cache des icônes. Retiré par uninstall.sh."""
    base = home / ".local" / "share" / "icons" / "hicolor"
    written = []
    for sub, src in launcher_icons(root):
        d = base / sub / "apps"
        d.mkdir(parents=True, exist_ok=True)
        for suffix in (".png", ".svg"):  # l'autre format d'une installation précédente masquerait celle-ci
            old = d / f"{ICON_NAME}{suffix}"
            if suffix != src.suffix and old.exists():
                old.unlink()
        dst = d / f"{ICON_NAME}{src.suffix}"
        shutil.copyfile(src, dst)
        written.append(dst)
    tool = shutil.which("gtk-update-icon-cache") or shutil.which("gtk4-update-icon-cache")
    if written and tool:
        run([tool, "-q", "-f", "-t", base], check=False)
    return written


def retirer_autostart_bulle(autostart: Path) -> None:
    """Ancien lanceur de la bulle (`astrel-bubble.desktop` de l'autostart GNOME) : désactivé, pas
    effacé (`X-GNOME-Autostart-enabled=false`), pour ne pas ouvrir une seconde bulle."""
    f = autostart / "astrel-bubble.desktop"
    if not f.exists():
        return
    text = re.sub(r"(?m)^X-GNOME-Autostart-enabled=.*\n?", "", f.read_text(encoding="utf-8", errors="replace"))
    f.write_text(text.rstrip("\n") + "\nX-GNOME-Autostart-enabled=false\n", encoding="utf-8")


def install_linux_autostart(root: Path, workdir: str) -> None:
    home = Path.home()
    unit_dir = home / ".config" / "systemd" / "user"
    autostart = home / ".config" / "autostart"
    apps = home / ".local" / "share" / "applications"
    for d in (unit_dir, autostart, apps):
        d.mkdir(parents=True, exist_ok=True)
    values = {"ROOT": str(root), "PATH": service_path(), "WORKDIR": workdir}
    for unit in ("astrel.service", "astrel-bubble.service"):
        (unit_dir / unit).write_text(
            render((root / "systemd" / unit).read_text(encoding="utf-8"), **values), encoding="utf-8")
    # 29/10 : la bulle démarre par son service, plus par l'autostart GNOME (les deux en ouvraient deux)
    retirer_autostart_bulle(autostart)
    (apps / "astrel-hud.desktop").write_text(
        render((root / "systemd" / "astrel-hud.desktop").read_text(encoding="utf-8"), **values), encoding="utf-8")
    if not install_icons(root, home):
        print("   icône du lanceur absente (hud/assets) : icône générique")
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", "astrel.service", "astrel-bubble.service"])
    if shutil.which("gsettings") and "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        if run([str(root / "hotkeys.sh")], check=False) != 0:
            print("   raccourcis non posés")
    else:
        print("   raccourcis clavier : GNOME seulement, non posés")


def has_systemd() -> bool:
    return bool(shutil.which("systemctl")) and Path("/run/systemd/system").exists()


# --- étapes ---


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
    ap = argparse.ArgumentParser(description="Installe Astrel (Linux, macOS, Windows), sans droits administrateur.")
    ap.add_argument("--rebuild-hud", action="store_true", help="recompiler le HUD (Node.js requis)")
    ap.add_argument("--projets", metavar="DOSSIER",
                    help="dossier de tes projets, où travaille Claude (défaut : celui du service déjà installé, "
                         "sinon ~/Projets)")
    ap.add_argument("--sans-demarrage", action="store_true", help="ne pas installer le démarrage automatique")
    ap.add_argument("--sans-modeles", action="store_true", help="ne pas télécharger les modèles (~550 Mo)")
    ap.add_argument("--force", action="store_true", help="continuer même si le contrôle trouve un élément bloquant")
    args = ap.parse_args(argv)
    sys_name = system()
    total = 7

    step(1, total, "Contrôle de la machine")
    check = load_check_install()
    results = check.run_checks()
    blocking = [c for c in results if c.status == check.MANQUE]
    for c in results:
        if c.status in (check.MANQUE, check.ALERTE):
            print(f"   {c.status:<7} {c.name} : {c.detail}" + (f"\n           → {c.fix}" if c.fix else ""))
    if blocking and not args.force:
        print("\nÉlément(s) bloquant(s) : corrige les lignes MANQUE ci-dessus, puis relance l'installation.")
        return 1

    step(2, total, "Environnement Python (.venv)")
    py = venv_python()
    fresh = not py.exists()
    if fresh:
        cmd = [sys.executable, "-m", "venv"]
        if sys_name == "linux":
            # GTK (bulle) et GStreamer (capture d'écran) viennent du système.
            cmd.append("--system-site-packages")
        run(cmd + [str(ROOT / ".venv")])
    run([py, "-m", "pip", "install", "-q", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "-q", "-r", ROOT / "requirements.txt"])

    step(3, total, "Réglages")
    copied = copy_examples()
    print("   copiés depuis les exemples : " + (", ".join(copied) if copied else "rien (déjà en place)"))
    if initial_autonomy(fresh=fresh):
        print("   mode de sécurité : PRUDENT (première installation ; à changer dans le HUD si tu le veux)")
    if initial_affichage(fresh=fresh):
        print("   affichage du HUD : SIMPLE (première installation ; Avancé dans Réglages › Apparence)")

    step(4, total, "Modèles (voix locale, gestes, visage)")
    if args.sans_modeles:
        print("   passé (--sans-modeles)")
    else:
        run([py, ROOT / "tools" / "fetch_models.py"])

    step(5, total, "Navigateur piloté (Playwright), pont WhatsApp et HUD")
    if shutil.which("npm"):
        run(["npm", "install", "--no-audit", "--no-fund", "--silent"], cwd=ROOT / "mcp-servers")
        # 18/10 (plan P5) : le pont WhatsApp, à versions FIXÉES (package-lock). Facultatif :
        # un échec n'arrête pas l'installation (bouton « Préparer WhatsApp » dans les Réglages).
        if (ROOT / "whatsapp" / "package-lock.json").exists() and \
                run(["npm", "ci", "--no-audit", "--no-fund", "--silent"], cwd=ROOT / "whatsapp", check=False) != 0:
            print("   pont WhatsApp non préparé : Réglages › WhatsApp › « Préparer WhatsApp » plus tard")
        if args.rebuild_hud:
            run(["npm", "install", "--no-audit", "--no-fund", "--silent"], cwd=ROOT / "hud")
            run(["npm", "run", "-s", "build"], cwd=ROOT / "hud")
        else:
            print("   HUD précompilé utilisé tel quel (--rebuild-hud pour le recompiler)")
    else:
        print("   npm absent : ni navigateur piloté ni WhatsApp ; le HUD précompilé sert tel quel.")

    step(6, total, "Démarrage automatique")
    workdir = args.projets or current_workdir(Path.home() / ".config/systemd/user/astrel.service") \
        or os.environ.get("ASTREL_WORKDIR") or str(Path.home() / "Projets")
    if args.sans_demarrage:
        print("   passé (--sans-demarrage)")
    elif sys_name == "linux" and has_systemd():
        print(f"   dossier des projets : {workdir}")
        if not Path(workdir).is_dir():
            print("   ⚠ ce dossier n'existe pas : crée-le ou relance avec --projets DOSSIER")
        install_linux_autostart(ROOT, workdir)
    else:
        print("   pas encore de démarrage automatique sur ce système (portage en cours) : lance Astrel à la main.")

    step(7, total, "Empreinte du garde-fou")
    if sys_name != "linux":
        print("   contrôle de santé pas encore porté sur ce système : passé.")
    elif (ROOT / "hooks" / "empreinte.json").exists():
        print("   déjà retenue : inchangée (après une modification VOULUE du garde-fou : ./run.sh --doctor --accept).")
    else:
        run([py, "-m", "astrel", "--doctor", "--accept"], check=False)

    print("\nInstallé.")
    if sys_name == "linux" and has_systemd() and not args.sans_demarrage:
        print("Pour démarrer maintenant :\n"
              "  systemctl --user start astrel     # l'assistant\n"
              "  ./bubble.sh &                     # la bulle (automatique à la prochaine session)\n"
              "  ./hud.sh                          # le HUD (ou Super+Maj+H)\n"
              "  journalctl --user -u astrel -f    # le journal en direct")
    else:
        launcher = r".venv\Scripts\python -m astrel" if sys_name == "windows" else ".venv/bin/python -m astrel"
        print(f"Pour démarrer : {launcher}   (puis, dans un autre terminal : {launcher} --hud)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
