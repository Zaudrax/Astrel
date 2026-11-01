"""Canal IPC entre le hook de permission et le cœur d'Astrel.

Le hook est un process court lancé par Claude Code ; il ne possède ni le micro
ni l'écran. Il interroge donc le cœur via une socket Unix (0600), qui pose la
question — à l'oral ET sur le HUD, la première réponse gagne — et renvoie la
décision. Une seule question à la fois : le micro n'est pas partageable.

15/10 (docs/DURCISSEMENT-GARDE-FOU.md) : la demande porte aussi `keys` (toutes
les autorisations « 30 min » nécessaires), `guard` (touche au garde-fou : même
« Autonome TOTAL » la fait confirmer) et `cwd` (dossier courant de Claude).
Le serveur note l'HEURE D'ARRIVÉE (le délai compte depuis elle, pas depuis la
fin de la question précédente) et passe `alive()` : faux dès que le hook a
raccroché (délai du CLI, tour interrompu), pour ne pas poser une question
périmée, ou la retirer de l'écran.

18/10 (plan P6, portage) : la socket passe par `ipc` (Unix sous Linux et macOS,
127.0.0.1 + jeton sous Windows). Format des demandes et réponses inchangé.

18/10 (plan P7) : la demande porte aussi `lift` (`Verdict.lift` : action levable
pendant une levée du garde-fou). Absent (ancien hook) : faux, donc demandé.

23/10 (plan P29) : la demande porte aussi `cats` (`Verdict.cats`, liste d'ids de
`limites.py`). Absent (ancien hook) : `cats=None`, le cœur garde les règles d'avant (rien ne
se desserre). Mal formé (pas une liste de textes) : `[]`, que `limites.decide` refuse
toujours. `cats` n'est passé qu'à un `ask` qui l'accepte (cœur d'avant le lot B : rien).
"""
from __future__ import annotations

import inspect
import json
import logging
import socket
import threading
import time
from typing import Callable

from . import config, ipc
from .ipc import peer_alive  # noqa: F401 — nom public d'origine (tests, appelants)

log = logging.getLogger("astrel.confirm")

# ask(tool, desc, cmd, key, *, keys, guard, cwd, received_at, alive, lift[, cats]) -> bool
Asker = Callable[..., bool]


def _accepts_cats(ask: Asker) -> bool:
    """L'`ask` du cœur prend-il `cats` (cœur du lot B du plan P29) ? Sinon on ne le passe pas :
    un argument inattendu ferait échouer la question, donc refuser toute action."""
    try:
        params = inspect.signature(ask).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "cats" or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


def parse_cats(req: dict) -> list[str] | None:
    """`cats` d'une demande : None si absent (ancien hook) ; [] si mal formé (toujours demandé)."""
    if "cats" not in req:
        return None
    cats = req.get("cats")
    if not isinstance(cats, list) or not all(isinstance(c, str) for c in cats):
        return []
    return list(cats)


class ConfirmServer:
    def __init__(self, ask: Asker) -> None:
        self._ask = ask
        self._with_cats = _accepts_cats(ask)
        self._listener: ipc.Listener | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        path = config.CONFIRM_SOCKET
        self._listener = ipc.listen(path, 4)
        self._listener.sock.settimeout(1.0)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        log.info("serveur de confirmation sur %s (%s)", path, self._listener.mode)

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.sock.close()
        config.CONFIRM_SOCKET.unlink(missing_ok=True)

    def _serve(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        received_at = time.time()  # avant d'attendre son tour derrière une autre question
        with conn:
            try:
                if self._listener is not None and not self._listener.admit(conn):
                    raise ValueError("jeton du canal absent ou faux")
                conn.settimeout(config.CONFIRM_REQUEST_TIMEOUT_S)
                raw = b""
                while not raw.endswith(b"\n"):
                    chunk = conn.recv(8192)
                    if not chunk:
                        break
                    raw += chunk
                req = json.loads(raw.decode("utf-8"))
                keys = req.get("keys")
                keys = [str(k) for k in keys] if isinstance(keys, list) else None
                extra = {"cats": parse_cats(req)} if self._with_cats else {}  # 23/10 (plan P29)
                # Une seule question à la fois : le micro n'est pas partageable.
                with self._lock:
                    ok = self._ask(
                        req.get("tool", "?"), req.get("desc", ""), req.get("cmd", ""), req.get("key", ""),
                        keys=keys, guard=bool(req.get("guard")), cwd=str(req.get("cwd") or ""),
                        received_at=received_at, alive=lambda: peer_alive(conn),
                        lift=req.get("lift") is True, **extra,
                    )
                conn.sendall((json.dumps({"allow": bool(ok)}) + "\n").encode())
            except Exception as exc:
                log.exception("échec de confirmation: %s", exc)
                try:
                    conn.sendall((json.dumps({"allow": False, "error": str(exc)}) + "\n").encode())
                except OSError:
                    pass


def request(tool: str, desc: str, cmd: str, key: str = "", timeout: float | None = None,
            keys: list[str] | tuple[str, ...] | None = None, guard: bool = False, cwd: str = "",
            lift: bool = False, cats: list[str] | tuple[str, ...] | None = None) -> bool | None:
    """Côté hook : demande la décision. None si Astrel n'est pas joignable.
    `cats` (23/10, plan P29) : catégories de l'action (`Verdict.cats`) ; None = non envoyé."""
    timeout = timeout or config.CONFIRM_REQUEST_TIMEOUT_S
    try:
        s = ipc.connect(config.CONFIRM_SOCKET, timeout)
    except OSError:
        return None
    with s:
        req = {"tool": tool, "desc": desc, "cmd": cmd, "key": key, "guard": bool(guard), "cwd": cwd,
               "lift": bool(lift)}
        if keys is not None:
            req["keys"] = list(keys)
        if cats is not None:
            req["cats"] = [str(c) for c in cats]
        s.sendall((json.dumps(req) + "\n").encode())
        raw = b""
        try:
            while not raw.endswith(b"\n"):
                chunk = s.recv(4096)
                if not chunk:
                    break
                raw += chunk
        except socket.timeout:
            return None
    if not raw:
        return None
    try:
        # 22/10 (P27, relecteur) : `bool("false")` est vrai ; seul le booléen true autorise.
        return json.loads(raw.decode("utf-8")).get("allow") is True
    except Exception:
        return None
