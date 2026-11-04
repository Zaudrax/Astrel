"""État d'Astrel et canal de contrôle local.

L'état est publié sur le bus (pour le HUD) ET recopié dans `runtime/state.json`,
que la bulle lit sans dépendance ni connexion : elle peut être fermée ou
relancée sans gêner l'assistant.

Les commandes (bulle, raccourcis clavier, `./run.sh --send`) arrivent par une
socket Unix en 0600 : seul l'utilisateur de la session peut s'en servir.

18/10 (plan P6, portage) : la socket passe par `ipc` (Unix sous Linux et macOS,
127.0.0.1 + jeton sous Windows) et l'examen des process par `platform.proc`
(/proc et cgroup sous Linux, inchangés ; psutil et marqueur `ASTREL_BRAIN`
ailleurs). Les fonctions publiques d'ici gardent leurs noms et signatures.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from typing import Callable

from . import config, ipc
from .events import bus
from .platform import proc

log = logging.getLogger("astrel.state")

# États, repris tels quels par la bulle et le HUD.
BOOTING = "booting"      # chargement des modèles
SLEEPING = "sleeping"    # en veille, le mot de réveil est écouté
LISTENING = "listening"  # enregistre la phrase
THINKING = "thinking"    # transcription ou Claude qui réfléchit
WORKING = "working"      # Claude exécute un outil
SPEAKING = "speaking"    # Astrel parle
ASKING = "asking"        # attend une confirmation
OFF = "off"              # micro coupé à la demande
# La pause de la VOIX n'est pas un état : Astrel peut travailler et réfléchir
# en pause (il n'écoute plus, en revanche : ni conversation, ni « Hey Jarvis »).
# C'est un drapeau (`paused`), comme `muted` pour le micro.

ALL = (BOOTING, SLEEPING, LISTENING, THINKING, WORKING, SPEAKING, ASKING, OFF)


class StatePublisher:
    """Tient l'état courant ; le publie sur le bus et dans le fichier de la bulle."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data = {"state": BOOTING, "text": "", "since": time.time(), "muted": False, "paused": False,
                      "detail": "", "usage": "conversation"}

    def set(
        self,
        state: str | None = None,
        text: str | None = None,
        muted: bool | None = None,
        detail: str | None = None,
        paused: bool | None = None,
        unless: tuple[str, ...] = (),
        only: tuple[str, ...] = (),
        usage: str | None = None,
    ) -> bool:
        """`unless` / `only` : condition sur l'état COURANT, vérifiée sous le
        verrou (le cerveau et l'écoute écrivent depuis deux fils ; un test puis
        un `set` séparés laissaient une tâche de fond écraser « j'écoute »).
        Renvoie False si rien n'a changé pour cette raison."""
        with self._lock:
            if (unless and self._data["state"] in unless) or (only and self._data["state"] not in only):
                return False
            if state is not None and state != self._data["state"]:
                self._data["since"] = time.time()
                # Le détail (« Bash : npm test ») n'a de sens que dans son état.
                if detail is None:
                    self._data["detail"] = ""
            if state is not None:
                self._data["state"] = state
            if text is not None:
                self._data["text"] = text
            if muted is not None:
                self._data["muted"] = muted
            if paused is not None:
                self._data["paused"] = paused  # voix en pause (⏸) : le travail continue, rien ne sort
            if detail is not None:
                self._data["detail"] = detail
            if usage is not None:
                self._data["usage"] = usage  # mode d'usage (conversation, silencieux, dictée) : la bulle l'affiche
            payload = dict(self._data)
        bus.publish("state", **payload)
        tmp = config.STATE_FILE.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, config.STATE_FILE)
        except OSError as exc:
            log.debug("écriture de l'état impossible: %s", exc)
        return True

    @property
    def current(self) -> str:
        with self._lock:
            return str(self._data["state"])

    @property
    def muted(self) -> bool:
        with self._lock:
            return bool(self._data["muted"])


def read_state() -> dict:
    """Côté bulle : lit l'état publié."""
    try:
        return json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"state": OFF, "text": "", "since": 0.0, "muted": False, "paused": False, "detail": ""}


def descends_from(pid: int, ancestor: int | None, depth: int = 64) -> bool:
    """`pid` est-il `ancestor` ou l'un de ses descendants ? (/proc sous Linux, psutil ailleurs)"""
    return proc.descends_from(pid, ancestor, depth)


def peer_pid(conn: socket.socket) -> int:
    """pid du client du canal (SO_PEERCRED sous Linux), 0 si inconnu."""
    return ipc.peer_pid(conn)


# --- 15/10 : « la commande vient-elle de Claude ? » (docs/DURCISSEMENT-GARDE-FOU.md) ---
# La descendance seule se contournait : un process DÉTACHÉ (double fork, setsid,
# nohup … &) est rattaché à systemd et ne descend plus de Claude. Mais il reste
# dans le cgroup du service astrel.service : tout process de ce cgroup autre que
# le cœur vient du cerveau (son Bash, ses scripts, ses serveurs MCP). La bulle et
# le Chrome du HUD, lancés par la session GNOME, ont chacun leur propre cgroup.
SERVICE_UNIT = os.environ.get("ASTREL_SERVICE_UNIT", "astrel.service")


def cgroup_of(pid: int) -> str:
    """Chemin du cgroup v2 du process (« /user.slice/…/astrel.service »), vide si
    illisible (et toujours hors Linux)."""
    return proc.cgroup_of(pid)


def service_cgroup() -> str:
    """Le cgroup du cœur s'il tourne en service (astrel.service), vide sinon :
    lancé depuis un terminal, son cgroup est celui du terminal, partagé avec le
    shell de l'utilisateur, qui ne doit pas passer pour Claude."""
    own = cgroup_of(os.getpid())
    return own if own.rsplit("/", 1)[-1] == SERVICE_UNIT else ""


def from_claude(pid: int, claude_pid: int | tuple[int, ...] | None, service_cg: str | None = None) -> bool:
    """Le programme `pid` peut-il être Claude (ou l'un de ses process) ?

    Vrai si : pid inconnu ou disparu (dans le doute, suspect), descendant du
    process Claude, ou membre du cgroup du service (hors le cœur lui-même).
    macOS et Windows (pas de cgroup) : membre du Job Object du cerveau (Windows,
    03/11), porteur du marqueur `ASTREL_BRAIN` du cerveau, ou environnement
    illisible (P6 ; sous macOS, plus faible qu'un cgroup).
    04/11 (moteur local, lot 4) : `claude_pid` peut être un tuple (le cerveau ET le CLI du modèle local avec
    outils) : descendre de l'un d'eux suffit."""
    if pid <= 0 or not proc.exists(pid):
        return True
    ancetres = claude_pid if isinstance(claude_pid, tuple) else (claude_pid,)
    if any(descends_from(pid, a) for a in ancetres):
        return True
    if pid == os.getpid():
        return False  # le cœur lui-même (tests, appels internes)
    if not proc.uses_cgroup():
        if proc.in_brain_job(pid):  # 03/11 : Windows, Job Object du cerveau (platform/proc.py)
            return True
        marked = proc.brain_marker(pid)
        if marked is None:
            log.debug("environnement du process %s illisible : suspect", pid)
        return marked is not False
    service_cg = service_cgroup() if service_cg is None else service_cg
    if service_cg:
        cg = cgroup_of(pid)
        if not cg or cg == service_cg or cg.startswith(service_cg + "/"):
            return True
    return False


def confinement_actif() -> bool:
    """04/11 (lot B, H3) : un process DÉTACHÉ de Claude (setsid, double fork, environnement vidé) reste-t-il
    reconnu ? Linux : le cœur tourne en service (cgroup `astrel.service`) ; Windows : le cerveau est dans le
    Job Object. Ailleurs (macOS, `./run.sh`, WSL2 sans systemd, rattachement raté) : non, seules
    l'ascendance et le marqueur `ASTREL_BRAIN` le reconnaissent, et le garde-fou ne juge pas leur retrait."""
    if proc.uses_cgroup():
        return bool(service_cgroup())
    return proc.job_actif()


def tcp_peer_pid(client_port: int, server_port: int) -> int:
    """Process qui tient le bout client d'une connexion locale au port du HUD
    (même principe que SO_PEERCRED, qui n'existe pas en TCP) : sous Linux, inode
    de la socket dans /proc/net/tcp, puis « socket:[inode] » dans /proc/*/fd ;
    ailleurs, psutil. 0 si introuvable (process d'un autre utilisateur, disparu…)."""
    return proc.tcp_owner(client_port, server_port)


class ControlServer:
    """Reçoit une commande texte par connexion (wake, toggle, reset, stop…).

    14/10 : chaque commande porte `_from_claude` (vrai si le programme qui
    l'envoie descend du process Claude du cerveau : son Bash, un script qu'il
    lance, le serveur MCP ; depuis le 15/10 aussi s'il est dans le cgroup du
    service ou introuvable, voir `from_claude`). Le cœur refuse alors ce qui desserrerait le
    garde-fou (lever l'arrêt d'urgence, changer le mode, répondre à une
    confirmation) : le texte de la commande ne suffit pas à l'en empêcher."""

    def __init__(self, claude_pid: Callable[[], int | None] | None = None) -> None:
        self._listener: ipc.Listener | None = None
        self._stop = threading.Event()
        self._claude_pid = claude_pid or (lambda: None)

    def start(self) -> None:
        path = config.CONTROL_SOCKET
        self._listener = ipc.listen(path, 8)
        self._listener.sock.settimeout(1.0)
        threading.Thread(target=self._serve, name="control", daemon=True).start()
        log.info("canal de contrôle sur %s (%s)", path, self._listener.mode)

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.sock.close()
        config.CONTROL_SOCKET.unlink(missing_ok=True)

    def _serve(self) -> None:
        assert self._listener is not None
        listener = self._listener
        while not self._stop.is_set():
            try:
                conn, _ = listener.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                cmd = ""
                try:
                    conn.settimeout(3.0)
                    if not listener.admit(conn):
                        raise ValueError("jeton du canal absent ou faux")
                    cmd = conn.recv(8192).decode("utf-8").strip()
                    # Texte simple (« wake », « announce Rappel : … ») ou JSON complet.
                    payload = json.loads(cmd) if cmd.startswith("{") else {"cmd": cmd}
                    if not isinstance(payload, dict):
                        raise ValueError("commande illisible")
                    # Ce que le client écrit lui-même dans ces champs est écrasé.
                    payload["_from_claude"] = from_claude(peer_pid(conn), self._claude_pid())
                    payload["_via"] = "socket"
                    # 04/11 (lot B, H3) : sans confinement, le cœur ne laisse rien desserrer par ce canal.
                    payload["_confine"] = confinement_actif()
                    reply = bus.command(payload)
                    text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
                    conn.sendall((text + "\n").encode())
                except Exception as exc:
                    log.warning("commande %r en échec: %s", cmd, exc)


def send_command(cmd: str, timeout: float = 3.0) -> str | None:
    """Côté client (bulle, raccourci, CLI). None si Astrel ne tourne pas."""
    try:
        s = ipc.connect(config.CONTROL_SOCKET, timeout)
    except OSError:
        return None
    with s:
        s.sendall(cmd.encode())
        try:
            return s.recv(4096).decode("utf-8").strip()
        except socket.timeout:
            return None
