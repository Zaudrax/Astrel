"""Appels du système que Windows n'a pas (03/11/2026, portage : Windows natif doit démarrer).

Le reste d'Astrel appelle ces aides au lieu de `os.getuid`, `os.O_NOFOLLOW`, `os.killpg`,
`signal.SIGKILL` ou `os.uname`, qui n'existent pas sous Windows (AttributeError au premier appel :
c'est ce qui faisait planter le démarrage).

Ce qui est PLUS FAIBLE sous Windows, et dit ici une fois pour toutes :
- `proprietaire_seul` : Windows n'a ni uid ni bits de mode ; les fichiers secrets d'Astrel vivent
  dans le profil de l'utilisateur, protégé par les droits NTFS du profil. On vérifie seulement que
  c'est un fichier ordinaire, pas un lien.
- `NOFOLLOW` : sans `O_NOFOLLOW`, un lien est refusé par un `lstat` juste avant l'ouverture (une
  course reste possible ; créer un lien demande sous Windows le mode développeur ou des droits).
"""
from __future__ import annotations

import contextlib
import os
import platform as _platform
import signal
import stat
import subprocess

UNIX = hasattr(os, "getuid")
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)  # Windows : SIGTERM = TerminateProcess


def uid() -> int | None:
    """uid de l'utilisateur, None sous Windows."""
    return os.getuid() if UNIX else None


def proprietaire_seul(st: os.stat_result) -> bool:
    """Fichier ordinaire, à nous, illisible des autres (Unix) ; ordinaire seulement (Windows)."""
    if not stat.S_ISREG(st.st_mode):
        return False
    if not UNIX:
        return True
    return st.st_uid == os.getuid() and not st.st_mode & 0o077


def ouvrir_sans_lien(path: str | os.PathLike, flags: int, mode: int = 0o600) -> int:
    """`os.open` qui refuse un lien : `O_NOFOLLOW` (Unix), `lstat` juste avant (Windows)."""
    if NOFOLLOW:
        return os.open(path, flags | NOFOLLOW, mode)
    with contextlib.suppress(FileNotFoundError):
        if stat.S_ISLNK(os.lstat(path).st_mode):
            raise OSError(f"lien refusé : {path}")
    return os.open(path, flags | getattr(os, "O_BINARY", 0), mode)


def nom_machine() -> str:
    """Nom de la machine (`os.uname` n'existe pas sous Windows)."""
    return _platform.node()


def tuer_arbre(pid: int, sig: int = signal.SIGTERM) -> None:
    """Le process et tout ce qu'il a lancé. Unix : son GROUPE (`killpg`, il a été lancé avec
    `start_new_session=True`) ; Windows : `taskkill /T /F`, l'arbre, toujours forcé (un programme
    en console ignore l'arrêt poli). Un process déjà parti n'est pas une erreur."""
    if UNIX:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, sig)
        return
    cmd = ["taskkill", "/PID", str(pid), "/T", "/F"]
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(cmd, capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def groupe_vivant(pid: int) -> bool:
    """Le groupe (Unix) ou le process (Windows) lancé avec ce pid tourne-t-il encore ?"""
    if UNIX:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        return False


def processus_avec(argument: str) -> list[int]:
    """PID des processus de l'utilisateur dont la ligne de commande contient exactement cet argument
    (le nôtre exclu). Linux : /proc (sans psutil, d'origine) ; ailleurs : psutil."""
    from pathlib import Path

    moi = os.getpid()
    if Path("/proc").is_dir():
        cible, out = argument.encode(), []
        for d in Path("/proc").iterdir():
            if not d.name.isdigit() or int(d.name) == moi:
                continue
            try:
                if d.stat().st_uid != os.getuid():
                    continue
                if cible in (d / "cmdline").read_bytes().split(b"\0"):
                    out.append(int(d.name))
            except OSError:
                continue
        return out
    import psutil

    nom, out = psutil.Process().username(), []
    for p in psutil.process_iter(["pid", "username", "cmdline"]):
        if p.info["pid"] != moi and p.info.get("username") == nom and argument in (p.info.get("cmdline") or []):
            out.append(p.info["pid"])
    return out


def pid_vivant(pid: int) -> bool:
    """Vivant et pas zombie."""
    from pathlib import Path

    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
    except (OSError, IndexError):
        if Path("/proc").is_dir():
            return False
    import psutil

    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False
