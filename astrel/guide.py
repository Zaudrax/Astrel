"""Guide de démarrage interactif du HUD (18/10, plan P5, demande de l'utilisateur : « un
guide de démarrage interactif qui explique tous les fonctionnements d'Astrel, tous
les réglages que la personne doit faire pour l'installation »).

Source UNIQUE : `docs/GUIDE-DEMARRAGE.md`, lisible tel quel sur le dépôt. Chaque
sujet est un titre `## …` suivi d'un commentaire qui le décrit :

    <!-- id: micro | etape | test: audio.test/mic | reglage: audio.mic -->

- `id` : identifiant stable (aide « ? » de la page « Réglages », progression) ;
- `etape` : étape d'installation, à cocher (les autres sujets expliquent) ;
- `test` : bouton « Tester » = `settings_action {key, action}` existant ;
- `reglage` : clé de la page « Réglages » montrée dans le guide (réglée sur place).

Le texte avant le premier `##` est l'introduction (titre `#` compris). Progression
dans `runtime/guide.json` ; cochée d'office quand c'est mesurable (compte Claude
connecté, micro vu par le contrôle de la machine, WhatsApp relié). « Terminé » pose
le marqueur du premier lancement (`demarrage.mark_first_run_done`).

Commandes du cœur : `guide_get`, `guide_mark {topic, done}` ou `{finish: true}`,
refusées à Claude.

20/10 (langue de l'interface) : `lang: "en"` dans la commande (sinon le réglage
`ui_language`, `astrel/i18n.py`) sert `docs/GUIDE-DEMARRAGE.en.md`, mêmes `id` (progression
commune) ; s'il manque, le guide français. Espaces insécables françaises : en français seulement.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any

from . import config

log = logging.getLogger("astrel.guide")

_META = re.compile(r"^\s*<!--\s*(.*?)\s*-->\s*$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_KEY = re.compile(r"^[a-z0-9_.]{1,60}$")


def parse(text: str, lang: str = "fr") -> dict:
    """Markdown du guide → {title, intro, topics: [{id, titre, corps, etape, test, reglage}]}.
    Un sujet sans commentaire `id` valide est ignoré (avec une ligne au journal)."""
    french = lang != "en"
    title, intro_lines = "", []
    topics: list[dict] = []
    current: dict | None = None
    body: list[str] = []
    in_code = False

    def close() -> None:
        if current is not None:
            current["corps"] = reflow("\n".join(body).strip(), french)
            topics.append(current)

    for line in str(text or "").splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("## "):
            close()
            current, body = {"titre": line[3:].strip(), "id": "", "etape": False, "test": None, "reglage": ""}, []
            continue
        if current is None:
            if not in_code and line.startswith("# ") and not title:
                title = line[2:].strip()
            else:
                intro_lines.append(line)
            continue
        meta = _META.match(line) if not in_code and not current["id"] else None
        if meta:
            _read_meta(current, meta.group(1))
            continue
        body.append(line)
    close()
    seen: set[str] = set()
    kept = []
    for t in topics:
        if not t["id"] or t["id"] in seen:
            log.warning("guide : sujet « %s » ignoré (id absent ou en double)", t["titre"][:60])
            continue
        seen.add(t["id"])
        kept.append(t)
    return {"title": title, "intro": reflow("\n".join(intro_lines).strip(), french), "topics": kept}


_BLOCK = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|#|>|\|)")


def _nbsp(line: str) -> str:
    """Espace insécable avant « : ; ? ! » et dans les guillemets français (le HUD ne
    renvoie plus « : » seul à la ligne) ; jamais dans un `code`."""
    parts = line.split("`")
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r" ([:;?!»])", "\u00a0\\1", parts[i]).replace("« ", "«\u00a0")
    return "`".join(parts)


def reflow(text: str, french: bool = True) -> str:
    """Recolle les lignes coupées à la main dans le .md (le HUD garde chaque saut de
    ligne d'un paragraphe) : une ligne de texte suit la précédente, sauf début de bloc
    (puce, numéro, titre, citation, tableau), ligne vide ou bloc de code."""
    out: list[str] = []
    in_code = joinable = False
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            out.append(line)
            joinable = False
            continue
        if in_code or not stripped:
            out.append(line)
            joinable = False
            continue
        line = _nbsp(line) if french else line
        stripped = line.strip()
        if joinable and not _BLOCK.match(line):
            out[-1] = out[-1].rstrip() + " " + stripped
        else:
            out.append(line)
        joinable = not stripped.startswith("#")
    return "\n".join(out)


def _read_meta(topic: dict, raw: str) -> None:
    for part in raw.split("|"):
        name, sep, value = part.strip().partition(":")
        name, value = name.strip().lower(), value.strip()
        if not sep and name == "etape":
            topic["etape"] = True
        elif name == "id" and _ID.match(value):
            topic["id"] = value
        elif name == "test":
            key, _, action = value.partition("/")
            if _KEY.match(key) and re.match(r"^[a-z0-9_]{1,40}$", action):
                topic["test"] = {"key": key, "action": action}
        elif name == "reglage" and _KEY.match(value):
            topic["reglage"] = value


# --------------------------------------------------------------------------- cochés d'office

def _claude_ok() -> bool:
    from .compte import compte
    state = compte.state() or {}
    return bool(state.get("loggedIn"))


def _micro_ok() -> bool:
    from .demarrage import demarrage
    return demarrage.check_status("micro") == "OK"


def _whatsapp_ok() -> bool:
    try:
        data = json.loads((config.WHATSAPP_AUTH_DIR / "self.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(str(data.get("pn") or "").strip())


AUTO = {"claude": _claude_ok, "micro": _micro_ok, "whatsapp": _whatsapp_ok}


class Guide:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict]] = {}  # langue → (mtime, guide lu)

    # --- source

    @staticmethod
    def _lang(lang: str | None) -> str:
        from . import i18n
        return lang if lang in i18n.LANGS else i18n.lang()

    def source(self, lang: str | None = None) -> dict:
        lang = self._lang(lang)
        path = config.GUIDE_FILE
        if lang == "en":
            english = path.with_name(path.stem + ".en" + path.suffix)
            if english.exists():
                path = english
            else:
                lang = "fr"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {"title": "", "intro": "", "topics": []}
        with self._lock:
            if mtime != self._cache.get(lang, (-1.0, None))[0]:
                try:
                    self._cache[lang] = (mtime, parse(path.read_text(encoding="utf-8"), lang))
                except OSError as exc:
                    log.warning("guide illisible : %s", exc)
                    return {"title": "", "intro": "", "topics": []}
            return self._cache[lang][1]

    # --- progression

    @staticmethod
    def _load() -> dict:
        try:
            data = json.loads(config.GUIDE_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _save(data: dict) -> None:
        path = config.GUIDE_STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _auto(topic_id: str) -> bool:
        fn = AUTO.get(topic_id)
        if fn is None:
            return False
        try:
            return bool(fn())
        except Exception as exc:  # un contrôle en panne ne casse pas le guide
            log.warning("guide : contrôle « %s » impossible : %s", topic_id, exc)
            return False

    def describe(self, lang: str | None = None) -> dict:
        from .demarrage import first_run

        src = self.source(lang)
        marked = self._load().get("done")
        marked = marked if isinstance(marked, dict) else {}
        topics = []
        for t in src["topics"]:
            auto = self._auto(t["id"]) if t["etape"] else False
            topics.append({**t, "auto": auto, "done": bool(t["etape"] and (auto or marked.get(t["id"])))})
        steps = [t for t in topics if t["etape"]]
        return {"ok": True, "title": src["title"], "intro": src["intro"], "topics": topics,
                "steps": len(steps), "steps_done": sum(1 for t in steps if t["done"]), "first_run": first_run()}

    def mark(self, topic: str, done: bool, lang: str | None = None) -> dict:
        ids = {t["id"]: t for t in self.source()["topics"]}
        t = ids.get(str(topic or ""))
        if t is None or not t["etape"]:
            return {"ok": False, "error": f"étape inconnue : {topic or '(vide)'}"}
        with self._lock:
            data = self._load()
            marked = data.get("done") if isinstance(data.get("done"), dict) else {}
            if done:
                marked[t["id"]] = int(time.time())
            else:
                marked.pop(t["id"], None)
            data["done"] = marked
            try:
                self._save(data)
            except OSError as exc:
                return {"ok": False, "error": f"progression non enregistrée : {exc}"}
        return self.describe(lang)

    def finish(self, lang: str | None = None) -> dict:
        """« Terminé » : le guide et la page « Réglages » ne s'ouvrent plus seuls."""
        from .demarrage import mark_first_run_done

        res = mark_first_run_done()
        if not res.get("ok"):
            return res
        return self.describe(lang)


guide = Guide()


def command(name: str, cmd: dict[str, Any]) -> dict:
    """`guide_get` / `guide_mark {topic, done}` / `guide_mark {finish: true}` (le cœur a
    déjà refusé ce qui vient de Claude)."""
    lang = str(cmd.get("lang") or "") or None
    if name == "guide_get":
        return guide.describe(lang)
    if cmd.get("finish") is True:
        return guide.finish(lang)
    return guide.mark(str(cmd.get("topic") or ""), cmd.get("done") is not False, lang)
