"""Capture d'écran, dans un processus à part (`python -m astrel.tools.screenshot`).
Imprime le chemin du PNG sur la PREMIÈRE ligne de la sortie standard, puis la
légende (quel écran est montré, lesquels existent) sur la deuxième ; ou
« ERREUR: … » sur la sortie d'erreur et code 1.

Options (22/10, plan P24, plusieurs écrans) :
- `--ecran NOM` : l'écran à montrer (« HDMI-1 », « LG », « portable »,
  « principal », « tous »…, voir `astrel/ecrans.py`). Absent : l'écran du réglage
  « Écran regardé par défaut », sinon le principal de GNOME ; un écran par défaut
  non partagé est alors remplacé par un écran partagé (remarque dans la légende),
  au lieu de l'erreur rendue pour un écran demandé par son nom.
- `--autoriser` (Linux) : ignore le jeton enregistré et rouvre le dialogue de
  GNOME pour choisir les écrans partagés (à cocher tous), puis capture. Lancé par
  l'utilisateur seulement : jamais depuis l'outil MCP.

Appelé par `mcp_server.screen_capture` et `senses/watch.py`. Le moteur dépend
du système :

- Linux : portail de PARTAGE D'ÉCRAN de GNOME + GStreamer
  (`astrel/platform/linux_capture.py`, code d'origine, autorisé une fois,
  jeton dans `runtime/screencast-token`) ;
- macOS : `screencapture` (livré avec macOS). La première fois, macOS demande
  l'autorisation « Enregistrement de l'écran » pour le terminal ou Python ;
- Windows : Pillow (`ImageGrab`) s'il est installé, sinon Windows PowerShell
  (System.Drawing), sans autorisation à donner.

macOS et Windows : NON VÉRIFIÉS sur une vraie machine au 17/10/2026.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from astrel.platform import SYSTEM, unavailable  # noqa: E402


def _temp_png() -> Path:
    fd, name = tempfile.mkstemp(prefix="astrel-ecran-", suffix=".png")
    os.close(fd)
    return Path(name)


def capture_macos() -> Path:
    dest = _temp_png()
    proc = subprocess.run(["screencapture", "-x", "-t", "png", str(dest)], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError("capture refusée : autorise « Enregistrement de l'écran » pour le terminal "
                           "(Réglages Système → Confidentialité et sécurité)")
    return dest


_PS_CAPTURE = (
    "Add-Type -AssemblyName System.Windows.Forms, System.Drawing; "
    "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen; "
    "$img = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
    "$g = [System.Drawing.Graphics]::FromImage($img); "
    "$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $img.Size); "
    "$img.Save($env:ASTREL_PNG, [System.Drawing.Imaging.ImageFormat]::Png); "
    "$g.Dispose(); $img.Dispose()"
)


def capture_windows() -> Path:
    dest = _temp_png()
    try:
        from PIL import ImageGrab

        ImageGrab.grab(all_screens=True).save(dest, "PNG")
        return dest
    except ImportError:
        pass
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _PS_CAPTURE],
        capture_output=True, text=True, timeout=30, env={**os.environ, "ASTREL_PNG": str(dest)},
    )
    if proc.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError((proc.stderr or "capture impossible").strip()[:300])
    return dest


def capturer(system: str | None = None, ecran: str | None = None, autoriser: bool = False) -> tuple[Path, str]:
    """(chemin du PNG, légende d'une ligne ; "" si le système ne sait pas choisir l'écran)."""
    system = system or SYSTEM
    ecran = (ecran or "").strip()
    if system == "linux":
        from astrel import ecrans
        from astrel.platform.linux_capture import capture as capture_linux

        explicite = bool(ecran)
        return capture_linux(ecran or ecrans.ecran_par_defaut(), autoriser=autoriser, explicite=explicite)
    remarque = (f"Choix de l'écran (« {ecran} ») non pris en charge sur ce système : capture habituelle."
                if ecran else "")
    if system == "macos":
        return capture_macos(), remarque
    if system == "windows":
        return capture_windows(), remarque
    raise unavailable("Capture d'écran", system=system)


def capture(system: str | None = None) -> Path:
    """Chemin du PNG de l'écran par défaut (contrat d'origine)."""
    return capturer(system)[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m astrel.tools.screenshot",
                                     description="Capture d'écran : chemin du PNG, puis légende.")
    parser.add_argument("--ecran", default="", help="écran à montrer (nom, connecteur, portable, principal, tous)")
    parser.add_argument("--autoriser", action="store_true",
                        help="rouvrir le dialogue de GNOME pour choisir les écrans partagés")
    args = parser.parse_args(argv)
    try:
        path, legende = capturer(ecran=args.ecran, autoriser=args.autoriser)
    except Exception as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1
    print(path)
    if legende:
        print(" ".join(legende.split()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
