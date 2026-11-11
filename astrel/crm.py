"""CRM freelance LOCAL d'Astrel : les PROSPECTS (leads) de l'utilisateur (09/11, carte T90, 1er
incrément validé par l'utilisateur).

Astrel (Claude) gère les prospects à la voix ou au chat : ajouter, lister, mettre à jour.
Stockage SQLite dans `runtime/crm.db` (module standard de Python, comme `history.py` et
`tools/memory.py`) : DONNÉE, pas du code. Ce sont les propres données clients de l'utilisateur —
pas des secrets, mais les ÉCRITURES sont classées « sensible » au garde-fou
(`guard.MCP_POLICY["astrel"]`), les lectures libres.

Pas d'envoi ni de relance ici : aucun I/O réseau, on ne contacte JAMAIS un prospect depuis ce
module (c'est le chantier T91, via la file d'envoi / outbox). Ici : seulement le stockage et la
gestion.

Modèle inspiré du projet perso MaestroAI de l'utilisateur (modèles `Fl*`, `FlLead`) : les colonnes et
le SCORE de qualification en reprennent l'idée (code réécrit pour Astrel).

Accès : connexion SQLite PAR APPEL, sous un verrou de module (`_lock`), comme `history.py` ; le
serveur MCP (un process) et le cœur peuvent l'appeler sans se marcher dessus. La base est créée en
0600 (données clients lisibles par l'utilisateur seul).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

DB = config.RUNTIME / "crm.db"
_lock = threading.Lock()

# Statuts d'un prospect, dans l'ordre du tunnel (prospection → signature).
STATUTS = ("nouveau", "contacté", "qualifié", "perdu", "gagné")
STATUT_DEFAUT = "nouveau"

# Colonnes modifiables par `maj` (jamais id/score/created : score est calculé, les deux autres fixés).
CHAMPS = ("nom", "telephone", "email", "site_web", "source", "ville", "secteur", "note", "avis",
          "statut", "notes")

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    nom        TEXT NOT NULL,
    telephone  TEXT NOT NULL DEFAULT '',
    email      TEXT NOT NULL DEFAULT '',
    site_web   TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT '',
    ville      TEXT NOT NULL DEFAULT '',
    secteur    TEXT NOT NULL DEFAULT '',
    note       REAL,
    avis       INTEGER,
    statut     TEXT NOT NULL DEFAULT 'nouveau',
    score      INTEGER NOT NULL DEFAULT 0,
    notes      TEXT NOT NULL DEFAULT '',
    created    TEXT NOT NULL,
    updated    TEXT NOT NULL
);
"""

_COLONNES = ("id", "nom", "telephone", "email", "site_web", "source", "ville", "secteur", "note",
             "avis", "statut", "score", "notes", "created", "updated")


# --------------------------------------------------------------------------- score de qualification
# Inspiré de MaestroAI (`FlLead.qualificationScore`) : un prospect SANS site web est le plus
# intéressant pour un freelance qui vend des sites (d'où le gros bonus) ; on peut le joindre
# (téléphone, e-mail) ; une excellente note publique est un signe de sérieux. Base 50, borné 0..100.
SCORE_BASE = 50
SCORE_SANS_SITE = 30        # pas de site web : la cible idéale → +30
SCORE_AVEC_SITE = -10       # déjà un site : moins prioritaire → -10
SCORE_TELEPHONE = 10        # joignable par téléphone → +10
SCORE_EMAIL = 5             # joignable par e-mail → +5
SCORE_BONNE_NOTE = 10       # note publique excellente (>= 4,5) → +10
NOTE_EXCELLENTE = 4.5


def calculer_score(lead: dict) -> tuple[int, str]:
    """Score de qualification d'un prospect (0 à 100) et sa raison, à partir de ses champs.
    Fonction PURE (aucune écriture) : appelée à l'ajout et à chaque mise à jour."""
    score = SCORE_BASE
    raisons: list[str] = []
    if str(lead.get("site_web") or "").strip():
        score += SCORE_AVEC_SITE
        raisons.append("site web présent")
    else:
        score += SCORE_SANS_SITE
        raisons.append("pas de site web")
    if str(lead.get("telephone") or "").strip():
        score += SCORE_TELEPHONE
        raisons.append("téléphone disponible")
    if str(lead.get("email") or "").strip():
        score += SCORE_EMAIL
        raisons.append("e-mail disponible")
    note = lead.get("note")
    try:
        if note is not None and float(note) >= NOTE_EXCELLENTE:
            score += SCORE_BONNE_NOTE
            raisons.append(f"excellente note ({note})")
    except (TypeError, ValueError):
        pass
    score = max(0, min(100, score))
    return score, " — ".join(raisons)


# --------------------------------------------------------------------------- accès à la base

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _connect() -> sqlite3.Connection:
    """Connexion à la base (créée en 0600 au besoin, le dossier runtime existe déjà)."""
    DB.parent.mkdir(parents=True, exist_ok=True)
    neuve = not DB.exists()
    if neuve:  # créée en 0600 avant que sqlite ne la touche (données clients, l'utilisateur seul)
        os.close(os.open(DB, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600))
    con = sqlite3.connect(str(DB), timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in _COLONNES}


def _norme_note(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError(f"note invalide : {v!r} (un nombre comme 4.5)")


def _norme_avis(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        raise ValueError(f"nombre d'avis invalide : {v!r} (un entier)")


def _norme_statut(v: Any) -> str:
    s = str(v or "").strip() or STATUT_DEFAUT
    if s not in STATUTS:
        raise ValueError(f"statut inconnu : {s!r} (un de {', '.join(STATUTS)})")
    return s


def ajouter(nom: str, telephone: str = "", email: str = "", site_web: str = "", source: str = "",
            ville: str = "", secteur: str = "", note: Any = None, avis: Any = None,
            statut: str = STATUT_DEFAUT, notes: str = "") -> dict:
    """Ajoute un prospect et rend sa fiche (dict), score calculé. `nom` est obligatoire."""
    nom = str(nom or "").strip()
    if not nom:
        raise ValueError("le nom du prospect est obligatoire")
    champs = {
        "nom": nom,
        "telephone": str(telephone or "").strip(),
        "email": str(email or "").strip(),
        "site_web": str(site_web or "").strip(),
        "source": str(source or "").strip(),
        "ville": str(ville or "").strip(),
        "secteur": str(secteur or "").strip(),
        "note": _norme_note(note),
        "avis": _norme_avis(avis),
        "statut": _norme_statut(statut),
        "notes": str(notes or "").strip(),
    }
    champs["score"] = calculer_score(champs)[0]
    now = _now()
    cols = [*champs.keys(), "created", "updated"]
    vals = [*champs.values(), now, now]
    with _lock:
        con = _connect()
        try:
            cur = con.execute(f"INSERT INTO leads ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
            con.commit()
            row = con.execute("SELECT * FROM leads WHERE id = ?", (cur.lastrowid,)).fetchone()
        finally:
            con.close()
    return _dict(row)


def get(lead_id: int) -> dict | None:
    """Fiche d'un prospect par son identifiant, ou None s'il n'existe pas."""
    with _lock:
        con = _connect()
        try:
            row = con.execute("SELECT * FROM leads WHERE id = ?", (int(lead_id),)).fetchone()
        finally:
            con.close()
    return _dict(row) if row else None


def lister(statut: str | None = None, min_score: int | None = None, limite: int = 50) -> list[dict]:
    """Prospects, du meilleur score au moins bon (puis les plus récemment modifiés d'abord).
    `statut` : n'en garder qu'un ; `min_score` : score minimum ; `limite` : au plus N fiches."""
    where, args = [], []
    if statut is not None and str(statut).strip():
        where.append("statut = ?")
        args.append(_norme_statut(statut))
    if min_score is not None:
        where.append("score >= ?")
        args.append(int(min_score))
    sql = "SELECT * FROM leads"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY score DESC, updated DESC, id DESC LIMIT ?"
    args.append(max(1, min(int(limite or 50), 500)))
    with _lock:
        con = _connect()
        try:
            rows = con.execute(sql, args).fetchall()
        finally:
            con.close()
    return [_dict(r) for r in rows]


def maj(lead_id: int, **champs: Any) -> dict:
    """Met à jour les champs donnés d'un prospect et recalcule son score ; rend la fiche.
    Champs permis : """ + ", ".join(CHAMPS) + ". Lève KeyError si le prospect n'existe pas."
    inconnus = [k for k in champs if k not in CHAMPS]
    if inconnus:
        raise ValueError(f"champ(s) inconnu(s) : {', '.join(inconnus)}")
    with _lock:
        con = _connect()
        try:
            row = con.execute("SELECT * FROM leads WHERE id = ?", (int(lead_id),)).fetchone()
            if row is None:
                raise KeyError(f"prospect {lead_id} introuvable")
            data = _dict(row)
            for k, v in champs.items():
                if k == "note":
                    data[k] = _norme_note(v)
                elif k == "avis":
                    data[k] = _norme_avis(v)
                elif k == "statut":
                    data[k] = _norme_statut(v)
                elif k == "nom":
                    nom = str(v or "").strip()
                    if not nom:
                        raise ValueError("le nom du prospect ne peut pas être vidé")
                    data[k] = nom
                else:
                    data[k] = str(v or "").strip()
            data["score"] = calculer_score(data)[0]
            data["updated"] = _now()
            maj_cols = [*CHAMPS, "score", "updated"]
            con.execute(f"UPDATE leads SET {', '.join(f'{c} = ?' for c in maj_cols)} WHERE id = ?",
                        [data[c] for c in maj_cols] + [int(lead_id)])
            con.commit()
            row = con.execute("SELECT * FROM leads WHERE id = ?", (int(lead_id),)).fetchone()
        finally:
            con.close()
    return _dict(row)
