"""DEVIS freelance liés aux prospects (T90, incrément suivant, 09/11).

On isole la base dans un fichier temporaire (`crm.DB` monkeypatché : devis réutilise la connexion et
le verrou de crm). On vérifie : créer un devis avec des lignes et des totaux HT/TTC corrects (avec
TVA) ; ajouter une ligne recalcule ; lister / get / maj ; le changement de statut ; `preparer` DÉPOSE
un brouillon dans l'outbox SANS rien envoyer ; la validation des montants ; les 6 outils MCP classés
au garde-fou (écritures sensibles, lectures libres) et rangés dans le groupe « clients ». INVARIANT :
aucun envoi automatique.
"""
from __future__ import annotations

import pytest

from astrel import capacites, crm, devis, guard
from astrel.outbox import Outbox, PENDING


@pytest.fixture(autouse=True)
def _base_isolee(tmp_path, monkeypatch):
    # devis.py passe par crm._connect / crm._lock : une seule base à isoler.
    monkeypatch.setattr(crm, "DB", tmp_path / "crm.db")


@pytest.fixture
def ob(monkeypatch):
    """Outbox neuve avec un canal WhatsApp factice branché, posée comme singleton du module."""
    envois: list[tuple[str, str]] = []
    o = Outbox()
    o.register_channel("whatsapp", "WhatsApp", lambda to, text: envois.append((to, text)) or {"ok": True})
    monkeypatch.setattr(devis, "outbox", o)
    o.envois = envois  # type: ignore[attr-defined]
    return o


# --------------------------------------------------------------------------- création et totaux

def test_creer_avec_lignes_totaux_corrects():
    lead = crm.ajouter("Boulangerie Durand", ville="Lille")
    d = devis.creer(lead["id"], lignes=[
        {"designation": "Site vitrine", "quantite": 1, "prix_unitaire": 1200},
        {"designation": "Jours de dev", "quantite": 2, "prix_unitaire": 400},
    ], tva_pct=20)
    assert d["lead_id"] == lead["id"]
    assert d["statut"] == "brouillon"
    assert d["numero"].startswith("DEV-")
    assert [li["designation"] for li in d["lignes"]] == ["Site vitrine", "Jours de dev"]
    # HT = 1*1200 + 2*400 = 2000 ; TTC = 2000 * 1.20 = 2400
    assert d["total_ht"] == 2000.0
    assert d["total_ttc"] == 2400.0


def test_creer_tva_zero_ht_egale_ttc():
    lead = crm.ajouter("Café Central")
    d = devis.creer(lead["id"], lignes=[{"designation": "Forfait", "quantite": 3, "prix_unitaire": 49.90}],
                    tva_pct=0)
    assert d["total_ht"] == 149.70
    assert d["total_ttc"] == 149.70  # TVA 0 % : TTC = HT


def test_creer_sans_lignes_totaux_zero():
    lead = crm.ajouter("Fleuriste Rose")
    d = devis.creer(lead["id"])
    assert d["lignes"] == []
    assert d["total_ht"] == 0.0 and d["total_ttc"] == 0.0


def test_creer_prospect_inexistant_refuse():
    with pytest.raises(KeyError):
        devis.creer(99999, lignes=[{"designation": "X", "quantite": 1, "prix_unitaire": 10}])


def test_calculer_totaux_pur():
    ht, ttc = devis.calculer_totaux(
        [{"quantite": 2, "prix_unitaire": 300}, {"quantite": 1.5, "prix_unitaire": 100}], 20)
    assert ht == 750.0  # 600 + 150
    assert ttc == 900.0  # 750 * 1.20


# --------------------------------------------------------------------------- ajout de ligne

def test_ajouter_ligne_recalcule():
    lead = crm.ajouter("Plomberie Martin")
    d = devis.creer(lead["id"], lignes=[{"designation": "Base", "quantite": 1, "prix_unitaire": 500}],
                    tva_pct=10)
    d2 = devis.ajouter_ligne(d["id"], "Option", quantite=2, prix_unitaire=100)
    assert len(d2["lignes"]) == 2
    assert d2["total_ht"] == 700.0  # 500 + 200
    assert d2["total_ttc"] == 770.0  # 700 * 1.10


def test_ajouter_ligne_devis_inexistant():
    with pytest.raises(KeyError):
        devis.ajouter_ligne(99999, "X", quantite=1, prix_unitaire=1)


# --------------------------------------------------------------------------- validation des montants

@pytest.mark.parametrize("qte,pu", [(-1, 10), (1, -10)])
def test_montants_negatifs_refuses(qte, pu):
    lead = crm.ajouter("Garage Petit")
    with pytest.raises(ValueError):
        devis.creer(lead["id"], lignes=[{"designation": "X", "quantite": qte, "prix_unitaire": pu}])


def test_designation_vide_refusee():
    lead = crm.ajouter("Menuiserie Bois")
    with pytest.raises(ValueError):
        devis.ajouter_ligne(devis.creer(lead["id"])["id"], "   ", quantite=1, prix_unitaire=1)


def test_tva_hors_bornes_refusee():
    lead = crm.ajouter("Coiffure Style")
    with pytest.raises(ValueError):
        devis.creer(lead["id"], tva_pct=150)


# --------------------------------------------------------------------------- lister / get / maj

def test_lister_et_get():
    lead = crm.ajouter("Épicerie Verte")
    autre = crm.ajouter("Autre client")
    d1 = devis.creer(lead["id"], lignes=[{"designation": "A", "quantite": 1, "prix_unitaire": 100}])
    devis.creer(autre["id"])
    tous = devis.lister()
    assert len(tous) == 2
    du_lead = devis.lister(lead_id=lead["id"])
    assert len(du_lead) == 1 and du_lead[0]["id"] == d1["id"]
    fiche = devis.get(d1["id"])
    assert fiche["id"] == d1["id"] and len(fiche["lignes"]) == 1
    assert devis.get(99999) is None


def test_maj_statut_et_tva_recalcule():
    lead = crm.ajouter("Restaurant Soleil")
    d = devis.creer(lead["id"], lignes=[{"designation": "A", "quantite": 1, "prix_unitaire": 1000}],
                    tva_pct=20)
    assert d["total_ttc"] == 1200.0
    maj = devis.maj(d["id"], statut="accepté", tva_pct=10, notes="à relancer")
    assert maj["statut"] == "accepté"
    assert maj["notes"] == "à relancer"
    assert maj["total_ht"] == 1000.0
    assert maj["total_ttc"] == 1100.0  # TVA passée à 10 % : TTC recalculé
    # lister par statut
    assert [x["id"] for x in devis.lister(statut="accepté")] == [d["id"]]


def test_maj_champ_inconnu_refuse():
    lead = crm.ajouter("Boucherie")
    d = devis.creer(lead["id"])
    with pytest.raises(ValueError):
        devis.maj(d["id"], lead_id=42)


def test_maj_statut_inconnu_refuse():
    lead = crm.ajouter("Pizzeria")
    d = devis.creer(lead["id"])
    with pytest.raises(ValueError):
        devis.maj(d["id"], statut="n_importe_quoi")


def test_maj_devis_inexistant():
    with pytest.raises(KeyError):
        devis.maj(99999, statut="envoyé")


# --------------------------------------------------------------------------- rendu texte

def test_rendu_texte_lisible():
    lead = crm.ajouter("Café Lumière", ville="Nice")
    d = devis.creer(lead["id"], lignes=[{"designation": "Site vitrine", "quantite": 1, "prix_unitaire": 1200}],
                    tva_pct=20)
    txt = devis.rendu_texte(d["id"])
    assert d["numero"] in txt
    assert "Café Lumière" in txt
    assert "Site vitrine" in txt
    assert "Total HT" in txt and "Total TTC" in txt


# --------------------------------------------------------------------------- préparer (outbox)

def test_preparer_depose_un_brouillon_sans_rien_envoyer(ob):
    lead = crm.ajouter("Hôtel Bellevue", ville="Annecy")
    d = devis.creer(lead["id"], lignes=[{"designation": "Refonte site", "quantite": 1, "prix_unitaire": 3000}],
                    tva_pct=20)
    res = devis.preparer(d["id"])
    assert res["ok"] is True
    # un brouillon « en attente » est dans l'outbox, RIEN n'a été envoyé
    en_attente = ob.pending()
    assert len(en_attente) == 1
    draft = en_attente[0]
    assert draft["state"] == PENDING
    assert draft["id"] == res["brouillon"]
    assert d["numero"] in draft["text"]
    assert "Refonte site" in draft["text"]
    assert ob.envois == []  # INVARIANT : aucun envoi automatique


def test_preparer_devis_inexistant(ob):
    res = devis.preparer(99999)
    assert res["ok"] is False
    assert ob.pending() == []  # rien déposé


def test_preparer_depot_echoue(ob):
    """Si le dépôt dans la file échoue (canal inconnu), rien n'est envoyé."""
    lead = crm.ajouter("Librairie Papier")
    d = devis.creer(lead["id"], lignes=[{"designation": "X", "quantite": 1, "prix_unitaire": 10}])
    res = devis.preparer(d["id"], stage=lambda *a, **k: {"ok": False, "error": "canal inconnu"})
    assert res["ok"] is False
    assert ob.envois == []


# --------------------------------------------------------------------------- garde-fou et capacités

def test_outils_classes_au_garde_fou():
    pol = guard.MCP_POLICY["astrel"]
    # écritures sensibles
    for outil in ("devis_create", "devis_add_line", "devis_update", "devis_prepare"):
        assert pol[outil] == guard.SENSITIVE
        v = guard.classify(f"mcp__astrel__{outil}", {})
        assert v.needs_confirm  # une écriture est confirmée (hors autonome)
    # lectures libres
    for outil in ("devis_list", "devis_get"):
        assert pol[outil] == ""
        v = guard.classify(f"mcp__astrel__{outil}", {})
        assert not v.needs_confirm


def test_outils_dans_le_groupe_clients():
    for outil in ("devis_create", "devis_add_line", "devis_list", "devis_get", "devis_update",
                  "devis_prepare"):
        assert capacites.groupe_outil("astrel", outil) == "clients"
