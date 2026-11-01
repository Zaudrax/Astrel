"""Rappels : un minuteur systemd utilisateur par rappel.

À l'heure dite, une notification GNOME s'affiche ET Astrel le dit à voix haute
(commande `announce` sur son canal de contrôle). Si Astrel ne tourne pas, la
notification suffit : le rappel ne se perd pas.

Pourquoi systemd et pas un minuteur en mémoire : un rappel doit survivre à un
redémarrage d'Astrel. `systemd-run --user` le confie au gestionnaire de
session, sans cron ni droit administrateur.

Sans systemd (macOS, Windows, certains WSL2 — P2, lot 4) : planificateur
interne, un processus détaché par rappel (`astrel/platform/minuteur.py`). Il
survit à une relance d'Astrel mais PAS à un redémarrage de la machine ; le
résultat de `create` le dit (`remarque`).
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime

from .. import config
from ..i18n import assistant_name, tr
from ..platform import SYSTEM

INDEX = config.RUNTIME / "reminders.json"
PREFIX = "astrel-reminder-"
MINUTEUR = "astrel.platform.minuteur"
NOTE_MINUTEUR = "rappel tenu par Astrel : il ne survit pas à un redémarrage de l'ordinateur"


def _load() -> dict:
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    INDEX.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def use_systemd() -> bool:
    """Le minuteur systemd d'origine, partout où il existe (Linux de référence)."""
    return SYSTEM == "linux" and shutil.which("systemd-run") is not None


def create(message: str, at: str = "", in_minutes: float = 0) -> dict:
    """`at` : date/heure ISO (« 2026-09-27T08:30 ») ; ou `in_minutes`."""
    message = " ".join(message.split())[:300]
    if not message:
        raise ValueError("message vide")
    uid = time.strftime("%Y%m%d%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"
    unit = PREFIX + uid
    if at:
        when = datetime.fromisoformat(at)
        if when <= datetime.now():
            raise ValueError("cette heure est déjà passée")
        timing = [f"--on-calendar={when.strftime('%Y-%m-%d %H:%M:%S')}"]
        due = when.isoformat(timespec="minutes")
        due_ts = when.timestamp()
    elif in_minutes > 0:
        seconds = int(in_minutes * 60)
        timing = [f"--on-active={seconds}s"]
        due = datetime.fromtimestamp(time.time() + seconds).isoformat(timespec="minutes")
        due_ts = time.time() + seconds
    else:
        raise ValueError("précise « at » (date ISO) ou « in_minutes »")

    if not use_systemd():
        return _create_minuteur(uid, message, due, due_ts)

    announce = f"announce Rappel : {message}"
    script = (
        f"notify-send --app-name={shlex.quote(assistant_name())} --icon=astrel-hud --urgency=critical {shlex.quote(tr('Rappel'))} {shlex.quote(message)}; "
        f"{shlex.quote(str(config.ROOT / 'run.sh'))} --send {shlex.quote(announce)} || true"
    )
    subprocess.run(
        ["systemd-run", "--user", f"--unit={unit}", "--collect", "--quiet",
         f"--description=Rappel Astrel : {message[:60]}", *timing, "/bin/sh", "-c", script],
        check=True, capture_output=True, text=True, timeout=10,
    )
    data = _load()
    data[uid] = {"message": message, "due": due, "unit": unit}
    _save(data)
    return {"id": uid, "message": message, "echeance": due}


def _create_minuteur(uid: str, message: str, due: str, due_ts: float) -> dict:
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "cwd": str(config.ROOT)}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([sys.executable, "-m", MINUTEUR, uid, f"{due_ts:.3f}", message], **kwargs)
    data = _load()
    data[uid] = {"message": message, "due": due, "pid": proc.pid}
    _save(data)
    return {"id": uid, "message": message, "echeance": due, "remarque": NOTE_MINUTEUR}


def _minuteur_alive(uid: str, pid: int) -> bool:
    try:
        import psutil

        cmd = psutil.Process(int(pid)).cmdline()
    except Exception:
        return False
    return MINUTEUR in cmd and uid in cmd


def forget(uid: str) -> None:
    """Retire un rappel échu de l'index (appelé par le minuteur interne)."""
    data = _load()
    if data.pop(uid, None) is not None:
        _save(data)


def list_all() -> list[dict]:
    data = _load()
    active: set[str] = set()
    if any("unit" in item for item in data.values()) and shutil.which("systemctl"):
        active = set(re.findall(rf"({PREFIX}\d+)\.timer",
                                subprocess.run(["systemctl", "--user", "list-timers", "--all", "--no-pager", "--plain"],
                                               capture_output=True, text=True, timeout=10).stdout))
    out, keep = [], {}
    for uid, item in data.items():
        if item.get("unit") in active or ("pid" in item and _minuteur_alive(uid, item["pid"])):
            keep[uid] = item
            out.append({"id": uid, "message": item["message"], "echeance": item["due"]})
    if keep != data:
        _save(keep)  # ménage : les rappels passés disparaissent de l'index
    return sorted(out, key=lambda r: r["echeance"])


def delete(uid: str) -> str:
    data = _load()
    item = data.pop(uid, None)
    if item is None:
        raise LookupError(f"aucun rappel {uid}")
    if "unit" in item:
        subprocess.run(["systemctl", "--user", "stop", f"{item['unit']}.timer"], capture_output=True, timeout=10)
    elif "pid" in item and _minuteur_alive(uid, item["pid"]):
        import psutil

        psutil.Process(int(item["pid"])).terminate()
    _save(data)
    return f"Rappel supprimé : {item['message']}"
