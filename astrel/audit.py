"""Journal d'audit unifié (plan P3, lot E ; demande du 17/10).

`record(kind, who, what, auth)` ajoute UNE ligne JSON à `logs/audit.jsonl`
(ajout seul, 0600, rotation à 5 Mo : `.1`, `.2`). Types écrits par le cœur :
- `confirmation` : chaque question du garde-fou et sa réponse ; `auth` = qui a
  tranché (voix, oral, écran, bulle, whatsapp, clavier, total, 30 min, délai,
  abandon), `ok` = permis ou non ;
- `whatsapp` : chaque message ou fichier mis en file vers l'utilisateur ;
- `plan` : plan validé (par l'utilisateur ou en mode automatique) ;
- `refus` : commande venue de Claude refusée par le cœur ;
- `levee` : levée du garde-fou activée ou coupée (18/10, plan P7) ; ce qui passe
  grâce à elle est une `confirmation` tranchée par « levée ».

`read()` fusionne aussi, sans les recopier : `logs/autonome-total.log` (TSV du
mode Autonome TOTAL, `core._total_log`) en type `total`, et `logs/route.jsonl`
(routage des sous-agents, `agents.log_route`) en type `agent`.

Limite connue : les actions passées SANS question (mode autonome, hook protégé
`permission_hook.py`) ne passent pas par le cœur et n'y figurent pas. Le journal
est protégé en écriture par le garde-fou depuis le 18/10 (`config.AUDIT_LOG`, plan P7).
Jamais d'exception vers l'appelant : un journal illisible ou plein n'arrête rien.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from . import config

log = logging.getLogger("astrel.audit")

AUDIT_FILE = config.AUDIT_LOG  # protégé par le garde-fou (18/10, plan P7)
ROTATE_BYTES = 5_000_000
KEEP = 2                     # fichiers tournés gardés (.1, .2)
WHAT_MAX = 300
READ_MAX_BYTES = 2_000_000   # octets lus au plus par source (fin du fichier)
LIMIT_MAX = 200
KINDS = ("confirmation", "whatsapp", "plan", "refus", "total", "agent", "levee", "memoire")  # 04/11 : T49
_lock = threading.Lock()


def _clean(text: str, limit: int = WHAT_MAX) -> str:
    from .tools import memory_scan

    text = " ".join(str(text or "").split())
    text, _ = memory_scan.mask_secrets(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _rotate() -> None:
    try:
        if AUDIT_FILE.stat().st_size < ROTATE_BYTES:
            return
    except OSError:
        return
    for i in range(KEEP, 1, -1):
        older = AUDIT_FILE.with_name(f"{AUDIT_FILE.name}.{i - 1}")
        if older.exists():
            os.replace(older, AUDIT_FILE.with_name(f"{AUDIT_FILE.name}.{i}"))
    os.replace(AUDIT_FILE, AUDIT_FILE.with_name(f"{AUDIT_FILE.name}.1"))


def record(kind: str, who: str, what: str, auth: str = "", ok: bool | None = None) -> None:
    """Ajoute une entrée au journal (jamais d'exception)."""
    entry = {"ts": round(time.time(), 3), "kind": str(kind), "who": str(who), "what": _clean(what),
             "auth": _clean(auth, 120)}
    if ok is not None:
        entry["ok"] = bool(ok)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    try:
        with _lock:
            AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
            _rotate()
            fd = os.open(AUDIT_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception as exc:  # noqa: BLE001 — le journal ne bloque jamais une action
        log.warning("journal d'audit non écrit : %s", exc)


# --------------------------------------------------------------------------- lecture

def _tail_lines(path: Path) -> list[str]:
    try:
        with path.open("rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - READ_MAX_BYTES))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = data.splitlines()
    return lines[1:] if size > READ_MAX_BYTES else lines  # la première ligne lue peut être coupée


def _own() -> list[dict]:
    out = []
    for path in [AUDIT_FILE.with_name(f"{AUDIT_FILE.name}.{i}") for i in range(KEEP, 0, -1)] + [AUDIT_FILE]:
        for line in _tail_lines(path):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and isinstance(e.get("ts"), (int, float)):
                out.append(e)
    return out


def _local_ts(text: str, fmt: str) -> float | None:
    try:
        return datetime.strptime(text, fmt).timestamp()
    except (TypeError, ValueError):
        return None


def _total() -> list[dict]:
    """`logs/autonome-total.log` : date, outil (ou MODE), commande, détail, séparés par des tabulations."""
    out = []
    for line in _tail_lines(config.TOTAL_LOG):
        parts = line.split("\t")
        ts = _local_ts(parts[0], "%Y-%m-%d %H:%M:%S") if parts else None
        if ts is None or len(parts) < 3:
            continue
        extra = parts[3] if len(parts) > 3 else ""
        if parts[1] == "MODE":
            out.append({"ts": ts, "kind": "total", "who": extra or "?", "what": f"Sans confirmation {parts[2]}",
                        "auth": "mode"})
        else:
            out.append({"ts": ts, "kind": "total", "who": "claude", "what": _clean(f"{parts[1]} : {parts[2]}"),
                        "auth": _clean(f"sans question (Total) {extra}".strip(), 120), "ok": True})
    return out


def _routes() -> list[dict]:
    from . import agents

    out = []
    for line in _tail_lines(agents.ROUTE_LOG):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        ts = _local_ts(e.get("at"), "%Y-%m-%dT%H:%M:%S") if isinstance(e, dict) else None
        if ts is None:
            continue
        model = e.get("final") or e.get("asked") or "modèle par défaut"
        desc = f" : {e['desc']}" if e.get("desc") else ""
        out.append({"ts": ts, "kind": "agent", "who": "claude",
                    "what": _clean(f"agent {e.get('type') or '?'}{desc} ({model})"),
                    "auth": _clean(f"{e.get('decision') or ''} {e.get('why') or ''}".strip(), 120),
                    "ok": e.get("decision") != "deny"})
    return out


def read(kinds: list[str] | None = None, before: float | None = None, limit: int = 50) -> list[dict]:
    """Entrées les plus RÉCENTES d'abord, toutes sources fusionnées. `kinds` : types
    voulus (tous sinon) ; `before` : seulement plus anciennes que cet instant (page
    suivante) ; `limit` : 200 au plus."""
    wanted = {k for k in (kinds or KINDS) if k in KINDS}
    limit = max(1, min(int(limit or 50), LIMIT_MAX))
    entries: list[dict] = []
    if wanted - {"total", "agent"}:
        entries += [e for e in _own() if e.get("kind") in wanted]
    if "total" in wanted:
        entries += _total()
    if "agent" in wanted:
        entries += _routes()
    if before is not None:
        entries = [e for e in entries if e["ts"] < float(before)]
    # Même instant : l'ordre d'écriture départage (le dernier écrit d'abord).
    order = sorted(range(len(entries)), key=lambda i: (entries[i]["ts"], i), reverse=True)
    # Une page ne coupe jamais un groupe de même instant (journal Total et agents : à la
    # seconde) : la suivante repart de `before` = dernier instant affiché, au sens strict.
    end = min(limit, len(order))
    while 0 < end < min(len(order), limit + LIMIT_MAX) and entries[order[end]]["ts"] == entries[order[end - 1]]["ts"]:
        end += 1
    return [entries[i] for i in order[:end]]


def parse_kinds(raw) -> list[str] | None:
    """`kinds` d'une commande : liste, ou texte « confirmation,refus » ; None = tous."""
    if isinstance(raw, str):
        raw = [k for k in re.split(r"[\s,]+", raw) if k]
    if not isinstance(raw, list) or not raw:
        return None
    return [str(k) for k in raw]
