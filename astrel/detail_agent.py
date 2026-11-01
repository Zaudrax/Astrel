"""Détail d'agent (21/10, plan P18, demande de l'utilisateur) : ce qu'un agent a fait, fait et
doit encore faire, montré au clic sur sa ligne du panneau « En cours » du HUD.

Tout est DÉRIVÉ du flux que le cœur reçoit déjà (outils d'un sous-agent, évènements de tâche
du CLI), sans rien demander à l'agent : zéro token. Ce module tient le journal d'agent enrichi
(`note_action`, `note_result`), garde les tâches finies (`remember_done`) et assemble la
réponse de la commande HUD `task_detail` (`assemble`).

Règles :
- DONNÉES seules, aucun texte d'écran (le HUD les met en mots, FR et EN) ; 6 Ko au plus.
- Rien n'est inventé : « reste à faire » n'existe que si l'agent tient une liste TodoWrite
  (`todos` vaut alors une liste, sinon None).
- Consigne, commandes et erreurs sont des données de l'agent : le HUD les affiche en texte,
  jamais en HTML. Rien n'est journalisé ni écrit sur disque ; mémoire vidée à la relance.
"""
from __future__ import annotations

import json
import time
from typing import Any

ACTIONS_MAX = 30         # dernières actions gardées par agent
ERRORS_KEEP = 3          # derniers textes d'erreur gardés
DONE_MAX = 10            # tâches finies gardées pour un clic après coup
DONE_KEEP_S = 600.0      # … pendant 10 min
MAX_BYTES = 6144         # plafond de la réponse `task_detail` (JSON UTF-8)

DESC_MAX = 200           # description d'une action (commande Bash comprise)
ERROR_MAX = 300
CONSIGNE_MAX = 1500
COMMANDE_MAX = 200       # consigne d'une commande de fond = la commande elle-même
SUMMARY_MAX = 300
TODO_MAX = 30
TODO_TEXT_MAX = 200
FILES_MAX = 50
PATH_MAX = 300
FICHE_FIELD_MAX = 400
TOOL_MAX = 80            # nom d'outil (un outil MCP peut être long)
ID_MAX = 120             # identifiant renvoyé en écho (`unknown_task`) ou dans la réponse
LABEL_MAX = 80           # kind, type, canal, mission
PASSATION_CACHE_S = 3.0  # fiches d'équipe relues au plus toutes les 3 s (HUD rafraîchi toutes les 2 s)
PASSATION_GRACE_S = 30.0  # une passation déposée juste après la fin compte encore
TODO_STATUSES = ("pending", "in_progress", "completed")


def _cut(value: Any, limit: int, lines: bool = False) -> str:
    """Texte sur une ligne (ou ses lignes gardées si `lines`), coupé à `limit` caractères
    (« … » compris)."""
    raw = str(value or "")
    if lines:
        text = "\n".join(" ".join(row.split()) for row in raw.strip().splitlines())
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")
    else:
        text = " ".join(raw.split())
    if limit <= 0:
        return ""
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# ------------------------------------------------------------------ journal d'agent (fil du cerveau)

def todos_from(raw: Any) -> list[dict[str, str]] | None:
    """`input.todos` d'un appel TodoWrite → [{text, status}] ; None si illisible."""
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw[:TODO_MAX]:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "pending")
        out.append({"text": _cut(item.get("content") or item.get("activeForm") or "", TODO_TEXT_MAX),
                    "status": status if status in TODO_STATUSES else "pending"})
    return out


def note_action(entry: dict, tool_id: str, tool: str, desc: str, tool_input: Any, now: float | None = None) -> None:
    """Un outil lancé par l'agent : ajouté à ses dernières actions (`ok` None = en cours)."""
    actions = entry.setdefault("actions", [])
    actions.append({"id": tool_id, "tool": _cut(tool, TOOL_MAX), "desc": _cut(desc, DESC_MAX), "ok": None,
                    "at": round(now if now is not None else time.time(), 1)})
    if len(actions) > ACTIONS_MAX:
        del actions[:-ACTIONS_MAX]
    if tool == "TodoWrite" and isinstance(tool_input, dict):
        todos = todos_from(tool_input.get("todos"))
        if todos is not None:
            entry["todos"] = todos


def note_result(entry: dict, tool_id: str, is_error: bool, text: str) -> None:
    """Le résultat d'un outil de l'agent : l'action passe à réussie ou échouée."""
    for action in reversed(entry.get("actions") or []):
        if action.get("id") == tool_id:
            action["ok"] = not is_error
            break
    if is_error:
        entry["errors"] = int(entry.get("errors") or 0) + 1
        last = entry.setdefault("last_errors", [])
        last.append(_cut(text, ERROR_MAX))
        if len(last) > ERRORS_KEEP:
            del last[:-ERRORS_KEEP]


# ------------------------------------------------------------------ tâches finies

def prune_done(done: dict[str, dict], now: float) -> None:
    """Oublie les tâches finies depuis plus de 10 min, et au-delà des 10 dernières."""
    for tid in [k for k, v in done.items() if now - float(v.get("ended") or 0.0) > DONE_KEEP_S]:
        done.pop(tid, None)
    while len(done) > DONE_MAX:
        done.pop(next(iter(done)))


def remember_done(done: dict[str, dict], task: dict, ev: dict, now: float) -> None:
    """Fin d'une tâche (`_on_task`, phase end) : gardée 10 min avec son statut et son résumé.
    Un second évènement de fin (le CLI envoie parfois `task_updated` puis `task_notification`)
    complète le statut et le résumé déjà gardés."""
    tid = str(task.get("id") or ev.get("id") or "")
    if not tid:
        return
    status, summary = str(ev.get("status") or ""), _cut(ev.get("summary"), SUMMARY_MAX)
    known = done.get(tid)
    if known is not None:
        if status:
            known["status"] = status
        if summary:
            known["summary"] = summary
        return
    if not task:
        return
    item = dict(task)
    item["ended"] = now
    item["elapsed_s"] = round(max(float(item.get("elapsed_s") or 0.0), now - float(item.get("started") or now)), 1)
    item["status"], item["summary"] = status, summary
    done[tid] = item
    prune_done(done, now)


def done_items(done: dict[str, dict]) -> list[dict]:
    """Tâches finies, en bref, pour l'évènement `tasks` du HUD (champ `done`)."""
    keys = ("id", "kind", "desc", "background", "channel", "started", "ended", "elapsed_s",
            "tokens", "requests", "tool_uses", "status")
    return [{k: v.get(k) for k in keys} for v in sorted(done.values(), key=lambda x: x.get("ended") or 0.0)]


# ------------------------------------------------------------------ la réponse `task_detail`

_FICHE_KEYS = ("fait", "a_tester", "risques", "preuves")


def echo_id(tid: Any) -> str:
    """Identifiant renvoyé en écho par `unknown_task` : borné (un id de 100 000 caractères
    ne doit pas faire une réponse de 100 Ko)."""
    return _cut(tid, ID_MAX)

def passation_window(start: float, end: float, others: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Fenêtre où une passation du métier appartient SÛREMENT à cet agent : de son début à sa fin
    (+ 30 s), bornée par les autres agents du même métier (`others` : (début, fin), fin = maintenant
    s'il tourne encore). None si l'un d'eux a travaillé EN MÊME TEMPS : la passation serait
    ambiguë (deux codeurs en parallèle), on ne la joint pas plutôt que de montrer celle de l'autre."""
    lo, hi = start - 5.0, end + PASSATION_GRACE_S
    for o_start, o_end in others:
        if o_start < end and o_end > start:
            return None
        if o_start >= end:
            hi = min(hi, o_start)
        elif o_end <= start:
            lo = max(lo, o_end)
    return lo, hi


_fiches_cache: dict[str, Any] = {"key": None, "at": 0.0, "fiches": []}


def _all_fiches(now: float) -> list[tuple[str, dict]]:
    """Toutes les passations, toutes missions ; relues sur disque au plus toutes les
    `PASSATION_CACHE_S` secondes (le HUD redemande le détail toutes les 2 s)."""
    from . import equipe
    key = str(equipe.EQUIPE_DIR)
    if _fiches_cache["key"] == key and 0.0 <= now - _fiches_cache["at"] < PASSATION_CACHE_S:
        return _fiches_cache["fiches"]
    fiches: list[tuple[str, dict]] = []
    try:
        folders = [p for p in equipe.EQUIPE_DIR.iterdir() if p.is_dir()]
    except OSError:
        folders = []
    for folder in folders:
        fiches.extend((folder.name, f) for f in equipe.read(folder.name))
    _fiches_cache.update(key=key, at=now, fiches=fiches)
    return fiches


def passation_for(de: str, window: tuple[float, float] | None, now: float) -> tuple[str, dict] | None:
    """Dernière passation déposée par le métier `de` dans la fenêtre de l'agent."""
    if window is None:
        return None
    lo, hi = window
    want = de.strip().lower()
    best: tuple[str, dict] | None = None
    for mission, fiche in _all_fiches(now):
        at = _num(fiche.get("at")) or 0.0
        if str(fiche.get("de") or "").strip().lower() != want or not lo <= at <= hi:
            continue
        if best is None or at >= (_num(best[1].get("at")) or 0.0):
            best = (mission, fiche)
    return best


def _fiche(found: tuple[str, dict] | None) -> dict | None:
    if not found:
        return None
    mission, fiche = found
    out = {"mission": _cut(mission, LABEL_MAX), "n": fiche.get("n") if isinstance(fiche.get("n"), int) else None,
           "a": _cut(fiche.get("a"), 40), "at": _num(fiche.get("at")) or 0.0}
    for key in _FICHE_KEYS:
        out[key] = _cut(fiche.get(key), FICHE_FIELD_MAX)
    return out


def _size(d: dict) -> int:
    return len(json.dumps(d, ensure_ascii=False).encode("utf-8"))


def _shrink_steps(d: dict) -> list:
    """Réductions successives, du moins utile au plus utile : chacune garde les actions les
    plus RÉCENTES et le début de la consigne. Appliquées une à une jusqu'à tenir."""
    def actions(n: int, desc: int | None = None):
        def step() -> None:
            d["actions"] = d["actions"][-n:] if n else []
            if desc:
                for a in d["actions"]:
                    a["desc"] = _cut(a.get("desc"), desc)
        return step

    def consigne(n: int):
        return lambda: d.update(consigne=_cut(d["consigne"], n, lines=True))

    def files(n: int, width: int = PATH_MAX):
        return lambda: d.update(files=[_cut(f, width) for f in (d["files"][-n:] if n else [])])

    def todos(n: int, width: int):
        return lambda: d.update(todos=None if d["todos"] is None else
                                [{**t, "text": _cut(t.get("text"), width)} for t in d["todos"][:n]])

    def errors(n: int, width: int):
        return lambda: d.update(last_errors=[_cut(e, width) for e in (d["last_errors"][-n:] if n else [])])

    def fiche(width: int):
        def step() -> None:
            if d["passation"]:
                for k in _FICHE_KEYS:
                    d["passation"][k] = _cut(d["passation"].get(k), width)
        return step

    def doing(n: int, width: int):
        return lambda: d.update(doing=[{"tool": _cut(x.get("tool"), 40), "desc": _cut(x.get("desc"), width)}
                                       for x in (d["doing"][-n:] if n else [])])

    def heads(width: int):
        # description, étape et résumé : courts mais jamais vides tant qu'il y a de la place
        return lambda: d.update(desc=_cut(d["desc"], width), step=_cut(d["step"], width),
                                summary=_cut(d["summary"], width * 2))

    return [actions(15), consigne(600), files(10), todos(TODO_MAX, 100), errors(ERRORS_KEEP, 150), fiche(150),
            doing(3, 100), actions(8), consigne(300), files(5, 150), todos(12, 80), actions(8, 80), errors(1, 150),
            actions(3, 60), consigne(100), todos(5, 60), fiche(60), files(2, 100), doing(2, 60), heads(80),
            actions(0), files(0), errors(0, 0), todos(0, 0), consigne(20), fiche(0), doing(0, 0), heads(30)]


def _fit(d: dict) -> dict:
    """Ramène la réponse sous `MAX_BYTES` ; `truncated` le dit."""
    if _size(d) <= MAX_BYTES:
        return d
    d["truncated"] = True
    for step in _shrink_steps(d):
        step()
        if _size(d) <= MAX_BYTES:
            break
    return d


EFFORT_MAX = 12          # niveau d'effort (« xhigh »)


def _effort(info: dict | None) -> dict:
    """Champs d'effort de la fiche (plan P28) : `effort` (reçu au lancement), `effort_next` (prochains
    agents du métier), `effort_default`, `effort_settable`, `applique` (le CLI en marche a déjà
    reçu `effort_next`), `effort_haiku`, `effort_levels`. Sans `info` : rien de réglable."""
    info = info if isinstance(info, dict) else {}
    levels = [_cut(x, EFFORT_MAX) for x in list(info.get("levels") or [])[:8]]
    return {"effort": _cut(info.get("effort"), EFFORT_MAX), "effort_next": _cut(info.get("effort_next"), EFFORT_MAX),
            "effort_default": _cut(info.get("effort_default"), EFFORT_MAX),
            "effort_settable": bool(info.get("settable")), "applique": bool(info.get("applique")),
            "effort_haiku": bool(info.get("haiku")), "effort_levels": levels}


def assemble(task: dict, entry: dict, found: tuple[str, dict] | None, now: float, live: bool,
             passation_ambiguous: bool = False, effort: dict | None = None) -> dict:
    """Réponse de `task_detail` : la tâche (`_tasks` ou tâches finies), son journal d'agent
    (peut être vide : tâche non lancée par un appel Agent/Bash de fond vu par le cœur) et la
    passation de CET agent (`passation_for`) ; `passation_ambiguous` : un autre agent du même
    métier travaillait en même temps, rien n'est joint. Une tâche finie n'a plus d'outil
    « en cours » (`doing` vide), même si le CLI ne l'a pas rendu. `effort` : `_effort` (plan P28).
    Données seules, ≤ `MAX_BYTES`."""
    started = _num(task.get("started")) or now
    ended = None if live else (_num(task.get("ended")) or now)
    elapsed = max(_num(task.get("elapsed_s")) or 0.0, (ended if ended is not None else now) - started)
    kind_type = str(entry.get("type") or "")
    limit = COMMANDE_MAX if kind_type == "commande" else CONSIGNE_MAX
    todos = entry.get("todos")
    pending = list((entry.get("pending") or {}).values())
    d: dict[str, Any] = {
        "ok": True,
        "task_id": _cut(task.get("id"), ID_MAX),
        "live": live,
        "state": "running" if live else (_cut(task.get("status"), LABEL_MAX) or "completed"),
        "kind": _cut(task.get("kind"), LABEL_MAX),
        "type": _cut(kind_type, LABEL_MAX),
        "desc": _cut(task.get("desc"), DESC_MAX),
        "channel": _cut(task.get("channel"), LABEL_MAX),
        "background": bool(task.get("background")),
        "started": round(started, 1),
        "ended": round(ended, 1) if ended is not None else None,
        "elapsed_s": round(elapsed, 1),
        "tokens": int(_num(task.get("tokens")) or 0),
        "requests": int(_num(task.get("requests")) or 0),
        "tool_uses": int(_num(task.get("tool_uses")) or 0),
        "step": _cut(task.get("step"), DESC_MAX),
        "consigne": _cut(entry.get("consigne"), limit, lines=True),
        "todos": [dict(t) for t in todos] if isinstance(todos, list) else None,
        "doing": [{"tool": _cut(tool, TOOL_MAX), "desc": _cut(desc, DESC_MAX)} for tool, desc in pending[-5:]]
        if live else [],
        "actions": [{"tool": _cut(a.get("tool"), TOOL_MAX), "desc": _cut(a.get("desc"), DESC_MAX),
                     "ok": a.get("ok") if isinstance(a.get("ok"), bool) else None, "at": _num(a.get("at"))}
                    for a in list(entry.get("actions") or [])],
        "errors": int(entry.get("errors") or 0),
        "last_errors": [str(e) for e in list(entry.get("last_errors") or [])],
        "files": [_cut(f, PATH_MAX) for f in list(entry.get("files") or [])[:FILES_MAX]],
        "summary": _cut(task.get("summary"), SUMMARY_MAX),
        "passation": _fiche(found),
        "passation_ambiguous": bool(passation_ambiguous),
        **_effort(effort),
        "truncated": False,
    }
    return _fit(d)
