"""DEVIS freelance du CRM LOCAL (09/11, carte T90, incrément suivant, validé par l'utilisateur).

Un DEVIS lie un prospect (`lead_id` de `crm.py`) à une suite de LIGNES (désignation, quantité, prix
unitaire). Astrel (Claude) peut créer un devis, lui ajouter des lignes, le lister, le modifier, et
surtout PRÉPARER le devis : son texte lisible (`rendu_texte`) est DÉPOSÉ comme BROUILLON dans la file
d'envoi (`outbox.py`), pour que l'utilisateur le relise et l'ENVOIE lui-même.

INVARIANT ABSOLU : aucun envoi automatique, aucun contact d'un prospect sans le clic de l'utilisateur au
HUD. Ce module n'envoie RIEN et ne fait AUCUN I/O réseau : il range des devis dans `runtime/crm.db`
et dépose des brouillons dans l'outbox (la SEULE voie de sortie, qui exige l'« Accepter » de
l'utilisateur). Pas de déclencheur autonome : tout est lancé à la demande.

Stockage dans la MÊME base que les prospects et les relances (`runtime/crm.db`) : on RÉUTILISE la
connexion et le verrou de `crm.py` (`crm._connect`, `crm._lock`), on ajoute juste les tables des
devis. Comme `crm._lock` n'est pas réentrant, ce module ne rappelle JAMAIS une fonction de `crm` (ni
une des siennes qui prend le verrou) sous le verrou : il lit la table `leads` et ses propres tables
en direct sur sa propre connexion, et il dépose dans l'outbox HORS verrou (comme `outreach.preparer`).

Modèle inspiré du projet perso MaestroAI de l'utilisateur (`FlQuote` / `FlSettings` : lignes, total, TVA,
TJM, signature) : l'idée d'un devis chiffré à lignes et de réglages freelance en est reprise, le code
est réécrit pour Astrel (MaestroAI envoyait ; Astrel ne fait que PROPOSER un brouillon).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from . import config, crm
from .outbox import outbox

# Canal d'envoi par défaut d'un brouillon de devis (chat « message à soi-même », filtré par le cœur).
CANAL_DEFAUT = "whatsapp"

# Statuts d'un devis, dans l'ordre de vie (jamais d'envoi ici : « envoyé » note seulement qu'il est parti).
STATUTS = ("brouillon", "envoyé", "accepté", "refusé")
STATUT_DEFAUT = "brouillon"

# Champs modifiables par `maj` (jamais id/lead_id/totaux/created : les totaux sont calculés, le reste fixé).
CHAMPS = ("numero", "statut", "tva_pct", "notes")

# Réglages freelance par défaut, sans `config/profile.json` (dépôt public, nouvelle machine). Aucun
# secret : juste des valeurs raisonnables. TVA 20 % (taux normal français), TJM 0 (à régler au profil).
TVA_DEFAUT = 20.0
TJM_DEFAUT = 0.0

# Bornes de validation (montants raisonnables, pas d'eval, arrondis à 2 décimales).
MONTANT_MAX = 10_000_000.0   # un prix unitaire ou un total HT au-delà est refusé
QUANTITE_MAX = 1_000_000.0
TVA_MAX = 100.0
DESIGNATION_MAX = 500
NOTES_MAX = 4_000
NUMERO_MAX = 40
LIGNES_MAX = 200             # un devis ne peut pas dépasser ce nombre de lignes


DEVIS_SCHEMA = """
CREATE TABLE IF NOT EXISTS devis (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL,
    numero     TEXT NOT NULL DEFAULT '',
    statut     TEXT NOT NULL DEFAULT 'brouillon',
    tva_pct    REAL NOT NULL DEFAULT 20,
    total_ht   REAL NOT NULL DEFAULT 0,
    total_ttc  REAL NOT NULL DEFAULT 0,
    notes      TEXT NOT NULL DEFAULT '',
    created    TEXT NOT NULL,
    updated    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS devis_lignes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    devis_id       INTEGER NOT NULL,
    designation    TEXT NOT NULL DEFAULT '',
    quantite       REAL NOT NULL DEFAULT 0,
    prix_unitaire  REAL NOT NULL DEFAULT 0,
    ordre          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_devis_lead ON devis(lead_id);
CREATE INDEX IF NOT EXISTS idx_lignes_devis ON devis_lignes(devis_id);
"""

_DEVIS_COLS = ("id", "lead_id", "numero", "statut", "tva_pct", "total_ht", "total_ttc", "notes",
               "created", "updated")
_LIGNE_COLS = ("id", "devis_id", "designation", "quantite", "prix_unitaire", "ordre")
# Champs du prospect montrés avec un devis (jamais un secret : ses propres données clients).
_LEAD_COLS = ("id", "nom", "telephone", "email", "ville", "secteur")


# --------------------------------------------------------------------------- réglages freelance

def reglages() -> dict:
    """Réglages freelance du profil (`config/profile.json`, clés « tjm », « tva_pct », « nom »,
    « signature »), sinon des défauts raisonnables. Lu à chaque appel (le profil peut changer).
    Aucun secret : des valeurs d'affichage (nom et signature au bas d'un devis, TVA et TJM par
    défaut). Fonction PURE (aucune écriture)."""
    profil = getattr(config, "PROFILE", None) or {}
    nom = str(profil.get("nom") or "").strip()
    signature = str(profil.get("signature") or "").strip() or nom
    return {
        "nom": nom,
        "signature": signature,
        "tva_pct": _norme_tva(profil.get("tva_pct"), defaut=TVA_DEFAUT),
        "tjm": _norme_montant(profil.get("tjm"), defaut=TJM_DEFAUT),
    }


# --------------------------------------------------------------------------- accès à la base

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _connect():
    """Connexion à `runtime/crm.db` (celle de `crm.py`, tables `leads` créées) + les tables des
    devis. À appeler SOUS `crm._lock` comme les fonctions de `crm`."""
    con = crm._connect()
    con.executescript(DEVIS_SCHEMA)
    return con


def _devis_dict(row) -> dict:
    return {k: row[k] for k in _DEVIS_COLS}


def _ligne_dict(row) -> dict:
    return {k: row[k] for k in _LIGNE_COLS}


def _lead_dict(row) -> dict:
    return {k: row[k] for k in _LEAD_COLS}


# --------------------------------------------------------------------------- validation des montants

def _norme_montant(v: Any, defaut: float = 0.0) -> float:
    """Un montant (prix) : nombre >= 0, arrondi à 2 décimales, borné. Pas d'eval (float() seul)."""
    if v is None or v == "":
        return round(float(defaut), 2)
    try:
        x = float(str(v).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        raise ValueError(f"montant invalide : {v!r} (un nombre comme 350 ou 49.90)")
    if x != x or x in (float("inf"), float("-inf")):  # NaN / inf
        raise ValueError(f"montant invalide : {v!r}")
    if x < 0:
        raise ValueError(f"montant négatif refusé : {v!r}")
    if x > MONTANT_MAX:
        raise ValueError(f"montant trop grand : {v!r} (maximum {MONTANT_MAX:.0f})")
    return round(x, 2)


def _norme_quantite(v: Any) -> float:
    """Une quantité (nombre de jours, d'unités) : nombre >= 0, arrondi à 2 décimales, borné."""
    if v is None or v == "":
        return 0.0
    try:
        x = float(str(v).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        raise ValueError(f"quantité invalide : {v!r} (un nombre comme 1 ou 2.5)")
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError(f"quantité invalide : {v!r}")
    if x < 0:
        raise ValueError(f"quantité négative refusée : {v!r}")
    if x > QUANTITE_MAX:
        raise ValueError(f"quantité trop grande : {v!r} (maximum {QUANTITE_MAX:.0f})")
    return round(x, 2)


def _norme_tva(v: Any, defaut: float = TVA_DEFAUT) -> float:
    """Un taux de TVA en pourcentage : nombre dans [0, 100], arrondi à 2 décimales."""
    if v is None or v == "":
        return round(float(defaut), 2)
    try:
        x = float(str(v).replace(",", ".").replace("%", "").replace(" ", ""))
    except (TypeError, ValueError):
        raise ValueError(f"taux de TVA invalide : {v!r} (un pourcentage comme 20 ou 0)")
    if x != x or x < 0 or x > TVA_MAX:
        raise ValueError(f"taux de TVA hors bornes : {v!r} (entre 0 et {TVA_MAX:.0f})")
    return round(x, 2)


def _norme_statut(v: Any) -> str:
    s = str(v or "").strip() or STATUT_DEFAUT
    if s not in STATUTS:
        raise ValueError(f"statut inconnu : {s!r} (un de {', '.join(STATUTS)})")
    return s


def _norme_designation(v: Any) -> str:
    d = str(v or "").strip()
    if not d:
        raise ValueError("la désignation d'une ligne est obligatoire")
    return d[:DESIGNATION_MAX]


def calculer_totaux(lignes: list[dict], tva_pct: float) -> tuple[float, float]:
    """Totaux d'un devis : HT = somme(quantité × prix unitaire), TTC = HT × (1 + TVA/100). Fonction
    PURE, arrondie à 2 décimales. Chaque ligne : {quantite, prix_unitaire} (déjà validés)."""
    ht = round(sum(round(float(li.get("quantite") or 0) * float(li.get("prix_unitaire") or 0), 2)
                   for li in lignes), 2)
    ttc = round(ht * (1 + float(tva_pct) / 100.0), 2)
    return ht, ttc


def _recalc(con, devis_id: int, tva_pct: float) -> tuple[float, float]:
    """Recalcule et ENREGISTRE les totaux d'un devis depuis ses lignes (connexion ouverte, verrou
    déjà tenu par l'appelant). Rend (HT, TTC)."""
    rows = con.execute("SELECT quantite, prix_unitaire FROM devis_lignes WHERE devis_id = ?",
                       (int(devis_id),)).fetchall()
    ht, ttc = calculer_totaux([dict(r) for r in rows], tva_pct)
    con.execute("UPDATE devis SET total_ht = ?, total_ttc = ?, updated = ? WHERE id = ?",
                (ht, ttc, _now(), int(devis_id)))
    return ht, ttc


def _numero_auto(devis_id: int, created: str) -> str:
    """Numéro de devis lisible (« DEV-2026-00007 »), de l'année de création et de l'identifiant."""
    annee = str(created or "")[:4] or str(datetime.now(timezone.utc).year)
    return f"DEV-{annee}-{int(devis_id):05d}"


# --------------------------------------------------------------------------- opérations

def creer(lead_id: int, lignes: list | None = None, tva_pct: Any = None, notes: str = "",
          numero: str = "") -> dict:
    """Crée un DEVIS pour un prospect (`lead_id`) et rend sa fiche (avec ses lignes et ses totaux).
    `lignes` : liste de {designation, quantite, prix_unitaire} (facultatif, on peut en ajouter après).
    `tva_pct` : taux de TVA (défaut : celui du profil). `notes` : texte libre. `numero` : imposé, sinon
    auto (« DEV-AAAA-NNNNN »). Lève KeyError si le prospect n'existe pas, ValueError sur un montant ou
    un statut invalide. Ne contacte personne, n'envoie rien : seulement le rangement."""
    tva = _norme_tva(tva_pct, defaut=reglages()["tva_pct"])
    notes = str(notes or "").strip()[:NOTES_MAX]
    numero = str(numero or "").strip()[:NUMERO_MAX]
    brutes = list(lignes or [])
    if len(brutes) > LIGNES_MAX:
        raise ValueError(f"trop de lignes : {len(brutes)} (maximum {LIGNES_MAX})")
    # Validation AVANT toute écriture (un montant invalide ne crée pas un devis à moitié).
    propres = [(_norme_designation((li or {}).get("designation")),
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
            cur = con.execute(
                "INSERT INTO devis (lead_id, numero, statut, tva_pct, total_ht, total_ttc, notes, "
                "created, updated) VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?)",
                (int(lead_id), numero, STATUT_DEFAUT, tva, notes, now, now))
            devis_id = cur.lastrowid
            if not numero:
                con.execute("UPDATE devis SET numero = ? WHERE id = ?",
                            (_numero_auto(devis_id, now), devis_id))
            for i, (desig, qte, pu) in enumerate(propres):
                con.execute("INSERT INTO devis_lignes (devis_id, designation, quantite, "
                            "prix_unitaire, ordre) VALUES (?, ?, ?, ?, ?)",
                            (devis_id, desig, qte, pu, i))
            _recalc(con, devis_id, tva)
            con.commit()
            return _lire(con, devis_id)
        finally:
            con.close()


def ajouter_ligne(devis_id: int, designation: str, quantite: Any = 1, prix_unitaire: Any = 0) -> dict:
    """Ajoute une ligne à un devis et recalcule ses totaux ; rend la fiche du devis. `designation` est
    obligatoire ; `quantite` >= 0, `prix_unitaire` >= 0 (validés). Lève KeyError si le devis n'existe
    pas, ValueError sur un montant invalide."""
    desig = _norme_designation(designation)
    qte = _norme_quantite(quantite)
    pu = _norme_montant(prix_unitaire)
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT tva_pct FROM devis WHERE id = ?", (int(devis_id),)).fetchone()
            if row is None:
                raise KeyError(f"devis {devis_id} introuvable")
            n = con.execute("SELECT COUNT(*) AS n FROM devis_lignes WHERE devis_id = ?",
                            (int(devis_id),)).fetchone()["n"]
            if n >= LIGNES_MAX:
                raise ValueError(f"ce devis a déjà {n} lignes (maximum {LIGNES_MAX})")
            con.execute("INSERT INTO devis_lignes (devis_id, designation, quantite, prix_unitaire, "
                        "ordre) VALUES (?, ?, ?, ?, ?)", (int(devis_id), desig, qte, pu, int(n)))
            _recalc(con, devis_id, row["tva_pct"])
            con.commit()
            return _lire(con, devis_id)
        finally:
            con.close()


def _lire(con, devis_id: int) -> dict:
    """Fiche d'un devis avec ses lignes, sur une connexion ouverte (verrou tenu par l'appelant)."""
    row = con.execute("SELECT * FROM devis WHERE id = ?", (int(devis_id),)).fetchone()
    out = _devis_dict(row)
    lignes = con.execute("SELECT * FROM devis_lignes WHERE devis_id = ? ORDER BY ordre, id",
                         (int(devis_id),)).fetchall()
    out["lignes"] = [_ligne_dict(li) for li in lignes]
    return out


def get(devis_id: int) -> dict | None:
    """Fiche d'un devis (avec ses lignes et ses totaux) par son identifiant, ou None s'il n'existe pas."""
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT id FROM devis WHERE id = ?", (int(devis_id),)).fetchone()
            if row is None:
                return None
            return _lire(con, devis_id)
        finally:
            con.close()


def lister(lead_id: int | None = None, statut: str | None = None) -> list[dict]:
    """Devis (avec leurs lignes), du plus récent au plus ancien. `lead_id` : ceux d'un prospect ;
    `statut` : n'en garder qu'un (brouillon, envoyé, accepté, refusé)."""
    where, args = [], []
    if lead_id is not None:
        where.append("lead_id = ?")
        args.append(int(lead_id))
    if statut is not None and str(statut).strip():
        where.append("statut = ?")
        args.append(_norme_statut(statut))
    sql = "SELECT id FROM devis"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    with crm._lock:
        con = _connect()
        try:
            ids = [r["id"] for r in con.execute(sql, args).fetchall()]
            return [_lire(con, did) for did in ids]
        finally:
            con.close()


def maj(devis_id: int, **champs: Any) -> dict:
    """Met à jour un devis (statut, tva_pct, notes, numero) et recalcule ses totaux ; rend la fiche.
    Champs permis : """ + ", ".join(CHAMPS) + ". Lève KeyError si le devis n'existe pas, ValueError sur "
    "un champ inconnu ou une valeur invalide."
    inconnus = [k for k in champs if k not in CHAMPS]
    if inconnus:
        raise ValueError(f"champ(s) inconnu(s) : {', '.join(inconnus)}")
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT * FROM devis WHERE id = ?", (int(devis_id),)).fetchone()
            if row is None:
                raise KeyError(f"devis {devis_id} introuvable")
            data = _devis_dict(row)
            for k, v in champs.items():
                if k == "statut":
                    data[k] = _norme_statut(v)
                elif k == "tva_pct":
                    data[k] = _norme_tva(v, defaut=data["tva_pct"])
                elif k == "numero":
                    data[k] = str(v or "").strip()[:NUMERO_MAX]
                else:  # notes
                    data[k] = str(v or "").strip()[:NOTES_MAX]
            con.execute("UPDATE devis SET numero = ?, statut = ?, tva_pct = ?, notes = ?, updated = ? "
                        "WHERE id = ?", (data["numero"], data["statut"], data["tva_pct"], data["notes"],
                                         _now(), int(devis_id)))
            _recalc(con, devis_id, data["tva_pct"])  # la TVA a pu changer : TTC à refaire
            con.commit()
            return _lire(con, devis_id)
        finally:
            con.close()


# --------------------------------------------------------------------------- rendu et préparation

def _fmt_eur(montant: float) -> str:
    return f"{float(montant):.2f} €".replace(".", ",")


def rendu_texte(devis_id: int) -> str:
    """Met le devis en TEXTE lisible (français) pour l'envoi : numéro, client, lignes chiffrées,
    totaux HT/TVA/TTC, signature du profil. Fonction de LECTURE (aucun envoi). Lève KeyError si le
    devis n'existe pas."""
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT * FROM devis WHERE id = ?", (int(devis_id),)).fetchone()
            if row is None:
                raise KeyError(f"devis {devis_id} introuvable")
            d = _devis_dict(row)
            lignes = [_ligne_dict(li) for li in con.execute(
                "SELECT * FROM devis_lignes WHERE devis_id = ? ORDER BY ordre, id",
                (int(devis_id),)).fetchall()]
            lead = con.execute("SELECT * FROM leads WHERE id = ?", (int(d["lead_id"]),)).fetchone()
            lead = _lead_dict(lead) if lead else None
        finally:
            con.close()
    reg = reglages()
    out: list[str] = []
    titre = f"Devis {d['numero']}".strip()
    out.append(titre)
    if lead and lead.get("nom"):
        out.append(f"Client : {lead['nom']}")
    out.append(f"Date : {str(d['created'])[:10]}")
    out.append("")
    for li in lignes:
        ligne_ht = round(float(li["quantite"]) * float(li["prix_unitaire"]), 2)
        out.append(f"- {li['designation']} : {li['quantite']:g} × {_fmt_eur(li['prix_unitaire'])} "
                   f"= {_fmt_eur(ligne_ht)}")
    if not lignes:
        out.append("(aucune ligne)")
    out.append("")
    out.append(f"Total HT : {_fmt_eur(d['total_ht'])}")
    out.append(f"TVA ({float(d['tva_pct']):g} %) : {_fmt_eur(round(d['total_ttc'] - d['total_ht'], 2))}")
    out.append(f"Total TTC : {_fmt_eur(d['total_ttc'])}")
    if d.get("notes"):
        out.append("")
        out.append(d["notes"])
    if reg["signature"]:
        out.append("")
        out.append(reg["signature"])
    return "\n".join(out).strip()


def preparer(devis_id: int, stage: Callable[..., dict] | None = None) -> dict:
    """Prépare un devis à l'envoi : construit son texte (`rendu_texte`), le DÉPOSE comme BROUILLON dans
    la file d'envoi (`outbox.stage`). Rien n'est envoyé : l'utilisateur relit et envoie lui-même depuis le
    HUD. Rend l'identifiant du brouillon.

    `stage` : fonction de dépôt `(channel, text, to=, source=) -> {"ok", "id"?, "error"?}` ; par défaut
    la file d'envoi locale (`outbox.stage`). Le serveur MCP y injecte un dépôt qui passe par le cœur (où
    la file et le canal WhatsApp vivent). Le dépôt se fait HORS verrou `crm._lock` (non réentrant)."""
    depot = stage or outbox.stage
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT id FROM devis WHERE id = ?", (int(devis_id),)).fetchone()
        finally:
            con.close()
    if row is None:
        return {"ok": False, "error": f"devis {devis_id} introuvable"}
    texte = rendu_texte(devis_id)  # prend et rend le verrou ; pas de dépôt tant qu'on le tient
    if not texte:
        return {"ok": False, "error": "ce devis est vide : rien à préparer"}
    res = depot(CANAL_DEFAUT, texte, to="", source="claude")
    if not isinstance(res, dict) or res.get("ok") is not True:
        err = res.get("error") if isinstance(res, dict) else None
        return {"ok": False, "error": str(err or "dépôt du brouillon impossible")}
    return {"ok": True, "brouillon": res.get("id"), "devis_id": int(devis_id), "texte": texte}
