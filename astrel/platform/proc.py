"""Process : parent, existence, nom, propriétaire d'une connexion TCP locale,
appartenance au cerveau (plan P6, lot A, portage du canal de sécurité).

Appelé par `state.py` (« la commande vient-elle de Claude ? »), `ipc.py` (pid
du client en mode TCP) et `doctor.py`. Un pid inconnu ou illisible n'est jamais
une preuve d'innocence : les appelants le traitent comme suspect.

- Linux (machine de référence) : /proc et cgroup v2, le code d'origine de
  `state.py` déplacé SANS changement de comportement ;
- macOS et Windows : psutil (déjà dans requirements.txt). Pas de cgroup : le
  cerveau reçoit la variable `ASTREL_BRAIN` (`brain_env`), héritée par son Bash,
  ses scripts et leurs process détachés, et relue par `brain_marker`.
  Protection plus FAIBLE que le cgroup : un process lancé avec un environnement
  vidé ET détaché n'est plus reconnu. Écrit d'après la documentation de
  psutil, JAMAIS lancé sur un vrai Mac ni un vrai Windows.

`SYSTEM` est relu à chaque appel : les tests le remplacent pour faire passer la
voie psutil sur Linux (psutil y fonctionne aussi).
"""
from __future__ import annotations

import logging
import os
import secrets
import sys
from pathlib import Path

from . import SYSTEM

log = logging.getLogger("astrel.proc")

# Marqueur posé dans l'environnement du cerveau (`Brain.start`). Sa seule
# PRÉSENCE suffit (un reste d'un ancien cerveau est aussi suspect) ; la valeur,
# propre à ce démarrage du cœur, ne sert qu'au journal.
BRAIN_VAR = "ASTREL_BRAIN"
BRAIN_TOKEN = secrets.token_hex(16)


def _linux() -> bool:
    return SYSTEM == "linux"


def brain_env(base: dict | None = None) -> dict:
    """Environnement du process du cerveau : celui du cœur + le marqueur."""
    env = dict(os.environ if base is None else base)
    env[BRAIN_VAR] = BRAIN_TOKEN
    return env


def uses_cgroup() -> bool:
    """Linux : le cgroup du service fait foi ; ailleurs, le marqueur."""
    return _linux()


# ------------------------------------------------------------------ parent, existence, nom

def exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if _linux():
        return (Path("/proc") / str(pid)).exists()
    import psutil

    return psutil.pid_exists(pid)


def parent(pid: int) -> int | None:
    """pid du parent ; 0 en haut de la chaîne (parent disparu) ; None si illisible."""
    if _linux():
        try:
            stat = (Path("/proc") / str(pid) / "stat").read_text()
            return int(stat.rsplit(")", 1)[1].split()[1])  # le nom peut contenir des espaces
        except (OSError, ValueError, IndexError):
            return None
    import psutil

    try:
        # `parent()` (et non `ppid()`) : sous Windows, un parent mort peut voir son
        # numéro repris par un autre process ; psutil vérifie l'ordre de création.
        par = psutil.Process(pid).parent()
    except (psutil.Error, OSError, ValueError):
        return None
    return par.pid if par is not None else 0


def descends_from(pid: int, ancestor: int | None, depth: int = 64) -> bool:
    """`pid` est-il `ancestor` ou l'un de ses descendants ?"""
    if not ancestor or pid <= 0:
        return False
    for _ in range(depth):
        if pid == ancestor:
            return True
        if pid <= 1:
            return False
        nxt = parent(pid)
        if nxt is None:
            return False
        pid = nxt
    return False


def name(pid: int) -> str | None:
    """Nom court du programme (« claude », sans « .exe ») ; None si illisible."""
    if _linux():
        try:
            return (Path("/proc") / str(pid) / "comm").read_text().strip()
        except OSError:
            return None
    import psutil

    try:
        raw = psutil.Process(pid).name()
    except (psutil.Error, OSError, ValueError):
        return None
    return raw[:-4] if raw.lower().endswith(".exe") else raw


# ------------------------------------------------------------------ cgroup (Linux) et marqueur (Mac/Windows)

def cgroup_of(pid: int) -> str:
    """Chemin du cgroup v2 du process (« /user.slice/…/astrel.service »), vide si
    illisible ou hors Linux."""
    if not _linux():
        return ""
    try:
        for line in (Path("/proc") / str(pid) / "cgroup").read_text().splitlines():
            if line.startswith("0::"):
                return line[3:].strip()
    except OSError:
        pass
    return ""


def brain_marker(pid: int) -> bool | None:
    """Le process porte-t-il le marqueur du cerveau ? None si son environnement
    est illisible (process d'un autre utilisateur, protégé, disparu). Lu par
    psutil même pour le cœur : l'auto-test du démarrage vérifie ainsi la lecture."""
    import psutil

    try:
        return bool(psutil.Process(pid).environ().get(BRAIN_VAR))
    except (psutil.Error, OSError, ValueError):
        return None


# ------------------------------------------------------------------ connexion TCP locale

def _tcp_inode(client_port: int, server_port: int) -> int | None:
    """Inode de la socket CLIENTE 127.0.0.1:client_port → 127.0.0.1:server_port
    (/proc/net/tcp), None si absente ou tenue par un autre utilisateur."""
    want_local, want_remote = f"0100007F:{client_port:04X}", f"0100007F:{server_port:04X}"
    try:
        lines = Path("/proc/net/tcp").read_text().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) > 9 and parts[1] == want_local and parts[2] == want_remote:
            try:
                if int(parts[7]) != os.getuid():
                    return None
                return int(parts[9])
            except ValueError:
                return None
    return None


def _tcp_owner_linux(client_port: int, server_port: int) -> int:
    inode = _tcp_inode(client_port, server_port)
    if not inode:
        return 0
    target = f"socket:[{inode}]"
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        fd_dir = f"/proc/{entry}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(f"{fd_dir}/{fd}") == target:
                    return int(entry)
            except OSError:
                continue
    return 0


def _same_user(pid: int | None) -> int:
    """`pid` s'il appartient au même utilisateur que le cœur, 0 sinon."""
    if not pid:
        return 0
    import psutil

    try:
        return pid if psutil.Process(pid).username() == psutil.Process().username() else 0
    except (psutil.Error, OSError, ValueError):
        return 0


def _tcp_owner_psutil(client_port: int, server_port: int) -> int:
    import psutil

    want_local, want_remote = ("127.0.0.1", client_port), ("127.0.0.1", server_port)

    def match(conn) -> bool:
        return bool(conn.laddr and conn.raddr) and tuple(conn.laddr)[:2] == want_local \
            and tuple(conn.raddr)[:2] == want_remote

    try:
        # Windows : la table TCP du système, sans droits d'administrateur.
        for conn in psutil.net_connections(kind="tcp4"):
            if match(conn):
                return _same_user(conn.pid)
        return 0
    except (psutil.AccessDenied, PermissionError):
        pass  # macOS : la liste du système demande root ; on interroge process par process
    except (psutil.Error, OSError):
        return 0
    for p in psutil.process_iter():
        try:
            if any(match(c) for c in p.net_connections(kind="tcp4")):
                return _same_user(p.pid)
        except (psutil.Error, OSError):
            continue
    return 0


def tcp_owner(client_port: int, server_port: int) -> int:
    """Process qui tient le bout client d'une connexion locale 127.0.0.1 (même
    principe que SO_PEERCRED, qui n'existe pas en TCP). 0 si introuvable
    (process d'un autre utilisateur, disparu…)."""
    if _linux():
        return _tcp_owner_linux(client_port, server_port)
    return _tcp_owner_psutil(client_port, server_port)


# ------------------------------------------------------------------ fichier ouvert

def opened_path(fd: int) -> Path | None:
    """Chemin réel du fichier OUVERT par `fd` (un lien échangé entre la
    vérification et l'ouverture ne passe pas). None si le système ne le dit pas.
    Linux : /proc/self/fd ; macOS : F_GETPATH ; Windows : GetFinalPathNameByHandleW."""
    if _linux():
        try:
            return Path(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:
            return None
    if sys.platform == "darwin":
        try:
            import fcntl

            raw = fcntl.fcntl(fd, getattr(fcntl, "F_GETPATH", 50), b"\0" * 1024)
            return Path(os.fsdecode(raw.split(b"\0", 1)[0])) if raw[:1] not in (b"", b"\0") else None
        except (OSError, ValueError):
            return None
    if sys.platform == "win32":
        try:
            import ctypes
            import msvcrt
            from ctypes import wintypes

            handle = msvcrt.get_osfhandle(fd)
            fn = ctypes.windll.kernel32.GetFinalPathNameByHandleW
            fn.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
            buf = ctypes.create_unicode_buffer(32768)
            n = fn(handle, buf, len(buf), 0)
            if not n or n >= len(buf):
                return None
            text = buf.value
            if text.startswith("\\\\?\\UNC\\"):
                text = "\\\\" + text[8:]
            elif text.startswith("\\\\?\\"):
                text = text[4:]
            return Path(text)
        except (OSError, ValueError, AttributeError):
            return None
    return None
