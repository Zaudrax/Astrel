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

03/11 (lot de sécurité B, T43.5-A, accord explicite de l'utilisateur) : le serveur ne CROIT plus le
client. N'importe quel process du même utilisateur peut se connecter (le hook descend lui aussi de
Claude) : avant, il faisait afficher la description et la commande de son choix, et « oui pour
30 min » accordait les clés qu'il envoyait. Désormais la demande (format 2) ne porte que l'outil,
son ENTRÉE (`input`, bornée à `MAX_ENTREE` octets par le hook, sans changer le verdict) et `cwd` ;
le serveur recalcule lui-même le verdict (`guard.refused`, `guard.assess`, même mode : celui du
fichier `autonomy.json`) et la carte montre CE verdict, « oui pour 30 min » n'accorde que SES clés.
Rien à demander d'après ce calcul : pas de carte (oui seulement si TOUTES ses clés sont déjà
accordées pour 30 min, sinon non). Ancien format (sans `input`), entrée illisible ou trop grande :
refusé, sans carte.

04/11 (lot B, reprise) : le hook relit ce fichier à chaque appel, alors que le cœur en marche garde
l'ANCIEN en mémoire jusqu'à sa relance. La demande porte donc AUSSI les champs d'avant (`desc`, `cmd`,
`key`, `keys`, `guard`, `lift`, `cats`) : l'ancien cœur les lit et ignore `input`, le nouveau les
ignore. Aucun mélange possible côté nouveau cœur : il ne lit que `tool`, `input` et `cwd`.
"""
from __future__ import annotations

import inspect
import json
import logging
import socket
import threading
import time
from typing import Any, Callable

from . import config, guard, ipc
from .ipc import peer_alive  # noqa: F401 — nom public d'origine (tests, appelants)

log = logging.getLogger("astrel.confirm")

# 03/11 (T43.5-A) : format des demandes du hook ; taille au plus de l'entrée envoyée (JSON, octets)
# et de la ligne lue par le serveur (entrée + enveloppe).
FORMAT = 2
MAX_ENTREE = 256 * 1024
MAX_LIGNE = MAX_ENTREE + 64 * 1024  # 04/11 : + les champs d'avant, envoyés pour l'ancien cœur
_GROS = 2048  # au-delà, une valeur que le garde-fou ne lit pas est remplacée par sa taille
_LUES = frozenset({"command", "file_path", "notebook_path", "path", "url", "pattern", "glob"})


def _taille(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _juge(v: "guard.Verdict") -> tuple:
    """Ce qui compte dans un verdict pour la carte et pour « oui pour 30 min »."""
    return (v.needs_confirm, v.desc, v.key, tuple(v.keys), v.guard, bool(getattr(v, "lift", False)),
            tuple(getattr(v, "cats", ()) or ()))


def entree_bornee(tool: str, tool_input: Any, cwd: str = "") -> dict | None:
    """Côté hook (03/11, T43.5-A) : l'entrée de l'outil telle qu'envoyée au cœur, au plus
    `MAX_ENTREE` octets. Trop grande : les longues valeurs que le garde-fou ne lit pas (contenu
    d'un Write, texte d'un Edit…) sont remplacées par leur taille, et SEULEMENT si le verdict
    reste exactement le même (sinon le cœur jugerait autre chose que ce qui s'exécuterait).
    None : illisible, ou impossible à borner sans changer le verdict (le hook refuse alors)."""
    if not isinstance(tool_input, dict):
        return None
    try:
        if _taille(tool_input) <= MAX_ENTREE:
            return tool_input
        reduite = {}
        for k, v in tool_input.items():
            if k not in _LUES and _taille(v) > _GROS:
                reduite[k] = f"[{_taille(v)} octets non transmis]"
            else:
                reduite[k] = v
        if _taille(reduite) > MAX_ENTREE:
            return None
        if _juge(guard.assess(tool, tool_input, cwd=cwd or None)) != _juge(guard.assess(tool, reduite,
                                                                                        cwd=cwd or None)):
            return None
        return reduite
    except (TypeError, ValueError):
        return None

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
                    if len(raw) > MAX_LIGNE:  # 03/11 (T43.5-A) : demande bornée
                        raise ValueError("demande de confirmation trop grande")
                req = json.loads(raw.decode("utf-8"))
                ok = self._decide(req, conn, received_at)
                conn.sendall((json.dumps({"allow": ok is True}) + "\n").encode())
            except Exception as exc:
                log.exception("échec de confirmation: %s", exc)
                try:
                    conn.sendall((json.dumps({"allow": False, "error": str(exc)}) + "\n").encode())
                except OSError:
                    pass

    def _decide(self, req: Any, conn: socket.socket, received_at: float) -> bool:
        """03/11 (T43.5-A) : le verdict est RECALCULÉ ici, depuis l'outil et son entrée ; ce que le
        client dit de la description, de la commande, des clés, du garde-fou, de la levée ou des
        catégories est ignoré. Dans le doute (ancien format, entrée illisible) : non, sans carte."""
        if not isinstance(req, dict) or "input" not in req or req.get("v") != FORMAT:
            log.warning("demande de confirmation à l'ancien format (sans l'entrée de l'outil) : refusée sans carte")
            return False
        tool, inp, cwd = req.get("tool"), req.get("input"), req.get("cwd") or ""
        if not isinstance(tool, str) or not tool or not isinstance(inp, dict) or not isinstance(cwd, str):
            log.warning("demande de confirmation illisible : refusée sans carte")
            return False
        if guard.refused(tool):
            log.warning("demande de confirmation pour un outil refusé d'office (%s) : refusée sans carte", tool)
            return False
        v = guard.assess(tool, inp, cwd=cwd or None)
        if not v.needs_confirm:
            # Rien à demander : déjà accordé pour 30 min (plusieurs agents, même action), ou demande
            # sans objet (forgée, ou garde-fou du hook différent de celui chargé ici) : non.
            covered = bool(v.keys) and all(v.keys) and all(guard.granted(k) for k in v.keys)
            if not covered:
                log.warning("demande de confirmation sans objet d'après le cœur (%s : %s) : refusée sans carte",
                            tool, guard.short_command(tool, inp))
            return covered
        extra = {"cats": list(v.cats or ())} if self._with_cats else {}  # 23/10 (plan P29)
        # Une seule question à la fois : le micro n'est pas partageable.
        with self._lock:
            return self._ask(
                tool, v.desc, guard.short_command(tool, inp), v.key,
                keys=list(v.keys), guard=bool(v.guard), cwd=cwd,
                received_at=received_at, alive=lambda: peer_alive(conn),
                lift=bool(getattr(v, "lift", False)), **extra,
            ) is True


def request(tool: str, desc: str, cmd: str, key: str = "", timeout: float | None = None,
            keys: list[str] | tuple[str, ...] | None = None, guard: bool = False, cwd: str = "",
            lift: bool = False, cats: list[str] | tuple[str, ...] | None = None,
            tool_input: dict | None = None) -> bool | None:
    """Côté hook : demande la décision. None si Astrel n'est pas joignable.
    `cats` (23/10, plan P29) : catégories de l'action (`Verdict.cats`) ; None = non envoyé.
    03/11 (T43.5-A) : le nouveau cœur ne lit que `tool`, `tool_input` (déjà borné par
    `entree_bornee`) et `cwd`, et recalcule le reste. 04/11 : `desc`, `cmd`, `key`, `keys`, `guard`,
    `lift`, `cats` partent AUSSI, pour le cœur d'avant encore en marche (qui ignore `input`) ; le
    nouveau les ignore. Sans `tool_input` (appelant d'avant le 03/11) : ancien format seul, que le
    nouveau cœur refuse (non)."""
    timeout = timeout or config.CONFIRM_REQUEST_TIMEOUT_S
    try:
        s = ipc.connect(config.CONFIRM_SOCKET, timeout)
    except OSError:
        return None
    with s:
        req: dict = {"tool": tool, "desc": desc, "cmd": cmd, "key": key, "guard": bool(guard), "cwd": cwd,
                     "lift": bool(lift)}
        if keys is not None:
            req["keys"] = list(keys)
        if cats is not None:
            req["cats"] = [str(c) for c in cats]
        if tool_input is not None:
            req.update(v=FORMAT, input=tool_input)
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
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
