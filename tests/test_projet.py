"""PROJETS freelance liés aux prospects (T90 Projet, incrément suivant, 10/11).

On isole la base dans un fichier temporaire (`crm.DB` monkeypatché : projet réutilise la connexion et le
verrou de crm, et la table `devis` pour une reprise). On vérifie : créer un projet (autonome et depuis un
DEVIS, qui reprend son titre et son montant) ; lister / get / maj ; le changement de statut ; le
récapitulatif lisible ; la validation (montant négatif, date invalide, statut inconnu, champ inconnu) ;
les 4 outils MCP classés au garde-fou (écritures sensibles, lectures libres) et rangés dans le groupe
« clients ». INVARIANT : aucun envoi, aucune facturation.
"""
from __future__ import annotations

import pytest

from astrel import capacites, crm, devis, guard, projet


@pytest.fixture(autouse=True)
def _base_isolee(tmp_path, monkeypatch):
    # projet.py passe par crm._connect / crm._lock : une seule base à isoler.
    monkeypatch.setattr(crm, "DB", tmp_path / "crm.db")


# --------------------------------------------------------------------------- création

def test_creer_projet_autonome():
    lead = crm.ajouter("Boulangerie Durand", ville="Lille")
    p = projet.creer(lead["id"], titre="Site vitrine", montant=1500, date_debut="2026-11-01",
                     livrables="Maquette, intégration, mise en ligne")
    assert p["lead_id"] == lead["id"]
    assert p["devis_id"] is None
    assert p["titre"] == "Site vitrine"
    assert p["statut"] == "prospect"  # statut initial
    assert p["montant"] == 1500.0
    assert p["date_debut"] == "2026-11-01"
    assert p["date_fin"] == ""
    assert "Maquette" in p["livrables"]


def test_creer_sans_montant_zero():
    lead = crm.ajouter("Café Central")
    p = projet.creer(lead["id"], titre="Refonte")
    assert p["montant"] == 0.0


def test_creer_prospect_inexistant_refuse():
    with pytest.raises(KeyError):
        projet.creer(99999, titre="X")


# --------------------------------------------------------------------------- naissance depuis un devis

def test_creer_depuis_un_devis_reprend_titre_et_montant():
    lead = crm.ajouter("Restaurant Soleil")
    d = devis.creer(lead["id"], lignes=[
        {"designation": "Refonte site", "quantite": 1, "prix_unitaire": 3000},
        {"designation": "Maintenance", "quantite": 6, "prix_unitaire": 50},
    ], tva_pct=10)
    p = projet.creer(lead["id"], devis_id=d["id"])
    assert p["devis_id"] == d["id"]
    # titre repris = numéro du devis ; montant repris = total TTC du devis (3300 * 1.10 = 3630)
    assert p["titre"] == d["numero"]
    assert p["montant"] == d["total_ttc"] == 3630.0


def test_creer_depuis_un_devis_titre_et_montant_donnes_priment():
    lead = crm.ajouter("Garage Petit")
    d = devis.creer(lead["id"], lignes=[{"designation": "Base", "quantite": 1, "prix_unitaire": 1000}],
                    tva_pct=20)
    p = projet.creer(lead["id"], devis_id=d["id"], titre="Chantier garage", montant=999)
    assert p["titre"] == "Chantier garage"  # le titre donné prime sur le numéro du devis
    assert p["montant"] == 999.0            # le montant donné prime sur le total du devis


def test_creer_depuis_un_devis_inexistant_refuse():
    lead = crm.ajouter("Menuiserie Bois")
    with pytest.raises(KeyError):
        projet.creer(lead["id"], devis_id=99999)


# --------------------------------------------------------------------------- lister / get

def test_lister_et_get():
    lead = crm.ajouter("Café Lumière")
    autre = crm.ajouter("Autre client")
    p1 = projet.creer(lead["id"], titre="A")
    projet.creer(autre["id"], titre="B")
    tous = projet.lister()
    assert len(tous) == 2
    du_lead = projet.lister(lead_id=lead["id"])
    assert len(du_lead) == 1 and du_lead[0]["id"] == p1["id"]
    fiche = projet.get(p1["id"])
    assert fiche["id"] == p1["id"] and fiche["titre"] == "A"
    assert projet.get(99999) is None


# --------------------------------------------------------------------------- maj et statut

def test_maj_avancement_statut():
    lead = crm.ajouter("Atelier Céramique")
    p = projet.creer(lead["id"], titre="Boutique")
    en_cours = projet.maj(p["id"], statut="en_cours", date_debut="2026-11-01")
    assert en_cours["statut"] == "en_cours"
    assert en_cours["date_debut"] == "2026-11-01"
    livre = projet.maj(p["id"], statut="livré", date_fin="2026-12-15", livrables="Site livré")
    assert livre["statut"] == "livré"
    assert livre["date_fin"] == "2026-12-15"
    assert livre["livrables"] == "Site livré"
    # lister par statut
    assert [x["id"] for x in projet.lister(statut="livré")] == [p["id"]]


def test_maj_montant():
    lead = crm.ajouter("Théâtre")
    p = projet.creer(lead["id"], titre="A", montant=1000)
    maj = projet.maj(p["id"], montant=2500)
    assert maj["montant"] == 2500.0


def test_maj_projet_inexistant():
    with pytest.raises(KeyError):
        projet.maj(99999, statut="clos")


# --------------------------------------------------------------------------- validation

@pytest.mark.parametrize("montant", [-1, -1000.5])
def test_montant_negatif_refuse(montant):
    lead = crm.ajouter("Épicerie Verte")
    with pytest.raises(ValueError):
        projet.creer(lead["id"], titre="X", montant=montant)


def test_date_invalide_refusee():
    lead = crm.ajouter("Librairie Papier")
    with pytest.raises(ValueError):
        projet.creer(lead["id"], titre="X", date_debut="pas une date")


def test_date_fin_invalide_refusee_en_maj():
    lead = crm.ajouter("Fromagerie")
    p = projet.creer(lead["id"], titre="X")
    with pytest.raises(ValueError):
        projet.maj(p["id"], date_fin="31/12/2026")


def test_statut_inconnu_refuse():
    lead = crm.ajouter("Pizzeria")
    p = projet.creer(lead["id"], titre="X")
    with pytest.raises(ValueError):
        projet.maj(p["id"], statut="n_importe_quoi")


def test_maj_champ_inconnu_refuse():
    lead = crm.ajouter("Primeur")
    p = projet.creer(lead["id"], titre="X")
    with pytest.raises(ValueError):
        projet.maj(p["id"], lead_id=42)


# --------------------------------------------------------------------------- récapitulatif

def test_resume_texte_lisible():
    lead = crm.ajouter("Café Lumière", ville="Nice")
    p = projet.creer(lead["id"], titre="Site vitrine", montant=1200,
                     date_debut="2026-11-01", date_fin="2026-12-31", livrables="Maquette puis dev",
                     notes="Client pressé")
    p = projet.maj(p["id"], statut="en_cours")
    txt = projet.resume_texte(p["id"])
    assert "Site vitrine" in txt
    assert "Café Lumière" in txt
    assert "en cours" in txt
    assert "1200,00 €" in txt
    assert "2026-11-01" in txt and "2026-12-31" in txt
    assert "Maquette puis dev" in txt
    assert "Client pressé" in txt


def test_resume_texte_projet_inexistant():
    with pytest.raises(KeyError):
        projet.resume_texte(99999)


# --------------------------------------------------------------------------- garde-fou et capacités

def test_outils_classes_au_garde_fou():
    pol = guard.MCP_POLICY["astrel"]
    # écritures sensibles
    for outil in ("projet_create", "projet_update"):
        assert pol[outil] == guard.SENSITIVE
        v = guard.classify(f"mcp__astrel__{outil}", {})
        assert v.needs_confirm  # une écriture est confirmée (hors autonome)
    # lectures libres
    for outil in ("projet_list", "projet_get"):
        assert pol[outil] == ""
        v = guard.classify(f"mcp__astrel__{outil}", {})
        assert not v.needs_confirm


def test_outils_dans_le_groupe_clients():
    for outil in ("projet_create", "projet_update", "projet_list", "projet_get"):
        assert capacites.groupe_outil("astrel", outil) == "clients"
