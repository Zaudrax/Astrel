"""FACTURES freelance liées aux prospects (T90 Facture, incrément suivant, 09/11).

On isole la base dans un fichier temporaire (`crm.DB` monkeypatché : facture réutilise la connexion et
le verrou de crm, et la table `devis` pour une reprise). On vérifie : créer une facture avec des lignes
et des totaux HT/TTC corrects (avec TVA) ; la naissance depuis un DEVIS (reprend ses lignes et sa TVA) ;
ajouter une ligne recalcule ; lister / get / maj ; les statuts payée / impayée ; l'échéance ;
`preparer` DÉPOSE un brouillon dans l'outbox SANS rien envoyer ; la validation des montants et de la
date ; les 6 outils MCP classés au garde-fou (écritures sensibles, lectures libres) et rangés dans le
groupe « clients ». INVARIANT : aucun envoi automatique.
"""
from __future__ import annotations

import pytest

from astrel import capacites, crm, devis, facture, guard
from astrel.outbox import Outbox, PENDING


@pytest.fixture(autouse=True)
def _base_isolee(tmp_path, monkeypatch):
    # facture.py passe par crm._connect / crm._lock : une seule base à isoler.
    monkeypatch.setattr(crm, "DB", tmp_path / "crm.db")


@pytest.fixture
def ob(monkeypatch):
    """Outbox neuve avec un canal WhatsApp factice branché, posée comme singleton du module."""
    envois: list[tuple[str, str]] = []
    o = Outbox()
    o.register_channel("whatsapp", "WhatsApp", lambda to, text: envois.append((to, text)) or {"ok": True})
    monkeypatch.setattr(facture, "outbox", o)
    o.envois = envois  # type: ignore[attr-defined]
    return o


# --------------------------------------------------------------------------- création et totaux

def test_creer_avec_lignes_totaux_corrects():
    lead = crm.ajouter("Boulangerie Durand", ville="Lille")
    f = facture.creer(lead["id"], lignes=[
        {"designation": "Site vitrine", "quantite": 1, "prix_unitaire": 1200},
        {"designation": "Jours de dev", "quantite": 2, "prix_unitaire": 400},
    ], tva_pct=20)
    assert f["lead_id"] == lead["id"]
    assert f["devis_id"] is None
    assert f["statut"] == "brouillon"
    assert f["numero"].startswith("FAC-")
    assert [li["designation"] for li in f["lignes"]] == ["Site vitrine", "Jours de dev"]
    # HT = 1*1200 + 2*400 = 2000 ; TTC = 2000 * 1.20 = 2400
    assert f["total_ht"] == 2000.0
    assert f["total_ttc"] == 2400.0


def test_creer_tva_zero_ht_egale_ttc():
    lead = crm.ajouter("Café Central")
    f = facture.creer(lead["id"], lignes=[{"designation": "Forfait", "quantite": 3, "prix_unitaire": 49.90}],
                      tva_pct=0)
    assert f["total_ht"] == 149.70
    assert f["total_ttc"] == 149.70  # TVA 0 % : TTC = HT


def test_creer_sans_lignes_totaux_zero():
    lead = crm.ajouter("Fleuriste Rose")
    f = facture.creer(lead["id"])
    assert f["lignes"] == []
    assert f["total_ht"] == 0.0 and f["total_ttc"] == 0.0


def test_creer_avec_echeance():
    lead = crm.ajouter("Hôtel du Parc")
    f = facture.creer(lead["id"], echeance="2026-12-31")
    assert f["echeance"] == "2026-12-31"


def test_creer_prospect_inexistant_refuse():
    with pytest.raises(KeyError):
        facture.creer(99999, lignes=[{"designation": "X", "quantite": 1, "prix_unitaire": 10}])


def test_calculer_totaux_pur():
    ht, ttc = facture.calculer_totaux(
        [{"quantite": 2, "prix_unitaire": 300}, {"quantite": 1.5, "prix_unitaire": 100}], 20)
    assert ht == 750.0  # 600 + 150
    assert ttc == 900.0  # 750 * 1.20


# --------------------------------------------------------------------------- naissance depuis un devis

def test_creer_depuis_un_devis_reprend_lignes_et_tva():
    lead = crm.ajouter("Restaurant Soleil")
    d = devis.creer(lead["id"], lignes=[
        {"designation": "Refonte site", "quantite": 1, "prix_unitaire": 3000},
        {"designation": "Maintenance", "quantite": 6, "prix_unitaire": 50},
    ], tva_pct=10)
    f = facture.creer(lead["id"], devis_id=d["id"])
    assert f["devis_id"] == d["id"]
    # lignes reprises du devis
    assert [li["designation"] for li in f["lignes"]] == ["Refonte site", "Maintenance"]
    # TVA reprise du devis (10 %) : HT = 3000 + 300 = 3300 ; TTC = 3630
    assert f["tva_pct"] == 10.0
    assert f["total_ht"] == 3300.0
    assert f["total_ttc"] == 3630.0


def test_creer_depuis_un_devis_plus_lignes_ajoutees():
    lead = crm.ajouter("Garage Petit")
    d = devis.creer(lead["id"], lignes=[{"designation": "Base", "quantite": 1, "prix_unitaire": 1000}],
                    tva_pct=20)
    f = facture.creer(lead["id"], devis_id=d["id"],
                      lignes=[{"designation": "Extra", "quantite": 1, "prix_unitaire": 200}])
    assert [li["designation"] for li in f["lignes"]] == ["Base", "Extra"]
    assert f["total_ht"] == 1200.0  # 1000 + 200
    assert f["total_ttc"] == 1440.0  # 1200 * 1.20


def test_creer_depuis_un_devis_tva_surchargee():
    lead = crm.ajouter("Coiffure Style")
    d = devis.creer(lead["id"], lignes=[{"designation": "A", "quantite": 1, "prix_unitaire": 1000}],
                    tva_pct=20)
    f = facture.creer(lead["id"], devis_id=d["id"], tva_pct=0)  # la TVA donnée prime sur celle du devis
    assert f["tva_pct"] == 0.0
    assert f["total_ht"] == 1000.0 and f["total_ttc"] == 1000.0


def test_creer_depuis_un_devis_inexistant_refuse():
    lead = crm.ajouter("Menuiserie Bois")
    with pytest.raises(KeyError):
        facture.creer(lead["id"], devis_id=99999)


# --------------------------------------------------------------------------- ajout de ligne

def test_ajouter_ligne_recalcule():
    lead = crm.ajouter("Plomberie Martin")
    f = facture.creer(lead["id"], lignes=[{"designation": "Base", "quantite": 1, "prix_unitaire": 500}],
                      tva_pct=10)
    f2 = facture.ajouter_ligne(f["id"], "Option", quantite=2, prix_unitaire=100)
    assert len(f2["lignes"]) == 2
    assert f2["total_ht"] == 700.0  # 500 + 200
    assert f2["total_ttc"] == 770.0  # 700 * 1.10


def test_ajouter_ligne_facture_inexistante():
    with pytest.raises(KeyError):
        facture.ajouter_ligne(99999, "X", quantite=1, prix_unitaire=1)


# --------------------------------------------------------------------------- validation

@pytest.mark.parametrize("qte,pu", [(-1, 10), (1, -10)])
def test_montants_negatifs_refuses(qte, pu):
    lead = crm.ajouter("Épicerie Verte")
    with pytest.raises(ValueError):
        facture.creer(lead["id"], lignes=[{"designation": "X", "quantite": qte, "prix_unitaire": pu}])


def test_designation_vide_refusee():
    lead = crm.ajouter("Boucherie")
    with pytest.raises(ValueError):
        facture.ajouter_ligne(facture.creer(lead["id"])["id"], "   ", quantite=1, prix_unitaire=1)


def test_tva_hors_bornes_refusee():
    lead = crm.ajouter("Pizzeria")
    with pytest.raises(ValueError):
        facture.creer(lead["id"], tva_pct=150)


def test_echeance_invalide_refusee():
    lead = crm.ajouter("Librairie Papier")
    with pytest.raises(ValueError):
        facture.creer(lead["id"], echeance="pas une date")


# --------------------------------------------------------------------------- lister / get / maj

def test_lister_et_get():
    lead = crm.ajouter("Café Lumière")
    autre = crm.ajouter("Autre client")
    f1 = facture.creer(lead["id"], lignes=[{"designation": "A", "quantite": 1, "prix_unitaire": 100}])
    facture.creer(autre["id"])
    toutes = facture.lister()
    assert len(toutes) == 2
    du_lead = facture.lister(lead_id=lead["id"])
    assert len(du_lead) == 1 and du_lead[0]["id"] == f1["id"]
    fiche = facture.get(f1["id"])
    assert fiche["id"] == f1["id"] and len(fiche["lignes"]) == 1
    assert facture.get(99999) is None


def test_maj_statut_paye_impaye():
    lead = crm.ajouter("Atelier Céramique")
    f = facture.creer(lead["id"], lignes=[{"designation": "A", "quantite": 1, "prix_unitaire": 1000}],
                      tva_pct=20)
    emise = facture.maj(f["id"], statut="émise", echeance="2026-12-01")
    assert emise["statut"] == "émise"
    assert emise["echeance"] == "2026-12-01"
    payee = facture.maj(f["id"], statut="payée")
    assert payee["statut"] == "payée"
    impayee = facture.maj(f["id"], statut="impayée")
    assert impayee["statut"] == "impayée"
    # lister par statut
    assert [x["id"] for x in facture.lister(statut="impayée")] == [f["id"]]


def test_maj_tva_recalcule():
    lead = crm.ajouter("Théâtre")
    f = facture.creer(lead["id"], lignes=[{"designation": "A", "quantite": 1, "prix_unitaire": 1000}],
                      tva_pct=20)
    assert f["total_ttc"] == 1200.0
    maj = facture.maj(f["id"], tva_pct=10)
    assert maj["total_ht"] == 1000.0
    assert maj["total_ttc"] == 1100.0  # TVA passée à 10 % : TTC recalculé


def test_maj_champ_inconnu_refuse():
    lead = crm.ajouter("Fromagerie")
    f = facture.creer(lead["id"])
    with pytest.raises(ValueError):
        facture.maj(f["id"], lead_id=42)


def test_maj_statut_inconnu_refuse():
    lead = crm.ajouter("Primeur")
    f = facture.creer(lead["id"])
    with pytest.raises(ValueError):
        facture.maj(f["id"], statut="n_importe_quoi")


def test_maj_facture_inexistante():
    with pytest.raises(KeyError):
        facture.maj(99999, statut="payée")


# --------------------------------------------------------------------------- rendu texte

def test_rendu_texte_lisible():
    lead = crm.ajouter("Café Lumière", ville="Nice")
    f = facture.creer(lead["id"], lignes=[{"designation": "Site vitrine", "quantite": 1, "prix_unitaire": 1200}],
                      tva_pct=20, echeance="2026-12-31")
    txt = facture.rendu_texte(f["id"])
    assert f["numero"] in txt
    assert "Café Lumière" in txt
    assert "Site vitrine" in txt
    assert "Échéance : 2026-12-31" in txt
    assert "Total HT" in txt and "Total TTC" in txt


# --------------------------------------------------------------------------- préparer (outbox)

def test_preparer_depose_un_brouillon_sans_rien_envoyer(ob):
    lead = crm.ajouter("Hôtel Bellevue", ville="Annecy")
    f = facture.creer(lead["id"], lignes=[{"designation": "Refonte site", "quantite": 1, "prix_unitaire": 3000}],
                      tva_pct=20)
    res = facture.preparer(f["id"])
    assert res["ok"] is True
    # un brouillon « en attente » est dans l'outbox, RIEN n'a été envoyé
    en_attente = ob.pending()
    assert len(en_attente) == 1
    draft = en_attente[0]
    assert draft["state"] == PENDING
    assert draft["id"] == res["brouillon"]
    assert f["numero"] in draft["text"]
    assert "Refonte site" in draft["text"]
    assert ob.envois == []  # INVARIANT : aucun envoi automatique


def test_preparer_facture_inexistante(ob):
    res = facture.preparer(99999)
    assert res["ok"] is False
    assert ob.pending() == []  # rien déposé


def test_preparer_depot_echoue(ob):
    """Si le dépôt dans la file échoue (canal inconnu), rien n'est envoyé."""
    lead = crm.ajouter("Librairie Papier")
    f = facture.creer(lead["id"], lignes=[{"designation": "X", "quantite": 1, "prix_unitaire": 10}])
    res = facture.preparer(f["id"], stage=lambda *a, **k: {"ok": False, "error": "canal inconnu"})
    assert res["ok"] is False
    assert ob.envois == []


# --------------------------------------------------------------------------- garde-fou et capacités

def test_outils_classes_au_garde_fou():
    pol = guard.MCP_POLICY["astrel"]
    # écritures sensibles
    for outil in ("facture_create", "facture_add_line", "facture_update", "facture_prepare"):
        assert pol[outil] == guard.SENSITIVE
        v = guard.classify(f"mcp__astrel__{outil}", {})
        assert v.needs_confirm  # une écriture est confirmée (hors autonome)
    # lectures libres
    for outil in ("facture_list", "facture_get"):
        assert pol[outil] == ""
        v = guard.classify(f"mcp__astrel__{outil}", {})
        assert not v.needs_confirm


def test_outils_dans_le_groupe_clients():
    for outil in ("facture_create", "facture_add_line", "facture_list", "facture_get",
                  "facture_update", "facture_prepare"):
        assert capacites.groupe_outil("astrel", outil) == "clients"
