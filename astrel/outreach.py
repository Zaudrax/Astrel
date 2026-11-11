"""Séquences de RELANCE (outreach) des prospects du CRM freelance LOCAL (09/11, carte T91, 1er
incrément validé par l'utilisateur).

Une SÉQUENCE lie un prospect (`lead_id` de `crm.py`) à un MODÈLE de relance : une suite d'ÉTAPES
planifiées (numéro, délai en jours, date d'échéance `du_le`, texte modèle). L'utilisateur (ou Astrel à sa
demande) peut lister les relances DUES (« quelles relances sont dues ? »), puis PRÉPARER la relance
d'une étape : son texte est construit depuis le modèle + les champs du prospect, puis DÉPOSÉ dans la
file d'envoi (`outbox.py`) comme BROUILLON. L'utilisateur le relit, l'édite, puis l'ENVOIE lui-même.

INVARIANT ABSOLU : aucun envoi automatique, aucun contact d'un prospect sans le clic de l'utilisateur au
HUD. Ce module n'envoie RIEN et ne fait AUCUN I/O réseau : il range des étapes dans `runtime/crm.db`
et dépose des brouillons dans l'outbox (la SEULE voie de sortie, qui exige l'« Accepter » de
l'utilisateur). Pas de déclencheur autonome : `dues()` LISTE seulement, `preparer()` est lancé à la
demande ; rien ne prépare ni n'envoie tout seul.

Stockage dans la MÊME base que les prospects (`runtime/crm.db`) : on RÉUTILISE la connexion et le
verrou de `crm.py` (`crm._connect`, `crm._lock`), on ajoute juste les tables de relance. Comme
`crm._lock` n'est pas réentrant, ce module ne rappelle JAMAIS une fonction de `crm` sous le verrou :
il lit la table `leads` en direct sur sa propre connexion.

Modèle inspiré du projet perso MaestroAI de l'utilisateur (`FlOutreachSequence` : leadId, step,
scheduledAt, status) : l'idée d'une séquence d'étapes planifiées en est reprise, le code est réécrit
pour Astrel (pas d'automatisation d'envoi : MaestroAI envoyait, Astrel ne fait que PROPOSER).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from . import crm
from .outbox import outbox

# Canal d'envoi par défaut d'un brouillon de relance (chat « message à soi-même », filtré par le cœur).
CANAL_DEFAUT = "whatsapp"

# Statuts d'une étape de relance, dans l'ordre de vie.
STATUTS = ("a_faire", "brouillon", "envoye", "repondu")
STATUT_DEFAUT = "a_faire"

# --------------------------------------------------------------------------- modèles de relance
# Un MODÈLE = une suite d'étapes (numéro, délai en jours depuis le démarrage, texte modèle). Le texte
# est une DONNÉE : des champs {nom}, {ville}, {secteur}… y sont remplacés par la fiche du prospect au
# moment de PRÉPARER le brouillon (jamais avant, jamais seul). Textes courts, polis, en français ;
# l'utilisateur les relit et les édite dans l'outbox avant d'envoyer.
MODELES: dict[str, dict[str, Any]] = {
    "site_web": {
        "titre": "Prospection site web (3 relances)",
        "etapes": [
            {"numero": 1, "delai_jours": 0, "texte_modele":
                "Bonjour, je suis développeur web indépendant et j'ai remarqué que {nom} "
                "n'a pas encore de site internet. Je crée des sites simples et efficaces pour "
                "les commerces de {ville}. Est-ce que ce serait utile d'en discuter cinq minutes ?"},
            {"numero": 2, "delai_jours": 3, "texte_modele":
                "Bonjour, je me permets de revenir vers {nom} au sujet d'un site internet. "
                "Je peux vous montrer quelques exemples de réalisations pour des {secteur}. "
                "Quand seriez-vous disponible pour un court échange ?"},
            {"numero": 3, "delai_jours": 7, "texte_modele":
                "Bonjour, dernier message de ma part pour {nom} : si un site internet vous "
                "intéresse un jour, n'hésitez pas à me recontacter, je reste disponible. "
                "Bonne continuation !"},
        ],
    },
    "relance_simple": {
        "titre": "Relance simple (2 messages)",
        "etapes": [
            {"numero": 1, "delai_jours": 0, "texte_modele":
                "Bonjour, je reviens vers {nom} suite à notre premier contact. "
                "Avez-vous eu le temps d'y réfléchir ? Je reste à votre disposition."},
            {"numero": 2, "delai_jours": 4, "texte_modele":
                "Bonjour, petit rappel amical à l'attention de {nom}. "
                "Dites-moi si le moment n'est pas opportun, je me permettrai de revenir plus tard."},
        ],
    },
}
MODELE_DEFAUT = "site_web"


# --------------------------------------------------------------------------- base (tables de relance)

OUTREACH_SCHEMA = """
CREATE TABLE IF NOT EXISTS outreach (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id  INTEGER NOT NULL,
    modele   TEXT NOT NULL,
    created  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outreach_steps (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_id  INTEGER NOT NULL,
    numero       INTEGER NOT NULL,
    delai_jours  INTEGER NOT NULL,
    statut       TEXT NOT NULL DEFAULT 'a_faire',
    du_le        TEXT NOT NULL,
    texte_modele TEXT NOT NULL DEFAULT '',
    created      TEXT NOT NULL,
    updated      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outreach_lead ON outreach(lead_id);
CREATE INDEX IF NOT EXISTS idx_steps_sequence ON outreach_steps(sequence_id);
"""

_SEQ_COLS = ("id", "lead_id", "modele", "created")
_STEP_COLS = ("id", "sequence_id", "numero", "delai_jours", "statut", "du_le", "texte_modele",
              "created", "updated")
# Champs du prospect montrés avec une relance due (jamais un secret : ses propres données clients).
_LEAD_COLS = ("id", "nom", "telephone", "email", "site_web", "ville", "secteur", "statut", "score")


def _connect():
    """Connexion à `runtime/crm.db` (celle de `crm.py`, tables `leads` créées) + les tables de
    relance. À appeler SOUS `crm._lock` comme les fonctions de `crm`."""
    con = crm._connect()
    con.executescript(OUTREACH_SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _aujourdhui() -> date:
    return datetime.now(timezone.utc).date()


def _comme_date(valeur: Any) -> date:
    """Normalise `maintenant` (None = aujourd'hui, date, datetime ou 'YYYY-MM-DD') en date."""
    if valeur is None:
        return _aujourdhui()
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    return date.fromisoformat(str(valeur)[:10])


def _seq_dict(row) -> dict:
    return {k: row[k] for k in _SEQ_COLS}


def _step_dict(row) -> dict:
    return {k: row[k] for k in _STEP_COLS}


def _lead_dict(row) -> dict:
    return {k: row[k] for k in _LEAD_COLS}


# --------------------------------------------------------------------------- remplissage du modèle

class _Defaut(dict):
    """Champ manquant d'un modèle → chaîne vide (jamais de KeyError sur un prospect incomplet)."""

    def __missing__(self, key: str) -> str:
        return ""


def remplir(texte_modele: str, lead: dict) -> str:
    """Construit le texte d'une relance : remplace {nom}, {ville}, {secteur}… par la fiche du
    prospect. Fonction PURE (aucune écriture, aucun envoi). Les valeurs du prospect ne sont pas
    reformatées (`format_map` n'agit que sur les champs du modèle), donc sûres."""
    champs = {k: str(lead.get(k) or "").strip() for k in
              ("nom", "telephone", "email", "site_web", "ville", "secteur", "source", "statut")}
    try:
        return str(texte_modele or "").format_map(_Defaut(champs)).strip()
    except (ValueError, IndexError):  # accolade non nommée dans un modèle : rendu brut
        return str(texte_modele or "").strip()


# --------------------------------------------------------------------------- séquences et étapes

def demarrer(lead_id: int, modele: str = MODELE_DEFAUT) -> dict:
    """Démarre une séquence de relance pour un prospect : crée la séquence et PLANIFIE ses étapes
    (chacune avec sa date d'échéance `du_le` = aujourd'hui + délai). Ne contacte personne, ne prépare
    aucun brouillon : seulement la planification. Lève ValueError si le modèle est inconnu, KeyError
    si le prospect n'existe pas. Rend la séquence avec ses étapes."""
    modele = str(modele or "").strip() or MODELE_DEFAUT
    if modele not in MODELES:
        raise ValueError(f"modèle de relance inconnu : {modele!r} (un de {', '.join(MODELES)})")
    base = _aujourdhui()
    now = _now()
    with crm._lock:
        con = _connect()
        try:
            lead = con.execute("SELECT id FROM leads WHERE id = ?", (int(lead_id),)).fetchone()
            if lead is None:
                raise KeyError(f"prospect {lead_id} introuvable")
            cur = con.execute("INSERT INTO outreach (lead_id, modele, created) VALUES (?, ?, ?)",
                              (int(lead_id), modele, now))
            seq_id = cur.lastrowid
            for etape in MODELES[modele]["etapes"]:
                du_le = (base + timedelta(days=int(etape["delai_jours"]))).isoformat()
                con.execute(
                    "INSERT INTO outreach_steps (sequence_id, numero, delai_jours, statut, du_le, "
                    "texte_modele, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (seq_id, int(etape["numero"]), int(etape["delai_jours"]), STATUT_DEFAUT, du_le,
                     str(etape["texte_modele"]), now, now))
            con.commit()
            seq = con.execute("SELECT * FROM outreach WHERE id = ?", (seq_id,)).fetchone()
            steps = con.execute(
                "SELECT * FROM outreach_steps WHERE sequence_id = ? ORDER BY numero", (seq_id,)
            ).fetchall()
        finally:
            con.close()
    out = _seq_dict(seq)
    out["etapes"] = [_step_dict(s) for s in steps]
    return out


def dues(maintenant: Any = None) -> list[dict]:
    """Étapes de relance DUES : celles « à faire » dont l'échéance `du_le` est atteinte
    (`du_le` <= `maintenant`, par défaut aujourd'hui), avec le prospect concerné. LISTE seulement :
    rien n'est préparé ni envoyé. De la plus en retard à la plus récente."""
    jour = _comme_date(maintenant).isoformat()
    with crm._lock:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT s.*, o.lead_id AS o_lead_id, o.modele AS o_modele "
                "FROM outreach_steps s JOIN outreach o ON o.id = s.sequence_id "
                "WHERE s.statut = ? AND s.du_le <= ? "
                "ORDER BY s.du_le ASC, s.id ASC",
                (STATUT_DEFAUT, jour)).fetchall()
            out: list[dict] = []
            for r in rows:
                step = _step_dict(r)
                step["modele"] = r["o_modele"]
                step["lead_id"] = r["o_lead_id"]
                lead = con.execute("SELECT * FROM leads WHERE id = ?", (r["o_lead_id"],)).fetchone()
                step["prospect"] = _lead_dict(lead) if lead else None
                out.append(step)
        finally:
            con.close()
    return out


def lister(lead_id: int) -> list[dict]:
    """Séquences de relance d'un prospect (avec leurs étapes), de la plus récente à la plus ancienne."""
    with crm._lock:
        con = _connect()
        try:
            seqs = con.execute("SELECT * FROM outreach WHERE lead_id = ? ORDER BY id DESC",
                               (int(lead_id),)).fetchall()
            out: list[dict] = []
            for seq in seqs:
                d = _seq_dict(seq)
                steps = con.execute("SELECT * FROM outreach_steps WHERE sequence_id = ? ORDER BY numero",
                                    (seq["id"],)).fetchall()
                d["etapes"] = [_step_dict(s) for s in steps]
                out.append(d)
        finally:
            con.close()
    return out


def etape(step_id: int) -> dict | None:
    """Une étape de relance par son identifiant (+ le `lead_id` de sa séquence), ou None."""
    with crm._lock:
        con = _connect()
        try:
            row = con.execute(
                "SELECT s.*, o.lead_id AS o_lead_id FROM outreach_steps s "
                "JOIN outreach o ON o.id = s.sequence_id WHERE s.id = ?", (int(step_id),)).fetchone()
        finally:
            con.close()
    if row is None:
        return None
    d = _step_dict(row)
    d["lead_id"] = row["o_lead_id"]
    return d


def marquer(step_id: int, statut: str) -> dict:
    """Change le statut d'une étape (a_faire, brouillon, envoye, repondu). Ne contacte personne :
    « envoye » note seulement qu'une relance est partie (l'envoi réel reste le clic de l'utilisateur au
    HUD, via l'outbox). Lève ValueError sur un statut inconnu, KeyError si l'étape n'existe pas."""
    statut = str(statut or "").strip()
    if statut not in STATUTS:
        raise ValueError(f"statut inconnu : {statut!r} (un de {', '.join(STATUTS)})")
    with crm._lock:
        con = _connect()
        try:
            row = con.execute("SELECT id FROM outreach_steps WHERE id = ?", (int(step_id),)).fetchone()
            if row is None:
                raise KeyError(f"étape de relance {step_id} introuvable")
            con.execute("UPDATE outreach_steps SET statut = ?, updated = ? WHERE id = ?",
                        (statut, _now(), int(step_id)))
            con.commit()
            row = con.execute("SELECT * FROM outreach_steps WHERE id = ?", (int(step_id),)).fetchone()
        finally:
            con.close()
    return _step_dict(row)


def preparer(step_id: int, stage: Callable[..., dict] | None = None) -> dict:
    """Prépare la relance d'une étape : construit le texte (modèle + fiche du prospect), le DÉPOSE
    comme BROUILLON dans la file d'envoi (`outbox.stage`), puis passe l'étape en « brouillon ». Rien
    n'est envoyé : l'utilisateur relit et envoie lui-même depuis le HUD. Rend l'identifiant du brouillon.

    `stage` : fonction de dépôt `(channel, text, to=, source=) -> {"ok", "id"?, "error"?}` ; par
    défaut la file d'envoi locale (`outbox.stage`). Le serveur MCP y injecte un dépôt qui passe par le
    cœur (où la file et le canal WhatsApp vivent). Le brouillon n'est PAS marqué si le dépôt échoue."""
    depot = stage or outbox.stage
    e = etape(step_id)
    if e is None:
        return {"ok": False, "error": f"étape de relance {step_id} introuvable"}
    if e["statut"] not in ("a_faire", "brouillon"):
        return {"ok": False, "error": f"cette étape est déjà « {e['statut']} » : rien à préparer"}
    with crm._lock:
        con = _connect()
        try:
            lead = con.execute("SELECT * FROM leads WHERE id = ?", (int(e["lead_id"]),)).fetchone()
        finally:
            con.close()
    if lead is None:
        return {"ok": False, "error": f"prospect {e['lead_id']} introuvable"}
    texte = remplir(e["texte_modele"], _lead_dict(lead))
    if not texte:
        return {"ok": False, "error": "le modèle de cette étape est vide : rien à préparer"}
    res = depot(CANAL_DEFAUT, texte, to="", source="claude")
    if not isinstance(res, dict) or res.get("ok") is not True:
        err = res.get("error") if isinstance(res, dict) else None
        return {"ok": False, "error": str(err or "dépôt du brouillon impossible")}
    marquer(step_id, "brouillon")
    return {"ok": True, "brouillon": res.get("id"), "step_id": int(step_id),
            "statut": "brouillon", "texte": texte}
