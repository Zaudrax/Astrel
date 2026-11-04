"""Canaux du HUD : un fil de discussion par projet (Général, Studio, Compta…).

La conversation avec Claude reste UNE seule session continue : rien ici ne
touche au cerveau. Seul l'AFFICHAGE est rangé : chaque message publié sur le
bus (`transcript`, `reply_delta`, `reply`) porte un champ `channel`, et le HUD
n'affiche que ceux du canal ouvert, en comptant les non-lus des autres.

La liste vient de `config/channels.json`, relue dès qu'elle change : ajouter un
projet ne demande ni code ni relance.

    {"default": "general",
     "channels": [{"id": "general", "label": "Général"},
                  {"id": "studio", "label": "Studio", "color": "#34e3a4", "keywords": ["bracelet"]}]}

Rattachement d'un message de l'utilisateur (le pourquoi : README, « Canaux ») :
1. le canal OUVERT dans le HUD au moment où il parle ou tape ;
2. sauf s'il s'adresse à un autre canal en COMMENÇANT par son nom
   (« Compta, … », « pour Studio … », « #compta … ») ;
3. depuis le canal par défaut, une demande qui nomme UN SEUL projet y part.
Dans les cas 2 et 3 le canal ouvert suit : le HUD bascule, et la suite de la
conversation vocale reste dans ce canal.

La réponse d'Astrel s'affiche dans le canal du DERNIER message de l'utilisateur
(`turn`) ; un tour relancé seul par le CLI à la fin d'une tâche de fond, dans
le canal où la tâche a été lancée. Un message mal rangé se déplace à la main
(clic droit dans le HUD, `move`).

27/10 (plan P41) : RIEN NE SE PERD dans Général. Ce qui n'a pas de projet (tour de réflexe,
d'équipe ou de courrier, tâche lancée depuis Général, carte ou compte rendu sans canal) va dans le
canal OUVERT (`attention`) ; ce qui a un projet reste dans son canal, et l'onglet du HUD compte les
non-lus. Le nom de l'assistant en tête de phrase ne désigne pas un canal (`strip_vocative`).
"""
from __future__ import annotations

import contextlib
import functools
import json
import logging
import os
import re
import tempfile
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config
from .events import bus

log = logging.getLogger("astrel.channels")

DEFAULT = "general"
PALETTE = ("#3ad6ff", "#34e3a4", "#9d7bff", "#ffb547", "#ff7ac8", "#c3f36b", "#7ea8ff")
# Mots qui peuvent précéder le nom d'un canal quand l'utilisateur s'adresse à lui
# (texte déjà sans accents ni majuscules : « côté » s'écrit « cote »).
_LEAD = re.compile(r"\s*(#|(?:(?:pour|sur|dans|cote|canal|projet|le|la|les|du|de|au)\s+|l')+)?")
_SEPARATORS = ",:;.!?-–—"
# Migrations faites une fois dans config/channels.json (`Channels.migrate`), marquées ici.
MIGRATIONS_KEY = "_migrations"
MIGRATION_ASTREL = "p41-astrel"


@dataclass(frozen=True)
class Channel:
    id: str
    label: str
    color: str
    keywords: tuple[str, ...] = ()
    workdir: str = ""  # dossier du projet (14/10) : rappelé à Claude quand on passe dans ce canal
    words: tuple[str, ...] = ()  # mots-clés tels qu'écrits dans channels.json (15/10, fenêtre du HUD)
    # 04/11 (moteur local) : « local » = un message écrit DANS ce canal est traité par le moteur local ;
    # jamais choisi pour un message qui ne fait que citer son nom (« le serveur local »).
    engine: str = ""

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "label": self.label, "color": self.color}
        if self.engine:
            out["engine"] = self.engine
        if self.workdir:
            out["workdir"] = self.workdir
        if self.words:  # 15/10 : préremplit la fenêtre « Modifier » du HUD
            out["keywords"] = list(self.words)
        return out


@dataclass
class TurnRef:
    """Où va la réponse en cours : canal, message auquel elle répond, et
    position dans le texte du tour où ce morceau de réponse commence.
    `floating` (27/10, plan P41) : tour SANS projet (réflexe, équipe, courrier, fin de tâche
    lancée depuis Général) posé dans le canal OUVERT ; ses tâches et ses alertes suivent le
    canal ouvert au moment où elles arrivent (`Channels.attention`)."""
    channel: str
    reply_to: str = ""
    start: int = 0
    floating: bool = False


def fold(text: str) -> str:
    """Minuscules sans accents : « Général » et « general » se valent."""
    s = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(c for c in s if not unicodedata.combining(c)).replace("’", "'")


def slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "-", fold(str(value or "")).strip()).strip("-")


def parse(data: Any) -> tuple[list[Channel], str]:
    """Canaux valides quoi que contienne le fichier : un fichier abîmé ne doit
    pas vider le HUD. Il y a toujours au moins un canal, et un canal par défaut."""
    raw = data.get("channels") if isinstance(data, dict) else None
    items: list[Channel] = []
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, str):
            entry = {"label": entry}
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or entry.get("id") or "").strip()
        cid = slug(entry.get("id") or label)
        if not cid or any(c.id == cid for c in items):
            continue
        color = str(entry.get("color") or "")
        if not re.fullmatch(r"#[0-9a-fA-F]{3,8}", color):
            color = PALETTE[len(items) % len(PALETTE)]
        words = entry.get("keywords") if isinstance(entry.get("keywords"), list) else []
        keys = [fold(w).strip() for w in (label, cid, cid.replace("-", " "), *map(str, words))]
        workdir = str(entry.get("workdir") or "").strip()
        engine = "local" if str(entry.get("moteur") or "").strip().lower() == "local" else ""
        items.append(Channel(cid, label or cid, color, tuple(dict.fromkeys(k for k in keys if k)), workdir,
                             tuple(str(w).strip() for w in words if str(w).strip()), engine))
    if not items:
        items = [Channel(DEFAULT, "Général", PALETTE[0], ("general",))]
    wanted = slug(data.get("default")) if isinstance(data, dict) else ""
    ids = [c.id for c in items]
    default = wanted if wanted in ids else DEFAULT if DEFAULT in ids else ids[0]
    return items, default


def _entry_id(entry: Any) -> str:
    """Id d'une entrée brute de config/channels.json (objet ou simple nom)."""
    if isinstance(entry, dict):
        return slug(entry.get("id") or entry.get("label"))
    return slug(entry)


class _Unreadable(Exception):
    """config/channels.json existe mais ne se lit pas : ne pas l'écraser."""


def _one_writer(method):
    """Une seule écriture du fichier à la fois, du `_raw()` au `_write()` (19/10)."""
    @functools.wraps(method)
    def wrapper(self: "Channels", *args: Any, **kwargs: Any) -> dict[str, Any]:
        with self._file_lock:
            try:
                return method(self, *args, **kwargs)
            except _Unreadable as exc:
                log.error("canaux NON modifiés, %s illisible : %s", config.CHANNELS_FILE, exc)
                return {"ok": False, "error": f"config/channels.json illisible ({exc}) : corrige-le à la main, "
                                              "rien n'a été écrit"}
    return wrapper


# 27/10 (plan P41) : nom de l'assistant en tête de phrase = on lui PARLE (« Astrel, ouvre… »), pas
# l'adresse du canal Astrel. Sans ça, renommer le canal « Astrel » en « Astrel » (nom affiché et mot
# d'éveil) aurait rangé dans ce canal tout message commençant par « Astrel ». « canal Astrel, … »,
# « pour Astrel … », « #astrel » visent toujours le canal (mot devant : pas retiré).
_VOCATIVE_LEAD = r"(?:(?:hey|he|ok|okay|dis|salut|bonjour)\s+)?"
_VOCATIVE_END = r"(?=$|[\s" + re.escape(_SEPARATORS) + r"])[\s" + re.escape(_SEPARATORS) + r"]*"
_LEGACY_NAMES = ("jarvis", "hey jarvis")  # ancien nom et ancien mot d'éveil (alias `wake_legacy`)


def _assistant_names() -> list[str]:
    """Noms de l'assistant, pliés : nom affiché + variantes d'éveil (`eveil.noms_actuels`) + ancien nom."""
    try:
        from . import eveil

        names = list(eveil.noms_actuels())
    except Exception:  # réglages illisibles : l'ancien nom suffit à ne rien casser
        names = []
    out = {" ".join(fold(n).split()) for n in [*names, *_LEGACY_NAMES]}
    return sorted((n for n in out if n), key=len, reverse=True)


def strip_vocative(text: str, names: list[str] | None = None) -> str:
    """Texte plié sans le nom de l'assistant en tête (« astrel, relance » → « relance »)."""
    names = _assistant_names() if names is None else names
    if not names:
        return text
    alts = "|".join(r"\s+".join(map(re.escape, n.split())) for n in names)
    return re.sub(r"^\s*" + _VOCATIVE_LEAD + r"(?:" + alts + r")" + _VOCATIVE_END, "", text, count=1)


def _mentions(text: str, ch: Channel) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", text) for k in ch.keywords)


def _addressed(text: str, ch: Channel) -> bool:
    """« Compta, relance la recherche », « pour Studio lance les tests » :
    le message COMMENCE par le nom du canal. Sans préposition, le nom doit être
    suivi d'une ponctuation (« Studio avance bien ? » parle de Studio sans
    s'adresser à son canal)."""
    m = _LEAD.match(text)
    lead, rest = m.group(1), text[m.end():]
    for k in sorted(ch.keywords, key=len, reverse=True):
        if not rest.startswith(k):
            continue
        after = rest[len(k):]
        if after[:1].isalnum() or after[:1] == "_":
            continue
        after = after.lstrip()
        if lead or not after or after[0] in _SEPARATORS:
            return True
    return False


class Channels:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._file_lock = threading.RLock()  # écritures de config/channels.json (`_one_writer`)
        self._mtime: float | None = None
        self._items: list[Channel] = []
        self.default = DEFAULT
        self._active = ""
        self.turn = TurnRef(DEFAULT)
        self._tasks: dict[str, str] = {}              # tâche du CLI → canal où elle a été lancée
        self._finished: deque[tuple[str, float]] = deque(maxlen=20)  # tâches de fond finies, pas encore racontées

    # --- la liste ---

    def _load(self) -> bool:
        """Relit le fichier s'il a changé. Vrai si la liste a changé."""
        try:
            mtime = config.CHANNELS_FILE.stat().st_mtime
        except OSError:
            mtime = 0.0
        with self.lock:
            if mtime == self._mtime:
                return False
            try:
                data = json.loads(config.CHANNELS_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                if mtime:
                    log.warning("canaux illisibles (%s) : %s", config.CHANNELS_FILE, exc)
                data = {}
            first = self._mtime is None
            self._mtime = mtime
            self._items, self.default = parse(data)
            ids = self.ids()
            if first:
                try:
                    saved = slug(config.CHANNEL_FILE.read_text(encoding="utf-8"))
                except OSError:
                    saved = ""
                self._active = saved
            if self._active not in ids:
                self._active = self.default
            if self.turn.channel not in ids:
                self.turn = TurnRef(self.default)
            return True

    @property
    def active(self) -> str:
        """Canal ouvert dans le HUD : là où part ce que l'utilisateur dit."""
        self._load()
        with self.lock:
            return self._active

    def items(self) -> list[Channel]:
        self._load()
        with self.lock:
            return list(self._items)

    def ids(self) -> list[str]:
        with self.lock:
            return [c.id for c in self._items]

    def local_ids(self) -> frozenset[str]:
        """04/11 : canaux dont les messages vont au moteur local (`"moteur": "local"`)."""
        return frozenset(c.id for c in self.items() if c.engine == "local")

    def find(self, name: str) -> str:
        """Id d'un canal donné par son id ou son nom (« Compta », « compta ») ; vide si inconnu."""
        wanted = slug(name)
        for c in self.items():
            if wanted in (c.id, slug(c.label)):
                return c.id
        return ""

    def publish(self) -> None:
        items = self.items()
        with self.lock:
            active, default = self.active, self.default
        bus.publish("channels", items=[c.public() for c in items], active=active, default=default)

    def refresh(self) -> None:
        """Republie la liste si le fichier a changé (projet ajouté à chaud)."""
        if self._load():
            self.publish()

    def describe(self) -> dict[str, Any]:
        items = self.items()
        with self.lock:
            return {"ok": True, "active": self.active, "default": self.default, "channels": [c.public() for c in items]}

    # --- le canal ouvert ---

    def select(self, name: str) -> dict[str, Any]:
        cid = self.find(name)
        if not cid:
            return {"ok": False, "error": f"canal inconnu : {name or '(vide)'} "
                                          f"({', '.join(self.ids())} ; la liste est dans config/channels.json)"}
        self._set_active(cid)
        return {"ok": True, "active": cid}

    def _set_active(self, cid: str) -> None:
        with self.lock:
            changed = cid != self._active
            self._active = cid
        if changed:
            try:
                config.CHANNEL_FILE.write_text(cid + "\n", encoding="utf-8")
            except OSError:
                log.warning("canal ouvert non retenu : %s", config.CHANNEL_FILE)
        self.publish()

    # --- rattachement ---

    def route(self, text: str, requested: str = "", follow: bool = True) -> tuple[str, bool]:
        """Canal d'un message de l'utilisateur, et s'il a été redirigé ailleurs que
        le canal demandé. `requested` : canal ouvert dans le HUD qui l'a envoyé
        (sinon le canal ouvert connu ici). `follow` : le canal ouvert suit le
        message (faux pour un réflexe, que l'utilisateur n'a pas dit)."""
        self.refresh()
        items = self.items()
        with self.lock:
            base = self.find(requested) if requested else ""
            base = base or (self.active if follow else self.default)
            t = strip_vocative(fold(text))
            addressed = [c.id for c in items if _addressed(t, c)]
            target = base
            if len(addressed) == 1:
                target = addressed[0]
            elif base == self.default and not addressed:
                mentioned = [c.id for c in items if c.id != self.default and not c.engine and _mentions(t, c)]
                if len(mentioned) == 1:
                    target = mentioned[0]
            move = follow and target != self.active
        if move:
            self._set_active(target)
        return target, target != base

    # --- tâches de fond ---

    def attention(self, cid: str = "") -> str:
        """27/10 (plan P41, demande de l'utilisateur : « des messages se perdent dans Général ») : canal
        d'une alerte pour l'utilisateur (carte, compte rendu, relance d'agent, fin de tâche). Celui de
        son PROJET s'il en a un ; sinon (vide, Général, canal retiré) le canal OUVERT dans le HUD."""
        found = self.find(cid) if cid else ""
        if found and found != self.default:
            return found
        return self.active

    def task_started(self, tid: str) -> str:
        """Une tâche lancée pendant un tour appartient au canal de ce tour ; celle d'un tour sans
        projet (`floating`) n'a pas de projet : canal par défaut, rangée à sa fin par `attention`."""
        with self.lock:
            return self._tasks.setdefault(tid, self.default if self.turn.floating else self.turn.channel)

    def task_ended(self, tid: str, background: bool) -> str:
        with self.lock:
            ch = self._tasks.pop(tid, self.turn.channel)
            if background:
                self._finished.append((ch, time.monotonic()))
            return ch

    def background_turn(self) -> str:
        """Canal d'un tour que le CLI relance seul : celui de la tâche de fond
        qui vient de finir (le CLI envoie la fin de tâche juste avant le tour,
        mesuré sur `tests/fixtures/claude-stream-fond.jsonl`)."""
        now = time.monotonic()
        with self.lock:
            while self._finished:
                ch, at = self._finished.popleft()
                if now - at <= config.CHANNEL_BACKGROUND_WINDOW_S and ch in self.ids():
                    return ch
            return self.default

    # --- créer, renommer, supprimer (14/10 : HUD « + », clic droit, outil MCP channel_create) ---
    # Le fichier reste celui que l'utilisateur édite à la main : ses autres champs
    # (« _aide », couleurs, mots-clés) sont gardés tels quels.

    # 19/10 (relecture) : chaque commande du HUD tourne dans son propre thread. Sans
    # verrou ni fichier temporaire unique, deux écritures croisées (Alt+→ maintenu)
    # lisaient un fichier tronqué, repartaient de « Général » seul et le réécrivaient :
    # canaux perdus. Les quatre méthodes d'écriture passent donc par `_one_writer`.

    def _raw(self) -> dict[str, Any]:
        """Contenu brut du fichier, à modifier puis réécrire. Fichier absent : la liste
        connue (première création). Fichier illisible ou sans tableau « channels » :
        `_Unreadable`, on ne réécrit RIEN (on perdrait les canaux qu'il contient)."""
        try:
            text = config.CHANNELS_FILE.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"channels": [c.public() for c in self.items()]}
        except OSError as exc:
            raise _Unreadable(str(exc)) from exc
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise _Unreadable(f"JSON invalide ({exc})") from exc
        if not isinstance(data, dict) or not isinstance(data.get("channels"), list):
            raise _Unreadable("pas de tableau « channels »")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        target = config.CHANNELS_FILE
        try:
            mode = target.stat().st_mode & 0o777
        except OSError:
            mode = 0o644
        fd, tmp = tempfile.mkstemp(prefix=".channels-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            os.chmod(tmp, mode)  # mkstemp crée en 0600 : garder les droits du fichier
            os.replace(tmp, target)  # atomique : un lecteur voit l'ancien ou le nouveau, jamais un morceau
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        with self.lock:
            self._mtime = -1.0  # relire même si le mtime n'a pas bougé (même seconde)
        self._load()
        self.publish()

    @staticmethod
    def guess_workdir(label: str) -> str:
        """Dossier de projet du même nom sous le dossier de travail (deux niveaux :
        « Studio », « Studio/Astrel »), vide si aucun ou plusieurs."""
        wanted = {slug(label), fold(label).replace(" ", "")}
        found: list[str] = []
        try:
            level1 = [p for p in config.WORKDIR.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))]
        except OSError:
            return ""
        candidates = list(level1)
        for p in level1:
            try:
                candidates += [q for q in p.iterdir() if q.is_dir() and not q.name.startswith((".", "_"))
                               and q.name != "node_modules"]
            except OSError:
                continue
        for p in candidates:
            if slug(p.name) in wanted or fold(p.name).replace(" ", "") in wanted:
                found.append(str(p))
        return found[0] if len(found) == 1 else ""

    @_one_writer
    def create(self, label: str, workdir: str = "", keywords: Any = None, color: str = "") -> dict[str, Any]:
        label = re.sub(r"\s+", " ", str(label or "")).strip()[:40]
        cid = slug(label)
        if not cid:
            return {"ok": False, "error": "nom de canal vide"}
        if self.find(label) or cid in self.ids():
            return {"ok": False, "error": f"le canal « {label} » existe déjà"}
        workdir = str(workdir or "").strip()
        if workdir:
            p = Path(workdir).expanduser()
            if not p.is_absolute():
                p = config.WORKDIR / p
            if not p.is_dir():
                return {"ok": False, "error": f"dossier introuvable : {workdir}"}
            workdir = str(p.resolve())
        else:
            workdir = self.guess_workdir(label)
        words = [str(w).strip() for w in (keywords if isinstance(keywords, list) else
                                          str(keywords or "").split(",")) if str(w).strip()]
        if workdir and fold(Path(workdir).name) not in (fold(label), *map(fold, words)):
            words.append(Path(workdir).name)
        data = self._raw()
        entry: dict[str, Any] = {"id": cid, "label": label}
        if re.fullmatch(r"#[0-9a-fA-F]{3,8}", color or ""):
            entry["color"] = color
        else:
            entry["color"] = PALETTE[len(data["channels"]) % len(PALETTE)]
        if words:
            entry["keywords"] = words
        if workdir:
            entry["workdir"] = workdir
        data["channels"].append(entry)
        self._write(data)
        log.info("canal créé : %s (%s)", label, workdir or "sans dossier")
        return {"ok": True, "channel": cid, "label": label, "workdir": workdir}

    @_one_writer
    def edit(self, name: str, label: str | None = None, workdir: str | None = None,
             keywords: Any = None) -> dict[str, Any]:
        """Renomme (l'id reste : les messages déjà rangés suivent) ou relie à un dossier."""
        cid = self.find(name)
        if not cid:
            return {"ok": False, "error": f"canal inconnu : {name}"}
        data = self._raw()
        for i, entry in enumerate(data["channels"]):
            if _entry_id(entry) != cid:
                continue
            if not isinstance(entry, dict):
                entry = {"id": cid, "label": str(entry)}
            data["channels"][i] = entry
            if label is not None:
                new = re.sub(r"\s+", " ", str(label)).strip()[:40]
                if not new:
                    return {"ok": False, "error": "nom vide"}
                other = self.find(new)
                if other and other != cid:
                    return {"ok": False, "error": f"le canal « {new} » existe déjà"}
                entry["label"] = new
            if workdir is not None:
                workdir = str(workdir).strip()
                p = Path(workdir).expanduser()
                if workdir and not p.is_absolute():
                    p = config.WORKDIR / p  # relatif au dossier de travail, comme à la création
                if workdir and not p.is_dir():
                    return {"ok": False, "error": f"dossier introuvable : {workdir}"}
                if workdir:
                    entry["workdir"] = str(p.resolve())
                else:
                    entry.pop("workdir", None)
            if keywords is not None:
                entry["keywords"] = [str(w).strip() for w in (keywords if isinstance(keywords, list)
                                                              else str(keywords).split(",")) if str(w).strip()]
            self._write(data)
            return {"ok": True, "channel": cid, "label": entry.get("label", "")}
        return {"ok": False, "error": f"canal « {name} » absent de config/channels.json"}

    @_one_writer
    def delete(self, name: str) -> dict[str, Any]:
        """Retire un canal de la liste. Ses messages restent, affichés dans le
        canal par défaut ; le canal par défaut ne se supprime pas."""
        cid = self.find(name)
        if not cid:
            return {"ok": False, "error": f"canal inconnu : {name}"}
        if cid == self.default:
            return {"ok": False, "error": "le canal par défaut ne se supprime pas"}
        data = self._raw()
        before = len(data["channels"])
        data["channels"] = [e for e in data["channels"] if _entry_id(e) != cid]
        if len(data["channels"]) == before:
            return {"ok": False, "error": f"canal « {name} » absent de config/channels.json"}
        self._write(data)
        log.info("canal supprimé : %s", cid)
        return {"ok": True, "deleted": cid}

    @_one_writer
    def reorder(self, order: Any) -> dict[str, Any]:
        """Classe les onglets (19/10, glisser-déposer du HUD) : l'ordre du tableau
        « channels » de config/channels.json fait foi. `order` = ids (ou noms) dans
        l'ordre voulu ; un canal absent de la liste (créé entre-temps par un autre
        HUD) garde son rang relatif, à la fin. Le canal par défaut ne change pas."""
        if not isinstance(order, list) or len(order) > 200:
            return {"ok": False, "error": "ordre attendu : une liste d'ids de canaux"}
        wanted: list[str] = []
        for name in order:
            cid = self.find(name) if isinstance(name, str) else ""
            if not cid:
                return {"ok": False, "error": f"canal inconnu : {name} ({', '.join(self.ids())})"}
            if cid in wanted:
                return {"ok": False, "error": f"canal en double : {cid}"}
            wanted.append(cid)
        current = [c.id for c in self.items()]  # items() relit le fichier, ids() non
        new = wanted + [c for c in current if c not in wanted]
        if new == current:
            return {"ok": True, "order": current, "changed": False}
        data = self._raw()
        rank = {cid: i for i, cid in enumerate(new)}
        # Tri stable : une entrée brute sans id lisible (que `parse` ignore) part en fin de liste.
        data["channels"] = sorted(data["channels"], key=lambda e: rank.get(_entry_id(e), len(rank)))
        self._write(data)
        log.info("canaux reclassés : %s", ", ".join(self.ids()))
        return {"ok": True, "order": self.ids(), "changed": True}

    @_one_writer
    def migrate(self) -> dict[str, Any]:
        """27/10 (plan P41, demande de l'utilisateur) : le canal « Astrel » s'appelle « Astrel » (nom de
        l'assistant). Seul le NOM affiché change : l'id `astrel` reste (runtime/channel.txt,
        historique, mémoire, mots-clés dont « Astrel »). Appelé au démarrage du cœur, fait UNE fois
        (marque dans `_migrations`) : un renommage ultérieur par l'utilisateur est respecté. Fichier
        absent : rien à faire."""
        if not config.CHANNELS_FILE.exists():
            return {"ok": True, "changed": False}
        data = self._raw()
        done = data.get(MIGRATIONS_KEY) if isinstance(data.get(MIGRATIONS_KEY), list) else []
        if MIGRATION_ASTREL in done:
            return {"ok": True, "changed": False}
        renamed = False
        for entry in data["channels"]:
            if (isinstance(entry, dict) and _entry_id(entry) == "astrel"
                    and str(entry.get("label") or "").strip() == "Astrel"):
                entry["label"] = "Astrel"
                renamed = True
        data[MIGRATIONS_KEY] = [*done, MIGRATION_ASTREL]
        self._write(data)
        if renamed:
            log.info("canal « Astrel » renommé « Astrel » (id astrel inchangé)")
        return {"ok": True, "changed": renamed}

    def workdir_of(self, cid: str) -> str:
        for c in self.items():
            if c.id == cid:
                return c.workdir
        return ""

    # --- déplacement à la main ---

    def move(self, mid: str, name: str) -> dict[str, Any]:
        """Range un message ailleurs. Un message de l'utilisateur emmène la réponse de
        Astrel avec lui ; une réponse seule ne déplace qu'elle-même."""
        cid = self.find(name)
        if not mid:
            return {"ok": False, "error": "message non précisé"}
        if not cid:
            return {"ok": False, "error": f"canal inconnu : {name}"}
        count = bus.update_history(lambda ev: mid in (ev.get("mid"), ev.get("reply_to")), channel=cid)
        with self.lock:
            if self.turn.reply_to == mid:
                self.turn.channel = cid  # la réponse en cours suit son message
        bus.publish("channel_move", mid=mid, channel=cid)
        return {"ok": True, "channel": cid, "moved": count}


channels = Channels()
