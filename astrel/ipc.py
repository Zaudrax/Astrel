"""Canal local entre les process d'Astrel (plan P6, lot A, portage).

Sert au canal de contrôle (`state.ControlServer`, `state.send_command`) et au
canal de confirmation du hook (`confirm.ConfirmServer`, `confirm.request`). Le
format échangé ne change pas : une ligne de texte ou de JSON.

Deux modes (`mode()`) :
- « unix » (Linux, macOS) : socket Unix en 0600 au chemin donné, comme avant.
  Le pid du client vient du noyau : SO_PEERCRED (Linux, code d'origine),
  LOCAL_PEERPID (macOS, d'après la documentation, jamais lancé sur un Mac) ;
- « tcp » (Windows, où `socket.AF_UNIX` manque ou ne donne pas le pid du
  client) : 127.0.0.1 sur un port libre ; le fichier du chemin donné (dans
  `runtime/`, protégé en écriture par le garde-fou comme les sockets) contient
  `{"port", "token", "pid"}`. Poignée de main (19/10) : le client vérifie que le
  `pid` du cœur vit, envoie un nonce, le serveur PROUVE qu'il connaît le jeton
  (HMAC du nonce) ; alors seulement le client envoie le jeton, puis sa demande.
  Un fichier périmé (cœur tombé, port repris par un autre programme) ne reçoit
  donc ni jeton ni commande. Le serveur borne la poignée de main à
  `ADMIT_TIMEOUT_S` au total : un client muet ne retient pas le canal.
  Le pid du client est lu dans la table TCP (`platform.proc.tcp_owner`).
  Le jeton écarte un process d'un autre utilisateur du poste ; il ne dit pas si
  le client est Claude (c'est le pid qui le dit). Sous Windows, le fichier
  hérite des droits du dossier d'Astrel : l'installer dans le profil de
  l'utilisateur (C:\\Users\\<nom>\\…), pas à la racine d'un disque, dont les
  droits par défaut laissent lire les autres comptes (`self_test` le signale).
  Jamais lancé sur un vrai Windows.

`ASTREL_IPC=tcp` force le mode TCP (tests « faux Windows », CI).

`self_test()` : au démarrage du cœur, le canal se connecte à lui-même et doit
retrouver son propre pid ; sinon tout client passerait pour Claude (ou pire),
et le cœur le dit au journal et au HUD.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import select
import socket
import struct
import threading
import time
from pathlib import Path

from .platform import SYSTEM, proc

TOKEN_MAX_BYTES = 256  # la ligne du jeton en mode TCP, rien d'autre
NONCE_MAX_BYTES = 128  # la ligne du nonce du client (hexadécimal)
# Mode TCP : durée TOTALE de la poignée de main côté serveur (19/10 ; 3 s par
# octet avant) ; un client muet ou sans jeton ne retient pas le canal plus.
ADMIT_TIMEOUT_S = 1.0
SELF_TEST_TIMEOUT_S = 3.0
_PROOF_LABEL = b"astrel-ipc-serveur:"
# macOS (<sys/un.h>) : SOL_LOCAL = 0, LOCAL_PEERPID = 0x002. Absents du module socket.
_SOL_LOCAL = 0
_LOCAL_PEERPID = 0x002


def mode() -> str:
    """« unix » ou « tcp ». Relu à chaque appel (variable ASTREL_IPC)."""
    forced = os.environ.get("ASTREL_IPC", "").strip().lower()
    if forced == "tcp" or not hasattr(socket, "AF_UNIX"):
        return "tcp"
    if forced == "unix":
        return "unix"
    return "tcp" if SYSTEM == "windows" else "unix"


def _proof(token: str, nonce: bytes) -> bytes:
    """Ce que le serveur renvoie au nonce du client : HMAC-SHA256 par le jeton."""
    return hmac.new(token.encode(), _PROOF_LABEL + nonce, hashlib.sha256).hexdigest().encode()


def _read_line(conn: socket.socket, deadline: float, limit: int) -> bytes | None:
    """Une ligne (sans « \\n »), lue OCTET PAR OCTET pour ne rien prendre de la
    suite, avant `deadline` (horloge monotone). None si trop longue, fermée,
    illisible ou en retard."""
    raw = b""
    while len(raw) <= limit:
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        try:
            conn.settimeout(left)
            ch = conn.recv(1)
        except OSError:
            return None
        if not ch:
            return None
        if ch == b"\n":
            return raw.strip()
        raw += ch
    return None


def _write_private(path: Path, text: str) -> None:
    """Écrit le fichier de rendez-vous en 0600 (sans effet sous Windows : il y
    hérite des droits du dossier d'Astrel), en remplaçant d'un coup."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


class Listener:
    """Bout serveur. `sock` est la socket d'écoute (accept, settimeout, close)."""

    def __init__(self, path: Path, backlog: int = 8) -> None:
        self.path = Path(path)
        self.mode = mode()
        self._token = ""
        if self.mode == "unix":
            self.path.unlink(missing_ok=True)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.bind(str(self.path))
                os.chmod(self.path, 0o600)
                sock.listen(backlog)
            except OSError:
                sock.close()
                raise
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows : pas de vol du port
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind(("127.0.0.1", 0))
                sock.listen(backlog)
                self._token = secrets.token_urlsafe(32)
                # Écrit APRÈS l'écoute : un client qui lit le fichier peut se connecter.
                # `pid` : le client vérifie que ce cœur vit encore (fichier périmé).
                _write_private(self.path, json.dumps({"port": sock.getsockname()[1], "token": self._token,
                                                      "pid": os.getpid()}))
            except OSError:
                sock.close()
                raise
        self.sock = sock

    def admit(self, conn: socket.socket) -> bool:
        """Mode TCP : poignée de main, `ADMIT_TIMEOUT_S` au total. Lit le nonce
        du client, renvoie la preuve (HMAC du nonce par le jeton), lit le jeton
        et le compare. Le reste de la demande reste dans la socket, lu ensuite
        comme avant. Mode Unix : vrai (les droits 0600 de la socket font ce travail)."""
        if self.mode == "unix":
            return True
        if not self._token:
            return False
        old = conn.gettimeout()
        deadline = time.monotonic() + ADMIT_TIMEOUT_S
        try:
            nonce = _read_line(conn, deadline, NONCE_MAX_BYTES)
            if not nonce or len(nonce) < 32 or any(c not in b"0123456789abcdef" for c in nonce):
                return False
            conn.settimeout(max(0.05, deadline - time.monotonic()))
            conn.sendall(_proof(self._token, nonce) + b"\n")
            raw = _read_line(conn, deadline, TOKEN_MAX_BYTES)
        except OSError:
            return False
        finally:
            try:
                conn.settimeout(old)
            except OSError:
                pass
        return raw is not None and hmac.compare_digest(raw, self._token.encode())

    def close(self) -> None:
        try:
            self.sock.close()
        finally:
            self.path.unlink(missing_ok=True)


def listen(path: Path, backlog: int = 8) -> Listener:
    return Listener(path, backlog)


def connect(path: Path, timeout: float) -> socket.socket:
    """Bout client, connecté (et jeton envoyé en mode TCP). OSError si le cœur
    n'écoute pas ou si le fichier de rendez-vous est illisible."""
    if mode() == "unix":
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(str(path))
        except OSError:
            s.close()
            raise
        return s
    try:
        info = json.loads(Path(path).read_text(encoding="utf-8"))
        port, token, pid = int(info["port"]), str(info["token"]), int(info.get("pid") or 0)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise OSError(f"rendez-vous du canal local illisible ({type(exc).__name__})") from None
    if not token or not proc.exists(pid):
        raise OSError("rendez-vous du canal local périmé : le cœur qui l'a écrit ne tourne plus")
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        # Le serveur prouve d'abord qu'il connaît le jeton : un port repris par
        # un autre programme ne reçoit ni le jeton ni la demande.
        nonce = secrets.token_hex(16).encode()
        s.sendall(nonce + b"\n")
        got = _read_line(s, time.monotonic() + timeout, 128)
        if got is None or not hmac.compare_digest(got, _proof(token, nonce)):
            raise OSError("le canal local n'a pas prouvé être Astrel (rendez-vous périmé ?)")
        s.settimeout(timeout)
        s.sendall(token.encode() + b"\n")
    except OSError:
        s.close()
        raise
    return s


def peer_pid(conn: socket.socket) -> int:
    """pid du process au bout de `conn` ; 0 si inconnu (traité comme suspect)."""
    try:
        if conn.family == socket.AF_INET:
            return proc.tcp_owner(int(conn.getpeername()[1]), int(conn.getsockname()[1]))
        if hasattr(socket, "SO_PEERCRED"):
            return struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                       struct.calcsize("3i")))[0]
        if SYSTEM == "macos":
            return int(conn.getsockopt(getattr(socket, "SOL_LOCAL", _SOL_LOCAL),
                                       getattr(socket, "LOCAL_PEERPID", _LOCAL_PEERPID)))
    except (OSError, AttributeError, struct.error, IndexError, TypeError, ValueError):
        return 0
    return 0


def _readable_now(conn: socket.socket) -> bool:
    """La socket a-t-elle quelque chose à lire (ou est-elle fermée), sans attendre ?
    poll() et non select() (19/10) : select lève ValueError pour un numéro de
    descripteur ≥ 1024, ce qui faisait croire le hook parti (question retirée,
    action refusée) dans un cœur qui a beaucoup de fichiers ouverts. Windows n'a
    pas poll ; son select n'a pas cette limite sur le numéro."""
    if hasattr(select, "poll"):
        p = select.poll()
        p.register(conn.fileno(), select.POLLIN | select.POLLPRI)
        return bool(p.poll(0))  # POLLHUP, POLLERR, POLLNVAL sont toujours rapportés
    readable, _, _ = select.select([conn], [], [], 0)
    return bool(readable)


def peer_alive(conn: socket.socket) -> bool:
    """Le client est-il toujours au bout de la socket ? (il n'écrit plus rien
    après sa demande : lisible = fermé, ou donnée inattendue)."""
    try:
        if not _readable_now(conn):
            return True
        # MSG_DONTWAIT n'existe pas sous Windows ; lisible = la lecture ne bloque pas.
        return conn.recv(1, socket.MSG_PEEK | getattr(socket, "MSG_DONTWAIT", 0)) != b""
    except BlockingIOError:
        return True
    except (OSError, ValueError):
        return False


def _under_home(folder: Path) -> bool:
    try:
        Path(folder).resolve().relative_to(Path.home().resolve())
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def self_test(folder: Path) -> list[str]:
    """Le canal retrouve-t-il son propre process ? Liste des problèmes, vide si
    tout va bien. Vérifie aussi que l'ascendance des process et (hors Linux) le
    marqueur du cerveau se lisent : sans eux, tout passerait pour Claude."""
    problems: list[str] = []
    me = os.getpid()
    path = Path(folder) / f"ipc-test-{me}.sock"
    try:
        lst = listen(path, 1)
    except Exception as exc:  # noqa: BLE001 — toute panne est à dire
        return [f"canal local ({mode()}) impossible à ouvrir : {exc}"]
    # Le serveur dans un fil : en TCP, `connect` attend la preuve du serveur.
    seen: dict = {}

    def serve() -> None:
        try:
            conn, _ = lst.sock.accept()
        except OSError as exc:
            seen["error"] = exc
            return
        with conn:
            seen["admitted"] = lst.admit(conn)
            seen["pid"] = peer_pid(conn)

    try:
        lst.sock.settimeout(SELF_TEST_TIMEOUT_S)
        server = threading.Thread(target=serve, name="ipc-auto-test", daemon=True)
        server.start()
        try:
            with connect(path, SELF_TEST_TIMEOUT_S):
                server.join(SELF_TEST_TIMEOUT_S)
        finally:
            server.join(SELF_TEST_TIMEOUT_S)
        if "error" in seen:
            raise seen["error"]
        if not seen.get("admitted"):
            problems.append("le canal local refuse son propre jeton")
        if seen.get("pid") != me:
            problems.append(f"le canal local ({mode()}) ne retrouve pas le process client "
                            f"(vu {seen.get('pid', 0)}, attendu {me}) : toute commande passerait pour Claude")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"canal local ({mode()}) en échec : {exc}")
    finally:
        try:
            lst.close()
        except OSError:
            pass
    if lst.mode == "tcp" and SYSTEM == "windows" and not _under_home(Path(folder)):
        problems.append("Astrel est installé hors du profil de l'utilisateur : d'autres comptes du poste "
                        "pourraient lire le jeton du canal local")
    if proc.parent(me) is None:
        problems.append("parent du process illisible : toute commande passerait pour Claude")
    if not proc.uses_cgroup() and proc.brain_marker(me) is None:
        problems.append("environnement des process illisible : toute commande passerait pour Claude")
    return problems
