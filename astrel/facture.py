"""FACTURES freelance du CRM LOCAL (09/11, carte T90, incrément suivant, validé par l'utilisateur).

Une FACTURE lie un prospect (`lead_id` de `crm.py`) à une suite de LIGNES (désignation, quantité, prix
unitaire), avec un numéro, une TVA, une échéance et un statut (brouillon → émise → payée / impayée).
Elle peut naître d'un DEVIS accepté (`devis_id`, dont on reprend lignes et TVA), ou être autonome.
Astrel (Claude) peut créer une facture, lui ajouter des lignes, la lister, la modifier, la marquer
payée ou impayée, et surtout PRÉPARER la facture : son texte lisible (`rendu_texte`) est DÉPOSÉ comme
BROUILLON dans la file d'envoi (`outbox.py`), pour que l'utilisateur la relise et l'ENVOIE lui-même.

INVARIANT ABSOLU : aucun envoi automatique, aucun contact d'un client sans le clic de l'utilisateur au HUD.
Ce module n'envoie RIEN et ne fait AUCUN I/O réseau : il range des factures dans `runtime/crm.db` et
dépose des brouillons dans l'outbox (la SEULE voie de sortie, qui exige l'« Accepter » de l'utilisateur).
Pas de déclencheur autonome : tout est lancé à la demande.

Stockage dans la MÊME base que les prospects, relances et devis (`runtime/crm.db`) : on RÉUTILISE la
connexion et le verrou de `crm.py` (`crm._connect`, `crm._lock`), on ajoute juste les tables des
factures. Comme `crm._lock` n'est pas réentrant, ce module ne rappelle JAMAIS une fonction de `crm`
(ni une de `devis`, ni une des siennes qui prend le verrou) sous le verrou : il lit la table `leads`,
la table `devis` (reprise d'un devis) et ses propres tables en direct sur sa propre connexion, et il
dépose dans l'outbox HORS verrou (comme `devis.preparer`).

Modèle inspiré du projet perso MaestroAI de l'utilisateur (`FlInvoice` : invoiceNumber, lines, subtotal,
tvaRate, totalTtc, status payé/impayé, échéance) : l'idée d'une facture chiffrée à lignes, née d'un
devis ou autonome, en est reprise ; le code est réécrit pour Astrel (MaestroAI envoyait ; Astrel ne
fait que PROPOSER un brouillon).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from . import config, crm, devis
from .outbox import outbox

# Canal d'envoi par défaut d'un brouillon de facture (chat « message à soi-même », filtré par le cœur).
CANAL_DEFAUT = "whatsapp"

# Statuts d'une facture, dans l'ordre de vie (jamais d'envoi ici : « émise » note seulement qu'elle est
# partie). « payée » / « impayée » = suivi du règlement ; « annulée » = facture abandonnée.
STATUTS = ("brouillon", "émise", "payée", "impayée", "annulée")
STATUT_DEFAUT = "brouillon"

# Champs modifiables par `maj` (jamais id/lead_id/devis_id/totaux/created : les totaux sont calculés,
# le reste fixé à la création).
CHAMPS = ("numero", "statut", "tva_pct", "echeance", "notes")

# Réglages freelance (TVA par défaut) : réutilisés tels quels de `devis` (profil, défauts raisonnables).
reglages = devis.reglages
TVA_DEFAUT = devis.TVA_DEFAUT

# Bornes de validation (réutilisées de `devis` : mêmes montants, mêmes limites, pas d'eval).
MONTANT_MAX = devis.MONTANT_MAX
QUANTITE_MAX = devis.QUANTITE_MAX
TVA_MAX = devis.TVA_MAX
DESIGNATION_MAX = devis.DESIGNATION_MAX
NOTES_MAX = devis.NOTES_MAX
NUMERO_MAX = devis.NUMERO_MAX
LIGNES_MAX = devis.LIGNES_MAX


FACTURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS factures (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL,
    devis_id   INTEGER,
    numero     TEXT NOT NULL DEFAULT '',
    statut     TEXT NOT NULL DEFAULT 'brouillon',
    tva_pct    REAL NOT NULL DEFAULT 20,
    total_ht   REAL NOT NULL DEFAULT 0,
    total_ttc  REAL NOT NULL DEFAULT 0,
    echeance   TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    created    TEXT NOT NULL,
    updated    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facture_lignes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    facture_id     INTEGER NOT NULL,
    designation    TEXT NOT NULL DEFAULT '',
    quantite       REAL NOT NULL DEFAULT 0,
    prix_unitaire  REAL NOT NULL DEFAULT 0,
    ordre          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_factures_lead ON factures(lead_id);
CREATE INDEX IF NOT EXISTS idx_factures_devis ON factures(devis_id);
CREATE INDEX IF NOT EXISTS idx_facture_lignes_facture ON facture_lignes(facture_id);
"""

_FACTURE_COLS = ("id", "lead_id", "devis_id", "numero", "statut", "tva_pct", "total_ht", "total_ttc",
                 "echeance", "notes", "created", "updated")
_LIGNE_COLS = ("id", "facture_id", "designation", "quantite", "prix_unitaire", "ordre")
# Champs du prospect montrés avec une facture (jamais un secret : ses propres données clients).
_LEAD_COLS = ("id", "nom", "telephone", "email", "ville", "secteur")


# --------------------------------------------------------------------------- accès à la base

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _connect():
    """Connexion à `runtime/crm.db` (celle de `crm.py`, tables `leads` créées) + les tables des
    devis (lues pour une reprise) et des factures. À appeler SOUS `crm._lock` comme les fonctions de
    `crm`. Les CREATE sont idempotents (IF NOT EXISTS) : on crée aussi les tables des devis pour
    pouvoir lire une reprise même si aucun devis n'existe encore (jamais d'appel à `devis.*`)."""
    con = crm._connect()
    con.executescript(devis.DEVIS_SCHEMA)  # reprise d'un devis : lecture directe, tables assurées
    con.executescript(FACTURE_SCHEMA)
    return con


def _facture_dict(row) -> dict:
    return {k: row[k] for k in _FACTURE_COLS}


def _ligne_dict(row) -> dict:
    return {k: row[k] for k in _LIGNE_COLS}


def _lead_dict(row) -> dict:
    return {k: row[k] for k in _LEAD_COLS}


# --------------------------------------------------------------------------- validation
# Montants, quantités, TVA, désignation, statut : réutilisés de `devis` (mêmes règles, pas d'eval).
_norme_montant = devis._norme_montant
_norme_quantite = devis._norme_quantite
_norme_tva = devis._norme_tva
_norme_designation = devis._norme_designation
calculer_totaux = devis.calculer_totaux


def _norme_statut(v: Any) -> str:
    s = str(v or "").strip() or STATUT_DEFAUT
    if s not in STATUTS:
        raise ValueError(f"statut inconnu : {s!r} (un de {', '.join(STATUTS)})")
    return s


def _norme_echeance(v: Any) -> str:
    """Une échéance : vide (pas d'échéance), ou une date ISO « AAAA-MM-JJ » (validée, pas d'heure)."""
    s = str(v or "").strip()
    if not s:
        return ""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(f"échéance invalide : {v!r} (une date comme 2026-12-31)")


def _recalc(con, facture_id: int, tva_pct: float) -> tuple[float, float]:
    """Recalcule et ENREGISTRE les totaux d'une facture depuis ses lignes (connexion ouverte, verrou
    déjà tenu par l'appelant). Rend (HT, TTC)."""
    rows = con.execute("SELECT quantite, prix_unitaire FROM facture_lignes WHERE facture_id = ?",
                       (int(facture_id),)).fetchall()
    ht, ttc = calculer_totaux([dict(r) for r in rows], tva_pct)
    con.execute("UPDATE factures SET total_ht = ?, total_ttc = ?, updated = ? WHERE id = ?",
                (ht, ttc, _now(), int(facture_id)))
    return ht, ttc


def _numero_auto(facture_id: int, created: str) -> str:
    """Numéro de facture lisible (« FAC-2026-00007 »), de l'année de création et de l'identifiant."""
    annee = str(created or "")[:4] or str(datetime.now(timezone.utc).year)
    return f"FAC-{annee}-{int(facture_id):05d}"


def _lignes_du_devis(con, devis_id: int) -> tuple[list[tuple[str, float, float]], float]:
    """Lignes (designation, quantite, prix_unitaire) et TVA d'un DEVIS, lues EN DIRECT sur la table
    `devis` (jamais d'appel à `devis.*`, pour ne pas reprendre le verrou). Lève KeyError si le devis
    n'existe pas. Verrou déjà tenu par l'appelant."""
    row = con.execute("SELECT tva_pct FROM devis WHERE id = ?", (int(devis_id),)).fetchone()
    if row is None:
        raise KeyError(f"devis {devis_id} introuvable")
    lignes = con.execute("SELECT designation, quantite, prix_unitaire FROM devis_lignes "
                         "WHERE devis_id = ? ORDER BY ordre, id", (int(devis_id),)).fetchall()
    propres = [(_norme_designation(li["designation"]), _norme_quantite(li["quantite"]),
                _norme_montant(li["prix_unitaire"])) for li in lignes]
    return propres, float(row["tva_pct"])


# --------------------------------------------------------------------------- opérations

def creer(lead_id: int, devis_id: int | None = None, lignes: list | None = None, tva_pct: Any = None,
          echeance: str = "", notes: str = "", numero: str = "") -> dict:
    """Crée une FACTURE pour un prospect (`lead_id`) et rend sa fiche (avec ses lignes et ses totaux).
    `devis_id` : facture née d'un DEVIS accepté — on reprend ses lignes et sa TVA (lecture directe) ;
    les `lignes` passées s'ajoutent après celles du devis. `lignes` : liste de {designation, quantite,
    prix_unitaire} (facultatif). `tva_pct` : taux de TVA (défaut : celui du devis repris, sinon du
    profil). `echeance` : date « AAAA-MM-JJ » (facultatif). `notes` : texte libre. `numero` : imposé,
    sinon auto (« FAC-AAAA-NNNNN »). Lève KeyError si le prospect (ou le devis) n'existe pas,
    ValueError sur un montant, une date ou un statut invalide. Ne contacte personne, n'envoie rien."""
    notes = str(notes or "").strip()[:NOTES_MAX]
    numero = str(numero or "").strip()[:NUMERO_MAX]
    echeance = _norme_echeance(echeance)
    tva_demande = None if (tva_pct is None or tva_pct == "") else _norme_tva(tva_pct)
    brutes = list(lignes or [])
    # Validation des lignes données AVANT toute écriture (un montant invalide ne crée pas de facture).
    propres_ajout = [(_norme_designation((li or {}).get("designation")),
                      _norme_quantite((li or {}).get("quantite")),
                      _norme_montant((li or {}).get("prix_unitaire")))
                     for li in brutes]
    now = _now()
    with crm._lock:
        con = _connect()
        try:
            lead = con.execute("SELECT id FROM leads WHERE id = ?", (int(lead_id),)).fetchone()
            if lead is None:
                raise KeyError(f"prospect {lead_id} introuvable")
            propres: list[tuple[str, float, float]] = []
            tva_devis = None
            if devis_id is not None:
                propres_devis, tva_devis = _lignes_du_devis(con, int(devis_id))
                propres.extend(propres_devis)
            propres.extend(propres_ajout)
            if len(propres) > LIGNES_MAX:
                raise ValueError(f"trop de lignes : {len(propres)} (maximum {LIGNES_MAX})")
            # TVA : celle demandée prime, sinon celle du devis repris, sinon le défaut du profil.
            tva = tva_demande if tva_demande is not None else \
                (tva_devis if tva_devis is not None else reglages()["tva_pct"])
            tva = _norme_tva(tva)
            cur = con.execute(
                "INSERT INTO factures (lead_id, devis_id, numero, statut, tva_pct, total_ht, "
                "total_ttc, echeance, notes, created, updated) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)",
                (int(lead_id), int(devis_id) if devis_id is not None else None, numero, STATUT_DEFAUT,
                 tva, echeance, notes, now, now))
            facture_id = cur.lastrowid
            if not numero:
                con.execute("UPDATE factures SET numero = ? WHERE id = ?",
                            (_numero_auto(facture_id, now), facture_id))
            for i, (desig, qte, pu) in enumerate(propres):
                con.execute("INSERT INTO facture_lignes (facture_id, designation, quantite, "
                            "prix_unitaire, ordre) VALUES (?, ?, ?, ?, ?)",
                            (facture_id, desig, qte, pu, i))
            _recalc(con, facture_id, tva)
            con.commit()
            return _lire(con, facture_id)
        finally:
            con.close()


def ajouter_ligne(facture_id: int, designation: str, quantite: Any = 1, prix_unitaire: Any = 0) -> dict:
    """Ajoute une ligne à une facture et recalcule ses totaux ; rend la fiche. `designation` est
    obligatoire ; `quantite` >= 0, `prix_unitaire` >= 0 (validés). Lève KeyError si la facture n'existe
    pas, ValueError sur un montant invalide."""
    desig = _norme_designation(designation)
    qte = _norme_quantite(quantite)
    pu = _norme_montant(prix_unitaire)
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT tva_pct FROM factures WHERE id = ?",
                              (int(facture_id),)).fetchone()
            if row is None:
                raise KeyError(f"facture {facture_id} introuvable")
            n = con.execute("SELECT COUNT(*) AS n FROM facture_lignes WHERE facture_id = ?",
                            (int(facture_id),)).fetchone()["n"]
            if n >= LIGNES_MAX:
                raise ValueError(f"cette facture a déjà {n} lignes (maximum {LIGNES_MAX})")
            con.execute("INSERT INTO facture_lignes (facture_id, designation, quantite, prix_unitaire, "
                        "ordre) VALUES (?, ?, ?, ?, ?)", (int(facture_id), desig, qte, pu, int(n)))
            _recalc(con, facture_id, row["tva_pct"])
            con.commit()
            return _lire(con, facture_id)
        finally:
            con.close()


def _lire(con, facture_id: int) -> dict:
    """Fiche d'une facture avec ses lignes, sur une connexion ouverte (verrou tenu par l'appelant)."""
    row = con.execute("SELECT * FROM factures WHERE id = ?", (int(facture_id),)).fetchone()
    out = _facture_dict(row)
    lignes = con.execute("SELECT * FROM facture_lignes WHERE facture_id = ? ORDER BY ordre, id",
                         (int(facture_id),)).fetchall()
    out["lignes"] = [_ligne_dict(li) for li in lignes]
    return out


def get(facture_id: int) -> dict | None:
    """Fiche d'une facture (avec ses lignes et ses totaux) par son identifiant, ou None si absente."""
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT id FROM factures WHERE id = ?", (int(facture_id),)).fetchone()
            if row is None:
                return None
            return _lire(con, facture_id)
        finally:
            con.close()


def lister(lead_id: int | None = None, statut: str | None = None) -> list[dict]:
    """Factures (avec leurs lignes), de la plus récente à la plus ancienne. `lead_id` : celles d'un
    prospect ; `statut` : n'en garder qu'un (brouillon, émise, payée, impayée, annulée)."""
    where, args = [], []
    if lead_id is not None:
        where.append("lead_id = ?")
        args.append(int(lead_id))
    if statut is not None and str(statut).strip():
        where.append("statut = ?")
        args.append(_norme_statut(statut))
    sql = "SELECT id FROM factures"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    with crm._lock:
        con = _connect()
        try:
            ids = [r["id"] for r in con.execute(sql, args).fetchall()]
            return [_lire(con, fid) for fid in ids]
        finally:
            con.close()


def maj(facture_id: int, **champs: Any) -> dict:
    """Met à jour une facture (statut, tva_pct, echeance, notes, numero) et recalcule ses totaux ; rend
    la fiche. Marquer payée / impayée = `maj(statut=…)`. Champs permis : """ + ", ".join(CHAMPS) + \
    ". Lève KeyError si la facture n'existe pas, ValueError sur un champ inconnu ou une valeur invalide."
    inconnus = [k for k in champs if k not in CHAMPS]
    if inconnus:
        raise ValueError(f"champ(s) inconnu(s) : {', '.join(inconnus)}")
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT * FROM factures WHERE id = ?", (int(facture_id),)).fetchone()
            if row is None:
                raise KeyError(f"facture {facture_id} introuvable")
            data = _facture_dict(row)
            for k, v in champs.items():
                if k == "statut":
                    data[k] = _norme_statut(v)
                elif k == "tva_pct":
                    data[k] = _norme_tva(v, defaut=data["tva_pct"])
                elif k == "echeance":
                    data[k] = _norme_echeance(v)
                elif k == "numero":
                    data[k] = str(v or "").strip()[:NUMERO_MAX]
                else:  # notes
                    data[k] = str(v or "").strip()[:NOTES_MAX]
            con.execute("UPDATE factures SET numero = ?, statut = ?, tva_pct = ?, echeance = ?, "
                        "notes = ?, updated = ? WHERE id = ?",
                        (data["numero"], data["statut"], data["tva_pct"], data["echeance"],
                         data["notes"], _now(), int(facture_id)))
            _recalc(con, facture_id, data["tva_pct"])  # la TVA a pu changer : TTC à refaire
            con.commit()
            return _lire(con, facture_id)
        finally:
            con.close()


# --------------------------------------------------------------------------- rendu et préparation

_fmt_eur = devis._fmt_eur


def rendu_texte(facture_id: int) -> str:
    """Met la facture en TEXTE lisible (français) pour l'envoi : numéro, client, date, échéance, lignes
    chiffrées, totaux HT/TVA/TTC, signature du profil. Fonction de LECTURE (aucun envoi). Lève KeyError
    si la facture n'existe pas."""
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT * FROM factures WHERE id = ?", (int(facture_id),)).fetchone()
            if row is None:
                raise KeyError(f"facture {facture_id} introuvable")
            f = _facture_dict(row)
            lignes = [_ligne_dict(li) for li in con.execute(
                "SELECT * FROM facture_lignes WHERE facture_id = ? ORDER BY ordre, id",
                (int(facture_id),)).fetchall()]
            lead = con.execute("SELECT * FROM leads WHERE id = ?", (int(f["lead_id"]),)).fetchone()
            lead = _lead_dict(lead) if lead else None
        finally:
            con.close()
    reg = reglages()
    out: list[str] = []
    out.append(f"Facture {f['numero']}".strip())
    if lead and lead.get("nom"):
        out.append(f"Client : {lead['nom']}")
    out.append(f"Date : {str(f['created'])[:10]}")
    if f.get("echeance"):
        out.append(f"Échéance : {f['echeance']}")
    out.append("")
    for li in lignes:
        ligne_ht = round(float(li["quantite"]) * float(li["prix_unitaire"]), 2)
        out.append(f"- {li['designation']} : {li['quantite']:g} × {_fmt_eur(li['prix_unitaire'])} "
                   f"= {_fmt_eur(ligne_ht)}")
    if not lignes:
        out.append("(aucune ligne)")
    out.append("")
    out.append(f"Total HT : {_fmt_eur(f['total_ht'])}")
    out.append(f"TVA ({float(f['tva_pct']):g} %) : {_fmt_eur(round(f['total_ttc'] - f['total_ht'], 2))}")
    out.append(f"Total TTC : {_fmt_eur(f['total_ttc'])}")
    if f.get("notes"):
        out.append("")
        out.append(f["notes"])
    if reg["signature"]:
        out.append("")
        out.append(reg["signature"])
    return "\n".join(out).strip()


def preparer(facture_id: int, stage: Callable[..., dict] | None = None) -> dict:
    """Prépare une facture à l'envoi : construit son texte (`rendu_texte`), le DÉPOSE comme BROUILLON
    dans la file d'envoi (`outbox.stage`). Rien n'est envoyé : l'utilisateur relit et envoie lui-même depuis
    le HUD. Rend l'identifiant du brouillon.

    `stage` : fonction de dépôt `(channel, text, to=, source=) -> {"ok", "id"?, "error"?}` ; par défaut
    la file d'envoi locale (`outbox.stage`). Le serveur MCP y injecte un dépôt qui passe par le cœur (où
    la file et le canal WhatsApp vivent). Le dépôt se fait HORS verrou `crm._lock` (non réentrant)."""
    depot = stage or outbox.stage
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT id FROM factures WHERE id = ?", (int(facture_id),)).fetchone()
        finally:
            con.close()
    if row is None:
        return {"ok": False, "error": f"facture {facture_id} introuvable"}
    texte = rendu_texte(facture_id)  # prend et rend le verrou ; pas de dépôt tant qu'on le tient
    if not texte:
        return {"ok": False, "error": "cette facture est vide : rien à préparer"}
    res = depot(CANAL_DEFAUT, texte, to="", source="claude")
    if not isinstance(res, dict) or res.get("ok") is not True:
        err = res.get("error") if isinstance(res, dict) else None
        return {"ok": False, "error": str(err or "dépôt du brouillon impossible")}
    return {"ok": True, "brouillon": res.get("id"), "facture_id": int(facture_id), "texte": texte}
