"""Séquences de RELANCE (outreach) des prospects (T91, 09/11, 1er incrément).

On isole la base dans un fichier temporaire (`crm.DB` monkeypatché : outreach réutilise la connexion
de crm). On vérifie : démarrer crée des étapes planifiées (dates d'échéance) ; `dues` ne rend que les
étapes « à faire » échues, avec le prospect ; `preparer` DÉPOSE un brouillon dans l'outbox et passe
l'étape en « brouillon » SANS rien envoyer ; `marquer` change le statut ; les 5 outils MCP existent et
sont classés au garde-fou (écritures sensibles, lectures libres). INVARIANT : aucun envoi automatique.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from astrel import capacites, crm, guard, outreach
from astrel.outbox import Outbox, PENDING


@pytest.fixture(autouse=True)
def _base_isolee(tmp_path, monkeypatch):
    # outreach.py passe par crm._connect / crm._lock : une seule base à isoler.
    monkeypatch.setattr(crm, "DB", tmp_path / "crm.db")


@pytest.fixture
def ob(monkeypatch):
    """Outbox neuve avec un canal WhatsApp factice branché, posée comme singleton du module."""
    envois: list[tuple[str, str]] = []
    o = Outbox()
    o.register_channel("whatsapp", "WhatsApp", lambda to, text: envois.append((to, text)) or {"ok": True})
    monkeypatch.setattr(outreach, "outbox", o)
    o.envois = envois  # type: ignore[attr-defined]
    return o


# --------------------------------------------------------------------------- démarrer une séquence

def test_demarrer_cree_des_etapes_planifiees():
    lead = crm.ajouter("Boulangerie Durand", ville="Lille")
    seq = outreach.demarrer(lead["id"], modele="site_web")
    assert seq["lead_id"] == lead["id"]
    assert seq["modele"] == "site_web"
    etapes = seq["etapes"]
    assert [e["numero"] for e in etapes] == [1, 2, 3]
    assert all(e["statut"] == "a_faire" for e in etapes)
    # dates d'échéance = aujourd'hui + délai du modèle (0, 3, 7)
    base = date.today()
    attendus = [(base + timedelta(days=d)).isoformat() for d in (0, 3, 7)]
    assert [e["du_le"] for e in etapes] == attendus
    # lister rend la même séquence avec ses étapes
    liste = outreach.lister(lead["id"])
    assert len(liste) == 1 and len(liste[0]["etapes"]) == 3


def test_demarrer_modele_inconnu_refuse():
    lead = crm.ajouter("Café Central")
    with pytest.raises(ValueError):
        outreach.demarrer(lead["id"], modele="n_importe_quoi")


def test_demarrer_prospect_inexistant_refuse():
    with pytest.raises(KeyError):
        outreach.demarrer(99999, modele="site_web")


# --------------------------------------------------------------------------- relances dues

def test_dues_ne_rend_que_les_echues_a_faire():
    lead = crm.ajouter("Plomberie Martin", ville="Paris")
    outreach.demarrer(lead["id"], modele="site_web")  # étapes à J+0, J+3, J+7
    base = date.today()

    # aujourd'hui : seule l'étape J+0 est due
    due0 = outreach.dues()
    assert [e["numero"] for e in due0] == [1]
    assert due0[0]["prospect"]["nom"] == "Plomberie Martin"  # le prospect accompagne la relance
    assert due0[0]["lead_id"] == lead["id"]

    # dans 5 jours : J+0 et J+3 sont dues, pas J+7
    due5 = outreach.dues(base + timedelta(days=5))
    assert [e["numero"] for e in due5] == [1, 2]

    # dans 10 jours : les trois
    due10 = outreach.dues(base + timedelta(days=10))
    assert [e["numero"] for e in due10] == [1, 2, 3]


def test_dues_exclut_les_etapes_non_a_faire():
    lead = crm.ajouter("Garage Petit")
    seq = outreach.demarrer(lead["id"], modele="relance_simple")  # J+0, J+4
    step1 = seq["etapes"][0]["id"]
    outreach.marquer(step1, "envoye")
    numeros = [e["numero"] for e in outreach.dues(date.today() + timedelta(days=10))]
    assert numeros == [2]  # l'étape 1, passée en « envoye », n'est plus due


# --------------------------------------------------------------------------- préparer un brouillon

def test_preparer_depose_un_brouillon_sans_rien_envoyer(ob):
    lead = crm.ajouter("Restaurant Soleil", ville="Nice")
    seq = outreach.demarrer(lead["id"], modele="site_web")
    step1 = seq["etapes"][0]["id"]

    res = outreach.preparer(step1)
    assert res["ok"] is True
    # un brouillon « en attente » est dans l'outbox, RIEN n'a été envoyé
    en_attente = ob.pending()
    assert len(en_attente) == 1
    d = en_attente[0]
    assert d["state"] == PENDING
    assert d["id"] == res["brouillon"]
    assert "Restaurant Soleil" in d["text"]  # le modèle a été rempli avec le nom du prospect
    assert "Nice" in d["text"]
    assert ob.envois == []  # INVARIANT : aucun envoi automatique

    # l'étape est passée en « brouillon »
    e = outreach.etape(step1)
    assert e["statut"] == "brouillon"


def test_preparer_etape_inconnue():
    res = outreach.preparer(99999)
    assert res["ok"] is False


def test_preparer_refuse_si_deja_envoyee(ob):
    lead = crm.ajouter("Fleuriste Rose")
    seq = outreach.demarrer(lead["id"], modele="relance_simple")
    step1 = seq["etapes"][0]["id"]
    outreach.marquer(step1, "envoye")
    res = outreach.preparer(step1)
    assert res["ok"] is False
    assert ob.pending() == []  # rien déposé


def test_preparer_depot_echoue_ne_marque_pas(ob):
    """Si le dépôt dans la file échoue (canal inconnu), l'étape reste « à faire »."""
    lead = crm.ajouter("Épicerie Verte")
    seq = outreach.demarrer(lead["id"], modele="relance_simple")
    step1 = seq["etapes"][0]["id"]
    res = outreach.preparer(step1, stage=lambda *a, **k: {"ok": False, "error": "canal inconnu"})
    assert res["ok"] is False
    assert outreach.etape(step1)["statut"] == "a_faire"  # pas marqué brouillon


# --------------------------------------------------------------------------- marquer un statut

def test_marquer_change_le_statut():
    lead = crm.ajouter("Menuiserie Bois")
    seq = outreach.demarrer(lead["id"], modele="relance_simple")
    step1 = seq["etapes"][0]["id"]
    maj = outreach.marquer(step1, "repondu")
    assert maj["statut"] == "repondu"
    assert outreach.etape(step1)["statut"] == "repondu"


def test_marquer_statut_inconnu_refuse():
    lead = crm.ajouter("Coiffure Style")
    seq = outreach.demarrer(lead["id"], modele="relance_simple")
    step1 = seq["etapes"][0]["id"]
    with pytest.raises(ValueError):
        outreach.marquer(step1, "n_importe_quoi")


def test_marquer_etape_inexistante_refuse():
    with pytest.raises(KeyError):
        outreach.marquer(99999, "envoye")


# --------------------------------------------------------------------------- remplissage du modèle

def test_remplir_remplace_les_champs_et_ignore_les_manquants():
    txt = outreach.remplir("Bonjour {nom} de {ville}, secteur {secteur}.",
                           {"nom": "Durand", "ville": "Lille"})
    assert txt == "Bonjour Durand de Lille, secteur ."  # champ manquant → vide, pas d'erreur


# --------------------------------------------------------------------------- garde-fou et capacités

def test_outils_classes_au_garde_fou():
    pol = guard.MCP_POLICY["astrel"]
    # écritures sensibles
    for outil in ("outreach_start", "outreach_prepare", "outreach_mark"):
        assert pol[outil] == guard.SENSITIVE
        v = guard.classify(f"mcp__astrel__{outil}", {})
        assert v.needs_confirm  # une écriture est confirmée (hors autonome)
    # lectures libres
    for outil in ("outreach_due", "outreach_list"):
        assert pol[outil] == ""
        v = guard.classify(f"mcp__astrel__{outil}", {})
        assert not v.needs_confirm


def test_outils_dans_le_groupe_clients():
    for outil in ("outreach_start", "outreach_due", "outreach_prepare", "outreach_mark", "outreach_list"):
        assert capacites.groupe_outil("astrel", outil) == "clients"
