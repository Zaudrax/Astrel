"""Fournisseur d'IA par son CLI OFFICIEL (22/10, plan P27 ; demande de l'utilisateur : « le plus d'IA
connues utilisables sur Astrel », Codex et Grok Build avec l'abonnement de l'utilisateur).

Le binaire du fournisseur (`codex`, `grok`) tourne avec la connexion que l'utilisateur y a faite
lui-même ; aucun jeton n'est lu ni extrait par Astrel. Un tour = un process (`codex exec`,
`grok -p`) ; `SessionCli` les enchaîne derrière l'interface d'un `subprocess.Popen` et TRADUIT
leur flux vers le format stream-json du CLI `claude`. Ainsi `Brain._translate`, le cœur, le HUD
et la consommation (P19) restent inchangés.

- `SessionCli.pid` = le process du tour en cours : `core._claude_pid` et `state.from_claude`
  voient le serveur MCP et le hook lancés par le CLI comme « Claude » (sinon le MCP passerait
  pour l'utilisateur et pourrait desserrer le garde-fou).
- Message en plein tour : mis en file, joint au tour suivant. Pas de sous-agents, ni tâches de
  fond, ni changement de modèle à chaud (réponses `control_response` en erreur).
- Configuration du CLI (MCP `astrel`/`connecteurs`, hook) dans `runtime/cli/<id>/`, dossier
  dédié (identifiants à part, réglages personnels de l'utilisateur intacts). Ce dossier est
  protégé par le garde-fou (lecture ET écriture) : un modèle ne doit pouvoir ni lire la connexion
  ni retirer son hook.
- Garde-fou : `hooks/permission_hook_cli.py` (outil inconnu, charge illisible, plantage = refus).
  Les outils ne sont permis que si le refus du hook est PROUVÉ (lot 0, `Capacites.hook_refus`),
  en mode PRUDENT, jamais sous Autonome TOTAL ni levée (`fournisseurs.refus_choix`). Jamais
  `--always-approve` ni équivalent (`verifier_argv`).

⚠️ NON PROUVÉ au 22/10 : aucun des deux CLI n'est installé ici. Formats, options et hooks de
`codex.py` et `grok.py` viennent de la documentation ; le lot 0 (runtime/equipe/p27/
lot0-procedure.md) les vérifie. D'ici là, `Capacites.prouve` est faux : le choix est refusé.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .. import config
from ..platform import enfants

log = logging.getLogger("astrel.fournisseurs_cli")

DOSSIER = config.RUNTIME / "cli"  # runtime/cli/<id>/ : un dossier par CLI (protégé par le garde-fou)
HOOK_CLI = config.ROOT / "hooks" / "permission_hook_cli.py"
MODE_OUTILS, MODE_CONVERSATION = "outils", "conversation"
MODELE_DEFAUT = "defaut"  # pas d'option de modèle : celui que le CLI choisit

# Options qui approuvent tout ou contournent bac à sable et hook : JAMAIS (consigne du plan P27).
INTERDITS = frozenset({
    "--always-approve", "--auto-approve", "--yolo", "--full-auto",
    "--dangerously-bypass-approvals-and-sandbox", "--dangerously-skip-permissions",
    "--danger-full-access", "danger-full-access", "--no-sandbox",
})
# Clés d'autres services retirées de l'environnement du CLI (leçon P11 : une variable du shell
# primait en silence sur l'abonnement). `fournisseurs.env_cerveau` retire déjà ANTHROPIC_*/CLE_API_*.
ENV_PURGE = ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_ORG_ID",
             "OPENAI_PROJECT_ID", "XAI_API_KEY", "XAI_BASE_URL", "GROK_API_KEY", "GROK_CODE_XAI_API_KEY",
             "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "AZURE_OPENAI_API_KEY",
             "CLAUDE_CODE_OAUTH_TOKEN")
ENV_PURGE_PREFIXES = ("ANTHROPIC_", "CLE_API_")  # déjà retirées par `env_cerveau` : redit ici, sans dépendre de lui
DETECTION_TTL_S = 30.0
DETECTION_DELAI_S = 3.0   # par commande de détection (version, connexion) : un CLI figé ne bloque pas les Réglages
ANNULATION_DELAI_S = 3.0  # SIGINT, puis SIGKILL du groupe
BALAYAGE_DELAI_S = 0.5    # le CLI fini, ses enfants ont ce délai pour sortir avant le SIGKILL du groupe
_TAIL_STDERR = 20
# Message d'un CLI dont la conversation reprise n'existe plus : SEUL cas où l'on repart à neuf
# (fiche 3 du journal p27 : une panne passagère effaçait la conversation et relançait le message).
_SESSION_PERDUE = re.compile(
    r"\b(session|conversation|thread|rollout)\b[^\n]{0,60}\b(introuvable|inconnue?|not found|unknown|"
    r"does not exist|doesn't exist|no such|n'existe (pas|plus))\b", re.I)
_MODELE_OK = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}")


@dataclass(frozen=True)
class Capacites:
    """Ce que le lot 0 a PROUVÉ sur cette machine pour ce CLI (tout faux tant qu'il n'est pas fait)."""
    prouve: bool = False       # un tour, la reprise et l'annulation vus en réel (C3, C4, C8)
    hook_refus: bool = False   # le hook REFUSE vraiment (C6, C7) : seule condition des outils
    mcp: bool = False          # MCP `astrel` appelé (C5)
    reprise: bool = False      # reprise d'une conversation (C4)
    usage: bool = False        # tokens lus dans le flux (C9)
    images: bool = False       # image jointe acceptée
    sous_agents: bool = False  # jamais (plan P27)


@dataclass
class Detection:
    installe: bool
    chemin: str = ""
    version: str = ""
    connecte: bool | None = None  # None : connexion non vérifiable par une commande du CLI
    raison: str = ""
    at: float = 0.0


@dataclass
class Commande:
    argv: list[str]
    env: dict[str, str]
    cwd: str
    entree: str | None = None  # texte écrit sur stdin (puis fermé) ; None : stdin vide


@dataclass
class EtatTour:
    """État du traducteur pendant UN tour (un process)."""
    texte: list[str] = field(default_factory=list)
    outils: dict[str, str] = field(default_factory=dict)  # id → nom, outils commencés
    usage: dict[str, int] = field(default_factory=dict)
    erreur: str = ""
    fini: bool = False     # un `result` a été émis
    sortie: bool = False   # le CLI a produit au moins un évènement traduit
    message: bool = False  # un message de l'assistant est ouvert (message_start émis)
    debut: float = field(default_factory=time.time)


# --------------------------------------------------------------------------- format stream-json de Claude

def ev_init(session_id: str, modele: str, outils: list[str], mcp: list[str], version: str, cwd: str) -> dict:
    return {"type": "system", "subtype": "init", "session_id": session_id, "model": modele, "tools": outils,
            "mcp_servers": [{"name": n, "status": "configured"} for n in mcp], "skills": [], "agents": [],
            "plugins": [], "slash_commands": [], "claude_code_version": version, "cwd": cwd}


def ev_message(etat: EtatTour) -> list[dict]:
    """Début d'un message de l'assistant (une fois par tour : le cœur sait qu'un tour tourne)."""
    if etat.message:
        return []
    etat.message = True
    return [{"type": "stream_event", "event": {"type": "message_start"}}]


def ev_texte(etat: EtatTour, texte: str) -> list[dict]:
    if not texte:
        return []
    etat.texte.append(texte)
    return ev_message(etat) + [
        {"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "text"}}},
        {"type": "stream_event", "event": {"type": "content_block_delta",
                                           "delta": {"type": "text_delta", "text": texte}}}]


def ev_outil(etat: EtatTour, tid: str, nom: str, entree: dict) -> list[dict]:
    if not tid or tid in etat.outils:
        return []
    etat.outils[tid] = nom
    return ev_message(etat) + [
        {"type": "stream_event", "event": {"type": "content_block_start",
                                           "content_block": {"type": "tool_use", "id": tid, "name": nom}}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": nom, "input": entree if isinstance(entree, dict) else {}}]}}]


def ev_resultat_outil(tid: str, texte: Any, erreur: bool) -> list[dict]:
    if not isinstance(texte, str):
        texte = json.dumps(texte, ensure_ascii=False) if texte is not None else ""
    return [{"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": texte[:4000], "is_error": bool(erreur)}]}}]


def ev_usage(etat: EtatTour, entree: Any = 0, cache: Any = 0, sortie: Any = 0) -> list[dict]:
    """Tokens du tour (si le CLI les donne) : contexte du cerveau, puis `usage` du `result`."""
    def n(v: Any) -> int:
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0

    total, lu = n(entree), n(cache)
    lu = min(lu, total) if total else lu
    etat.usage = {"input_tokens": max(0, total - lu), "cache_read_input_tokens": lu, "output_tokens": n(sortie)}
    if not any(etat.usage.values()):
        etat.usage = {}
        return []
    return [{"type": "assistant", "message": {"role": "assistant", "content": [], "usage": dict(etat.usage)}}]


def ev_fin(etat: EtatTour, erreur: str = "") -> list[dict]:
    """`result` du tour (une seule fois). `session_id` et durée sont posés par `SessionCli`."""
    if etat.fini:
        return []
    etat.fini = True
    return [{"type": "result", "subtype": "error_during_execution" if erreur else "success",
             "is_error": bool(erreur), "result": erreur or "".join(etat.texte).strip(),
             "num_turns": 1, "usage": dict(etat.usage)}]


def verifier_argv(argv: list[str]) -> None:
    """Refuse (ValueError) une ligne de commande qui approuverait tout : jamais, même par erreur."""
    bad = [a for a in argv if a in INTERDITS or a.split("=", 1)[0] in INTERDITS]
    if bad:
        raise ValueError(f"option interdite pour un fournisseur CLI : {bad[0]}")


def verifier_modele(modele: str) -> None:
    """Un nom de modèle va dans l'argv (`-m <nom>`) : jamais une option (« -c … », « --yolo »)."""
    if modele and modele != MODELE_DEFAUT and not _MODELE_OK.fullmatch(modele):
        raise ValueError(f"nom de modèle refusé : {modele[:40]!r}")


# --------------------------------------------------------------------------- un CLI

class FournisseurCli:
    """Interface d'un CLI. Les sous-classes donnent `commande`, `traduire`, `config_outils`."""

    id = ""
    nom = ""
    binaire = ""
    env_dossier = ""          # variable qui place la config et la connexion du CLI dans son dossier
    commande_connexion = ""   # à lancer par l'utilisateur dans SON terminal (affichée, jamais lancée ici)
    capacites = Capacites()

    def __init__(self) -> None:
        self._det: Detection | None = None
        self._det_lock = threading.Lock()

    # --- détection ---

    @property
    def env_binaire(self) -> str:
        """`ASTREL_CLI_CODEX`… : chemin ou ligne de commande du binaire (tests : le faux CLI)."""
        return f"ASTREL_CLI_{self.id.upper()}"

    def argv0(self) -> list[str] | None:
        override = os.environ.get(self.env_binaire, "").strip()
        if override:
            return shlex.split(override)
        found = shutil.which(self.binaire) if self.binaire else None
        return [found] if found else None

    def installe(self) -> bool:
        return self.argv0() is not None

    def dossier(self) -> Path:
        return DOSSIER / self.id

    def espace(self) -> Path:
        """Dossier de travail VIDE de la conversation seule (rien à lire par un chemin relatif)."""
        return self.dossier() / "espace"

    def detecter(self, frais: bool = False) -> Detection:
        """Installé, version, connecté. Résultat gardé `DETECTION_TTL_S` (la page Réglages la relit)."""
        with self._det_lock:
            if not frais and self._det is not None and time.time() - self._det.at < DETECTION_TTL_S:
                return self._det
        argv = self.argv0()
        if argv is None:
            det = Detection(False, raison="non installé")
        else:
            det = Detection(True, chemin=argv[-1] if len(argv) == 1 else " ".join(argv))
            env = self.env(os.environ)
            out = self._lancer_court(argv + ["--version"], env)
            det.version = (out[1].strip().splitlines() or [""])[0][:80] if out and out[0] == 0 else ""
            try:
                det.connecte = self._connexion(argv, env)
            except Exception:  # noqa: BLE001 — une détection ne fait jamais tomber les Réglages
                log.exception("connexion %s non vérifiée", self.id)
                det.connecte = None
        det.at = time.time()
        with self._det_lock:
            self._det = det
        return det

    def _connexion(self, argv: list[str], env: dict) -> bool | None:
        return None

    @staticmethod
    def _lancer_court(argv: list[str], env: dict, timeout: float | None = None) -> tuple[int, str] | None:
        try:
            p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=DETECTION_DELAI_S if timeout is None else timeout, env=env,
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return None
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    # --- environnement, configuration ---

    def env(self, base: dict | os._Environ) -> dict[str, str]:
        """Environnement du CLI : celui du cœur (marqueur du cerveau compris), sans clé d'API ni
        variable Anthropic, le dossier de configuration du CLI posé sur `runtime/cli/<id>`."""
        from .. import fournisseurs

        env = fournisseurs.env_cerveau(dict(base), "cli")
        env = {k: v for k, v in env.items() if k not in ENV_PURGE and not k.startswith(ENV_PURGE_PREFIXES)}
        if self.env_dossier:
            env[self.env_dossier] = str(self.dossier())
        return env

    def preparer(self, mode: str) -> None:
        """Dossiers en 0700, puis la configuration du CLI pour ce mode."""
        for d in (DOSSIER, self.dossier(), self.espace()):
            d.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass
        self.config_outils(mode)

    def commande_hook(self, mode: str) -> str:
        """Ligne du hook de garde-fou, telle que le CLI la lance (shell)."""
        # 23/10 (audit P29) : mode isolé comme le hook de Claude ; sans « || exit 2 » (ces CLI ne
        # lancent pas forcément la ligne par un shell).
        if os.name == "nt":  # 03/11 : sous Windows, l'invite de commandes ne connaît que les « "…" »
            return subprocess.list2cmdline(config.hook_words(HOOK_CLI, self.id, mode))
        return config.hook_command(HOOK_CLI, self.id, mode, fail_closed=False)

    def delai_hook_s(self) -> int:
        return int(config.CONFIRM_REQUEST_TIMEOUT_S + 20)

    def serveurs_mcp(self) -> dict[str, dict]:
        """MCP `astrel` et `connecteurs` comme `mcp_config.write` ; jamais les navigateurs ni Home
        Assistant (pas prouvés derrière un autre CLI). Marqueur du cerveau dans l'env : sous macOS
        et Windows, `state.from_claude` s'y fie (pas de cgroup)."""
        from .. import mcp_config
        from ..platform import proc

        commun = {"PYTHONPATH": str(config.ROOT), proc.BRAIN_VAR: "cli", **mcp_config._session_vars()}
        return {
            "astrel": {"command": config.VENV_PYTHON.as_posix(), "args": ["-m", "astrel.mcp_server"],
                       "env": dict(commun)},
            "connecteurs": {"command": config.VENV_PYTHON.as_posix(), "args": ["-m", "astrel.mcp_connectors"],
                            "env": {**commun, **mcp_config._connector_env()}},
        }

    def config_outils(self, mode: str) -> None:
        raise NotImplementedError

    @staticmethod
    def ecrire(path: Path, texte: str) -> None:
        """Écriture atomique en 0600 (la configuration peut porter des jetons de connecteurs)."""
        tmp = path.with_name("." + path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(texte)
        os.replace(tmp, path)
        os.chmod(path, 0o600)

    # --- un tour ---

    def commande(self, texte: str, session: str, modele: str, mode: str,
                 images: list[Path] | None = None) -> Commande:
        """argv, env épuré, cwd. `session` : id de conversation du CLI à reprendre ("" : neuve)."""
        raise NotImplementedError

    def traduire(self, obj: dict, etat: EtatTour) -> list[dict]:
        """Un évènement JSON du CLI → évènements au format Claude. `{"type": "_session", "id": …}`
        donne l'id de conversation du CLI (gardé par `SessionCli`, jamais transmis)."""
        raise NotImplementedError

    def outils_annonces(self, mode: str) -> list[str]:
        return []

    def session_perdue(self, stderr: str) -> bool:
        """La reprise a échoué parce que la conversation du CLI n'existe plus (et pas pour une panne).
        ⚠️ Message réel de chaque CLI NON PROUVÉ (lot 0, C4) : motif tolérant, à resserrer ensuite."""
        return bool(stderr and _SESSION_PERDUE.search(stderr))


# --------------------------------------------------------------------------- annulation

def _groupe_vivant(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # PermissionError : un membre existe encore
        return True
    return True


def balayer(pgid: int, delai: float = BALAYAGE_DELAI_S) -> None:
    """SIGKILL de ce qui reste du groupe du tour (petit-enfant qui ignore SIGINT, serveur MCP
    orphelin), après au plus `delai` pour sortir seul. Le CLI lance son tour avec
    `start_new_session` : le groupe n'est fait QUE de ses descendants."""
    if os.name == "nt":  # pragma: no cover — pas de groupe sous Windows
        return
    t0 = time.time()
    while _groupe_vivant(pgid) and time.time() - t0 < delai:
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass


def annuler(p: subprocess.Popen | None, delai: float = ANNULATION_DELAI_S) -> None:
    """SIGINT au groupe du process (le CLI et ses enfants : MCP, commandes), puis SIGKILL du
    GROUPE : même quand le CLI est sorti, un petit-enfant qui ignore SIGINT garderait stdout
    ouvert et bloquerait le tour (fiche 2 du journal p27)."""
    if p is None:
        return
    if os.name == "nt":  # pragma: no cover — pas de groupe sous Windows
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=delai)
            except subprocess.TimeoutExpired:
                p.kill()
        return
    if p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGINT)
        except OSError:
            pass
        try:
            p.wait(timeout=delai)
        except subprocess.TimeoutExpired:
            pass
    balayer(p.pid, BALAYAGE_DELAI_S if p.poll() is not None else 0.0)
    try:
        p.wait(timeout=delai)
    except subprocess.TimeoutExpired:
        log.warning("process %s toujours là après SIGKILL", p.pid)


# --------------------------------------------------------------------------- session (faux Popen)

class _Entree:
    """stdin de `SessionCli` : les lignes JSON que `Brain._write` envoie."""

    def __init__(self, on_line, on_close) -> None:
        self._buf, self._on_line, self._on_close, self.closed = "", on_line, on_close, False

    def write(self, s: str) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file")
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._on_line(line)
        return len(s)

    def flush(self) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed file")

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._on_close()


class _Lignes:
    def __init__(self, q: "queue.Queue[str | None]") -> None:
        self._q = q

    def __iter__(self) -> Iterator[str]:
        while True:
            item = self._q.get()
            if item is None:
                return
            yield item + "\n"


class SessionCli:
    """Remplace le `subprocess.Popen` du cerveau pour un fournisseur CLI (même interface : stdin,
    stdout, stderr, pid, poll, wait, kill). Un tour = un process du CLI."""

    def __init__(self, f: FournisseurCli, modele: str, mode: str, session_id: str = "", consigne: str = "",
                 base_env: dict | None = None, cwd: Path | None = None) -> None:
        self.f, self.modele, self.mode, self.consigne = f, modele or MODELE_DEFAUT, mode, consigne
        self.session_id = session_id or str(uuid.uuid4())  # id côté Astrel (UUID : `runtime/session.json`)
        self._env = f.env(os.environ if base_env is None else base_env)
        self._cwd = Path(cwd) if (cwd is not None and mode == MODE_OUTILS) else f.espace()
        self._out: queue.Queue[str | None] = queue.Queue()
        self._err: queue.Queue[str | None] = queue.Queue()
        self._todo: queue.Queue[dict | None] = queue.Queue()
        self.stdin = _Entree(self._recevoir, self._fermer)
        self.stdout = _Lignes(self._out)
        self.stderr = _Lignes(self._err)
        self._lock = threading.Lock()
        self._child: subprocess.Popen | None = None
        self._last_pid: int | None = None
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._closed = False
        self.returncode: int | None = None
        f.preparer(mode)
        threading.Thread(target=self._boucle, name=f"cli-{f.id}", daemon=True).start()

    # --- interface Popen ---

    @property
    def pid(self) -> int | None:
        """Le process du tour en cours (sinon le dernier) : `core._claude_pid` le lit."""
        with self._lock:
            return self._child.pid if self._child is not None else self._last_pid

    def poll(self) -> int | None:
        return self.returncode if self._done.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired(f"cli-{self.f.id}", timeout or 0)
        return self.returncode or 0

    def kill(self) -> None:
        self._fermer()

    terminate = kill

    # --- entrée ---

    def _emit(self, obj: dict) -> None:
        self._out.put(json.dumps(obj, ensure_ascii=False))

    def _reponse(self, rid: str, erreur: str = "") -> None:
        resp: dict[str, Any] = {"subtype": "error" if erreur else "success", "request_id": rid}
        if erreur:
            resp["error"] = erreur
        self._emit({"type": "control_response", "response": resp})

    def _recevoir(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            log.warning("cli %s : entrée illisible ignorée", self.f.id)
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "control_request":
            rid = str(msg.get("request_id") or "")
            sub = str((msg.get("request") or {}).get("subtype") or "")
            if sub == "interrupt":
                self.annuler(vider=True)
                self._reponse(rid)
            else:  # set_model, stop_task… : sans effet avec un CLI (plan P27)
                self._reponse(rid, f"{sub or 'requête'} sans effet avec un fournisseur CLI")
            return
        if msg.get("type") == "user":
            self._todo.put(msg)

    def _fermer(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stdin.closed = True
        self.annuler(vider=True)
        self._todo.put(None)

    def annuler(self, vider: bool = False) -> None:
        """Arrête le tour en cours (SIGINT puis SIGKILL du groupe), dans un fil : l'appelant (le
        cœur) n'attend pas. `vider` : les messages en file sont jetés (■, arrêt du cerveau)."""
        if vider:
            while True:
                try:
                    item = self._todo.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    self._todo.put(None)
                    break
        with self._lock:
            child = self._child
        if child is not None and child.poll() is None:
            self._cancel.set()
            threading.Thread(target=annuler, args=(child,), name=f"cli-{self.f.id}-annule", daemon=True).start()

    # --- tours ---

    @staticmethod
    def _contenu(msg: dict) -> tuple[str, list[dict]]:
        content = (msg.get("message") or {}).get("content")
        if isinstance(content, str):
            return content, []
        texts, images = [], []
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                texts.append(str(block.get("text") or ""))
            elif block.get("type") == "image" and isinstance(block.get("source"), dict):
                images.append(block["source"])
        return "\n".join(t for t in texts if t), images

    def _boucle(self) -> None:
        try:
            while True:
                msg = self._todo.get()
                if msg is None:
                    break
                batch = [msg]
                while True:  # messages arrivés pendant le tour précédent : un seul tour
                    try:
                        nxt = self._todo.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is None:
                        self._todo.put(None)
                        break
                    batch.append(nxt)
                parts = [self._contenu(m) for m in batch]
                texte = "\n\n".join(t for t, _ in parts if t)
                images = [img for _, imgs in parts for img in imgs]
                try:
                    self._tour(texte, images)
                except Exception as exc:  # noqa: BLE001 — un tour raté ne tue pas la session
                    log.exception("tour %s en échec", self.f.id)
                    etat = EtatTour()
                    self._sortir(ev_fin(etat, f"{self.f.nom} : tour en échec ({type(exc).__name__})."), etat)
        finally:
            self.returncode = 0
            self._done.set()
            self._out.put(None)
            self._err.put(None)

    # Correspondance id Astrel → id du CLI (`runtime/cli/<id>/sessions.json`, 0600).
    def _carte(self) -> dict[str, str]:
        try:
            data = json.loads((self.f.dossier() / "sessions.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _retenir(self, cli_id: str | None) -> None:
        carte = self._carte()
        carte.pop(self.session_id, None)
        if cli_id:
            carte[self.session_id] = cli_id
        carte = dict(list(carte.items())[-50:])
        try:
            self.f.ecrire(self.f.dossier() / "sessions.json", json.dumps(carte, indent=1))
        except OSError:
            log.warning("session %s non retenue", self.f.id)

    def cli_session(self) -> str:
        return self._carte().get(self.session_id, "")

    def _images(self, images: list[dict]) -> tuple[list[Path], str]:
        """Images jointes : fichiers temporaires (0600) si le CLI les accepte, sinon une note."""
        if not images:
            return [], ""
        if not self.f.capacites.images:
            return [], f"\n\n({len(images)} image(s) jointe(s) non transmise(s) : {self.f.nom} ne les reçoit pas ici.)"
        out: list[Path] = []
        d = self.f.dossier() / "images"
        d.mkdir(parents=True, exist_ok=True)
        for src in images:
            if src.get("type") != "base64":
                continue
            ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}.get(
                str(src.get("media_type")), ".png")
            path = d / f"{uuid.uuid4().hex}{ext}"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(base64.b64decode(str(src.get("data") or "")))
                out.append(path)
            except (OSError, ValueError):
                log.warning("image jointe non écrite pour %s", self.f.id)
        return out, ""

    def _tour(self, texte: str, images: list[dict]) -> None:
        paths, note = self._images(images)
        try:
            cli_id = self.cli_session()
            self._emit(ev_init(self.session_id, self.modele, self.f.outils_annonces(self.mode),
                               ["astrel", "connecteurs"] if self.mode == MODE_OUTILS else [],
                               f"{self.f.nom} (CLI)", str(self._cwd)))
            if cli_id and not self._lancer(texte + note, cli_id, paths, reprise=True):
                # Reprise refusée par le CLI parce que la conversation n'existe plus (`session_perdue`),
                # avant toute réponse : on repart d'une conversation neuve, consigne comprise, sous le
                # même id Astrel. Toute autre panne est rendue en erreur, la conversation gardée.
                log.warning("reprise %s impossible (conversation introuvable) : conversation neuve", self.f.id)
                self._retenir(None)
                cli_id = ""
            if not cli_id:
                self._lancer(self._premier(texte) + note, "", paths, reprise=False)
        finally:
            for p in paths:
                try:
                    p.unlink()
                except OSError:
                    pass

    def _premier(self, texte: str) -> str:
        """Premier message d'une conversation neuve : la consigne d'Astrel en tête (aucun CLI
        n'a une option d'invite système sûre et commune ; la reprise la garde)."""
        if not self.consigne:
            return texte
        return (f"[Consigne d'Astrel, valable pour toute la conversation]\n{self.consigne}\n\n"
                f"[Message]\n{texte}")

    def _sortir(self, outs: list[dict], etat: EtatTour) -> None:
        for out in outs:
            if out.get("type") == "result":
                out["session_id"] = self.session_id
                out["duration_ms"] = int((time.time() - etat.debut) * 1000)
            self._emit(out)

    def _lancer(self, prompt: str, cli_id: str, images: list[Path], reprise: bool) -> bool:
        """Un process. Faux seulement si une REPRISE échoue avant toute sortie parce que la
        conversation du CLI est introuvable (à refaire à neuf) ; toute autre panne est rendue."""
        etat = EtatTour()
        try:
            verifier_modele(self.modele)
            cmd = self.f.commande(prompt, cli_id, self.modele, self.mode, images)
            verifier_argv(cmd.argv)
        except ValueError as exc:  # jamais lancé : option interdite ou nom de modèle qui serait une option
            self._sortir(ev_fin(etat, f"{self.f.nom} : {exc}."), etat)
            return True
        cmd.env = dict(self._env)  # celui du cerveau (marqueur `ASTREL_BRAIN` compris), épuré
        self._cancel.clear()
        try:
            # errors="replace" : un octet non UTF-8 ne fait tomber ni la lecture de stdout ni celle de
            # stderr (fiche 3 du journal p27 : process laissé vivant, ou stderr plein et CLI bloqué).
            # 01/11 : le CLI du tour meurt avec le cœur (Linux, `platform.enfants`).
            p = enfants.popen(cmd.argv, stdin=subprocess.PIPE if cmd.entree is not None else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                 errors="replace", bufsize=1, env=cmd.env, cwd=cmd.cwd,
                                 start_new_session=(os.name != "nt"))
        except OSError as exc:
            self._sortir(ev_fin(etat, f"{self.f.nom} : impossible de lancer le CLI ({exc.strerror or exc})."), etat)
            return True
        with self._lock:
            self._child, self._last_pid = p, p.pid
        tail: deque[str] = deque(maxlen=_TAIL_STDERR)
        eof = threading.Event()

        def lire_err() -> None:
            """Ne meurt jamais avant la fin de stderr : sinon le tube se remplit et le CLI se fige."""
            assert p.stderr is not None
            try:
                for line in p.stderr:
                    try:
                        if line.strip():
                            tail.append(line.strip())
                            self._err.put(line.rstrip("\n"))
                    except Exception:  # noqa: BLE001
                        log.debug("ligne stderr %s ignorée", self.f.id, exc_info=True)
            except Exception:  # noqa: BLE001 — tube fermé sous nos pieds (process tué)
                log.debug("lecture stderr %s arrêtée", self.f.id, exc_info=True)

        lignes: queue.Queue[str | None] = queue.Queue()
        abandon = threading.Event()

        def lire_out() -> None:
            assert p.stdout is not None
            try:
                for line in p.stdout:
                    lignes.put(line)
            except Exception:  # noqa: BLE001 — tube fermé sous nos pieds (process tué)
                log.debug("lecture stdout %s arrêtée", self.f.id, exc_info=True)
            finally:
                eof.set()
                lignes.put(None)

        def veiller() -> None:
            """Le CLI sorti, un descendant qui garde stdout ouvert ne bloque pas le tour : SIGKILL
            du groupe, puis, s'il a quitté le groupe (setsid), lecture abandonnée."""
            p.wait()
            if eof.wait(BALAYAGE_DELAI_S + 1.5):
                return
            log.warning("%s sorti, stdout encore tenu par un descendant : groupe tué", self.f.id)
            balayer(p.pid, 0.0)
            if not eof.wait(1.5):
                log.warning("%s : stdout tenu hors du groupe, lecture abandonnée", self.f.id)
                abandon.set()

        t_err = threading.Thread(target=lire_err, name=f"cli-{self.f.id}-err", daemon=True)
        t_err.start()
        threading.Thread(target=lire_out, name=f"cli-{self.f.id}-out", daemon=True).start()
        threading.Thread(target=veiller, name=f"cli-{self.f.id}-veille", daemon=True).start()
        if cmd.entree is not None and p.stdin is not None:
            try:
                p.stdin.write(cmd.entree)
                p.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
        illisible = ""  # dernière ligne non JSON de stdout
        try:
            while True:
                try:
                    raw = lignes.get(timeout=0.2)
                except queue.Empty:
                    if abandon.is_set():
                        break
                    continue
                if raw is None:
                    break
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    illisible = raw
                    self._err.put(f"(sortie non JSON) {raw[:300]}")
                    continue
                if not isinstance(obj, dict):
                    continue
                try:
                    outs = self.f.traduire(obj, etat)
                except Exception as exc:  # noqa: BLE001 — format inattendu : tour arrêté, CLI tué
                    log.exception("évènement %s illisible par le traducteur", self.f.id)
                    annuler(p)
                    self._sortir(ev_fin(etat, f"{self.f.nom} : réponse illisible du CLI ({type(exc).__name__}), "
                                              "tour arrêté."), etat)
                    break
                for out in outs:
                    if out.get("type") == "_session":
                        if out.get("id"):
                            self._retenir(str(out["id"]))
                        continue
                    etat.sortie = True
                    self._sortir([out], etat)
            p.wait()
            balayer(p.pid)  # enfants orphelins du tour (serveurs MCP…) : un tour = un process
        finally:
            if p.poll() is None:  # exception pendant la lecture : le CLI ne reste jamais vivant
                annuler(p)
            t_err.join(timeout=2)
            with self._lock:
                self._child = None
        if etat.fini:
            return True
        if self._cancel.is_set():
            self._sortir(ev_fin(etat), etat)  # arrêt demandé : fin de tour normale, sans erreur
            return True
        if p.returncode != 0:
            if reprise and not etat.sortie and self.f.session_perdue("\n".join(tail)):
                return False  # conversation du CLI introuvable : seul cas où l'on repart à neuf
            why = etat.erreur or (tail[-1] if tail else "")
            self._sortir(ev_fin(etat, f"{self.f.nom} s'est arrêté (code {p.returncode})"
                                      + (f" : {why[:300]}" if why else ".")), etat)
            return True
        if not etat.sortie and not etat.erreur:  # code 0 sans rien de lisible : jamais un succès vide
            self._sortir(ev_fin(etat, f"{self.f.nom} n'a rien répondu de lisible"
                                      + (f" ({illisible[:300]})." if illisible else ".")), etat)
            return True
        self._sortir(ev_fin(etat, etat.erreur), etat)
        return True
