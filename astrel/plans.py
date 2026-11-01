"""Mode plan (15/10, demande de l'utilisateur ; contrat `docs/CONTRAT-PLANS-2026-10-15.md`).

Pour un gros chantier, le planificateur dépose un plan (outil MCP `plan_submit`) ;
l'utilisateur le relit point par point dans la page « Plans » du HUD, le commente, en
demande une révision, puis le valide : SEULEMENT alors Astrel lance un codeur.
Réglage `plan_mode` (`settings.py`) : `manual` (défaut) ou `auto` (validé dès le dépôt).

Ici : l'état, le stockage et le découpage, sans cerveau ni HUD (testables seuls).
Le cœur (`core._plan_command`, `_plan_tool`) refuse à Claude les commandes du HUD et
envoie au cerveau les messages préparés ici (source « plan »).

- Stockage : `runtime/plans/P<n>.json` (fait foi : versions, fil, statut) et
  `P<n>.md` (la version en cours, que lit le codeur ; à la validation, les remarques
  jamais envoyées y sont ajoutées). Écriture atomique, comme `settings.py`. La
  numérotation reprend après une relance (plus grand numéro trouvé sur le disque).
- Découpage : sections `##` (le contrat demande `## Étapes`, `## Fichiers`,
  `## Risques`, `## Lots`, `## Vérification`) ; dans chacune, le texte avant la
  première liste est l'intro, chaque élément de liste (et chaque paragraphe après)
  un point. Points numérotés `p1…pn` sur tout le plan, pour une version donnée.
- Statuts : `pending` (à relire), `revising` (révision demandée, en attente de la
  version suivante), `validated`, `rejected`. Seul `pending` se valide, et seulement
  la version que l'utilisateur a sous les yeux.

Évènements : `plans` (STICKY) {mode, pending, items:[{id, title, channel, status,
version, points, remarks, created, updated, by}]} et `plan` {new?, plan:{…champs de
items, request, body, sections, thread, reject_reason}}. `by` : qui a tranché
(« utilisateur », « auto »), vide tant que le plan est ouvert ; `remarks` : remarques de
l'utilisateur pas encore envoyées à Astrel.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from . import config
from .events import bus

log = logging.getLogger("astrel.plans")

PENDING, REVISING, VALIDATED, REJECTED = "pending", "revising", "validated", "rejected"
STATUSES = (PENDING, REVISING, VALIDATED, REJECTED)
OPEN = (PENDING, REVISING)
MODES = (("manual", "Manuel"), ("auto", "Automatique"))

BODY_MAX = 6000      # caractères (contrat) ; le canal de contrôle lit 8192 octets
TITLE_MAX = 160
REQUEST_MAX = 1500
REMARK_MAX = 2000
REPLY_MAX = 3000
REASON_MAX = 500
THREAD_MAX = 300
LIST_MAX = 20        # `plan_list` : la réponse du canal de contrôle tient en 4 Ko
LIST_BYTES = 3500

_FILE = re.compile(r"^P(\d+)\.(?:json|md)$")
_POINT = re.compile(r"^p\d{1,4}$")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f‪-‮⁦-⁩]")  # \t et \n permis
_HEAD = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_ITEM = re.compile(r"^( *)(?:[-*+]|(\d{1,3}[.)]))\s+(.*)$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")


# --------------------------------------------------------------------------- textes

def _line(text: Any, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    return " ".join(_CONTROLS.sub(" ", text).split())[:limit].strip()


def _lines(text: Any, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    text = _CONTROLS.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))
    return "\n".join(" ".join(ln.split()) for ln in text.strip().splitlines()).strip()[:limit].strip()


def clean_body(text: Any) -> str:
    """Markdown du plan : retours à la ligne et indentation GARDÉS (listes, code),
    caractères de contrôle et d'inversion retirés, blancs de fin de ligne ôtés."""
    if not isinstance(text, str):
        return ""
    text = _CONTROLS.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n")).replace("\t", "    ")
    return "\n".join(ln.rstrip() for ln in text.splitlines()).strip("\n").strip()


def split(body: str) -> list[dict[str, Any]]:
    """Sections `#`/`##` → [{title, intro, points:[{id, text}]}], points numérotés
    p1…pn sur tout le plan. Un élément de liste (pas plus de 1 espace devant) ouvre
    un point ; une ligne plus indentée ou collée au point le continue ; un paragraphe
    après une ligne vide en ouvre un autre. Un bloc de code reste d'un seul tenant."""
    sections: list[dict[str, Any]] = []
    cur: dict[str, Any] = {"title": "", "intro": [], "points": []}
    count = 0
    fence = ""
    gap = False

    def target() -> list[str]:
        return cur["points"][-1]["lines"] if cur["points"] else cur["intro"]

    def close() -> None:
        if cur["title"] or cur["intro"] or cur["points"]:
            sections.append(cur)

    for raw in body.splitlines():
        stripped = raw.strip()
        if fence:
            target().append(raw)
            if stripped.startswith(fence):
                fence = ""
            continue
        m_fence = _FENCE.match(raw)
        if m_fence:
            fence = m_fence.group(1)
            target().append(raw)
            gap = False
            continue
        if not stripped:
            gap = True
            continue
        head = _HEAD.match(raw)
        if head and len(head.group(1)) <= 2:
            close()
            cur = {"title": head.group(2).strip(), "intro": [], "points": []}
            gap = False
            continue
        item = _ITEM.match(raw)
        indented = raw[: len(raw) - len(raw.lstrip())]
        if item and (len(item.group(1)) <= 1 or not cur["points"]):  # sous-liste : suite du point
            count += 1
            text = (item.group(2) + " " if item.group(2) else "") + item.group(3)
            cur["points"].append({"id": f"p{count}", "lines": [text], "offset": len(raw) - len(item.group(3))})
        elif cur["points"] and (not gap or len(indented) >= 2):
            # Suite du point, indentation relative gardée (sous-liste) : décalée du marqueur.
            cut = min(len(indented), cur["points"][-1]["offset"])
            cur["points"][-1]["lines"].append(raw[cut:].rstrip())
        elif cur["points"]:
            count += 1  # paragraphe après la liste : un point à part, commentable lui aussi
            cur["points"].append({"id": f"p{count}", "lines": [stripped], "offset": 0})
        else:
            if gap and cur["intro"]:
                cur["intro"].append("")
            cur["intro"].append(stripped)
        gap = False
    close()
    return [{"title": s["title"], "intro": "\n".join(s["intro"]).strip(),
             "points": [{"id": p["id"], "text": "\n".join(p["lines"]).strip()} for p in s["points"]]}
            for s in sections]


def point_ids(body: str) -> list[str]:
    return [p["id"] for s in split(body) for p in s["points"]]


def plan_path(pid: str, suffix: str = ".md") -> Path:
    return config.PLANS_DIR / f"{pid}{suffix}"


# --------------------------------------------------------------------------- messages au cerveau

def _ref(point: str, text: str) -> str:
    if not point:
        return "le plan en général"
    short = " ".join(text.split())
    short = short if len(short) <= 140 else short[:139] + "…"
    return f"le point {point} « {short} »" if short else f"le point {point}"


def validated_message(pid: str, auto: bool = False) -> str:
    how = " (mode automatique)" if auto else ""
    return (f"Plan {pid} validé{how} : lance UN SEUL codeur qui lit {plan_path(pid)} "
            "(ce fichier est sa consigne : il ne refait pas le plan).")


def validated_shown(pid: str, auto: bool = False) -> str:
    return f"Plan {pid} validé{' automatiquement' if auto else ''} : lancement d'un codeur."


def _replies_hint(pid: str) -> str:
    return (f"Réponds dans le fil du plan avec plan_reply(plan_id=\"{pid}\", point=…, text=…), "
            "jamais dans la conversation ; pas de codeur.")


# --------------------------------------------------------------------------- état

class Plans:
    """Plans en mémoire, relus du disque au premier usage (seul le cœur écrit)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, dict[str, Any]] | None = None
        self._top = 0

    def reset(self) -> None:
        """Oublie tout (tests) : le dossier sera relu."""
        with self._lock:
            self._items, self._top = None, 0

    # --- disque

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._items is not None:
            return self._items
        items: dict[str, dict[str, Any]] = {}
        top = 0
        try:
            files = sorted(config.PLANS_DIR.iterdir())
        except OSError:
            files = []
        for f in files:
            m = _FILE.match(f.name)
            if not m:
                continue
            top = max(top, int(m.group(1)))  # un .md seul compte aussi : jamais deux P3
            if f.suffix != ".json":
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if not (isinstance(data, dict) and data.get("id") == f.stem and data.get("status") in STATUSES
                        and isinstance(data.get("body"), str) and isinstance(data.get("version"), int)):
                    raise ValueError("format inattendu")
            except (OSError, ValueError) as exc:
                log.warning("plan %s illisible (%s) : ignoré", f.name, exc)
                continue
            data.setdefault("thread", [])
            data.setdefault("versions", [])
            items[data["id"]] = data
        self._items, self._top = items, top
        return items

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)  # jamais de fichier à moitié écrit après une coupure

    def _save(self, plan: dict[str, Any]) -> None:
        pid = plan["id"]
        self._write(plan_path(pid, ".json"), json.dumps(plan, ensure_ascii=False, indent=2))
        self._write(plan_path(pid, ".md"), self.markdown(plan))

    @staticmethod
    def markdown(plan: dict[str, Any]) -> str:
        """Ce que lit le codeur : titre, demande, plan, puis les remarques de l'utilisateur
        jamais envoyées à Astrel (ajoutées à la validation)."""
        out = [f"# Plan {plan['id']} : {plan['title']} (version {plan['version']})", ""]
        if plan.get("request"):
            out += [f"Demande de l'utilisateur : {plan['request']}", ""]
        out.append(plan["body"])
        if plan.get("final_remarks"):
            out += ["", "## Remarques de l'utilisateur à respecter", *[f"- {r}" for r in plan["final_remarks"]]]
        return "\n".join(out).rstrip() + "\n"

    # --- lecture

    def _get(self, pid: Any) -> dict[str, Any] | None:
        pid = str(pid or "").strip().upper() if isinstance(pid, str) else ""
        return self._load().get(pid)

    @staticmethod
    def _mode() -> str:
        from .settings import reglages

        return str(reglages.value("plan_mode"))

    @staticmethod
    def _unsent(plan: dict[str, Any]) -> list[dict[str, Any]]:
        return [t for t in plan["thread"] if t.get("who") == "utilisateur" and not t.get("sent")]

    def _summary(self, plan: dict[str, Any]) -> dict[str, Any]:
        return {"id": plan["id"], "title": plan["title"], "channel": plan.get("channel", ""),
                "status": plan["status"], "version": plan["version"], "points": len(point_ids(plan["body"])),
                "remarks": len(self._unsent(plan)), "created": plan["created"], "updated": plan["updated"],
                "by": plan.get("validated_by") or ("utilisateur" if plan["status"] == REJECTED else "")}

    def _full(self, plan: dict[str, Any]) -> dict[str, Any]:
        return {**self._summary(plan), "request": plan.get("request", ""), "body": plan["body"],
                "sections": split(plan["body"]), "thread": [dict(t) for t in plan["thread"]],
                "reject_reason": plan.get("reject_reason", ""), "validated_by": plan.get("validated_by", "")}

    def get(self, pid: Any) -> dict[str, Any] | None:
        with self._lock:
            plan = self._get(pid)
            return self._full(plan) if plan else None

    def describe(self) -> dict[str, Any]:
        with self._lock:
            ordered = sorted(self._load().values(), key=lambda p: int(p["id"][1:]), reverse=True)
            items = [self._summary(p) for p in ordered]
        return {"mode": self._mode(), "pending": sum(1 for i in items if i["status"] == PENDING), "items": items}

    def compact(self) -> dict[str, Any]:
        """Pour l'outil MCP `plan_list` : les derniers plans, courts (réponse ≤ 4 Ko)."""
        d = self.describe()
        rows = [{"id": i["id"], "title": i["title"][:70], "status": i["status"], "version": i["version"],
                 "remarks": i["remarks"]} for i in d["items"][:LIST_MAX]]
        while rows and len(json.dumps(rows, ensure_ascii=False).encode()) > LIST_BYTES:
            rows.pop()
        return {"ok": True, "mode": d["mode"], "pending": d["pending"], "total": len(d["items"]), "plans": rows}

    # --- publication

    def publish(self) -> dict[str, Any]:
        payload = self.describe()
        bus.publish("plans", **payload)
        return payload

    def _changed(self, plan: dict[str, Any], new: bool = False) -> None:
        with self._lock:
            full = self._full(plan)
        bus.publish("plan", plan=full, **({"new": True} if new else {}))
        self.publish()

    def publish_plan(self, pid: Any) -> dict[str, Any] | None:
        with self._lock:
            plan = self._get(pid)
            full = self._full(plan) if plan else None
        if full is not None:
            bus.publish("plan", plan=full)
        return full

    # --- écriture

    def _commit(self, plan: dict[str, Any], before: dict[str, Any] | None) -> str:
        """Enregistre ; en cas d'échec, remet l'état d'avant et rend l'erreur."""
        try:
            self._save(plan)
        except OSError as exc:
            items = self._load()
            if before is None:
                items.pop(plan["id"], None)
            else:
                items[plan["id"]] = before
            log.warning("plan %s non enregistré : %s", plan["id"], exc)
            return f"plan non enregistré : {exc}"
        return ""

    def submit(self, title: Any, body: Any, request: Any = "", channel: str = "", plan_id: Any = "") -> dict[str, Any]:
        """Dépôt d'un plan (outil MCP `plan_submit`), ou d'une nouvelle version avec `plan_id`."""
        title = _line(title, TITLE_MAX)
        raw = clean_body(body)
        if not title:
            return {"ok": False, "error": "titre vide"}
        if not raw:
            return {"ok": False, "error": "plan vide"}
        if len(raw) > BODY_MAX:
            return {"ok": False, "error": f"plan trop long : {len(raw)} caractères ({BODY_MAX} au plus), resserre-le"}
        if not point_ids(raw):
            return {"ok": False, "error": "aucun point : écris les rubriques en listes (« - … » ou « 1. … »)"}
        request = _lines(request, REQUEST_MAX)
        now = time.time()
        with self._lock:
            items = self._load()
            if plan_id:
                plan = self._get(plan_id)
                if plan is None:
                    return {"ok": False, "error": f"plan inconnu : {plan_id}"}
                if plan["status"] not in OPEN:
                    return {"ok": False, "error": f"plan {plan['id']} clos ({plan['status']}) : dépose un nouveau "
                                                  "plan, sans plan_id"}
                before = json.loads(json.dumps(plan))
                plan.update(title=title, body=raw, version=plan["version"] + 1, status=PENDING, updated=now)
                if request:
                    plan["request"] = request
                if channel:
                    plan["channel"] = channel
                new = False
            else:
                before = None
                self._top += 1
                pid = f"P{self._top}"
                plan = {"id": pid, "title": title, "channel": channel, "status": PENDING, "version": 1,
                        "request": request, "body": raw, "created": now, "updated": now, "validated_by": "",
                        "reject_reason": "", "final_remarks": [], "thread": [], "versions": []}
                items[pid] = plan
                new = True
            plan["versions"].append({"version": plan["version"], "body": raw, "ts": now})
            error = self._commit(plan, before)
            if error:
                return {"ok": False, "error": error}
            result = {"ok": True, "id": plan["id"], "version": plan["version"], "status": plan["status"],
                      "points": len(point_ids(raw)), "new": new}
        log.info("plan %s version %d déposé (%d points)", result["id"], result["version"], result["points"])
        self._changed(plan, new=new)
        return result

    def reply(self, pid: Any, text: Any, point: Any = "") -> dict[str, Any]:
        """Réponse d'Astrel dans le fil du plan (outil MCP `plan_reply`)."""
        text = _lines(text, REPLY_MAX)
        point = str(point or "").strip().lower() if isinstance(point, str) else ""
        if not text:
            return {"ok": False, "error": "réponse vide"}
        if point and not _POINT.match(point):
            return {"ok": False, "error": f"point illisible : {point} (p1, p2…)"}
        with self._lock:
            plan = self._get(pid)
            if plan is None:
                return {"ok": False, "error": f"plan inconnu : {pid}"}
            before = json.loads(json.dumps(plan))
            self._append(plan, {"who": "astrel", "point": point, "text": text, "ts": time.time(),
                                "version": plan["version"], "sent": True})
            error = self._commit(plan, before)
            if error:
                return {"ok": False, "error": error}
        self._changed(plan)
        return {"ok": True, "id": plan["id"], "message": "Réponse posée dans le fil du plan (rien n'est dit)."}

    @staticmethod
    def _append(plan: dict[str, Any], entry: dict[str, Any]) -> None:
        plan["thread"].append(entry)
        plan["updated"] = entry["ts"]
        if len(plan["thread"]) > THREAD_MAX:
            del plan["thread"][: len(plan["thread"]) - THREAD_MAX]

    def _point_text(self, plan: dict[str, Any], version: int, point: str) -> str:
        body = next((v["body"] for v in plan["versions"] if v.get("version") == version), plan["body"])
        return next((p["text"] for s in split(body) for p in s["points"] if p["id"] == point), "")

    def comment(self, pid: Any, point: Any, text: Any, ask: bool = False) -> dict[str, Any]:
        """Remarque de l'utilisateur sur un point (ou sur tout le plan si `point` est vide).
        Gardée sans appeler le modèle ; avec `ask`, elle part tout de suite (une question)."""
        text = _lines(text, REMARK_MAX)
        point = str(point or "").strip().lower() if isinstance(point, str) else ""
        if not text:
            return {"ok": False, "error": "remarque vide"}
        with self._lock:
            plan = self._get(pid)
            if plan is None:
                return {"ok": False, "error": f"plan inconnu : {pid}"}
            if plan["status"] not in OPEN:
                return {"ok": False, "error": f"plan {plan['id']} clos ({plan['status']})"}
            if point and point not in point_ids(plan["body"]):
                return {"ok": False, "error": f"point inconnu : {point}"}
            before = json.loads(json.dumps(plan))
            entry = {"who": "utilisateur", "point": point, "text": text, "ts": time.time(),
                     "version": plan["version"], "sent": bool(ask)}
            self._append(plan, entry)
            error = self._commit(plan, before)
            if error:
                return {"ok": False, "error": error}
            pid = plan["id"]
            result: dict[str, Any] = {"ok": True, "id": pid, "remark": dict(entry)}
            if ask:
                ref = _ref(point, self._point_text(plan, plan["version"], point))
                result["message"] = (f"Question de l'utilisateur sur le plan {pid} (version {plan['version']}), "
                                     f"{ref} : {text}\n{_replies_hint(pid)} Lis {plan_path(pid)} si besoin ; "
                                     "pas de nouvelle version sauf s'il la demande. Puis réponds seulement [SILENT].")
                result["shown"] = f"Plan {pid} : question sur {point or 'le plan'}."
                result["channel"] = plan.get("channel", "")
        self._changed(plan)
        return result

    def revise(self, pid: Any) -> dict[str, Any]:
        """« Demander une révision » : toutes les remarques pas encore envoyées, en UN message."""
        with self._lock:
            plan = self._get(pid)
            if plan is None:
                return {"ok": False, "error": f"plan inconnu : {pid}"}
            if plan["status"] not in OPEN:
                return {"ok": False, "error": f"plan {plan['id']} clos ({plan['status']})"}
            remarks = self._unsent(plan)
            if not remarks:
                return {"ok": False, "error": "aucune remarque à envoyer : commente un point d'abord"}
            before = json.loads(json.dumps(plan))
            lines = []
            for r in remarks:
                r["sent"] = True
                ref = _ref(r["point"], self._point_text(plan, r.get("version", plan["version"]), r["point"]))
                lines.append(f"- Sur {ref} : {r['text']}")
            plan["status"], plan["updated"] = REVISING, time.time()
            error = self._commit(plan, before)
            if error:
                return {"ok": False, "error": error}
            pid = plan["id"]
            message = (f"Révision du plan {pid} (version {plan['version']}) demandée par l'utilisateur. Ses remarques :\n"
                       + "\n".join(lines)
                       + f"\nLis {plan_path(pid)}. {_replies_hint(pid)} Puis republie le plan corrigé EN ENTIER avec "
                         f"plan_submit(plan_id=\"{pid}\", …), mêmes rubriques. Ensuite réponds seulement [SILENT].")
            result = {"ok": True, "id": pid, "count": len(remarks), "message": message,
                      "shown": f"Plan {pid} : révision demandée ({len(remarks)} remarque{'s' if len(remarks) > 1 else ''}).",
                      "channel": plan.get("channel", "")}
        self._changed(plan)
        return result

    def validate(self, pid: Any, version: Any, by: str = "utilisateur") -> dict[str, Any]:
        """Validation : seulement un plan `pending`, et seulement la version affichée.
        Les remarques jamais envoyées rejoignent le `.md` du codeur."""
        if isinstance(version, bool):
            version = None
        try:
            wanted = int(str(version).strip()) if version is not None and str(version).strip() else None
        except ValueError:
            wanted = None
        with self._lock:
            plan = self._get(pid)
            if plan is None:
                return {"ok": False, "error": f"plan inconnu : {pid}"}
            if plan["status"] != PENDING:
                why = {REVISING: "une révision est en cours, attends la nouvelle version"}.get(plan["status"],
                                                                                              f"plan clos ({plan['status']})")
                return {"ok": False, "error": f"plan {plan['id']} non validable : {why}", "status": plan["status"]}
            if wanted != plan["version"]:
                return {"ok": False, "error": f"version périmée : tu as la version {version}, le plan en est à la "
                                              f"version {plan['version']} ; relis-la avant de valider",
                        "version": plan["version"]}
            before = json.loads(json.dumps(plan))
            final = []
            for r in self._unsent(plan):
                r["sent"] = True
                ref = _ref(r["point"], self._point_text(plan, r.get("version", plan["version"]), r["point"]))
                final.append(f"Sur {ref} : {r['text']}")
            plan["final_remarks"] = final
            plan.update(status=VALIDATED, validated_by=by, validated_at=time.time(), updated=time.time())
            error = self._commit(plan, before)
            if error:
                return {"ok": False, "error": error}
            pid = plan["id"]
            auto = by == "auto"
            result = {"ok": True, "id": pid, "version": plan["version"], "remarks": len(final), "by": by,
                      "message": validated_message(pid, auto), "shown": validated_shown(pid, auto),
                      "channel": plan.get("channel", "")}
        log.info("plan %s version %d validé (%s, %d remarque(s) jointe(s))", pid, result["version"], by, len(final))
        self._changed(plan)
        return result

    def reject(self, pid: Any, reason: Any = "") -> dict[str, Any]:
        """Rejet par l'utilisateur (motif facultatif). Rien n'est envoyé au cerveau."""
        reason = _lines(reason, REASON_MAX)
        with self._lock:
            plan = self._get(pid)
            if plan is None:
                return {"ok": False, "error": f"plan inconnu : {pid}"}
            if plan["status"] not in OPEN:
                return {"ok": False, "error": f"plan {plan['id']} clos ({plan['status']})"}
            before = json.loads(json.dumps(plan))
            plan.update(status=REJECTED, reject_reason=reason, updated=time.time())
            error = self._commit(plan, before)
            if error:
                return {"ok": False, "error": error}
            result = {"ok": True, "id": plan["id"]}
        log.info("plan %s rejeté%s", plan["id"], f" : {reason}" if reason else "")
        self._changed(plan)
        return result


plans = Plans()
