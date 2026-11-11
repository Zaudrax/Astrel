"""CONTRATS DE MAINTENANCE freelance du CRM LOCAL (10/11, carte T90, dernier module cœur,
validé par l'utilisateur).

Un CONTRAT DE MAINTENANCE est la maintenance RÉCURRENTE d'un site ou d'une app livré à un client : il
lie un prospect (`lead_id` de `crm.py`) à un titre, un type (forfait, horaire, mensuel, annuel), un
montant, une fréquence (mensuelle, trimestrielle, annuelle, au besoin), un statut (actif, suspendu,
terminé) et une prochaine échéance. Il peut naître d'un PROJET livré (`projet_id`, dont on reprend le
prospect et le titre par lecture directe), ou être autonome. Astrel (Claude) peut créer un contrat, le
lister, le modifier et en donner un récapitulatif lisible (`resume_texte`) POUR CONSULTATION.

INVARIANT ABSOLU : aucun envoi, aucune facturation, aucun contact d'un client, AUCUNE surveillance
réseau / uptime ici. Ce module ne fait AUCUN I/O réseau : il range des contrats dans `runtime/crm.db`,
c'est tout (DONNÉES seulement, comme `projet.py`). Si une facture récurrente doit partir, elle passe par
le module `facture.py` (qui dépose un brouillon dans l'outbox) ; un contrat ne s'envoie pas. La
surveillance (uptime, réseau) est un chantier SÉPARÉ : rien de tel ici. Pas de déclencheur autonome :
tout est lancé à la demande.

Stockage dans la MÊME base que les prospects, relances, devis, factures et projets (`runtime/crm.db`) :
on RÉUTILISE la connexion et le verrou de `crm.py` (`crm._connect`, `crm._lock`), on ajoute juste la
table des maintenances. Comme `crm._lock` n'est pas réentrant, ce module ne rappelle JAMAIS une fonction
de `crm` (ni de `projet`, ni une des siennes qui prend le verrou) sous le verrou : il lit la table
`leads` et la table `projets` (reprise d'un projet) EN DIRECT sur sa propre connexion.

Modèle inspiré du projet perso MaestroAI de l'utilisateur (`FlMaintenanceContract` : leadId, projectId, type,
amount, frequency, status, nextDue) : l'idée d'un contrat récurrent né d'un projet ou autonome, suivi par
son statut et son échéance, en est reprise ; le code est réécrit pour Astrel (MaestroAI pilotait un
tableau de bord web ; Astrel ne fait que ranger et récapituler à la voix / au chat).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import crm, devis, projet

# Types de contrat (manière de facturer la maintenance).
TYPES = ("forfait", "horaire", "mensuel", "annuel")
TYPE_DEFAUT = "forfait"

# Fréquences de la maintenance récurrente.
FREQUENCES = ("mensuelle", "trimestrielle", "annuelle", "au_besoin")
FREQUENCE_DEFAUT = "mensuelle"

# Statuts d'un contrat, dans l'ordre de vie : actif → suspendu (en pause) → terminé.
STATUTS = ("actif", "suspendu", "termine")
STATUT_DEFAUT = "actif"

# Champs modifiables par `maj` (jamais id/lead_id/projet_id/created : les deux liens et la date de
# création sont fixés à la naissance du contrat).
CHAMPS = ("titre", "type", "montant", "frequence", "statut", "prochaine_echeance", "notes")

# Bornes de validation : montant et notes réutilisés de `devis` (mêmes règles, pas d'eval) ; un titre a
# sa propre longueur (celle de `projet`).
MONTANT_MAX = devis.MONTANT_MAX
NOTES_MAX = devis.NOTES_MAX
TITRE_MAX = projet.TITRE_MAX


MAINT_SCHEMA = """
CREATE TABLE IF NOT EXISTS maintenances (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id            INTEGER NOT NULL,
    projet_id          INTEGER,
    titre              TEXT NOT NULL DEFAULT '',
    type               TEXT NOT NULL DEFAULT 'forfait',
    montant            REAL NOT NULL DEFAULT 0,
    frequence          TEXT NOT NULL DEFAULT 'mensuelle',
    statut             TEXT NOT NULL DEFAULT 'actif',
    prochaine_echeance TEXT NOT NULL DEFAULT '',
    notes              TEXT NOT NULL DEFAULT '',
    created            TEXT NOT NULL,
    updated            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_maint_lead ON maintenances(lead_id);
CREATE INDEX IF NOT EXISTS idx_maint_projet ON maintenances(projet_id);
"""

_MAINT_COLS = ("id", "lead_id", "projet_id", "titre", "type", "montant", "frequence", "statut",
               "prochaine_echeance", "notes", "created", "updated")
# Champs du prospect montrés avec un contrat (jamais un secret : ses propres données clients).
_LEAD_COLS = ("id", "nom", "telephone", "email", "ville", "secteur")


# --------------------------------------------------------------------------- accès à la base

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _connect():
    """Connexion à `runtime/crm.db` (celle de `crm.py`, tables `leads` créées) + la table des projets
    (lue pour une reprise) et celle des maintenances. À appeler SOUS `crm._lock` comme les fonctions de
    `crm`. Les CREATE sont idempotents (IF NOT EXISTS) : on crée aussi la table des projets pour pouvoir
    lire une reprise même si aucun projet n'existe encore (jamais d'appel à `projet.*`)."""
    con = crm._connect()
    con.executescript(projet.PROJET_SCHEMA)  # reprise d'un projet : lecture directe, table assurée
    con.executescript(MAINT_SCHEMA)
    return con


def _maint_dict(row) -> dict:
    return {k: row[k] for k in _MAINT_COLS}


def _lead_dict(row) -> dict:
    return {k: row[k] for k in _LEAD_COLS}


# --------------------------------------------------------------------------- validation
# Montant : réutilisé de `devis` (nombre >= 0, arrondi à 2 décimales, borné, pas d'eval).
_norme_montant = devis._norme_montant
# Date : réutilisée de `projet` (vide, ou une date ISO « AAAA-MM-JJ » validée, pas d'eval).
_norme_date = projet._norme_date
# Titre : réutilisé de `projet` (nettoyé, borné à TITRE_MAX).
_norme_titre = projet._norme_titre


def _norme_type(v: Any) -> str:
    s = str(v or "").strip() or TYPE_DEFAUT
    if s not in TYPES:
        raise ValueError(f"type inconnu : {s!r} (un de {', '.join(TYPES)})")
    return s


def _norme_frequence(v: Any) -> str:
    s = str(v or "").strip() or FREQUENCE_DEFAUT
    if s not in FREQUENCES:
        raise ValueError(f"fréquence inconnue : {s!r} (une de {', '.join(FREQUENCES)})")
    return s


def _norme_statut(v: Any) -> str:
    s = str(v or "").strip() or STATUT_DEFAUT
    if s not in STATUTS:
        raise ValueError(f"statut inconnu : {s!r} (un de {', '.join(STATUTS)})")
    return s


def _projet_lead_titre(con, projet_id: int) -> tuple[int, str]:
    """Prospect (`lead_id`) et titre d'un PROJET, lus EN DIRECT sur la table `projets` (jamais d'appel à
    `projet.*`, pour ne pas reprendre le verrou). Lève KeyError si le projet n'existe pas. Verrou déjà
    tenu par l'appelant."""
    row = con.execute("SELECT lead_id, titre FROM projets WHERE id = ?", (int(projet_id),)).fetchone()
    if row is None:
        raise KeyError(f"projet {projet_id} introuvable")
    return int(row["lead_id"]), str(row["titre"] or "").strip()


# --------------------------------------------------------------------------- opérations

def creer(lead_id: int, projet_id: int | None = None, titre: str = "", type_: str = TYPE_DEFAUT,
          montant: Any = None, frequence: str = FREQUENCE_DEFAUT, prochaine_echeance: str = "",
          notes: str = "") -> dict:
    """Crée un CONTRAT DE MAINTENANCE pour un prospect (`lead_id`) et rend sa fiche. `projet_id` : contrat
    né d'un PROJET livré — on reprend son PROSPECT et son titre par LECTURE DIRECTE de la table (le
    `lead_id` passé est alors ignoré ; le titre donné prime). `titre` : nom du contrat (sinon celui du
    projet, sinon vide). `type_` : forfait, horaire, mensuel ou annuel. `montant` : prix de la maintenance
    en euros (défaut 0). `frequence` : mensuelle, trimestrielle, annuelle ou au_besoin. `prochaine_echeance`
    : date « AAAA-MM-JJ » (facultative). `notes` : texte libre. Lève KeyError si le prospect (ou le projet)
    n'existe pas, ValueError sur un montant, une date, un type, une fréquence ou un statut invalide. Ne
    contacte personne, n'envoie rien, ne facture rien, ne surveille rien."""
    titre = _norme_titre(titre)
    type_ = _norme_type(type_)
    frequence = _norme_frequence(frequence)
    notes = str(notes or "").strip()[:NOTES_MAX]
    prochaine_echeance = _norme_date(prochaine_echeance)
    montant = _norme_montant(montant)
    now = _now()
    with crm._lock:
        con = _connect()
        try:
            # Reprise d'un projet : prospect ET titre repris par lecture directe (le titre donné prime).
            if projet_id is not None:
                lead_id, titre_projet = _projet_lead_titre(con, int(projet_id))
                if not titre:
                    titre = _norme_titre(titre_projet)
            lead = con.execute("SELECT id FROM leads WHERE id = ?", (int(lead_id),)).fetchone()
            if lead is None:
                raise KeyError(f"prospect {lead_id} introuvable")
            cur = con.execute(
                "INSERT INTO maintenances (lead_id, projet_id, titre, type, montant, frequence, "
                "statut, prochaine_echeance, notes, created, updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(lead_id), int(projet_id) if projet_id is not None else None, titre, type_, montant,
                 frequence, STATUT_DEFAUT, prochaine_echeance, notes, now, now))
            con.commit()
            return _lire(con, cur.lastrowid)
        finally:
            con.close()


def _lire(con, maint_id: int) -> dict:
    """Fiche d'un contrat, sur une connexion ouverte (verrou tenu par l'appelant)."""
    row = con.execute("SELECT * FROM maintenances WHERE id = ?", (int(maint_id),)).fetchone()
    return _maint_dict(row)


def get(maint_id: int) -> dict | None:
    """Fiche d'un contrat de maintenance par son identifiant, ou None s'il n'existe pas."""
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT id FROM maintenances WHERE id = ?", (int(maint_id),)).fetchone()
            if row is None:
                return None
            return _lire(con, maint_id)
        finally:
            con.close()


def lister(lead_id: int | None = None, statut: str | None = None) -> list[dict]:
    """Contrats de maintenance, du plus récent au plus ancien. `lead_id` : ceux d'un prospect ; `statut` :
    n'en garder qu'un (actif, suspendu, termine)."""
    where, args = [], []
    if lead_id is not None:
        where.append("lead_id = ?")
        args.append(int(lead_id))
    if statut is not None and str(statut).strip():
        where.append("statut = ?")
        args.append(_norme_statut(statut))
    sql = "SELECT id FROM maintenances"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    with crm._lock:
        con = _connect()
        try:
            ids = [r["id"] for r in con.execute(sql, args).fetchall()]
            return [_lire(con, mid) for mid in ids]
        finally:
            con.close()


def maj(maint_id: int, **champs: Any) -> dict:
    """Met à jour un contrat (titre, type, montant, frequence, statut, prochaine_echeance, notes) ; rend
    la fiche. Suspendre ou terminer = `maj(statut=…)`. Champs permis : """ + ", ".join(CHAMPS) + \
    ". Lève KeyError si le contrat n'existe pas, ValueError sur un champ inconnu ou une valeur invalide."
    inconnus = [k for k in champs if k not in CHAMPS]
    if inconnus:
        raise ValueError(f"champ(s) inconnu(s) : {', '.join(inconnus)}")
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT * FROM maintenances WHERE id = ?", (int(maint_id),)).fetchone()
            if row is None:
                raise KeyError(f"contrat {maint_id} introuvable")
            data = _maint_dict(row)
            for k, v in champs.items():
                if k == "type":
                    data[k] = _norme_type(v)
                elif k == "frequence":
                    data[k] = _norme_frequence(v)
                elif k == "statut":
                    data[k] = _norme_statut(v)
                elif k == "montant":
                    data[k] = _norme_montant(v, defaut=data["montant"])
                elif k == "prochaine_echeance":
                    data[k] = _norme_date(v)
                elif k == "titre":
                    data[k] = _norme_titre(v)
                else:  # notes
                    data[k] = str(v or "").strip()[:NOTES_MAX]
            maj_cols = [*CHAMPS, "updated"]
            data["updated"] = _now()
            con.execute(f"UPDATE maintenances SET {', '.join(f'{c} = ?' for c in maj_cols)} WHERE id = ?",
                        [data[c] for c in maj_cols] + [int(maint_id)])
            con.commit()
            return _lire(con, maint_id)
        finally:
            con.close()


# --------------------------------------------------------------------------- récapitulatif

_fmt_eur = devis._fmt_eur
# Libellés lisibles pour le récapitulatif (affichage, pas d'envoi).
_TYPE_LABEL = {"forfait": "forfait", "horaire": "à l'heure", "mensuel": "mensuel", "annuel": "annuel"}
_FREQUENCE_LABEL = {"mensuelle": "mensuelle", "trimestrielle": "trimestrielle", "annuelle": "annuelle",
                    "au_besoin": "au besoin"}
_STATUT_LABEL = {"actif": "actif", "suspendu": "suspendu", "termine": "terminé"}


def resume_texte(maint_id: int) -> str:
    """Met le contrat en TEXTE lisible (français) POUR CONSULTATION : titre, client, type, montant,
    fréquence, statut, prochaine échéance, notes. Fonction de LECTURE (aucun envoi, ce n'est PAS un
    message sortant). Lève KeyError si le contrat n'existe pas."""
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT * FROM maintenances WHERE id = ?", (int(maint_id),)).fetchone()
            if row is None:
                raise KeyError(f"contrat {maint_id} introuvable")
            m = _maint_dict(row)
            lead = con.execute("SELECT * FROM leads WHERE id = ?", (int(m["lead_id"]),)).fetchone()
            lead = _lead_dict(lead) if lead else None
        finally:
            con.close()
    out: list[str] = []
    out.append(f"Contrat de maintenance {m['titre']}".strip())
    if lead and lead.get("nom"):
        out.append(f"Client : {lead['nom']}")
    out.append(f"Type : {_TYPE_LABEL.get(m['type'], m['type'])}")
    out.append(f"Montant : {_fmt_eur(m['montant'])}")
    out.append(f"Fréquence : {_FREQUENCE_LABEL.get(m['frequence'], m['frequence'])}")
    out.append(f"Statut : {_STATUT_LABEL.get(m['statut'], m['statut'])}")
    if m.get("prochaine_echeance"):
        out.append(f"Prochaine échéance : {m['prochaine_echeance']}")
    if m.get("notes"):
        out.append("")
        out.append(m["notes"])
    return "\n".join(out).strip()
