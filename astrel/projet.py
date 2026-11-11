"""PROJETS freelance du CRM LOCAL (10/11, carte T90, incrément suivant, validé par l'utilisateur).

Un PROJET est le TRAVAIL livré à un client : il lie un prospect (`lead_id` de `crm.py`) à un titre, un
statut d'avancement (prospect → en cours → livré → clos / annulé), un montant, des dates (début, fin) et
des livrables. Il peut naître d'un DEVIS accepté (`devis_id`, dont on reprend le titre et le montant), ou
être autonome. Astrel (Claude) peut créer un projet, le lister, le modifier et en donner un récapitulatif
lisible (`resume_texte`) POUR CONSULTATION (jamais pour l'envoi).

INVARIANT ABSOLU : aucun envoi, aucune facturation, aucun contact d'un client ici. Ce module ne fait
AUCUN I/O réseau : il range des projets dans `runtime/crm.db`, c'est tout. La facturation est son propre
module (`facture.py`, qui dépose un brouillon dans l'outbox) ; un projet ne s'envoie pas. Pas de
déclencheur autonome : tout est lancé à la demande.

Stockage dans la MÊME base que les prospects, relances, devis et factures (`runtime/crm.db`) : on
RÉUTILISE la connexion et le verrou de `crm.py` (`crm._connect`, `crm._lock`), on ajoute juste la table
des projets. Comme `crm._lock` n'est pas réentrant, ce module ne rappelle JAMAIS une fonction de `crm`
(ni de `devis`, ni une des siennes qui prend le verrou) sous le verrou : il lit la table `leads` et la
table `devis` (reprise d'un devis) EN DIRECT sur sa propre connexion.

Modèle inspiré du projet perso MaestroAI de l'utilisateur (`FlProject` : leadId, quoteId, name, phase,
progress, montant, dates, livrables) : l'idée d'un projet né d'un devis ou autonome, suivi par son
avancement, en est reprise ; le code est réécrit pour Astrel (MaestroAI pilotait un tableau de bord web ;
Astrel ne fait que ranger et récapituler à la voix / au chat).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import crm, devis

# Statuts d'un projet, dans l'ordre de vie : prospect (pressenti) → en cours → livré → clos (terminé et
# réglé) ; « annulé » = projet abandonné.
STATUTS = ("prospect", "en_cours", "livré", "clos", "annulé")
STATUT_DEFAUT = "prospect"

# Champs modifiables par `maj` (jamais id/lead_id/devis_id/created : les deux liens et la date de
# création sont fixés à la naissance du projet).
CHAMPS = ("titre", "statut", "montant", "date_debut", "date_fin", "livrables", "notes")

# Bornes de validation : montant et notes réutilisés de `devis` (mêmes règles, pas d'eval) ; un titre et
# une liste de livrables ont leurs propres longueurs.
MONTANT_MAX = devis.MONTANT_MAX
NOTES_MAX = devis.NOTES_MAX
TITRE_MAX = 200
LIVRABLES_MAX = 4_000


PROJET_SCHEMA = """
CREATE TABLE IF NOT EXISTS projets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL,
    devis_id   INTEGER,
    titre      TEXT NOT NULL DEFAULT '',
    statut     TEXT NOT NULL DEFAULT 'prospect',
    montant    REAL NOT NULL DEFAULT 0,
    date_debut TEXT NOT NULL DEFAULT '',
    date_fin   TEXT NOT NULL DEFAULT '',
    livrables  TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    created    TEXT NOT NULL,
    updated    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projets_lead ON projets(lead_id);
CREATE INDEX IF NOT EXISTS idx_projets_devis ON projets(devis_id);
"""

_PROJET_COLS = ("id", "lead_id", "devis_id", "titre", "statut", "montant", "date_debut", "date_fin",
                "livrables", "notes", "created", "updated")
# Champs du prospect montrés avec un projet (jamais un secret : ses propres données clients).
_LEAD_COLS = ("id", "nom", "telephone", "email", "ville", "secteur")


# --------------------------------------------------------------------------- accès à la base

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _connect():
    """Connexion à `runtime/crm.db` (celle de `crm.py`, tables `leads` créées) + la table des devis
    (lue pour une reprise) et celle des projets. À appeler SOUS `crm._lock` comme les fonctions de
    `crm`. Les CREATE sont idempotents (IF NOT EXISTS) : on crée aussi la table des devis pour pouvoir
    lire une reprise même si aucun devis n'existe encore (jamais d'appel à `devis.*`)."""
    con = crm._connect()
    con.executescript(devis.DEVIS_SCHEMA)  # reprise d'un devis : lecture directe, tables assurées
    con.executescript(PROJET_SCHEMA)
    return con


def _projet_dict(row) -> dict:
    return {k: row[k] for k in _PROJET_COLS}


def _lead_dict(row) -> dict:
    return {k: row[k] for k in _LEAD_COLS}


# --------------------------------------------------------------------------- validation
# Montant : réutilisé de `devis` (nombre >= 0, arrondi à 2 décimales, borné, pas d'eval).
_norme_montant = devis._norme_montant


def _norme_statut(v: Any) -> str:
    s = str(v or "").strip() or STATUT_DEFAUT
    if s not in STATUTS:
        raise ValueError(f"statut inconnu : {s!r} (un de {', '.join(STATUTS)})")
    return s


def _norme_date(v: Any) -> str:
    """Une date : vide (pas de date), ou une date ISO « AAAA-MM-JJ » (validée, pas d'heure)."""
    s = str(v or "").strip()
    if not s:
        return ""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(f"date invalide : {v!r} (une date comme 2026-12-31)")


def _norme_titre(v: Any) -> str:
    return str(v or "").strip()[:TITRE_MAX]


def _devis_titre_montant(con, devis_id: int) -> tuple[str, float]:
    """Titre (numéro) et montant (total TTC) d'un DEVIS, lus EN DIRECT sur la table `devis` (jamais
    d'appel à `devis.*`, pour ne pas reprendre le verrou). Lève KeyError si le devis n'existe pas.
    Verrou déjà tenu par l'appelant."""
    row = con.execute("SELECT numero, total_ttc FROM devis WHERE id = ?", (int(devis_id),)).fetchone()
    if row is None:
        raise KeyError(f"devis {devis_id} introuvable")
    return str(row["numero"] or "").strip(), round(float(row["total_ttc"] or 0), 2)


# --------------------------------------------------------------------------- opérations

def creer(lead_id: int, devis_id: int | None = None, titre: str = "", montant: Any = None,
          date_debut: str = "", date_fin: str = "", livrables: str = "", notes: str = "") -> dict:
    """Crée un PROJET pour un prospect (`lead_id`) et rend sa fiche. `devis_id` : projet né d'un DEVIS
    accepté — on reprend son titre (numéro) et son montant (total TTC) par LECTURE DIRECTE de la table
    (si `titre` / `montant` ne sont pas donnés). `titre` : nom du projet (sinon celui du devis, sinon
    vide). `montant` : prix du projet en euros (défaut : celui du devis repris, sinon 0). `date_debut` /
    `date_fin` : dates « AAAA-MM-JJ » (facultatives). `livrables` : texte libre (ce qui est à livrer).
    `notes` : texte libre. Lève KeyError si le prospect (ou le devis) n'existe pas, ValueError sur un
    montant, une date ou un statut invalide. Ne contacte personne, n'envoie rien, ne facture rien."""
    titre = _norme_titre(titre)
    livrables = str(livrables or "").strip()[:LIVRABLES_MAX]
    notes = str(notes or "").strip()[:NOTES_MAX]
    date_debut = _norme_date(date_debut)
    date_fin = _norme_date(date_fin)
    montant_demande = None if (montant is None or montant == "") else _norme_montant(montant)
    now = _now()
    with crm._lock:
        con = _connect()
        try:
            lead = con.execute("SELECT id FROM leads WHERE id = ?", (int(lead_id),)).fetchone()
            if lead is None:
                raise KeyError(f"prospect {lead_id} introuvable")
            titre_devis, montant_devis = "", None
            if devis_id is not None:
                titre_devis, montant_devis = _devis_titre_montant(con, int(devis_id))
            # Titre : celui donné prime, sinon celui (numéro) du devis repris.
            if not titre:
                titre = titre_devis
            # Montant : celui donné prime, sinon celui du devis repris, sinon 0.
            montant = montant_demande if montant_demande is not None else \
                (montant_devis if montant_devis is not None else 0.0)
            montant = _norme_montant(montant)
            cur = con.execute(
                "INSERT INTO projets (lead_id, devis_id, titre, statut, montant, date_debut, "
                "date_fin, livrables, notes, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(lead_id), int(devis_id) if devis_id is not None else None, titre, STATUT_DEFAUT,
                 montant, date_debut, date_fin, livrables, notes, now, now))
            con.commit()
            return _lire(con, cur.lastrowid)
        finally:
            con.close()


def _lire(con, projet_id: int) -> dict:
    """Fiche d'un projet, sur une connexion ouverte (verrou tenu par l'appelant)."""
    row = con.execute("SELECT * FROM projets WHERE id = ?", (int(projet_id),)).fetchone()
    return _projet_dict(row)


def get(projet_id: int) -> dict | None:
    """Fiche d'un projet par son identifiant, ou None s'il n'existe pas."""
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT id FROM projets WHERE id = ?", (int(projet_id),)).fetchone()
            if row is None:
                return None
            return _lire(con, projet_id)
        finally:
            con.close()


def lister(lead_id: int | None = None, statut: str | None = None) -> list[dict]:
    """Projets, du plus récent au plus ancien. `lead_id` : ceux d'un prospect ; `statut` : n'en garder
    qu'un (prospect, en_cours, livré, clos, annulé)."""
    where, args = [], []
    if lead_id is not None:
        where.append("lead_id = ?")
        args.append(int(lead_id))
    if statut is not None and str(statut).strip():
        where.append("statut = ?")
        args.append(_norme_statut(statut))
    sql = "SELECT id FROM projets"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    with crm._lock:
        con = _connect()
        try:
            ids = [r["id"] for r in con.execute(sql, args).fetchall()]
            return [_lire(con, pid) for pid in ids]
        finally:
            con.close()


def maj(projet_id: int, **champs: Any) -> dict:
    """Met à jour un projet (titre, statut, montant, date_debut, date_fin, livrables, notes) ; rend la
    fiche. Faire avancer le projet = `maj(statut=…)`. Champs permis : """ + ", ".join(CHAMPS) + \
    ". Lève KeyError si le projet n'existe pas, ValueError sur un champ inconnu ou une valeur invalide."
    inconnus = [k for k in champs if k not in CHAMPS]
    if inconnus:
        raise ValueError(f"champ(s) inconnu(s) : {', '.join(inconnus)}")
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT * FROM projets WHERE id = ?", (int(projet_id),)).fetchone()
            if row is None:
                raise KeyError(f"projet {projet_id} introuvable")
            data = _projet_dict(row)
            for k, v in champs.items():
                if k == "statut":
                    data[k] = _norme_statut(v)
                elif k == "montant":
                    data[k] = _norme_montant(v, defaut=data["montant"])
                elif k in ("date_debut", "date_fin"):
                    data[k] = _norme_date(v)
                elif k == "titre":
                    data[k] = _norme_titre(v)
                elif k == "livrables":
                    data[k] = str(v or "").strip()[:LIVRABLES_MAX]
                else:  # notes
                    data[k] = str(v or "").strip()[:NOTES_MAX]
            maj_cols = [*CHAMPS, "updated"]
            data["updated"] = _now()
            con.execute(f"UPDATE projets SET {', '.join(f'{c} = ?' for c in maj_cols)} WHERE id = ?",
                        [data[c] for c in maj_cols] + [int(projet_id)])
            con.commit()
            return _lire(con, projet_id)
        finally:
            con.close()


# --------------------------------------------------------------------------- récapitulatif

_fmt_eur = devis._fmt_eur
# Libellés lisibles des statuts pour le récapitulatif (affichage, pas d'envoi).
_STATUT_LABEL = {"prospect": "pressenti", "en_cours": "en cours", "livré": "livré", "clos": "clos",
                 "annulé": "annulé"}


def resume_texte(projet_id: int) -> str:
    """Met le projet en TEXTE lisible (français) POUR CONSULTATION : titre, client, statut, montant,
    dates, livrables, notes. Fonction de LECTURE (aucun envoi, ce n'est PAS un message sortant). Lève
    KeyError si le projet n'existe pas."""
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT * FROM projets WHERE id = ?", (int(projet_id),)).fetchone()
            if row is None:
                raise KeyError(f"projet {projet_id} introuvable")
            p = _projet_dict(row)
            lead = con.execute("SELECT * FROM leads WHERE id = ?", (int(p["lead_id"]),)).fetchone()
            lead = _lead_dict(lead) if lead else None
        finally:
            con.close()
    out: list[str] = []
    out.append(f"Projet {p['titre']}".strip())
    if lead and lead.get("nom"):
        out.append(f"Client : {lead['nom']}")
    out.append(f"Statut : {_STATUT_LABEL.get(p['statut'], p['statut'])}")
    out.append(f"Montant : {_fmt_eur(p['montant'])}")
    if p.get("date_debut"):
        out.append(f"Début : {p['date_debut']}")
    if p.get("date_fin"):
        out.append(f"Fin : {p['date_fin']}")
    if p.get("livrables"):
        out.append("")
        out.append("Livrables :")
        out.append(p["livrables"])
    if p.get("notes"):
        out.append("")
        out.append(p["notes"])
    return "\n".join(out).strip()
