"""CRM freelance local : prospects (leads) stockés dans runtime/crm.db (T90, 09/11).

On isole la base dans un fichier temporaire par test (`crm.DB` monkeypatché), on vérifie le
stockage (ajout, liste, get, mise à jour, persistance après réouverture), le score de
qualification (inspiré de MaestroAI), la présence des 4 outils MCP et leur classement au
garde-fou (écritures sensibles, lectures libres) et dans la page Capacités.
"""
from __future__ import annotations

import pytest

from astrel import capacites, crm, guard


@pytest.fixture(autouse=True)
def _base_isolee(tmp_path, monkeypatch):
    monkeypatch.setattr(crm, "DB", tmp_path / "crm.db")


# --------------------------------------------------------------------------- stockage

def test_ajouter_puis_lister_et_get():
    fiche = crm.ajouter("Boulangerie Durand", telephone="0102030405", ville="Lille", secteur="boulangerie")
    assert fiche["id"] >= 1
    assert fiche["nom"] == "Boulangerie Durand"
    assert fiche["statut"] == "nouveau"  # défaut
    assert fiche["created"] and fiche["updated"]

    tous = crm.lister()
    assert [f["id"] for f in tous] == [fiche["id"]]

    lu = crm.get(fiche["id"])
    assert lu == fiche
    assert crm.get(9999) is None


def test_nom_obligatoire():
    with pytest.raises(ValueError):
        crm.ajouter("   ")


def test_maj_d_un_champ():
    fiche = crm.ajouter("Plomberie Martin", email="")
    maj = crm.maj(fiche["id"], statut="contacté", email="martin@example.com", notes="rappeler lundi")
    assert maj["statut"] == "contacté"
    assert maj["email"] == "martin@example.com"
    assert maj["notes"] == "rappeler lundi"
    assert maj["created"] == fiche["created"]  # created ne bouge pas
    # e-mail ajouté : le score monte (+5)
    assert maj["score"] == fiche["score"] + crm.SCORE_EMAIL


def test_maj_statut_inconnu_refuse():
    fiche = crm.ajouter("Garage Petit")
    with pytest.raises(ValueError):
        crm.maj(fiche["id"], statut="n_importe_quoi")


def test_maj_champ_inconnu_refuse():
    fiche = crm.ajouter("Café Central")
    with pytest.raises(ValueError):
        crm.maj(fiche["id"], id=42)


def test_maj_prospect_absent():
    with pytest.raises(KeyError):
        crm.maj(1234, statut="perdu")


def test_persistance_apres_reouverture():
    fiche = crm.ajouter("Restaurant Soleil", site_web="https://soleil.fr")
    # Une connexion neuve (par appel) relit bien la même base sur le disque.
    relu = crm.get(fiche["id"])
    assert relu["nom"] == "Restaurant Soleil"
    assert relu["site_web"] == "https://soleil.fr"
    assert [f["id"] for f in crm.lister()] == [fiche["id"]]


# --------------------------------------------------------------------------- filtres de la liste

def test_lister_par_statut_et_score():
    a = crm.ajouter("A sans site", telephone="01")      # score élevé (pas de site + tel)
    b = crm.ajouter("B avec site", site_web="http://b")  # score bas (site présent)
    # Tri par score décroissant : A avant B.
    ids = [f["id"] for f in crm.lister()]
    assert ids.index(a["id"]) < ids.index(b["id"])

    crm.maj(b["id"], statut="perdu")
    perdus = crm.lister(statut="perdu")
    assert [f["id"] for f in perdus] == [b["id"]]

    hauts = crm.lister(min_score=a["score"])
    assert a["id"] in [f["id"] for f in hauts]
    assert b["id"] not in [f["id"] for f in hauts]

    assert len(crm.lister(limite=1)) == 1


# --------------------------------------------------------------------------- score de qualification

def test_score_sans_site_ajoute_30():
    base = crm.SCORE_BASE
    fiche = crm.ajouter("Sans site")  # pas de site, pas de tel, pas d'e-mail, pas de note
    assert fiche["score"] == base + crm.SCORE_SANS_SITE
    raison = crm.calculer_score(fiche)[1]
    assert "pas de site web" in raison


def test_score_avec_site_retire_10():
    assert crm.calculer_score({"site_web": "https://x.fr"})[0] == crm.SCORE_BASE + crm.SCORE_AVEC_SITE


def test_score_bonus_telephone_email_note():
    # Pas de site (+30), téléphone (+10), e-mail (+5), excellente note (+10) : 50+30+10+5+10 = 105,
    # borné à 100.
    score, _ = crm.calculer_score({"telephone": "06", "email": "x@y.fr", "note": 4.6})
    brut = (crm.SCORE_BASE + crm.SCORE_SANS_SITE + crm.SCORE_TELEPHONE
            + crm.SCORE_EMAIL + crm.SCORE_BONNE_NOTE)
    assert brut == 105
    assert score == min(100, brut) == 100


def test_score_note_insuffisante_pas_de_bonus():
    assert crm.calculer_score({"note": 4.0})[0] == crm.SCORE_BASE + crm.SCORE_SANS_SITE


def test_score_borne_0_100():
    score, _ = crm.calculer_score({"telephone": "06", "email": "x@y.fr", "note": 5.0})
    assert 0 <= score <= 100


def test_note_virgule_acceptee():
    fiche = crm.ajouter("Note FR", note="4,5")
    assert fiche["note"] == pytest.approx(4.5)
    assert fiche["score"] == crm.SCORE_BASE + crm.SCORE_SANS_SITE + crm.SCORE_BONNE_NOTE


# --------------------------------------------------------------------------- outils MCP et garde-fou

CRM_OUTILS = ("crm_lead_add", "crm_lead_list", "crm_lead_update", "crm_lead_get")


def test_les_4_outils_declares_et_classes():
    chemin = capacites.MCP_SOURCES[0][1]  # mcp_server.py
    noms = {o for o, _ in capacites.outils_mcp(chemin)}
    table = guard.MCP_POLICY["astrel"]
    for outil in CRM_OUTILS:
        assert outil in noms, f"{outil} non déclaré dans mcp_server.py"
        assert outil in table, f"{outil} sans classe dans guard.MCP_POLICY"


def test_classification_garde_fou():
    # Écritures : sensibles (confirmées en prudent). Lectures : libres.
    for ecriture in ("crm_lead_add", "crm_lead_update"):
        v = guard.classify(f"mcp__astrel__{ecriture}", {})
        assert v.needs_confirm and v.level == guard.SENSITIVE, ecriture
    for lecture in ("crm_lead_list", "crm_lead_get"):
        v = guard.classify(f"mcp__astrel__{lecture}", {})
        assert not v.needs_confirm, lecture


def test_outils_dans_la_page_capacites():
    data = capacites.describe("fr")
    groupes = {g["id"]: g for g in data["groupes"]}
    assert "clients" in groupes, "groupe CRM absent de la page Capacités"
    noms = {e["nom"] for e in groupes["clients"]["entrees"]}
    assert set(CRM_OUTILS) <= noms
    # Gravité lue dans le garde-fou, affichée dans la page.
    grav = {e["nom"]: e.get("gravite") for e in groupes["clients"]["entrees"]}
    assert grav["crm_lead_add"] == "sensible"
    assert grav["crm_lead_get"] == "libre"
