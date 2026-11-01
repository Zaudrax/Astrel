"""La mémoire d'Astrel : un coffre de notes Markdown (`vault/`), compatible
Obsidian. Claude y lit et y écrit ; le graphe du HUD le montre, liens `[[…]]`
compris.

Pourquoi des fichiers et pas une base : l'utilisateur peut les lire, les corriger et
les ranger lui-même, dans n'importe quel éditeur. Une mémoire qu'on ne peut pas
relire est une mémoire à laquelle on ne peut pas faire confiance.

Toute écriture reste DANS le coffre : chemins absolus, `..` et fichiers autres
que `.md` sont refusés. Avant d'écrire : secrets masqués, consignes injectées
refusées (`memory_scan`) ; une note écrasée est d'abord copiée
(`vault_backup`) ; un `replace` sur une note qui a changé depuis sa lecture
est refusé (Obsidian, autre session : on ne perd pas leur modification).
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path

from .. import config
from . import memory_scan, vault_backup

VAULT = config.ROOT / "vault"

# Empreinte (mtime, taille) de chaque note à sa dernière lecture ou écriture par
# CE processus (le serveur MCP, partagé par le cerveau et ses sous-agents).
_seen: dict[str, tuple[int, int]] = {}


def _sig(path: Path) -> tuple[int, int]:
    st = path.stat()
    return st.st_mtime_ns, st.st_size


def _safe(rel: str) -> Path:
    rel = rel.strip().lstrip("/")
    if not rel:
        raise ValueError("chemin vide")
    if not rel.endswith(".md"):
        rel += ".md"
    path = (VAULT / rel).resolve()
    if VAULT.resolve() not in path.parents:
        raise ValueError("chemin hors du coffre de mémoire")
    return path


def _fold(text: str) -> str:
    """minuscules sans accents, pour chercher « memoire » et trouver « mémoire »."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def list_notes(folder: str = "") -> list[dict]:
    base = VAULT / folder.strip("/") if folder else VAULT
    if not base.is_dir():
        return []
    out = []
    for p in sorted(base.rglob("*.md")):
        rel = str(p.relative_to(VAULT))
        if rel.startswith("."):
            continue
        out.append({"path": rel, "title": _title(p), "modifie": time.strftime("%Y-%m-%d", time.localtime(p.stat().st_mtime))})
    return out


def _title(p: Path) -> str:
    try:
        m = re.search(r"^#\s+(.+)$", p.read_text(encoding="utf-8"), re.M)
    except OSError:
        m = None
    return m.group(1).strip() if m else p.stem


# --- recherche : index FTS5 (bm25) du coffre ---
#
# Index SQLite FTS5 dans `runtime/memoire.db` (module standard de Python, comme
# `history.py`), accents ignorés. Rafraîchi À CHAQUE RECHERCHE par l'empreinte
# (mtime, taille) des notes : Obsidian, l'utilisateur ou un autre processus écrivent
# hors de `write()`, on ne compte donc pas sur l'écriture pour le tenir à jour.
# Le coffre (≈ 25 notes) se vérifie en moins d'une milliseconde par note.
# Si FTS5 manque ou que l'index est inutilisable : repli sur le score naïf.

INDEX_DB = config.RUNTIME / "memoire.db"
_index_lock = threading.Lock()

_SCHEMA_VERSION = "2"
_INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (path TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    path UNINDEXED, title, name, quand, body,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""
# Poids bm25 par colonne (path, title, name, quand, body) : le titre et le nom du
# fichier comptent plus qu'un mot perdu dans le texte, comme l'ancien score (×5).
# `quand` (ligne « Quand l'utiliser » des procédures) est déjà dans le texte.
_BM25 = "bm25(notes_fts, 0.0, 5.0, 3.0, 0.0, 1.0)"
_BM25_PROC = "bm25(notes_fts, 0.0, 5.0, 0.0, 3.0, 0.0)"  # procédures : titre et « Quand l'utiliser »
_QUAND_RE = re.compile(r"^\W*Quand l['’]utiliser\W*:\s*(.+)$", re.M | re.I)

# Mots trop courants pour départager deux notes (la phrase dite est longue,
# l'ancien score les comptait tous). Comparés sans accents.
_STOP = frozenset("""
les des une est pas que qui quoi pour par sur dans avec mais plus tout tous toute toutes
cette ces son ses leur leurs mon mes ton tes notre nos votre vos elle elles ils nous vous
lui eux moi toi aux ont etait sont fait faire fais faut peux peut veux veut vais vas
dit dire comme quand alors donc bien tres aussi encore deja juste rien chose choses
sais sait savoir sache pense penses crois trouve trouves regarde regardes vois voir
comment pourquoi combien quel quelle quels quelles ici voila oui non cela ceci celui celle
ceux avoir etre sera serait suis etes
the and for you with this that what
""".split())


def _terms(query: str) -> list[str]:
    """Mots utiles de la question, sans accents, sans doublons, en « racine » grossière
    (pluriel, et deux dernières lettres dès 8 lettres : « relancer » trouve « relance »)."""
    out: list[str] = []
    for w in re.findall(r"\w+", _fold(query)):
        if len(w) < 3 or w in _STOP or w.isdigit() and len(w) < 4:
            continue
        if len(w) > 4 and w[-1] in "sx":
            w = w[:-1]
        if len(w) >= 8:
            w = w[:-2]
        if w not in out:
            out.append(w)
    return out


def _fts_query(terms: list[str], joiner: str = " ") -> str:
    """Requête FTS5 sûre : chaque terme entre guillemets (aucun opérateur, aucune
    `colonne:` ne passe), préfixe dès 4 lettres. `joiner` : « » (ET) ou « OR »."""
    return joiner.join(f'"{t}"*' if len(t) >= 4 else f'"{t}"' for t in terms)


def _connect_index(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db), timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = con.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
    if not row or row[0] != _SCHEMA_VERSION:  # index d'une autre version : refait de zéro
        con.executescript("DROP TABLE IF EXISTS notes; DROP TABLE IF EXISTS notes_fts; DELETE FROM meta;")
        con.execute("INSERT INTO meta VALUES ('schema', ?)", (_SCHEMA_VERSION,))
    con.executescript(_INDEX_SCHEMA)
    return con


def _note_files() -> dict[str, Path]:
    out = {}
    for p in VAULT.rglob("*.md"):
        rel = str(p.relative_to(VAULT))
        if not rel.startswith("."):
            out[rel] = p
    return out


def _refresh(con: sqlite3.Connection) -> None:
    """Remet l'index d'accord avec le coffre : notes ajoutées, changées, effacées.
    Un autre coffre que celui indexé (tests, déplacement) : tout est refait."""
    root = str(VAULT.resolve())
    row = con.execute("SELECT value FROM meta WHERE key = 'vault'").fetchone()
    if not row or row[0] != root:
        con.execute("DELETE FROM notes")
        con.execute("DELETE FROM notes_fts")
        con.execute("INSERT OR REPLACE INTO meta VALUES ('vault', ?)", (root,))
    known = {r[0]: (r[1], r[2]) for r in con.execute("SELECT path, mtime_ns, size FROM notes")}
    files = _note_files()
    for rel in known.keys() - files.keys():
        con.execute("DELETE FROM notes WHERE path = ?", (rel,))
        con.execute("DELETE FROM notes_fts WHERE path = ?", (rel,))
    for rel, p in files.items():
        try:
            sig = _sig(p)
            if known.get(rel) == sig:
                continue
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        con.execute("DELETE FROM notes_fts WHERE path = ?", (rel,))
        quand = _QUAND_RE.search(text)
        con.execute("INSERT INTO notes_fts (path, title, name, quand, body) VALUES (?,?,?,?,?)",
                    (rel, _title(p), re.sub(r"[-_]+", " ", p.stem), quand.group(1).strip() if quand else "", text))
        con.execute("INSERT OR REPLACE INTO notes VALUES (?,?,?)", (rel, *sig))
    con.commit()


def _search_fts(terms: list[str], limit: int) -> list[dict]:
    sql = (f"SELECT path, title, snippet(notes_fts, 4, '', '', ' … ', 24), {_BM25} "
           f"FROM notes_fts WHERE notes_fts MATCH ? ORDER BY {_BM25} LIMIT ?")
    with _index_lock:
        con = _connect_index(INDEX_DB)
        try:
            _refresh(con)
            rows = con.execute(sql, (_fts_query(terms), limit)).fetchall()
            if not rows and len(terms) > 1:  # aucun résultat avec TOUS les mots : relâcher en OU
                rows = con.execute(sql, (_fts_query(terms, " OR "), limit)).fetchall()
        finally:
            con.close()
    return [{"path": path, "title": title, "score": round(-score, 2), "extrait": " ".join(snip.split())}
            for path, title, snip, score in rows]


def search(query: str, limit: int = 8) -> list[dict]:
    """Notes les plus proches de la question, la meilleure d'abord :
    `{path, title, score, extrait}`. Score bm25 (plus haut = plus proche)."""
    terms = _terms(query)
    if not terms or not VAULT.is_dir():
        return []
    limit = max(1, int(limit or 8))
    try:
        return _search_fts(terms, limit)
    except (sqlite3.Error, OSError):
        return _search_naive(query, limit)  # FTS5 absent, runtime/ illisible : l'ancien score


def title_matches(query: str, limit: int = 5, procedures: bool = False) -> list[dict]:
    """Notes dont le TITRE ou le nom de fichier contient des mots de la phrase
    (`procedures=True` : notes de `procedures/`, jugées sur leur titre et leur ligne
    « Quand l'utiliser »), la meilleure d'abord : `{path, title, score, quand}`.
    Sert au rappel automatique (`astrel/rappel.py`) : un mot du texte seul y fait
    trop de bruit. Lève sqlite3.Error si FTS5 manque (l'appelant se tait alors)."""
    terms = _terms(query)
    if procedures:  # « procédure » est dans le titre de toutes : il ne départage rien
        terms = [t for t in terms if not (t.startswith("proc") and "procedure".startswith(t[:9]))]
    if not terms or not VAULT.is_dir():
        return []
    cols, where, rank = (("{title quand}", "LIKE", _BM25_PROC) if procedures
                         else ("{title name}", "NOT LIKE", _BM25))
    sql = (f"SELECT path, title, quand, {rank} FROM notes_fts WHERE notes_fts MATCH ? "
           f"AND path {where} 'procedures/%' ORDER BY {rank} LIMIT ?")
    with _index_lock:
        con = _connect_index(INDEX_DB)
        try:
            _refresh(con)
            rows = con.execute(sql, (f"{cols} : ({_fts_query(terms, ' OR ')})", max(1, int(limit)))).fetchall()
        finally:
            con.close()
    return [{"path": path, "title": title, "score": round(-score, 2), "quand": quand}
            for path, title, quand, score in rows]


def _search_naive(query: str, limit: int = 8) -> list[dict]:
    words = [w for w in re.findall(r"\w+", _fold(query)) if len(w) > 2]
    if not words or not VAULT.is_dir():
        return []
    results = []
    for p in VAULT.rglob("*.md"):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        folded = _fold(text)
        title = _fold(_title(p))
        score = sum(folded.count(w) + 5 * title.count(w) for w in words)
        if score == 0:
            continue
        first = min((folded.find(w) for w in words if w in folded), default=0)
        start = max(0, first - 80)
        snippet = " ".join(text[start:start + 260].split())
        results.append({"path": str(p.relative_to(VAULT)), "title": _title(p), "score": score, "extrait": snippet})
    return sorted(results, key=lambda r: -r["score"])[:limit]


def read(rel: str) -> str:
    path = _safe(rel)
    if not path.exists():
        raise FileNotFoundError(f"pas de note {rel}")
    text = path.read_text(encoding="utf-8")
    _seen[str(path)] = _sig(path)
    return memory_scan.warning_for_read(text) + text


def write(rel: str, content: str, mode: str = "append") -> str:
    """mode : « create » (échoue si elle existe), « replace », « append ».
    Rend le chemin de la note, suivi des remarques du filtre s'il y en a."""
    if mode not in ("create", "replace", "append"):
        raise ValueError(f"mode inconnu : {mode!r} (append, replace ou create)")
    path = _safe(rel)
    content, remarks = memory_scan.clean_for_write(content)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    if mode == "create" and path.exists():
        raise FileExistsError(f"la note {rel} existe déjà")
    key = str(path)
    up_to_date = not path.exists() or key not in _seen or _sig(path) == _seen[key]
    if mode == "replace" and not up_to_date:
        when = time.strftime("%H:%M", time.localtime(path.stat().st_mtime))
        raise ValueError(f"la note {rel} a changé depuis ta dernière lecture (modifiée à {when}) : "
                         "relis-la (memory_read) avant de la remplacer, ou ajoute avec mode=append")
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode == "append" and path.exists():
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n\n<!-- {stamp} -->\n{content.strip()}\n")
    else:
        if path.exists():
            vault_backup.save_version(VAULT, path)
        body = content.strip()
        if not body.startswith("#"):
            body = f"# {path.stem.replace('-', ' ').capitalize()}\n\n{body}"
        path.write_text(body + "\n", encoding="utf-8")
    if up_to_date:
        _seen[key] = _sig(path)
    out = str(path.relative_to(VAULT))
    return out + (f" ({' ; '.join(remarks)})" if remarks else "")


def versions(rel: str, restore: str = "") -> list[dict] | str:
    """Versions gardées d'une note (avant chaque remplacement), ou rétablit
    celle dont l'identifiant est `restore` (la version actuelle est gardée)."""
    path = _safe(rel)
    if restore:
        msg = vault_backup.restore_version(VAULT, path, restore)
        _seen[str(path)] = _sig(path)
        return msg
    return vault_backup.list_versions(VAULT, path)


def ensure_vault() -> None:
    """Crée le coffre et sa note d'accueil au premier démarrage."""
    if (VAULT / "Accueil.md").exists():
        return
    VAULT.mkdir(parents=True, exist_ok=True)
    (VAULT / "Accueil.md").write_text(
        "# Accueil\n\n"
        "Le coffre de mémoire d'Astrel. Chaque note est un fichier Markdown ; "
        "les liens [[comme ceci]] relient les notes, et le HUD les dessine.\n\n"
        "- [[Moi]] — qui je sers\n"
        "- [[Projets]] — ce sur quoi on travaille\n"
        "- [[Préférences]] — comment l'utilisateur aime que je fasse les choses\n",
        encoding="utf-8",
    )
    (VAULT / "Moi.md").write_text(
        "# Moi\n\n"
        "- Parle français ; préfère qu'on avance par étapes claires.\n"
        "- Expliquer avant d'agir sur le système.\n\n"
        "Voir [[Projets]] et [[Préférences]].\n",
        encoding="utf-8",
    )
    (VAULT / "Projets.md").write_text(
        "# Projets\n\n"
        "Dossier de travail : ~/Projets.\n\n"
        "- [[Boutique]] — exemple de projet : une boutique en ligne.\n"
        "- [[Astrel]] — cet assistant.\n",
        encoding="utf-8",
    )
    (VAULT / "Préférences.md").write_text(
        "# Préférences\n\n"
        "- Confirmer à voix haute avant toute action destructrice, tout commit git ou toute écriture hors des projets.\n"
        "- Réponses orales courtes : une à trois phrases.\n",
        encoding="utf-8",
    )
    (VAULT / "Boutique.md").write_text(
        "# Boutique\n\nExemple de projet : une boutique en ligne. "
        "Dépôt : ~/Projets/Boutique. Voir [[Projets]].\n",
        encoding="utf-8",
    )
    (VAULT / "Astrel.md").write_text(
        "# Astrel\n\nAssistant vocal local branché sur Claude Code. Dépôt : "
        "~/Astrel. Voir [[Projets]].\n",
        encoding="utf-8",
    )
