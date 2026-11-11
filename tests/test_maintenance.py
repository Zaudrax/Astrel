"""CONTRATS DE MAINTENANCE freelance liés aux prospects (T90 Maintenance, dernier module cœur, 10/11).

On isole la base dans un fichier temporaire (`crm.DB` monkeypatché : maintenance réutilise la connexion et
le verrou de crm, et la table `projets` pour une reprise). On vérifie : créer un contrat (autonome et
depuis un PROJET, qui reprend son client et son titre) ; lister / get / maj ; les changements de statut ;
le récapitulatif lisible ; la validation (montant négatif, date invalide, type / fréquence / statut
inconnus, champ inconnu) ; les 4 outils MCP classés au garde-fou (écritures sensibles, lectures libres) et
rangés dans le NOUVEAU groupe « suivi ». INVARIANT : aucun envoi, aucune facturation, aucune surveillance.
"""
from __future__ import annotations

import pytest

from astrel import capacites, crm, guard, maintenance, projet


@pytest.fixture(autouse=True)
def _base_isolee(tmp_path, monkeypatch):
    # maintenance.py passe par crm._connect / crm._lock : une seule base à isoler.
    monkeypatch.setattr(crm, "DB", tmp_path / "crm.db")


# --------------------------------------------------------------------------- création

def test_creer_contrat_autonome():
    lead = crm.ajouter("Boulangerie Durand", ville="Lille")
    m = maintenance.creer(lead["id"], titre="Maintenance site", type_="mensuel", montant=80,
                          frequence="mensuelle", prochaine_echeance="2026-12-01",
                          notes="Mises à jour + sauvegardes")
    assert m["lead_id"] == lead["id"]
    assert m["projet_id"] is None
    assert m["titre"] == "Maintenance site"
    assert m["type"] == "mensuel"
    assert m["montant"] == 80.0
    assert m["frequence"] == "mensuelle"
    assert m["statut"] == "actif"  # statut initial
    assert m["prochaine_echeance"] == "2026-12-01"
    assert "sauvegardes" in m["notes"]


def test_creer_defauts():
    lead = crm.ajouter("Café Central")
    m = maintenance.creer(lead["id"], titre="Suivi")
    assert m["type"] == "forfait"
    assert m["frequence"] == "mensuelle"
    assert m["statut"] == "actif"
    assert m["montant"] == 0.0
    assert m["prochaine_echeance"] == ""


def test_creer_prospect_inexistant_refuse():
    with pytest.raises(KeyError):
        maintenance.creer(99999, titre="X")


# --------------------------------------------------------------------------- naissance depuis un projet

def test_creer_depuis_un_projet_reprend_client_et_titre():
    lead = crm.ajouter("Restaurant Soleil")
    p = projet.creer(lead["id"], titre="Refonte site", montant=3000)
    m = maintenance.creer(lead["id"], projet_id=p["id"], type_="annuel", montant=400)
    assert m["projet_id"] == p["id"]
    assert m["lead_id"] == lead["id"]       # client repris du projet
    assert m["titre"] == "Refonte site"     # titre repris du projet
    assert m["montant"] == 400.0


def test_creer_depuis_un_projet_reprend_le_client_meme_si_lead_id_different():
    lead = crm.ajouter("Vrai Client")
    autre = crm.ajouter("Autre Client")
    p = projet.creer(lead["id"], titre="Boutique")
    # lead_id passé = autre, mais projet_id donné : le client EST celui du projet (reprise).
    m = maintenance.creer(autre["id"], projet_id=p["id"])
    assert m["lead_id"] == lead["id"]
    assert m["titre"] == "Boutique"


def test_creer_depuis_un_projet_titre_donne_prime():
    lead = crm.ajouter("Garage Petit")
    p = projet.creer(lead["id"], titre="Chantier garage")
    m = maintenance.creer(lead["id"], projet_id=p["id"], titre="Contrat garage")
    assert m["titre"] == "Contrat garage"  # le titre donné prime sur celui du projet


def test_creer_depuis_un_projet_inexistant_refuse():
    lead = crm.ajouter("Menuiserie Bois")
    with pytest.raises(KeyError):
        maintenance.creer(lead["id"], projet_id=99999)


# --------------------------------------------------------------------------- lister / get

def test_lister_et_get():
    lead = crm.ajouter("Café Lumière")
    autre = crm.ajouter("Autre client")
    m1 = maintenance.creer(lead["id"], titre="A")
    maintenance.creer(autre["id"], titre="B")
    tous = maintenance.lister()
    assert len(tous) == 2
    du_lead = maintenance.lister(lead_id=lead["id"])
    assert len(du_lead) == 1 and du_lead[0]["id"] == m1["id"]
    fiche = maintenance.get(m1["id"])
    assert fiche["id"] == m1["id"] and fiche["titre"] == "A"
    assert maintenance.get(99999) is None


# --------------------------------------------------------------------------- maj et statut

def test_maj_suspendre_puis_terminer():
    lead = crm.ajouter("Atelier Céramique")
    m = maintenance.creer(lead["id"], titre="Boutique", montant=100)
    suspendu = maintenance.maj(m["id"], statut="suspendu")
    assert suspendu["statut"] == "suspendu"
    termine = maintenance.maj(m["id"], statut="termine", prochaine_echeance="2027-01-01")
    assert termine["statut"] == "termine"
    assert termine["prochaine_echeance"] == "2027-01-01"
    # lister par statut
    assert [x["id"] for x in maintenance.lister(statut="termine")] == [m["id"]]


def test_maj_type_frequence_montant():
    lead = crm.ajouter("Théâtre")
    m = maintenance.creer(lead["id"], titre="A", type_="forfait", montant=1000)
    # maj prend les noms de colonnes (« type », pas « type_ ») : on passe par **champs.
    maj = maintenance.maj(m["id"], **{"type": "horaire", "frequence": "trimestrielle", "montant": 2500})
    assert maj["type"] == "horaire"
    assert maj["frequence"] == "trimestrielle"
    assert maj["montant"] == 2500.0


def test_maj_contrat_inexistant():
    with pytest.raises(KeyError):
        maintenance.maj(99999, statut="suspendu")


# --------------------------------------------------------------------------- validation

@pytest.mark.parametrize("montant", [-1, -1000.5])
def test_montant_negatif_refuse(montant):
    lead = crm.ajouter("Épicerie Verte")
    with pytest.raises(ValueError):
        maintenance.creer(lead["id"], titre="X", montant=montant)


def test_date_invalide_refusee():
    lead = crm.ajouter("Librairie Papier")
    with pytest.raises(ValueError):
        maintenance.creer(lead["id"], titre="X", prochaine_echeance="pas une date")


def test_date_invalide_refusee_en_maj():
    lead = crm.ajouter("Fromagerie")
    m = maintenance.creer(lead["id"], titre="X")
    with pytest.raises(ValueError):
        maintenance.maj(m["id"], prochaine_echeance="31/12/2026")


def test_type_inconnu_refuse():
    lead = crm.ajouter("Pizzeria")
    with pytest.raises(ValueError):
        maintenance.creer(lead["id"], titre="X", type_="n_importe_quoi")


def test_frequence_inconnue_refusee():
    lead = crm.ajouter("Fleuriste")
    with pytest.raises(ValueError):
        maintenance.creer(lead["id"], titre="X", frequence="hebdomadaire")


def test_statut_inconnu_refuse():
    lead = crm.ajouter("Primeur")
    m = maintenance.creer(lead["id"], titre="X")
    with pytest.raises(ValueError):
        maintenance.maj(m["id"], statut="n_importe_quoi")


def test_maj_champ_inconnu_refuse():
    lead = crm.ajouter("Quincaillerie")
    m = maintenance.creer(lead["id"], titre="X")
    with pytest.raises(ValueError):
        maintenance.maj(m["id"], lead_id=42)


# --------------------------------------------------------------------------- récapitulatif

def test_resume_texte_lisible():
    lead = crm.ajouter("Café Lumière", ville="Nice")
    m = maintenance.creer(lead["id"], titre="Maintenance site", type_="annuel", montant=400,
                          frequence="annuelle", prochaine_echeance="2027-01-15",
                          notes="Hébergement compris")
    m = maintenance.maj(m["id"], statut="suspendu")
    txt = maintenance.resume_texte(m["id"])
    assert "Maintenance site" in txt
    assert "Café Lumière" in txt
    assert "annuel" in txt         # type
    assert "annuelle" in txt       # fréquence
    assert "400,00 €" in txt
    assert "suspendu" in txt
    assert "2027-01-15" in txt
    assert "Hébergement compris" in txt


def test_resume_texte_statut_termine_affiche_accent():
    lead = crm.ajouter("Épicerie")
    m = maintenance.creer(lead["id"], titre="X")
    m = maintenance.maj(m["id"], statut="termine")
    assert "terminé" in maintenance.resume_texte(m["id"])


def test_resume_texte_contrat_inexistant():
    with pytest.raises(KeyError):
        maintenance.resume_texte(99999)


# --------------------------------------------------------------------------- garde-fou et capacités

def test_outils_classes_au_garde_fou():
    pol = guard.MCP_POLICY["astrel"]
    # écritures sensibles
    for outil in ("maintenance_create", "maintenance_update"):
        assert pol[outil] == guard.SENSITIVE
        v = guard.classify(f"mcp__astrel__{outil}", {})
        assert v.needs_confirm  # une écriture est confirmée (hors autonome)
    # lectures libres
    for outil in ("maintenance_list", "maintenance_get"):
        assert pol[outil] == ""
        v = guard.classify(f"mcp__astrel__{outil}", {})
        assert not v.needs_confirm


def test_outils_dans_le_groupe_suivi():
    for outil in ("maintenance_create", "maintenance_update", "maintenance_list", "maintenance_get"):
        assert capacites.groupe_outil("astrel", outil) == "suivi"
