"""`./run.sh --doctor` (ou `./run.sh doctor`) : UN contrôle de santé, en lecture seule.

Idées HERMES 1, 2, 46, 47 et 70 (`docs/idees/HERMES-cli-docs-tests.md`). Une ligne
par contrôle, « OK » ou « ALERTE » et la raison ; code de sortie 1 s'il reste une
alerte. Rien n'est écrit, sauf `--accept`, qui retient l'empreinte du garde-fou et
que seul l'utilisateur lance, depuis son terminal (refusé à un process de Claude).

Ce qui est vérifié :
- le hook de permission est déclaré dans les réglages du CLI et REFUSE quand il
  plante : entrée illisible, garde-fou qui lève une exception, garde-fou
  impossible à importer. Un hook qui sort en code 1 sans décision laisse passer
  l'action (erreur « non bloquante » de Claude Code) : c'est l'alerte visée. Les
  essais ne vont jamais jusqu'à la demande de confirmation (aucune carte) ;
- l'empreinte (sha256) des fichiers du garde-fou n'a pas changé depuis `--accept` ;
- les serveurs MCP « astrel » et « connecteurs » répondent (poignée de main MCP,
  liste des outils) ;
- les droits des fichiers sensibles (jeton et clé du HUD, profil du HUD, jetons
  MCP, secrets, sockets) : propriétaire et aucun accès pour les autres. Ni leur
  contenu ni leur chemin ne sont affichés, seulement leur nom en clair ;
- la socket de contrôle répond (« status ») ;
- le coffre de mémoire est lisible ; les réflexes se lisent (idée 70) ;
- WhatsApp (plan P1) : s'il est relié, le pont est installé à la version fixée.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import select
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from . import config, state
from .platform import systeme
from .platform import proc as procinfo  # « proc » est déjà le nom des sous-process ici

OK, ALERT = "OK", "ALERTE"
Result = tuple[str, str]  # (OK | ALERTE, raison)

# Ce qui décide de laisser passer une action : son empreinte est retenue par `--accept`.
GUARD_FILES = [
    config.ROOT / "astrel" / "guard.py",
    config.ROOT / "astrel" / "confirm.py",
    config.ROOT / "astrel" / "totalgate.py",
    # Plan P29 : catalogue et décision des limites de Total et de la levée (appelé par totalgate).
    config.ROOT / "astrel" / "limites.py",
    # P29 lot B : cases de confirmation en vigueur (relues du fichier signé).
    config.ROOT / "astrel" / "cases_confirmation.py",
    config.ROOT / "astrel" / "state.py",
    # Plan P6 : state.py et confirm.py s'appuient sur eux (canal local, examen des process).
    config.ROOT / "astrel" / "ipc.py",
    config.ROOT / "astrel" / "platform" / "proc.py",
    config.HOOK_SCRIPT,
    # Plan P27 : hook des fournisseurs CLI (Codex, Grok Build), même règle de refus.
    config.ROOT / "hooks" / "permission_hook_cli.py",
    # Plan P12, lot 6 : politique d'outils lue par le garde-fou (« absent » si pas de fichier).
    config.ROOT / "hooks" / "tool-policy.json",
    # 01/11 : ce que « annule » copie et réécrit hors du hook, et le hook qui l'appelle.
    config.ROOT / "astrel" / "instantanes.py",
    config.SUIVI_HOOK_SCRIPT,
    # 04/11 (lot de sécurité A, accord de l'utilisateur) : tout ce que `guard.PROTECTED_CODE` protège depuis ce jour.
    # (a) fermeture des imports des hooks qui voient Bash et Write (`tests/test_securite_lot_a.py` la calcule) ;
    config.ROOT / "astrel" / "__init__.py", config.ROOT / "astrel" / "config.py", config.ROOT / "astrel" / "events.py",
    config.ROOT / "astrel" / "platform" / "__init__.py", config.ROOT / "astrel" / "platform" / "materiel.py",
    config.ROOT / "astrel" / "platform" / "systeme.py",
    # (b) autres scripts cités par les réglages du CLI, leurs imports directs, le programme ajouté par rtk ;
    config.ROUTE_HOOK_SCRIPT, config.RTK_HOOK_SCRIPT, config.QUOTA_HOOK_SCRIPT, config.CLE_API_SCRIPT,
    config.MEMORY_HOOK_SCRIPT, config.STOP_HOOK_SCRIPT, config.ROOT / ".rtk" / "rtk",  # clés relatives à ROOT
    config.ROOT / "astrel" / "agents.py", config.ROOT / "astrel" / "memoire.py",
    config.ROOT / "astrel" / "quota_arret.py", config.ROOT / "astrel" / "rappel.py",
    # (c) contrôles : ce module lui-même (il retient l'empreinte), la preuve de mission, la surveillance de la levée.
    config.ROOT / "astrel" / "doctor.py", config.ROOT / "astrel" / "preuve.py", config.ROOT / "astrel" / "levee.py",
]
# Dans hooks/ : protégé par le garde-fou (PROTECTED_PATHS), Claude ne peut pas le réécrire.
FINGERPRINT_FILE = config.ROOT / "hooks" / "empreinte.json"

# Nom en clair, chemin, dossier ? Le chemin n'est JAMAIS affiché.
SENSITIVE = [
    ("jeton du HUD", config.HUD_TOKEN_FILE, False),
    ("clé de session du HUD", config.HUD_SESSION_KEY_FILE, False),
    ("profil Chrome du HUD", config.HUD_PROFILE_DIR, True),
    ("configuration MCP (jetons)", config.MCP_CONFIG_FILE, False),
    ("secrets des connecteurs", config.SECRETS_FILE, False),
    ("jeton de capture d'écran", config.RUNTIME / "screencast-token", False),
    ("socket de confirmation", config.CONFIRM_SOCKET, False),
    ("socket de contrôle", config.CONTROL_SOCKET, False),
    # Plan P1 : les identifiants WhatsApp valent le compte.
    ("identifiants WhatsApp", config.WHATSAPP_AUTH_DIR, True),
    ("état WhatsApp", config.WHATSAPP_STATE_FILE, False),
    ("notes vocales WhatsApp", config.WHATSAPP_MEDIA_DIR, True),
]

MCP_SERVERS = {"astrel": "astrel.mcp_server", "connecteurs": "astrel.mcp_connectors"}
MCP_TIMEOUT_S = 20.0
HOOK_TIMEOUT_S = 20.0


# ------------------------------------------------------------------ hook

# Le hook lancé tel que le CLI le lance, mais avec une panne simulée : `{setup}` est
# exécuté avant lui. `sys.argv[1]` = le script du hook.
_HOOK_RUNNER = (
    "import runpy, sys\n"
    "sys.path.insert(0, {root!r})\n"
    "{setup}\n"
    "runpy.run_path(sys.argv[1], run_name='__main__')\n"
)
_HOOK_CASES = [
    # (description, préparation, entrée)
    ("entrée illisible", "", "pas du JSON"),
    ("garde-fou en panne",
     "from astrel import guard\n"
     "def _panne(*a, **k):\n    raise RuntimeError('panne simulée par astrel doctor')\n"
     "guard.assess = _panne", None),
    ("garde-fou impossible à importer", "sys.modules['astrel.guard'] = None", None),
]


def _hook_declared() -> str:
    """Vide si les réglages du CLI déclarent le hook pour tous les outils, sinon la raison."""
    try:
        settings = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "réglages du CLI absents (Astrel jamais lancé ?)"
    except (OSError, ValueError):
        return "réglages du CLI illisibles"
    wanted = config.hook_command(config.HOOK_SCRIPT)
    old = config.python_command(config.HOOK_SCRIPT)  # avant le 23/10 (audit P29) : sans mode isolé
    seen_old = False
    for entry in ((settings.get("hooks") or {}).get("PreToolUse") or []):
        if isinstance(entry, dict) and entry.get("matcher") == "*":
            commands = [h.get("command") for h in entry.get("hooks") or [] if isinstance(h, dict)]
            if wanted in commands:
                return ""
            seen_old = seen_old or old in commands
    if seen_old:
        return "hook déclaré sans le mode isolé : relancer Astrel (le cerveau réécrit ses réglages)"
    return "hook absent des réglages du CLI (PreToolUse « * »)"


def _hook_decision(setup: str, stdin: str) -> str:
    """Lance le hook ; « deny », « allow », ou la raison pour laquelle il laisserait passer."""
    code = _HOOK_RUNNER.format(root=str(config.ROOT), setup=setup)
    try:
        proc = subprocess.run([str(config.VENV_PYTHON), *config.hook_python_args(), "-c", code,
                               str(config.HOOK_SCRIPT)], input=stdin,
                              capture_output=True, text=True, timeout=HOOK_TIMEOUT_S, cwd=str(config.ROOT))
    except subprocess.TimeoutExpired:
        return "délai dépassé"
    except OSError as exc:
        return f"impossible à lancer ({exc.strerror or type(exc).__name__})"
    if proc.returncode == 2:
        return "deny"  # code 2 = blocage pour Claude Code
    if proc.returncode != 0:
        return f"code {proc.returncode} sans décision : Claude Code laisserait passer"
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        return str(out["hookSpecificOutput"]["permissionDecision"])
    except (ValueError, KeyError, IndexError, TypeError):
        return "sortie sans décision : Claude Code laisserait passer"


def check_hook() -> Result:
    if not config.HOOK_SCRIPT.is_file():
        return ALERT, "script du hook introuvable"
    # Windows (plan P6) : pas de bit d'exécution, le fichier suffit.
    if not (config.VENV_PYTHON.is_file() and (os.name == "nt" or os.access(config.VENV_PYTHON, os.X_OK))):
        return ALERT, "interpréteur de la venv introuvable : le hook ne pourrait pas tourner"
    declared = _hook_declared()
    if declared:
        return ALERT, declared
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "true"}, "cwd": str(config.ROOT)})
    for desc, setup, stdin in _HOOK_CASES:
        got = _hook_decision(setup, payload if stdin is None else stdin)
        if got != "deny":
            return ALERT, f"{desc} : {'laisse passer' if got == 'allow' else got}"
    return OK, "déclaré, refuse sur entrée illisible et sur panne du garde-fou"


# ------------------------------------------------------------------ empreinte

def fingerprint() -> dict[str, str]:
    """sha256 de chaque fichier du garde-fou, par chemin relatif (« absent » s'il manque)."""
    out = {}
    for path in GUARD_FILES:
        rel = path.relative_to(config.ROOT).as_posix()  # 03/11 : mêmes clés sous Windows (pas de « \\ »)
        try:
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            out[rel] = "absent"
    return out


def check_fingerprint() -> Result:
    try:
        known = json.loads(FINGERPRINT_FILE.read_text(encoding="utf-8")).get("files") or {}
    except FileNotFoundError:
        return ALERT, "aucune empreinte de référence : ./run.sh --doctor --accept, depuis ton terminal"
    except (OSError, ValueError, AttributeError):
        return ALERT, "empreinte de référence illisible"
    now = fingerprint()
    changed = sorted(f for f in set(now) | set(known) if now.get(f) != known.get(f))
    if changed:
        return ALERT, "modifié depuis la dernière validation : " + ", ".join(Path(f).name for f in changed)
    return OK, f"inchangée ({len(now)} fichiers)"


def check_core_fingerprint() -> Result:
    """04/11 (lot B, H2) : le code qui décide côté cœur (`totalgate.CORE_FILES`) est-il celui validé ?
    Sinon, au démarrage, Total, levée et validation automatique des plans sont refusés."""
    from . import totalgate

    try:
        known = json.loads(FINGERPRINT_FILE.read_text(encoding="utf-8")).get("coeur")
    except (OSError, ValueError, AttributeError):
        known = None
    changed = totalgate.core_changes(known) if isinstance(known, dict) and known else None
    if changed is None:
        return ALERT, "aucune empreinte du cœur validée : ./run.sh --doctor --accept, depuis ton terminal"
    if changed:
        return ALERT, "modifié depuis la dernière validation : " + ", ".join(Path(f).name for f in changed)
    return OK, f"inchangée ({len(totalgate.core_files())} fichiers)"


def check_tool_policy() -> Result:
    """Plan P12, lot 6 : un fichier de politique illisible est IGNORÉ par le garde-fou
    (valeurs du code) ; le dire ici plutôt qu'en silence."""
    from . import guard

    if not guard.TOOL_POLICY_FILE.exists():
        return OK, "aucun fichier (valeurs du code)"
    error = guard.policy_error()
    if error:
        return ALERT, f"illisible, ignorée : {error}"
    data = guard._tool_policy()
    return OK, (f"{sum(len(t) for t in data.get('mcp', {}).values())} règles MCP, "
                f"{len(data.get('refuser', []))} outils refusés")


def launched_by_claude(pid: int | None = None) -> bool:
    """Un process de Claude (son Bash, un script qu'il lance) ? Variable posée par
    Claude Code ou marqueur du cerveau (`ASTREL_BRAIN`, plan P6), ancêtre nommé
    « claude », ou cgroup du service d'Astrel (Linux). Process lus par
    `platform.proc` : /proc sous Linux, psutil ailleurs."""
    if os.environ.get("CLAUDECODE") or os.environ.get(procinfo.BRAIN_VAR):
        return True
    pid = os.getpid() if pid is None else pid
    if state.cgroup_of(pid).rsplit("/", 1)[-1] == state.SERVICE_UNIT:
        return True
    for _ in range(64):
        if pid <= 1:
            return False
        name, parent = procinfo.name(pid), procinfo.parent(pid)
        if name is None or parent is None:
            return True  # dans le doute, suspect
        if name == "claude":
            return True
        pid = parent
    return False


def accept() -> tuple[bool, str]:
    """Retient l'empreinte actuelle. L'utilisateur seul : refusé à un process de Claude."""
    if launched_by_claude():
        return False, "Refusé : l'empreinte du garde-fou se valide depuis ton terminal, pas par Claude."
    from . import totalgate  # 04/11 (lot B, H2) : empreinte du code qui décide côté cœur, clé « coeur »

    FINGERPRINT_FILE.write_text(json.dumps({"at": time.time(), "files": fingerprint(),
                                            "coeur": totalgate.core_fingerprint()}, indent=2) + "\n",
                                encoding="utf-8")
    return True, "Empreinte du garde-fou et du cœur retenue."


# ------------------------------------------------------------------ MCP

def _lecteur(proc: subprocess.Popen) -> Callable[[float], bytes]:
    """Lecture de la sortie du serveur avec un délai : `select` sous Unix ; sous Windows, `select` ne
    prend que des sockets (WinError 10038, 03/11) : un fil lit le tube et passe les morceaux par une file."""
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    if systeme.UNIX:
        def lire(left: float) -> bytes:
            if not select.select([fd], [], [], left)[0]:
                raise TimeoutError("pas de réponse à temps")
            return os.read(fd, 65536)
        return lire
    morceaux: queue.Queue[bytes] = queue.Queue()

    def pomper() -> None:
        try:
            while chunk := os.read(fd, 65536):
                morceaux.put(chunk)
        except OSError:
            pass
        morceaux.put(b"")  # fin du tube

    threading.Thread(target=pomper, name="doctor-mcp", daemon=True).start()

    def lire_file(left: float) -> bytes:
        try:
            return morceaux.get(timeout=left)
        except queue.Empty:
            raise TimeoutError("pas de réponse à temps") from None
    return lire_file


def _read_reply(proc: subprocess.Popen, rid: int, deadline: float, lire: Callable[[float], bytes] | None = None,
                reste: list[bytes] | None = None) -> dict:
    """Réponse JSON-RPC d'identifiant `rid` (les notifications et journaux sont sautés). `lire` : le lecteur
    du serveur (`_lecteur`), un seul par process ; `reste` garde ce qui suit la réponse pour l'appel suivant."""
    lire = lire or _lecteur(proc)
    reste = [b""] if reste is None else reste
    buf = reste[0]
    while True:
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == rid:
                reste[0] = buf
                if "error" in msg:
                    raise RuntimeError(str((msg["error"] or {}).get("message", "erreur")))
                return msg.get("result") or {}
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("pas de réponse à temps")
        chunk = lire(left)
        if not chunk:
            raise RuntimeError("le serveur s'est arrêté")
        buf += chunk


def probe_mcp(module: str, timeout: float = MCP_TIMEOUT_S) -> int:
    """Lance le serveur MCP `module`, fait la poignée de main et rend le nombre
    d'outils. Le serveur est démarré par `server.run`, pas par son `main` : celui de
    « astrel » crée le coffre et en prend un instantané, ce contrôle n'écrit rien."""
    env = {**os.environ, "PYTHONPATH": str(config.ROOT)}
    code = f"import {module} as m; m.server.run('stdio')"
    proc = subprocess.Popen([str(config.VENV_PYTHON), "-c", code], cwd=str(config.ROOT), env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None
    deadline = time.monotonic() + timeout

    def send(msg: dict) -> None:
        proc.stdin.write((json.dumps({"jsonrpc": "2.0", **msg}) + "\n").encode())
        proc.stdin.flush()

    try:
        send({"id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "astrel-doctor", "version": "1"}}})
        lire, reste = _lecteur(proc), [b""]
        _read_reply(proc, 1, deadline, lire, reste)
        send({"method": "notifications/initialized"})
        send({"id": 2, "method": "tools/list"})
        return len(_read_reply(proc, 2, deadline, lire, reste).get("tools") or [])
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            proc.kill()
            proc.wait()


def _declared_servers() -> set[str] | None:
    """Serveurs déclarés au CLI (None si la configuration MCP n'a jamais été écrite)."""
    try:
        return set((json.loads(config.MCP_CONFIG_FILE.read_text(encoding="utf-8")).get("mcpServers") or {}))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, AttributeError):
        return set()


def check_mcp(name: str) -> Result:
    declared = _declared_servers()
    if declared is not None and name not in declared:
        return ALERT, "non déclaré au CLI"
    try:
        n = probe_mcp(MCP_SERVERS[name])
    except Exception as exc:  # noqa: BLE001
        return ALERT, f"injoignable ({exc})"
    if not n:
        return ALERT, "répond mais n'annonce aucun outil"
    return OK, f"joignable, {n} outils"


# ------------------------------------------------------------------ droits

def _mode_problem(path: Path, is_dir: bool) -> str:
    """Vide si les droits vont, sinon la raison (jamais le chemin). Absent : rien à exposer."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return ""
    if stat.S_ISLNK(st.st_mode):
        return "lien symbolique"
    if is_dir != stat.S_ISDIR(st.st_mode):
        return "type inattendu"
    if not systeme.UNIX:
        return ""  # 03/11 : ni uid ni bits de mode sous Windows (droits NTFS du profil)
    if st.st_uid != os.getuid():
        return "n'appartient pas à l'utilisateur"
    if st.st_mode & 0o077:
        return f"accessible aux autres ({stat.S_IMODE(st.st_mode):04o})"
    return ""


def check_permissions() -> Result:
    bad, seen = [], 0
    for label, path, is_dir in SENSITIVE:
        try:
            problem = _mode_problem(path, is_dir)
            present = os.path.lexists(path)
        except OSError as exc:
            problem, present = f"illisible ({exc.strerror or type(exc).__name__})", True
        seen += present
        if problem:
            bad.append(f"{label} {problem}")
    if bad:
        return ALERT, " ; ".join(bad)
    return OK, f"{seen} fichiers sensibles fermés aux autres"


# ------------------------------------------------------------------ cœur, coffre, réflexes

def check_control() -> Result:
    reply = state.send_command("status", timeout=3.0)
    if reply is None:
        return ALERT, "ne répond pas (Astrel arrêté ?)"
    try:
        data = json.loads(reply)
    except ValueError:
        return ALERT, "réponse illisible"
    if not isinstance(data, dict) or not data.get("ok"):
        return ALERT, "réponse inattendue"
    return OK, f"répond (état : {data.get('state', '?')})"


def check_vault() -> Result:
    from .tools import memory

    vault = memory.VAULT
    if not vault.is_dir():
        return ALERT, "coffre introuvable"
    if not os.access(vault, os.R_OK | os.X_OK):
        return ALERT, "coffre illisible"
    notes = sorted(vault.rglob("*.md"))
    if not notes:
        return ALERT, "coffre vide"
    unreadable = 0
    for note in notes:
        try:
            with note.open("rb") as fh:
                fh.read(1)
        except OSError:
            unreadable += 1
    if unreadable:
        return ALERT, f"{unreadable} note(s) illisible(s) sur {len(notes)}"
    return OK, f"lisible, {len(notes)} notes"


def check_reflexes() -> Result:
    from .senses import reflex

    try:
        rules = json.loads(reflex.RULES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return OK, "aucun fichier (règles par défaut au démarrage)"
    except (OSError, ValueError):
        return ALERT, "fichier des réflexes illisible ou JSON invalide"
    if not isinstance(rules, list):
        return ALERT, "fichier des réflexes : une liste est attendue"
    broken = [str(r.get("id", "?")) if isinstance(r, dict) else "?" for r in rules
              if not (isinstance(r, dict) and r.get("id") and isinstance(r.get("when"), dict)
                      and isinstance(r.get("do"), dict))]
    if broken:
        return ALERT, "règle(s) incomplète(s) : " + ", ".join(broken)
    return OK, f"{len(rules)} règles valides ({sum(1 for r in rules if r.get('enabled'))} actives)"


def check_whatsapp() -> Result:
    """Plan P1 : non relié = OK (option). Relié : le pont doit être installé à la
    version FIXÉE de whatsapp/package.json."""
    linked = (config.WHATSAPP_AUTH_DIR / "self.json").exists()
    if not linked:
        return OK, "non relié (option : ./run.sh whatsapp link)"
    try:
        wanted = json.loads((config.WHATSAPP_BRIDGE_DIR / "package.json").read_text(encoding="utf-8"))
        wanted = str(wanted.get("dependencies", {}).get("baileys", ""))
        got = json.loads((config.WHATSAPP_BRIDGE_DIR / "node_modules" / "baileys" / "package.json")
                         .read_text(encoding="utf-8")).get("version", "")
    except (OSError, ValueError):
        return ALERT, "relié mais pont non installé (cd whatsapp && npm ci)"
    if got != wanted:
        return ALERT, f"Baileys {got or '?'} installé, {wanted or '?'} attendu (cd whatsapp && npm ci)"
    return OK, f"relié, Baileys {got}"


_HOOK_CLI = config.ROOT / "hooks" / "permission_hook_cli.py"
_BASH_TRUE = json.dumps({"tool_name": "Bash", "tool_input": {"command": "true"}, "cwd": str(config.ROOT)})
_HOOK_CLI_CASES = [
    # (description, préparation, entrée, mode) : chaque cas doit REFUSER.
    ("entrée illisible", "", "pas du JSON", "outils"),
    (_HOOK_CASES[1][0], _HOOK_CASES[1][1], _BASH_TRUE, "outils"),
    (_HOOK_CASES[2][0], _HOOK_CASES[2][1], _BASH_TRUE, "outils"),
    ("conversation seule", "", _BASH_TRUE, "conversation"),
    ("outil inconnu", "", json.dumps({"tool_name": "outil_inconnu", "tool_input": {}}), "outils"),
]


def _hook_cli_decision(setup: str, stdin: str, mode: str) -> str:
    """Comme `_hook_decision`, pour le hook des fournisseurs CLI (arguments : CLI, mode)."""
    # sys.argv du hook = [script, CLI, mode], comme lancé par le CLI.
    code = _HOOK_RUNNER.replace("runpy.run_path(sys.argv[1]", "sys.argv = sys.argv[1:]\nrunpy.run_path(sys.argv[0]") \
        .format(root=str(config.ROOT), setup=setup)
    try:
        proc = subprocess.run([str(config.VENV_PYTHON), *config.hook_python_args(), "-c", code, str(_HOOK_CLI),
                               "codex", mode], input=stdin,
                              capture_output=True, text=True, timeout=HOOK_TIMEOUT_S, cwd=str(config.ROOT))
    except subprocess.TimeoutExpired:
        return "délai dépassé"
    except OSError as exc:
        return f"impossible à lancer ({exc.strerror or type(exc).__name__})"
    if proc.returncode == 2:
        return "deny"
    try:
        return str(json.loads(proc.stdout.strip().splitlines()[-1])["hookSpecificOutput"]["permissionDecision"])
    except (ValueError, KeyError, IndexError, TypeError):
        return f"code {proc.returncode} sans décision"


def check_hook_cli() -> Result:
    """Plan P27 : le hook des fournisseurs CLI (Codex, Grok Build) refuse dans le doute."""
    if not _HOOK_CLI.is_file():
        return ALERT, "script du hook des fournisseurs CLI introuvable"
    for desc, setup, stdin, mode in _HOOK_CLI_CASES:
        got = _hook_cli_decision(setup, stdin, mode)
        if got != "deny":
            return ALERT, f"{desc} : {'laisse passer' if got == 'allow' else got}"
    return OK, "refuse sur entrée illisible, panne, conversation seule et outil inconnu"


CHECKS: list[tuple[str, Callable[[], Result]]] = [
    ("hook de permission", check_hook),
    ("hook des fournisseurs CLI", check_hook_cli),
    ("empreinte du garde-fou", check_fingerprint),
    ("empreinte du cœur", check_core_fingerprint),  # 04/11 (lot B, H2)
    ("politique d'outils", check_tool_policy),
    ("MCP astrel", lambda: check_mcp("astrel")),
    ("MCP connecteurs", lambda: check_mcp("connecteurs")),
    ("droits des fichiers sensibles", check_permissions),
    ("socket de contrôle", check_control),
    ("coffre de mémoire", check_vault),
    ("réflexes", check_reflexes),
    ("WhatsApp", check_whatsapp),
]


def run(checks: list[tuple[str, Callable[[], Result]]] | None = None) -> tuple[int, list[str]]:
    """Lance chaque contrôle (un contrôle qui plante devient une alerte, sans
    bloquer les autres) ; rend le code de sortie et les lignes à afficher."""
    lines, alerts = [], 0
    for title, fn in checks or CHECKS:
        try:
            level, reason = fn()
        except Exception as exc:  # noqa: BLE001
            # Le type seul : le message d'une erreur système peut citer un chemin sensible.
            level, reason = ALERT, f"contrôle en échec ({type(exc).__name__})"
        alerts += level != OK
        lines.append(f"{level:<6}  {title} : {reason}")
    return (1 if alerts else 0), lines


def main(accept_now: bool = False) -> int:
    if accept_now:
        ok, message = accept()
        print(message)
        if not ok:
            return 1
    code, lines = run()
    print("\n".join(lines))
    return code
