"""Recherche plein texte dans l'historique des conversations (outil MCP `history_search`).

Source : les transcriptions que le CLI `claude` écrit lui-même, une par session
(`~/.claude/projects/-home-user-Projets/*.jsonl`, sessions d'Astrel ET
sessions de l'utilisateur dans ce dossier). Index : SQLite FTS5 (`runtime/history.db`,
module standard de Python), accents ignorés. Mise à jour INCRÉMENTALE à chaque
recherche : on retient pour chaque fichier l'octet déjà lu et on ne lit que la
suite ; un fichier raccourci ou remplacé est réindexé.

Ce qui est indexé : le texte des messages de l'utilisateur et des réponses (pas les
sorties d'outils, ni les rapports d'agents, ni la réflexion), coupé à 4 000
caractères par message. Les messages automatiques d'Astrel (réflexes, alertes,
relève, tâches de fond) sont gardés mais classés après ceux de l'utilisateur
(idée d'hermes-agent). Le résultat : des EXTRAITS courts avec la date et la
session, pour retrouver « ce qu'on avait dit sur X » sans relire de gros fichiers.

04/11 (lot de sécurité B, T45) : la base (et ses journaux -wal, -shm, -journal) est en 0600, créée ainsi et
corrigée si elle existait (les transcriptions sont en 0600 : l'index ne doit pas les rendre lisibles aux
autres comptes) ; les secrets (clés, jetons, mots de passe) sont MASQUÉS à l'indexation
(`memory_scan.mask_secrets`, le masquage du coffre) ; `secure_delete` efface vraiment ce qui est retiré.
Un index d'avant (sans version, ou autre version) est vidé UNE fois puis compacté (VACUUM) : il contenait
du texte non masqué ; il se reconstruit à la recherche suivante.

04/11 (demande de l'utilisateur : « une recherche dans tous les canaux ») : table `fil`, les messages du FIL du
HUD tels qu'il les voit (canal, id du message, heure), indexés par le cœur à leur publication (`FilIndex`),
et commande du HUD `history_query` (`commande_hud`, `query`). Voir la section « fil du HUD » plus bas.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import config
from .tools import memory_scan

log = logging.getLogger("astrel.history")

MAX_TEXT = 4000
SCHEMA_VERSION = "2"  # 04/11 (T45) : textes masqués à l'indexation ; une autre version est refaite
_AUTO_RE = re.compile(r"^\s*(\[(Réflexe programmé|Alerte automatique|Relève automatique|Nouvelle session)"
                      r"|<task-notification>|<system-reminder>|<command-|Caveat:)")
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, offset INTEGER, size INTEGER, mtime REAL);
CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
    text, role UNINDEXED, session UNINDEXED, ts UNINDEXED, auto UNINDEXED, path UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
CREATE VIRTUAL TABLE IF NOT EXISTS fil USING fts5(
    text, who UNINDEXED, channel UNINDEXED, mid UNINDEXED, reply_to UNINDEXED, ts UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""
# 04/11 : `fil` (messages du HUD) ajoutée SANS changer SCHEMA_VERSION : créée à côté dans un index existant,
# rien n'est reconstruit ; une future version la vide avec le reste (`_connect`).


def base_privee(db: Path) -> None:
    """04/11 (T45) : la base SQLite `db` et ses journaux en 0600 (lecture et écriture pour l'utilisateur seul).
    Créée ainsi si elle manque (sqlite crée ensuite ses journaux avec les mêmes droits), corrigée sinon.
    Un lien à la place de la base : refusé (OSError), plutôt que d'ouvrir ailleurs."""
    db.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(db, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(fd)
    for path in (db, *(db.with_name(db.name + s) for s in ("-wal", "-shm", "-journal"))):
        try:
            if path.exists() and not path.is_symlink():
                os.chmod(path, 0o600)
        except OSError as exc:
            log.warning("droits de %s non corrigés : %s", path.name, exc)


def ouvrir(db: Path, version: str, drop: str) -> sqlite3.Connection:
    """04/11 (T45) : connexion à une base privée (0600, `secure_delete`). Version de schéma différente de
    `version` (ou absente) : `drop` (SQL) vide l'ancien contenu, puis VACUUM le compacte (les pages libérées
    ne gardent pas l'ancien texte) ; une seule fois, la version est notée dans la table `meta`."""
    base_privee(db)
    con = sqlite3.connect(str(db), timeout=10)
    try:
        con.execute("PRAGMA secure_delete = ON")  # ce qui est retiré est écrasé, pas seulement oublié
        con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        row = con.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        if not row or row[0] != version:
            log.info("%s : index d'une autre version (%s), vidé puis compacté", db.name, row[0] if row else "aucune")
            con.executescript(drop + "\nDELETE FROM meta;")
            con.execute("INSERT INTO meta VALUES ('schema', ?)", (version,))
            con.commit()
            try:
                con.execute("VACUUM")
            except sqlite3.OperationalError as exc:  # base occupée par un autre process : secure_delete a écrasé
                log.warning("%s : VACUUM impossible (%s)", db.name, exc)
    except BaseException:
        con.close()
        raise
    return con


def _connect(db: Path) -> sqlite3.Connection:
    con = ouvrir(db, SCHEMA_VERSION, "DROP TABLE IF EXISTS messages; DROP TABLE IF EXISTS files; "
                                     "DROP TABLE IF EXISTS fil;")
    con.executescript(SCHEMA)
    return con


def preparer(db: Path | None = None) -> None:
    """04/11 (T45) : au démarrage du cœur, une base déjà là est mise en 0600 et, si elle est d'avant le
    masquage, vidée et compactée tout de suite (sans attendre une recherche). Rien si elle n'existe pas."""
    db = db or config.HISTORY_DB
    if not db.exists():
        return
    with _lock:
        _connect(db).close()


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text" and b.get("text"))
    return ""


def _entry(line: str) -> tuple[str, str, str, str] | None:
    """(rôle, texte, session, horodatage) d'une ligne de transcription, ou None."""
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if d.get("type") not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta"):
        return None
    msg = d.get("message") or {}
    text = _text_of(msg.get("content")).strip()
    if not text:
        return None
    # 04/11 (T45) : secrets masqués AVANT la coupe (un secret à cheval sur la limite ne reste pas à moitié).
    text = memory_scan.mask_secrets(text)[0]
    return d["type"], text[:MAX_TEXT], str(d.get("sessionId") or ""), str(d.get("timestamp") or "")


def update(folder: Path | None = None, db: Path | None = None) -> int:
    """Indexe ce qui est nouveau dans les transcriptions ; rend le nombre de messages ajoutés."""
    folder = folder or config.TRANSCRIPTS_DIR
    db = db or config.HISTORY_DB
    added = 0
    with _lock:
        con = _connect(db)
        try:
            known = {row[0]: row[1:] for row in con.execute("SELECT path, offset, size, mtime FROM files")}
            for path in sorted(folder.glob("*.jsonl")):
                try:
                    st = path.stat()
                except OSError:
                    continue
                key = str(path)
                offset, size, mtime = known.get(key, (0, 0, 0.0))
                if st.st_size == size and st.st_mtime == mtime:
                    continue
                if st.st_size < offset:  # fichier raccourci ou remplacé : tout reprendre
                    con.execute("DELETE FROM messages WHERE path = ?", (key,))
                    offset = 0
                rows = []
                with path.open("rb") as fh:
                    fh.seek(offset)
                    data = fh.read()
                end = data.rfind(b"\n") + 1  # jamais une ligne encore en cours d'écriture
                for raw in data[:end].splitlines():
                    entry = _entry(raw.decode("utf-8", errors="replace"))
                    if entry:
                        role, text, session, ts = entry
                        rows.append((text, role, session or path.stem, ts, int(bool(_AUTO_RE.match(text))), key))
                con.executemany("INSERT INTO messages (text, role, session, ts, auto, path) VALUES (?,?,?,?,?,?)", rows)
                con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)", (key, offset + end, st.st_size, st.st_mtime))
                added += len(rows)
            con.commit()
            days = keep_days()
            if days > 0:
                _purge(con, days)
        finally:
            con.close()
    return added


# --------------------------------------------------------------------------- oubli (17/10, plan P4)
# Page « Réglages », groupe « Confidentialité » : l'index ne garde que les N derniers
# jours (`history_keep_days`, 0 = tout), ou est vidé. Les fichiers déjà lus gardent leur
# position (`files`) : les vieux messages ne reviennent pas à la mise à jour suivante.
# Les transcriptions de Claude Code (`TRANSCRIPTS_DIR`) ne sont jamais touchées.

def keep_days() -> int:
    """Réglage lu dans le FICHIER à chaque fois : le serveur MCP (autre process) le voit aussi."""
    try:
        data = json.loads(config.ASTREL_SETTINGS_FILE.read_text(encoding="utf-8"))
        value = (data.get("values") or {}).get("history_keep_days", 0)
        return max(0, int(value)) if not isinstance(value, bool) else 0
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def _purge(con: sqlite3.Connection, days: int) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    n = con.execute("DELETE FROM messages WHERE ts != '' AND ts < ?", (since,)).rowcount
    if n:
        con.execute("INSERT INTO messages(messages) VALUES('optimize')")  # retire vraiment le texte de l'index
    # 04/11 : le fil du HUD suit le même oubli (heure en secondes)
    k = con.execute("DELETE FROM fil WHERE ts < ?", (time.time() - days * 86400,)).rowcount
    if k:
        con.execute("INSERT INTO fil(fil) VALUES('optimize')")
    if n or k:
        con.commit()
    return n + k


def purge(days: int, db: Path | None = None) -> int:
    """Retire de l'index les messages de plus de `days` jours ; rend leur nombre."""
    if days <= 0:
        return 0
    with _lock:
        con = _connect(db or config.HISTORY_DB)
        try:
            return _purge(con, days)
        finally:
            con.close()


def clear(db: Path | None = None) -> int:
    """Vide l'index (« Effacer l'historique ») et compacte le fichier ; rend le nombre de messages retirés."""
    db = db or config.HISTORY_DB
    if not db.exists():
        return 0
    with _lock:
        con = _connect(db)
        try:
            n = con.execute("SELECT count(*) FROM messages").fetchone()[0]
            n += con.execute("SELECT count(*) FROM fil").fetchone()[0]  # 04/11 : le fil du HUD aussi
            con.execute("DELETE FROM messages")
            con.execute("DELETE FROM fil")
            con.execute("INSERT INTO messages(messages) VALUES('optimize')")
            con.execute("INSERT INTO fil(fil) VALUES('optimize')")
            # Les transcriptions d'avant l'effacement ne reviennent pas en « archives » (position gardée) ;
            # celles d'après sont déjà dans le fil : les archives s'arrêtent ici.
            con.execute("INSERT OR REPLACE INTO meta VALUES ('fil_debut', ?)", (_iso(time.time()),))
            con.commit()
            con.execute("VACUUM")  # le texte effacé ne traîne plus dans les pages libres du fichier
        finally:
            con.close()
    return int(n)


def _fts_query(query: str) -> str:
    """Mots de la question → requête FTS5 sûre : chaque mot entre guillemets, préfixe
    pour les mots de 4 lettres ou plus, tous requis (ET)."""
    words = re.findall(r"[\w'’-]+", query, flags=re.UNICODE)
    terms = []
    for w in words:
        w = w.strip("'’-").replace('"', "")
        if len(w) < 2:
            continue
        terms.append(f'"{w}"*' if len(w) >= 4 else f'"{w}"')
    return " ".join(terms)


def search(query: str, limit: int = 8, days: float = 0, role: str = "",
           folder: Path | None = None, db: Path | None = None) -> list[dict]:
    """Extraits les plus pertinents. `days` : seulement les N derniers jours ;
    `role` : « user » ou « assistant »."""
    try:
        update(folder, db)
    except (OSError, sqlite3.Error):
        pass  # un index en retard vaut mieux que pas de réponse
    fts = _fts_query(query)
    if not fts:
        raise ValueError("question vide : donne un ou plusieurs mots à chercher")
    limit = max(1, min(int(limit or 8), 25))
    sql = ("SELECT snippet(messages, 0, '«', '»', ' … ', 24), role, session, ts, auto, bm25(messages) "
           "FROM messages WHERE messages MATCH ?")
    args: list[Any] = [fts]
    if days and days > 0:
        since = (datetime.now(timezone.utc) - timedelta(days=float(days))).strftime("%Y-%m-%dT%H:%M:%S")
        sql += " AND ts >= ?"
        args.append(since)
    if role in ("user", "assistant"):
        sql += " AND role = ?"
        args.append(role)
    # Les messages de l'utilisateur d'abord à pertinence égale, les automatiques après.
    sql += " ORDER BY auto, bm25(messages) LIMIT ?"
    args.append(limit)
    with _lock:
        con = _connect(db or config.HISTORY_DB)
        try:
            rows = con.execute(sql, args).fetchall()
        except sqlite3.OperationalError as exc:
            raise ValueError(f"recherche impossible : {exc}") from exc
        finally:
            con.close()
    out = []
    for snip, rl, session, ts, auto, _score in rows:
        out.append({
            "date": _local(ts),
            "qui": "l'utilisateur" if rl == "user" and not auto else ("automatique" if auto else "Astrel/Claude"),
            "session": session[:8],
            "extrait": " ".join(snip.split()),
        })
    return out


def _local(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return ts[:16]


# --------------------------------------------------------------------------- fil du HUD (04/11)
# Demande de l'utilisateur : « une recherche dans tous les canaux ». Source CHOISIE : les messages du FIL du HUD
# (« mes conversations dans Astrel »), tels qu'il les voit : ses messages (`transcript`, dits, tapés ou venus
# de WhatsApp) et les réponses et comptes rendus d'Astrel (`reply`, `report`), avec leur CANAL, leur id
# (`mid`, pour y retourner) et leur heure. Les transcriptions du CLI (table `messages`) n'ont ni canal ni id,
# portent les notes du cœur pour Claude ([Mode silencieux…], [Canal x : dossier…]) et les tours automatiques :
# elles servent d'ARCHIVES pour ce qui précède le premier message du fil (`fil_debut`, pas de doublon).
# Jamais indexé : un tour du moteur local (« rien n'est gardé », coeur/local_tour.py), un canal Local, une
# dictée (jamais envoyée), un message automatique (réflexe, mission, plan : une consigne, pas l'utilisateur).
# Secrets masqués comme le reste (`memory_scan.mask_secrets`), oubli après N jours et « Effacer l'historique »
# compris (`_purge`, `clear`). Commande `history_query` : HUD de l'utilisateur SEUL (Claude a `history_search`).

FIL_TYPES = ("transcript", "reply", "report", "channel_move")
FIL_SOURCES = ("voix", "clavier", "whatsapp")
FIL_MAX = 30          # résultats du fil par recherche
ARCHIVES_MAX = 10     # résultats des archives (transcriptions du CLI d'avant le fil)
EXTRAIT_MAX = 320     # caractères d'un extrait
REQUETE_MAX = 200     # caractères d'une requête
TERMES_MAX = 8        # mots d'une requête
MARQUE_DEBUT, MARQUE_FIN = "\x02", "\x03"  # mots trouvés, entourés par FTS5 (le HUD en fait des <mark>)
_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Notes du cœur pour Claude dans un message des transcriptions (core.py `handle`, langue.NOTES_EN)
_NOTES = re.compile(r"\[(?:Mode silencieux|Silent mode|the user te parle|the user is talking|Canal [^\]\n]{1,80}"
                    r" : dossier du projet|WhatsApp :|Capture de l'écran|Screenshot of l'utilisateur|l'utilisateur switched)"
                    r"[^\]\n]{0,400}\]\s*")
_MARKDOWN = re.compile(r"\*\*|~~|`+|^#{1,6}\s+", re.M)  # « __ » gardé : __init__.py


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _epoch(ts: str) -> float:
    """Secondes d'une heure ISO ; sans fuseau (`_iso`, `fil_debut`), c'est de l'UTC."""
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def fil_ligne(ev: dict, exclus: frozenset[str] = frozenset()) -> tuple | None:
    """(texte, qui, canal, mid, reply_to, ts) d'un évènement du bus à indexer, ou None.
    `exclus` : canaux dont rien n'est gardé (canaux Local)."""
    kind = ev.get("type")
    if ev.get("engine") == "local" or kind not in ("transcript", "reply", "report"):
        return None
    if kind == "transcript" and (ev.get("who") != "user" or ev.get("source") not in FIL_SOURCES):
        return None
    channel = str(ev.get("channel") or "")
    mid = str(ev.get("mid") or "")
    text = str(ev.get("text") or "").strip()
    if not text or not mid or channel in exclus:
        return None
    text = _CONTROLE.sub(" ", memory_scan.mask_secrets(text)[0])[:MAX_TEXT]
    try:
        ts = float(ev.get("ts") or time.time())
    except (TypeError, ValueError):
        ts = time.time()
    return (text, "user" if kind == "transcript" else "astrel", channel, mid, str(ev.get("reply_to") or ""), ts)


def fil_ecrire(events: list[dict], db: Path | None = None, exclus: frozenset[str] = frozenset()) -> int:
    """Indexe des évènements du bus (messages, déplacements de canal) ; rend le nombre de messages ajoutés.
    Un message déjà indexé (même `mid` : message proposé à nouveau au démarrage) n'est pas doublé."""
    rows, moves = [], []
    for ev in events:
        if ev.get("type") == "channel_move":
            if ev.get("mid") and ev.get("channel"):
                moves.append((str(ev["channel"]), str(ev["mid"]), str(ev["mid"])))
            continue
        row = fil_ligne(ev, exclus)
        if row:
            rows.append(row)
    if not rows and not moves:
        return 0
    added = 0
    with _lock:
        con = _connect(db or config.HISTORY_DB)
        try:
            for row in rows:
                if con.execute("SELECT 1 FROM fil WHERE mid = ? LIMIT 1", (row[3],)).fetchone():
                    continue
                con.execute("INSERT INTO fil (text, who, channel, mid, reply_to, ts) VALUES (?,?,?,?,?,?)", row)
                added += 1
            if added and not con.execute("SELECT 1 FROM meta WHERE key = 'fil_debut'").fetchone():
                first = min(r[5] for r in rows)
                con.execute("INSERT INTO meta VALUES ('fil_debut', ?)", (_iso(first),))
            # Message déplacé à la main dans un autre canal (`channels.move`) : il emmène sa réponse.
            for move in moves:
                con.execute("UPDATE fil SET channel = ? WHERE mid = ? OR reply_to = ?", move)
            con.commit()
        finally:
            con.close()
    return added


class FilIndex:
    """Abonné du bus (cœur, démo du HUD) : met en FILE les messages du fil, un fil à part les écrit dans
    l'index (un abonné ne doit jamais retenir l'éditeur, events.py). File bornée : pleine, un message est
    perdu pour la recherche (journal), jamais le cœur ralenti."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=2000)
        self._thread: threading.Thread | None = None
        self._exclus: Callable[[], frozenset[str]] = frozenset
        self._plein = False

    def brancher(self, bus, exclus: Callable[[], frozenset[str]] | None = None) -> Callable[[], None]:
        if exclus is not None:
            self._exclus = exclus
        if self._thread is None:
            self._thread = threading.Thread(target=self._boucle, name="history-fil", daemon=True)
            self._thread.start()
        return bus.subscribe(self.noter)

    def noter(self, ev: dict) -> None:
        if ev.get("type") not in FIL_TYPES or ev.get("replay"):
            return
        try:
            self._q.put_nowait(ev)
        except queue.Full:
            if not self._plein:
                log.warning("recherche du HUD : file pleine, des messages ne seront pas retrouvés")
            self._plein = True

    def _boucle(self) -> None:
        while True:
            batch = [self._q.get()]
            while len(batch) < 100:
                try:
                    batch.append(self._q.get_nowait())
                except queue.Empty:
                    break
            try:
                exclus = frozenset(self._exclus())
            except Exception:
                exclus = frozenset()
            try:
                fil_ecrire(batch, exclus=exclus)
                self._plein = False
            except (OSError, sqlite3.Error) as exc:
                log.warning("recherche du HUD : %d message(s) non indexé(s) (%s)", len(batch), exc)


fil = FilIndex()


def nettoyer_requete(raw: Any) -> str:
    """Texte d'une recherche du HUD : chaîne, sans caractère de contrôle, espaces resserrés, bornée."""
    if not isinstance(raw, str):
        return ""
    return " ".join(_CONTROLE.sub(" ", raw).split())[:REQUETE_MAX]


def _requete_fts(texte: str) -> str:
    """Requête FTS5 SÛRE (chaque mot entre guillemets, voir `_fts_query`), TERMES_MAX mots au plus."""
    return " ".join(_fts_query(texte).split(" ")[:TERMES_MAX])


def _extrait(snip: str, notes: bool = False) -> str:
    """Extrait affichable : notes du cœur retirées (archives), espaces resserrés, EXTRAIT_MAX caractères ;
    une marque ouverte et coupée est refermée (le HUD s'en accommode de toute façon)."""
    s = _NOTES.sub("", snip) if notes else snip
    s = _MARKDOWN.sub("", s)  # **gras**, `code`, # titre : l'extrait se lit en texte simple
    s = " ".join(s.split())
    if len(s) > EXTRAIT_MAX:
        s = s[:EXTRAIT_MAX].rstrip() + " …"
    if s.count(MARQUE_DEBUT) > s.count(MARQUE_FIN):
        s += MARQUE_FIN
    return s


def query(texte: str, db: Path | None = None, folder: Path | None = None,
          limit: int = FIL_MAX, archives: int = ARCHIVES_MAX) -> dict:
    """Recherche du HUD : messages du fil (canal, id, heure), puis archives d'avant le fil. ValueError si la
    question est vide ou si l'index refuse la requête."""
    fts = _requete_fts(nettoyer_requete(texte))
    if not fts:
        raise ValueError("question vide : donne un ou plusieurs mots à chercher")
    try:
        update(folder, db)  # archives à jour (incrémental) ; un index en retard vaut mieux que pas de réponse
    except (OSError, sqlite3.Error):
        pass
    limit, archives = max(1, min(int(limit), FIL_MAX)), max(0, min(int(archives), ARCHIVES_MAX))
    marks = (MARQUE_DEBUT, MARQUE_FIN, " … ")
    with _lock:
        con = _connect(db or config.HISTORY_DB)
        try:
            rows = con.execute("SELECT snippet(fil, 0, ?, ?, ?, 24), who, channel, mid, reply_to, ts FROM fil "
                               "WHERE fil MATCH ? ORDER BY bm25(fil) LIMIT ?", (*marks, fts, limit + 1)).fetchall()
            row = con.execute("SELECT value FROM meta WHERE key = 'fil_debut'").fetchone()
            debut = row[0] if row else ""
            sql = ("SELECT snippet(messages, 0, ?, ?, ?, 24), role, ts FROM messages "
                   "WHERE messages MATCH ? AND auto = 0" + (" AND ts < ?" if debut else "")
                   + " ORDER BY bm25(messages) LIMIT ?")
            old = con.execute(sql, (*marks, fts, *((debut,) if debut else ()), archives)).fetchall() if archives else []
        except sqlite3.OperationalError as exc:
            raise ValueError(f"recherche impossible : {exc}") from exc
        finally:
            con.close()
    found = [{"who": who, "channel": ch, "mid": mid, "reply_to": rt, "ts": float(ts or 0), "extrait": _extrait(s)}
             for s, who, ch, mid, rt, ts in rows[:limit]]
    old_found = [{"who": "user" if role == "user" else "astrel", "ts": _epoch(ts), "extrait": _extrait(s, notes=True)}
                 for s, role, ts in old]
    return {"results": found, "archives": [x for x in old_found if x["extrait"]], "more": len(rows) > limit,
            "fil_debut": _epoch(debut) if debut else 0}


def commande_hud(cmd: dict, db: Path | None = None, folder: Path | None = None) -> dict:
    """Commande `history_query {query}` du HUD (cœur `_cmd_history_query`, démo du HUD : même contrôle).
    RÉSERVÉE au HUD de l'utilisateur : refusée venant de Claude (il a l'outil MCP `history_search`) et hors du
    HUD (canal de contrôle, `--send`) : la réponse porte du texte de ses conversations."""
    name = "history_query"
    if cmd.get("_from_claude") is True or cmd.get("_via") != "hud":
        log.warning("history_query refusé : hors du HUD de l'utilisateur (%s)", cmd.get("_via") or "?")
        return {"ok": False, "error": f"{name} est réservé au HUD de l'utilisateur"}  # même texte qu'open_link
    texte = nettoyer_requete(cmd.get("query"))
    try:
        out = query(texte, db=db, folder=folder)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except (OSError, sqlite3.Error) as exc:
        log.warning("recherche du HUD impossible : %s", exc)
        return {"ok": False, "error": f"recherche impossible : {exc}"}
    return {"ok": True, "query": texte, **out}
