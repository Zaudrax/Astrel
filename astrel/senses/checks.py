"""Vérifications PAR SCRIPT des réflexes (déclencheur `check`) : aucun modèle.

Une vérification rend (condition remplie ?, valeurs vues). Le réflexe ne fait
son action (et ne réveille le cerveau, pour `ask`) que si elle est remplie.
Exemples, dans `config/reflexes.json` :

    {"type": "check", "every_s": 120,
     "check": {"kind": "command", "argv": ["eas", "build:view", "ID", "--json"],
               "contains": "\\"status\\": \\"(FINISHED|ERRORED|CANCELED)\\""}}
    {"type": "check", "check": {"kind": "url", "url": "https://exemple.fr", "status": 200, "not": true}}
    {"type": "check", "check": {"kind": "file_exists", "path": "/tmp/build/app.apk"}}
    {"type": "check", "check": {"kind": "quota_above", "pct": 90}}

Types : `file_exists`, `file_missing`, `file_contains` (regex sur la fin du
fichier), `file_changed` (vrai une fois à chaque changement), `url` (code HTTP
attendu, texte `contains`), `command` (code de sortie `exit`, texte
`contains`), `quota_above` (fenêtre de 5 h ou de 7 jours au-dessus de `pct` %).
`not: true` inverse la condition (site qui tombe, fichier qui disparaît).

`changed: true` sur `url` ou `command` (idée « monitor » de Hermes) : vrai UNE
fois à chaque changement de la sortie (page, prix, état), le premier passage
servant de référence ; une erreur (code HTTP ou de sortie inattendu, délai)
n'est jamais un changement et ne touche pas à la référence. `extract` (regex :
ne comparer que ce morceau, son groupe 1 s'il en a un) et `ignore` (regex
retirée avant comparaison : heures, jetons de session…) évitent les réveils pour
rien. `{diff}` (diff unifié, court) et `{output}` (nouvelle valeur) dans le
texte. Référence gardée dans `runtime/check-seen.json` : une relance d'Astrel
ne rate ni n'invente un changement.

`vault_review` : le conservateur du coffre de mémoire (`tools/vault_review.py`)
relit les notes et écrit son rapport, sans modèle et sans rien modifier ; vrai
s'il a relevé au moins `min` (1) NOUVEAUX points depuis sa passe précédente
(`new_only: false` : tous les points). Prend aussi l'instantané quotidien du
coffre s'il est dû. `{count}`, `{new}`, `{output}` (résumé), `{path}` (rapport).
Ménage de nuit (plan P3, lot D) : `kinds` (parmi doublons_probables,
a_consolider, perimees) choisit au plus 3 notes NOUVELLES (une fois par version,
une sélection par nuit, `tools/vault_review.candidates`) ; vrai s'il y en a
`min` ; `{path}` = `runtime/vault-menage.json`. `night: true` : seulement de 2 h
à 6 h, rien n'est relu hors de ces heures.

`initiative_due` (24/10, plan P32) : vrai si l'heure `at` (« 07:30 ») est passée, le jour permis
(`days`, comme `time`) et l'initiative `type` pas encore proposée aujourd'hui (registre
`runtime/initiatives.json`). Contrairement au déclencheur `time` (fenêtre de 15 min), il RATTRAPE
un PC allumé tard. `{key}` (`brief:2026-10-24`), `{type}`. `slot` (facultatif, 26/10, P32 lot 4) :
plusieurs passages du même type par jour, une clé chacun (`courrier:2026-10-26#matin`).

Une commande de vérification doit être en LECTURE SEULE : elle est refusée si
le garde-fou d'Astrel (`guard.classify`, appelé tel quel) la jugerait sensible
ou critique, à la création comme à chaque exécution, et elle tourne sans shell
(liste d'arguments), avec un délai maximal.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import config, guard, hors_ligne

DEFAULT_EVERY_S = 60.0
MAX_TIMEOUT_S = 60.0
KINDS = ("file_exists", "file_missing", "file_contains", "file_changed", "url", "command", "quota_above",
         "vault_review", "initiative_due")
SEEN_FILE = config.RUNTIME / "check-seen.json"  # `changed` : dernière sortie vue, par vérification
NIGHT_HOURS = (2, 6)  # `vault_review` avec `night: true` : de 2 h à 6 h (plan P3, lot D)
DIFF_MAX = 1500
KEEP_MAX = 20000

_seen: dict[str, tuple[float, int]] = {}  # file_changed : dernière empreinte vue


def _argv(check: dict) -> list[str]:
    argv = check.get("argv")
    if isinstance(argv, list) and argv:
        return [str(a) for a in argv]
    cmd = str(check.get("cmd") or "").strip()
    return shlex.split(cmd) if cmd else []


def _cwd(check: dict) -> str:
    """Dossier où la commande d'une vérification tourne : `cwd` (chemin ABSOLU exigé), sinon le dossier de
    travail. 04/11 (lot A, audit 2 T44-C) : `validate` la jugeait sans dossier alors qu'elle tournait ici, un
    chemin relatif était jugé ailleurs que là où il s'exécutait."""
    cwd = check.get("cwd")
    if cwd in (None, ""):
        return str(config.WORKDIR)
    if not isinstance(cwd, str) or not guard.absolu(cwd):
        raise ValueError("`cwd` : un chemin absolu est attendu")
    return cwd


def validate(check: dict) -> None:
    """Lève ValueError si la vérification est mal formée ou pas en lecture seule."""
    kind = check.get("kind")
    if kind not in KINDS:
        raise ValueError(f"vérification inconnue : {kind!r} (attendu : {', '.join(KINDS)})")
    if kind.startswith("file_") and not check.get("path"):
        raise ValueError("chemin `path` requis")
    if kind == "file_contains" and not check.get("pattern"):
        raise ValueError("motif `pattern` requis")
    if kind == "url":
        if not re.match(r"^https?://", str(check.get("url") or "")):
            raise ValueError("adresse http(s) `url` requise")
    if kind == "command":
        argv = _argv(check)
        if not argv:
            raise ValueError("commande `argv` (liste) ou `cmd` requise")
        verdict = guard.classify("Bash", {"command": shlex.join(argv)}, cwd=_cwd(check))  # 04/11 : là où elle tourne
        if verdict.needs_confirm:
            raise ValueError(f"une vérification doit être en lecture seule ; refusée : {verdict.desc}")
    if kind == "quota_above" and not isinstance(check.get("pct"), (int, float)):
        raise ValueError("seuil `pct` requis")
    if check.get("changed") and kind not in ("url", "command"):
        raise ValueError("`changed` ne vaut que pour `url` ou `command`")
    if kind == "initiative_due":
        from .. import initiatives

        if check.get("type") not in initiatives.TYPES:
            raise ValueError("`type` d'initiative requis : " + ", ".join(initiatives.TYPES))
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(check.get("at") or "")):
            raise ValueError("heure `at` (HH:MM) requise")
        if "slot" in check and not re.fullmatch(r"[a-z0-9-]{1,16}", str(check.get("slot") or "")):
            raise ValueError("`slot` : 1 à 16 caractères parmi a-z, 0-9 et -")
    if (check.get("kinds") or check.get("night")) and kind != "vault_review":
        raise ValueError("`kinds` et `night` ne valent que pour `vault_review`")
    if check.get("kinds") is not None:
        from ..tools.vault_review import MENAGE_KINDS

        kinds = check["kinds"]
        if not isinstance(kinds, list) or not kinds or not set(kinds) <= set(MENAGE_KINDS):
            raise ValueError("`kinds` : liste parmi " + ", ".join(MENAGE_KINDS))
    for key in ("contains", "pattern", "extract", "ignore"):
        if check.get(key):
            re.compile(str(check[key]))


def run(check: dict, quota: Callable[[], dict] | None = None) -> tuple[bool, dict]:
    validate(check)
    hit, values = _RUNNERS[check["kind"]](check, quota or (lambda: {}))
    return (not hit if check.get("not") else hit), values


def _path(check: dict) -> Path:
    return Path(str(check["path"])).expanduser()


def _file_exists(check: dict, _q) -> tuple[bool, dict]:
    p = _path(check)
    return p.exists(), {"path": str(p)}


def _file_missing(check: dict, _q) -> tuple[bool, dict]:
    p = _path(check)
    return not p.exists(), {"path": str(p)}


def _file_contains(check: dict, _q) -> tuple[bool, dict]:
    p = _path(check)
    try:
        size = p.stat().st_size
        with p.open("rb") as fh:
            fh.seek(max(0, size - int(check.get("tail_kb", 64)) * 1024))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return False, {"path": str(p)}
    m = re.search(str(check["pattern"]), text)
    return bool(m), {"path": str(p), "output": m.group(0)[:300] if m else ""}


def _file_changed(check: dict, _q) -> tuple[bool, dict]:
    p = _path(check)
    try:
        st = p.stat()
        mark = (st.st_mtime, st.st_size)
    except OSError:
        mark = (0.0, -1)
    key = str(p)
    before = _seen.get(key)
    _seen[key] = mark
    return before is not None and before != mark, {"path": key}


def _url(check: dict, _q) -> tuple[bool, dict]:
    hors_ligne.verifier("vérification d'une adresse web")  # 04/11 : le réflexe est sauté (erreur, rien déclenché)
    import httpx

    timeout = min(float(check.get("timeout", 15)), MAX_TIMEOUT_S)
    try:
        resp = httpx.get(str(check["url"]), timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        return False, {"url": check["url"], "status": 0, "output": str(exc)[:300]}
    want = int(check.get("status", 200))
    ok = resp.status_code == want
    if check.get("changed"):
        return _changed(check, resp.text if ok else None, {"url": check["url"], "status": resp.status_code})
    if ok and check.get("contains"):
        ok = re.search(str(check["contains"]), resp.text) is not None
    return ok, {"url": check["url"], "status": resp.status_code, "output": resp.text[:300]}


def _command(check: dict, _q) -> tuple[bool, dict]:
    argv = _argv(check)
    timeout = min(float(check.get("timeout", 30)), MAX_TIMEOUT_S)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              cwd=_cwd(check), env=os.environ.copy())  # 04/11 : le dossier jugé par `validate`
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return False, {"status": -1, "output": str(exc)[:300]}
    out = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == int(check.get("exit", 0))
    if check.get("changed"):
        return _changed(check, proc.stdout if ok else None, {"status": proc.returncode})
    if ok and check.get("contains"):
        m = re.search(str(check["contains"]), out)
        ok = m is not None
    return ok, {"status": proc.returncode, "output": " ".join(out.split())[:300]}


def _quota_above(check: dict, quota: Callable[[], dict]) -> tuple[bool, dict]:
    q = quota() or {}
    want = str(check.get("window") or "")
    level = 0.0
    for name, w in (q.get("windows") or {}).items():
        if want and name != want or not isinstance(w, dict):
            continue
        u = w.get("utilization")
        if isinstance(u, (int, float)):
            level = max(level, u * 100 if u <= 1 else u)
    return level >= float(check["pct"]), {"pct": round(level)}


def _seen_key(check: dict) -> str:
    ident = {k: check.get(k) for k in ("kind", "url", "argv", "cmd", "cwd", "extract", "ignore")}
    return hashlib.sha1(json.dumps(ident, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _load_seen() -> dict:
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _changed(check: dict, raw: str | None, values: dict) -> tuple[bool, dict]:
    """`changed: true` : vrai si la sortie (filtrée) diffère de la précédente.
    `raw` vaut None en cas d'erreur : ni changement, ni nouvelle référence."""
    if raw is None:
        return False, {**values, "output": "", "diff": ""}
    text = raw
    if check.get("extract"):
        m = re.search(str(check["extract"]), text, re.S)
        if not m:                       # page incomplète ou changée de forme : pas un changement
            return False, {**values, "output": "", "diff": ""}
        text = (m.group(1) or "") if m.groups() else m.group(0)
    if check.get("ignore"):
        text = re.sub(str(check["ignore"]), "", text)
    text = text.strip()[:KEEP_MAX]
    seen = _load_seen()
    key = _seen_key(check)
    before = seen.get(key)
    values = {**values, "output": " ".join(text.split())[:300], "diff": ""}
    if before is not None and before.get("text") == text:
        return False, values
    seen[key] = {"text": text, "at": time.strftime("%Y-%m-%d %H:%M")}
    try:
        SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SEEN_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SEEN_FILE)
    except OSError:
        pass
    if before is None:
        return False, values            # premier passage : la référence
    diff = "\n".join(difflib.unified_diff(str(before.get("text", "")).splitlines(), text.splitlines(),
                                          "avant", "maintenant", lineterm="", n=1))
    values["diff"] = diff[:DIFF_MAX] + ("…" if len(diff) > DIFF_MAX else "")
    return True, values


def _hour() -> int:
    return time.localtime().tm_hour


def _vault_review(check: dict, _q) -> tuple[bool, dict]:
    from ..tools import memory, vault_backup, vault_review

    if check.get("night") and not NIGHT_HOURS[0] <= _hour() < NIGHT_HOURS[1]:
        return False, {"count": 0, "new": 0, "output": "", "path": ""}  # hors de la nuit : rien n'est relu
    try:
        vault_backup.snapshot_if_due(memory.VAULT)
    except OSError:
        pass
    if check.get("kinds"):
        # Ménage de nuit (plan P3, lot D) : candidats NOUVEAUX seulement, retenus comme revus ;
        # le cerveau (agent archiviste) n'est réveillé que s'il y en a.
        found = vault_review.candidates(kinds=list(check["kinds"]), mark=True)
        notes = sorted({n for c in found for n in c["notes"]})
        return len(found) >= int(check.get("min", 1)), {
            "count": len(found), "new": len(found), "path": str(vault_review.MENAGE_FILE),
            "output": (f"{len(found)} candidat{'s' if len(found) > 1 else ''} au ménage : " + ", ".join(notes))
            if found else "aucun candidat au ménage"}
    report, new = vault_review.run()
    count = len(new) if check.get("new_only", True) else report["points"]
    return count >= int(check.get("min", 1)), {
        "count": report["points"], "new": len(new), "output": vault_review.summary(report),
        "path": str(vault_review.REPORT_MD)}


def _now() -> datetime:
    return datetime.now()


def _initiative_due(check: dict, _q) -> tuple[bool, dict]:
    """Initiative du jour à proposer (plan P32) : heure passée, jour permis, pas encore faite."""
    from .. import initiatives

    now = _now()
    kind = str(check["type"])
    key = initiatives.day_key(kind, now)
    if check.get("slot"):  # P32 lot 4 : un passage du courrier le matin, un l'après-midi
        key += f"#{check['slot']}"
    values = {"type": kind, "key": key}
    hh, mm = (int(x) for x in str(check["at"]).split(":"))
    if not initiatives.days_match(str(check.get("days") or "tous"), now.weekday()):
        return False, values
    if (now.hour, now.minute) < (hh, mm):
        return False, values
    return not initiatives.done(key), values


_RUNNERS = {
    "file_exists": _file_exists, "file_missing": _file_missing, "file_contains": _file_contains,
    "file_changed": _file_changed, "url": _url, "command": _command, "quota_above": _quota_above,
    "vault_review": _vault_review, "initiative_due": _initiative_due,
}
