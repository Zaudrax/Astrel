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
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import config

MAX_TEXT = 4000
_AUTO_RE = re.compile(r"^\s*(\[(Réflexe programmé|Alerte automatique|Relève automatique|Nouvelle session)"
                      r"|<task-notification>|<system-reminder>|<command-|Caveat:)")
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, offset INTEGER, size INTEGER, mtime REAL);
CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
    text, role UNINDEXED, session UNINDEXED, ts UNINDEXED, auto UNINDEXED, path UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


def _connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db), timeout=10)
    con.executescript(SCHEMA)
    return con


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
        con.commit()
    return n


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
            con.execute("DELETE FROM messages")
            con.execute("INSERT INTO messages(messages) VALUES('optimize')")
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
