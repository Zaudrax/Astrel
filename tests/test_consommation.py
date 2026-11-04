"""astrel/consommation.py (22/10, plan P19 lot 1) : mode selon la connexion, compteur par jour et
mois (delta par process), coût d'une clé API d'après les tarifs, fiabilité, forfait sans dollars."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from astrel import consommation as C
from astrel import fournisseurs

FIXTURES = Path(__file__).parent / "fixtures"


def _results(name: str) -> list[dict]:
    out = []
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        if ev.get("type") == "result":
            out.append(ev)
    return out


@pytest.fixture
def cpt(tmp_path):
    return C.Compteur(tmp_path / "consommation.json")


@pytest.fixture
def tarifs():
    return C.charger_tarifs(perso=Path("/nonexistent/tarifs.json"))


def _mu(modele: str, entree=0, sortie=0, lu=0, ecrit=0, cout=0.0) -> dict:
    return {modele: {"inputTokens": entree, "outputTokens": sortie, "cacheReadInputTokens": lu,
                     "cacheCreationInputTokens": ecrit, "costUSD": cout}}


# --------------------------------------------------------------------------- mode

def test_mode_selon_l_effectif():
    E = fournisseurs.Effectif
    assert C.mode_de(None) == "fenetres"
    assert C.mode_de(E("abonnement", "abonnement")) == "fenetres"
    assert C.mode_de(E("cle_claude", "cle_claude")) == "cle"
    assert C.mode_de(E("passerelle", "openai", "gpt-5")) == "cle"
    assert C.mode_de(E("passerelle", "xai", "grok-4.3")) == "cle"
    # Grok par abonnement : `Effectif.cle_api` est vrai (genre passerelle), mais ce n'est pas une clé API.
    eff = E("passerelle", "xaiabo", "grok-4.3")
    assert eff.cle_api and C.mode_de(eff) == "forfait"
    assert C.mode_de(E("abonnement", "abonnement"), local=True) == "local"
    assert C.facturation("cle_claude") == "usage" and C.facturation("xaiabo") == "forfait"


# --------------------------------------------------------------------------- mesure

def test_model_usage_de_la_fixture_reelle():
    lu = C.lire_model_usage(_results("claude-stream.jsonl")[0]["modelUsage"])
    opus = lu["claude-opus-5-5[1m]"]
    assert opus["modele"] == "claude-opus-5-5"  # canonicalModel, sans « [1m] »
    assert (opus["entree"], opus["sortie"], opus["cache_lecture"], opus["cache_ecriture"]) == (4, 228, 53016, 16160)
    assert opus["cout_cli_usd"] == pytest.approx(0.1444592)
    assert lu["claude-haiku-4-5-20251001"]["modele"] == "claude-haiku-4-5"


def test_delta_par_process_fixture_fond(cpt):
    # Deux `result` du même process : `modelUsage` est CUMULÉ (308 puis 368 tokens de sortie).
    r1, r2 = _results("claude-stream-fond.jsonl")
    cpt.noter("cle_claude", r1["modelUsage"], process=100, jour="2026-10-22")
    ajout = cpt.noter("cle_claude", r2["modelUsage"], process=100, jour="2026-10-22")
    assert ajout[0]["sortie"] == 60
    row = cpt.donnees()["jours"]["2026-10-22"]["lignes"]["cle_claude|claude-haiku-4-5"]
    assert row["sortie"] == 368 and row["cache_lecture"] == 86466
    assert row["cout_cli_usd"] == pytest.approx(0.0209026)
    assert cpt.donnees()["jours"]["2026-10-22"]["echanges"] == 2
    # CLI relancé (autre process) : son cumul repart de zéro, tout compte.
    cpt.noter("cle_claude", r1["modelUsage"], process=200, jour="2026-10-22")
    row = cpt.donnees()["jours"]["2026-10-22"]["lignes"]["cle_claude|claude-haiku-4-5"]
    assert row["sortie"] == 368 + 308


def test_meme_cumul_rien_ajoute_et_cumul_qui_recule(cpt):
    mu = _mu("gpt-5", entree=1000, sortie=100)
    cpt.noter("openai", mu, process=1, jour="2026-10-22")
    assert cpt.noter("openai", mu, process=1, jour="2026-10-22") == []
    assert cpt.donnees()["jours"]["2026-10-22"]["echanges"] == 1
    # Cumul qui recule dans le même process (remis à zéro) : compté entier, jamais négatif.
    cpt.noter("openai", _mu("gpt-5", entree=10, sortie=5), process=1, jour="2026-10-22")
    row = cpt.donnees()["jours"]["2026-10-22"]["lignes"]["openai|gpt-5"]
    assert (row["entree"], row["sortie"]) == (1010, 105)


def test_repli_sur_usage_du_tour(cpt):
    # `modelUsage` absent (jamais vu hors Claude) : `usage` est PAR TOUR, ajouté tel quel.
    u = {"input_tokens": 500, "output_tokens": 50, "cache_read_input_tokens": 0}
    cpt.noter("gemini", None, process=1, usage=u, modele="gemini-2.5-flash", jour="2026-10-22")
    cpt.noter("gemini", {}, process=1, usage=u, modele="gemini-2.5-flash", jour="2026-10-22")
    row = cpt.donnees()["jours"]["2026-10-22"]["lignes"]["gemini|gemini-2.5-flash"]
    assert (row["entree"], row["sortie"], row["cout_cli_usd"]) == (1000, 100, 0.0)
    assert cpt.noter("gemini", None, process=1, usage=None) == []



def test_modele_date_regroupe(cpt, tarifs):
    """04/11 (T50) : « claude-haiku-4-5-20251001 » et « claude-haiku-4-5 » font UNE ligne à l'affichage ;
    le fichier garde ce qu'a dit le CLI. Les autres fournisseurs gardent leur nom."""
    u = {"input_tokens": 100, "output_tokens": 10}
    for modele in ("claude-haiku-4-5-20251001", "claude-haiku-4-5", "gpt-4o-2024-08-06"):
        cpt.noter("abonnement" if modele.startswith("claude") else "openai", None, process=1, usage=u,
                  modele=modele, jour="2026-11-04")
    v = C.vue("fenetres", cpt.donnees(), tarifs, jour="2026-11-04")
    lignes = {(l["fournisseur"], l["modele"]): l for l in v["jour"]["lignes"]}
    assert set(lignes) == {("abonnement", "claude-haiku-4-5"), ("openai", "gpt-4o-2024-08-06")}
    assert lignes[("abonnement", "claude-haiku-4-5")]["entree"] == 200
    assert "abonnement|claude-haiku-4-5-20251001" in cpt.donnees()["jours"]["2026-11-04"]["lignes"]
    assert C.modele_canonique("claude-opus-4-1-20250805") == "claude-opus-4-1"

def test_valeurs_invalides_ignorees(cpt):
    mu = {"gpt-5": {"inputTokens": -5, "outputTokens": True, "costUSD": float("nan")}, "x": "pas un dict"}
    assert cpt.noter("openai", mu, process=1, jour="2026-10-22") == []
    assert not cpt.path.exists()


def test_persistance_et_ecriture_atomique(tmp_path):
    path = tmp_path / "sous" / "consommation.json"
    C.Compteur(path).noter("openai", _mu("gpt-5", entree=7), process=1, jour="2026-10-22")
    data = C.Compteur(path).donnees()
    assert data["version"] == 1 and data["jours"]["2026-10-22"]["lignes"]["openai|gpt-5"]["entree"] == 7
    assert [p.name for p in path.parent.iterdir()] == ["consommation.json"]  # aucun .tmp laissé


def test_fichier_illisible_mis_de_cote(cpt):
    cpt.path.write_text("{pas du json", encoding="utf-8")
    cpt.noter("openai", _mu("gpt-5", entree=3), process=1, jour="2026-10-22")
    assert cpt.path.with_name("consommation.json.illisible").read_text(encoding="utf-8") == "{pas du json"
    assert cpt.donnees()["jours"]["2026-10-22"]["lignes"]["openai|gpt-5"]["entree"] == 3


@pytest.mark.parametrize("contenu", [
    "[]",
    '{"jours": []}',
    '{"version": 1, "jours": {"2026-10-21": "x"}}',
    '{"jours": {"2026-10-21": {"echanges": 1, "lignes": []}}}',
    '{"jours": {"2026-10-21": {"echanges": "beaucoup", "lignes": {}}}}',
    '{"jours": {"2026-10-21": {"echanges": 1, "lignes": {"openai|gpt-5": 7}}}}',
    '{"jours": {"pas une date": {"echanges": 1, "lignes": {}}}}',
])
def test_forme_fausse_mise_de_cote(cpt, contenu):
    # Relecture P19 (A) : une forme fausse était écrasée en silence, ou faisait lever à CHAQUE appel
    # (jour de type str → AttributeError). Désormais mise de côté comme un fichier corrompu.
    cpt.path.write_text(contenu, encoding="utf-8")
    cpt.noter("openai", _mu("gpt-5", entree=3), process=1, jour="2026-10-22")
    assert cpt.path.with_name("consommation.json.illisible").read_text(encoding="utf-8") == contenu
    assert cpt.donnees()["jours"] == {"2026-10-22": {"echanges": 1, "lignes": {"openai|gpt-5": {
        "fournisseur": "openai", "modele": "gpt-5", "entree": 3, "sortie": 0, "cache_lecture": 0,
        "cache_ecriture": 0, "cout_cli_usd": 0.0}}}}
    # Appel suivant : aucune exception, le compte continue.
    cpt.noter("openai", _mu("gpt-5", entree=5), process=1, jour="2026-10-22")
    assert cpt.donnees()["jours"]["2026-10-22"]["lignes"]["openai|gpt-5"]["entree"] == 5


def test_forme_juste_gardee(cpt):
    # Une forme valide (échanges absents, ligne aux champs partiels) n'est PAS mise de côté.
    cpt.path.write_text('{"version": 1, "jours": {"2026-10-21": {"lignes": {"openai|gpt-5": '
                        '{"fournisseur": "openai", "modele": "gpt-5", "entree": 4}}}}}', encoding="utf-8")
    cpt.noter("openai", _mu("gpt-5", entree=3), process=1, jour="2026-10-22")
    assert not cpt.path.with_name("consommation.json.illisible").exists()
    assert sorted(cpt.donnees()["jours"]) == ["2026-10-21", "2026-10-22"]


def test_jour_futur_n_elague_pas_l_historique(cpt, monkeypatch):
    # Relecture P19 (B) : une horloge avancée UNE fois (2030) supprimait tout l'historique.
    monkeypatch.setattr(C, "aujourd_hui", lambda: "2026-10-22")
    for jour in ("2026-08-21", "2026-10-01"):
        cpt.noter("openai", None, usage={"input_tokens": 1}, modele="gpt-5", jour=jour)
    cpt.noter("openai", None, usage={"input_tokens": 1}, modele="gpt-5", jour="2030-01-01")
    # Élagage par rapport à la date réelle (22/10/2026) : 2026-08-21 a plus de 62 jours, 2026-10-01 reste.
    assert sorted(cpt.donnees()["jours"]) == ["2026-10-01", "2030-01-01"]


def test_model_usage_inexploitable_repli_sur_usage(cpt):
    # Relecture P19 (C) : `modelUsage` non vide mais inexploitable ou à zéro ignorait l'`usage` du tour.
    u = {"input_tokens": 9, "output_tokens": 1}
    assert cpt.noter("openai", {"x": 5}, process=1, usage=u, modele="gpt-5", jour="2026-10-22")[0]["entree"] == 9
    assert cpt.noter("openai", _mu("gpt-5"), process=1, usage=u, modele="gpt-5", jour="2026-10-22")[0]["entree"] == 9
    row = cpt.donnees()["jours"]["2026-10-22"]["lignes"]["openai|gpt-5"]
    assert (row["entree"], row["sortie"]) == (18, 2)


def test_delta_nul_pas_de_repli(cpt):
    # Cumul inchangé (delta nul) : l'`usage` du tour est DÉJÀ dans le cumul, ne pas le recompter.
    mu = _mu("gpt-5", entree=1000, sortie=100)
    u = {"input_tokens": 1000, "output_tokens": 100}
    cpt.noter("openai", mu, process=1, usage=u, modele="gpt-5", jour="2026-10-22")
    assert cpt.noter("openai", mu, process=1, usage=u, modele="gpt-5", jour="2026-10-22") == []
    row = cpt.donnees()["jours"]["2026-10-22"]["lignes"]["openai|gpt-5"]
    assert (row["entree"], row["sortie"]) == (1000, 100)


def test_soixante_deux_jours_gardes(cpt):
    for jour in ("2026-08-21", "2026-08-22", "2026-10-01"):
        cpt.noter("openai", None, usage={"input_tokens": 1}, modele="gpt-5", jour=jour)
    cpt.noter("openai", None, usage={"input_tokens": 1}, modele="gpt-5", jour="2026-10-22")
    assert sorted(cpt.donnees()["jours"]) == ["2026-08-22", "2026-10-01", "2026-10-22"]


def test_verrou_entre_fils(cpt):
    def travail():
        for _ in range(40):
            cpt.noter("openai", None, usage={"input_tokens": 1, "output_tokens": 2}, modele="gpt-5",
                      jour="2026-10-22")

    fils = [threading.Thread(target=travail) for _ in range(6)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    j = cpt.donnees()["jours"]["2026-10-22"]
    assert j["echanges"] == 240
    assert (j["lignes"]["openai|gpt-5"]["entree"], j["lignes"]["openai|gpt-5"]["sortie"]) == (240, 480)


# --------------------------------------------------------------------------- tarifs et fiabilité

def test_tarifs_livres_conformes_au_lot_0(tarifs):
    # Valeurs de la note du coffre P19-lot0-tarifs-2026-10-21 ($ par million de tokens).
    assert (tarifs["gpt-5"]["entree"], tarifs["gpt-5"]["sortie"], tarifs["gpt-5"]["cache_lecture"]) == (1.25, 10.0, 0.125)
    assert (tarifs["gemini-2.5-flash"]["entree"], tarifs["gemini-2.5-flash"]["sortie"]) == (0.3, 2.5)
    assert (tarifs["grok-4.3"]["entree"], tarifs["grok-4.3"]["sortie"], tarifs["grok-4.3"]["cache_lecture"]) == (1.25, 2.5, 0.2)
    assert tarifs["gpt-5"]["verifie"] == "2026-10-21"
    assert tarifs["grok-code-fast-1"]["verifie"] is None  # une seule source : non vérifié
    # Chaque modèle du catalogue servi en direct (hors OpenRouter) a un tarif.
    for fid in ("openai", "gemini", "xai"):
        for m, _ in fournisseurs.CATALOGUE[fid].modeles:
            assert C.tarif_de(tarifs, fid, m) is not None, m


def test_resume_des_tarifs(tarifs):
    r = C.resume_tarifs(tarifs, "2026-10-22")
    assert r == {"modeles": 9, "verifies": 8, "non_verifies": 1, "perimes": 0, "plus_ancien": "2026-10-21",
                 "plus_recent": "2026-10-21", "perso": 0}
    assert C.resume_tarifs(tarifs, "2027-02-01")["perimes"] == 8


def test_surcharge_par_config_tarifs(tmp_path):
    perso = tmp_path / "tarifs.json"
    perso.write_text(json.dumps({"modeles": {
        "gpt-5": {"sortie": 12.0},                       # prix changé, sans date : non vérifié
        "openrouter:openai/gpt-5": {"entree": 1.3, "sortie": 10.5, "verifie": "2026-10-20"},
        "mauvais": {"entree": "cher"},                   # ignoré
        "_commentaire": {"entree": 1, "sortie": 1},     # ignoré
    }}), encoding="utf-8")
    t = C.charger_tarifs(perso=perso)
    assert t["gpt-5"]["sortie"] == 12.0 and t["gpt-5"]["entree"] == 1.25  # champs livrés gardés
    assert t["gpt-5"]["verifie"] is None and t["gpt-5"]["source"] == "perso"
    assert "mauvais" not in t and "_commentaire" not in t
    assert C.tarif_de(t, "openrouter", "openai/gpt-5")["entree"] == 1.3
    assert C.tarif_de(t, "openrouter", "qwen/qwen3-coder") is None
    perso.write_text("{illisible", encoding="utf-8")
    assert C.charger_tarifs(perso=perso)["gpt-5"]["sortie"] == 10.0  # repli sur la table livrée


def test_cout_mesure_avec_tarif_verifie(tarifs):
    ligne = {"fournisseur": "openai", "modele": "gpt-5", "entree": 1_000_000, "sortie": 100_000,
             "cache_lecture": 200_000, "cache_ecriture": 0, "cout_cli_usd": 9.99}
    # 1,25 + 0,1 × 10 + 0,2 × 0,125 = 2,275 $ (le coût du CLI, au prix Claude, est ignoré).
    assert C.cout_ligne(ligne, tarifs, "2026-10-22") == {"cout_usd": 2.275, "fiabilite": "mesure", "perime": False}


def test_cout_estime_tarif_perime_ou_non_verifie(tarifs):
    ligne = {"fournisseur": "openai", "modele": "gpt-5", "entree": 1_000_000}
    assert C.cout_ligne(ligne, tarifs, "2027-02-01") == {"cout_usd": 1.25, "fiabilite": "estime", "perime": True}
    grok = {"fournisseur": "xai", "modele": "grok-code-fast-1", "sortie": 1_000_000}
    assert C.cout_ligne(grok, tarifs, "2026-10-22") == {"cout_usd": 2.0, "fiabilite": "estime", "perime": False}
    # Écriture de cache sans prix : celui de l'entrée, et le coût n'est plus « mesuré ».
    ecrit = {"fournisseur": "openai", "modele": "gpt-5", "cache_ecriture": 1_000_000}
    assert C.cout_ligne(ecrit, tarifs, "2026-10-22") == {"cout_usd": 1.25, "fiabilite": "estime", "perime": False}


def test_tarif_inconnu_tokens_seuls(tarifs):
    ligne = {"fournisseur": "openrouter", "modele": "qwen/qwen3-coder", "entree": 5000}
    assert C.cout_ligne(ligne, tarifs) == {"cout_usd": None, "fiabilite": "tokens_seuls", "perime": False}
    # Le CLI a chiffré un modèle qui n'est pas Claude (au prix Claude) : ordre de grandeur.
    assert C.cout_ligne({**ligne, "cout_cli_usd": 0.4}, tarifs)["fiabilite"] == "estime"


def test_cle_claude_cout_du_cli_mesure(cpt, tarifs):
    r = _results("claude-stream.jsonl")[0]
    cpt.noter("cle_claude", r["modelUsage"], process=1, jour="2026-10-22")
    v = C.vue("cle", cpt.donnees(), tarifs, "2026-10-22")
    tot = v["jour"]["total"]
    assert tot["cout_usd"] == pytest.approx(r["total_cost_usd"]) and tot["fiabilite"] == "mesure"
    assert tot["tokens"] == 4 + 228 + 53016 + 16160 + 923 + 15
    assert [l["modele"] for l in v["jour"]["lignes"]] == ["claude-opus-5-5", "claude-haiku-4-5"]


def test_forfait_xaiabo_sans_dollars(cpt, tarifs):
    # Même modèle que la clé API Grok (tarif connu) et un coût du CLI : jamais de dollars.
    cpt.noter("xaiabo", _mu("grok-4.3", entree=10_000, sortie=500, cout=0.03), process=1, jour="2026-10-22")
    v = C.vue("forfait", cpt.donnees(), tarifs, "2026-10-22")
    l = v["jour"]["lignes"][0]
    assert (l["cout_usd"], l["fiabilite"], l["facturation"]) == (None, "tokens_seuls", "forfait")
    assert v["jour"]["total"] == {"tokens": 10_500, "cout_usd": None, "fiabilite": None, "partiel": False,
                                  "equivalent_usd": None}
    assert v["jour"]["echanges"] == 1 and v["mode"] == "forfait"


def test_abonnement_equivalent_non_facture(cpt, tarifs):
    r = _results("claude-stream.jsonl")[0]
    cpt.noter("abonnement", r["modelUsage"], process=1, jour="2026-10-22")
    tot = C.vue("fenetres", cpt.donnees(), tarifs, "2026-10-22")["jour"]["total"]
    assert tot["cout_usd"] is None and tot["fiabilite"] is None
    assert tot["equivalent_usd"] == pytest.approx(r["total_cost_usd"])


def test_local_zero_dollar(cpt, tarifs):
    cpt.noter("local", None, usage={"input_tokens": 800, "output_tokens": 80}, modele="qwen2.5-coder:7b",
              jour="2026-10-22")
    l = C.vue("local", cpt.donnees(), tarifs, "2026-10-22")["jour"]["lignes"][0]
    assert (l["tokens"], l["cout_usd"], l["fiabilite"], l["facturation"]) == (880, 0.0, "mesure", "local")


def test_jour_et_mois_et_coût_partiel(cpt, tarifs):
    cpt.noter("openai", None, usage={"input_tokens": 1_000_000}, modele="gpt-5", jour="2026-10-21")
    cpt.noter("openai", None, usage={"output_tokens": 100_000}, modele="gpt-5", jour="2026-10-22")
    cpt.noter("openrouter", None, usage={"input_tokens": 300}, modele="qwen/qwen3-coder", jour="2026-10-22")
    cpt.noter("openai", None, usage={"input_tokens": 9_000_000}, modele="gpt-5", jour="2026-09-30")
    v = C.vue("cle", cpt.donnees(), tarifs, "2026-10-22", seuil_mois_usd=2.0)
    assert v["jour"]["total"]["cout_usd"] == 1.0  # 0,1 M × 10 $
    assert v["jour"]["total"]["partiel"] and v["jour"]["total"]["fiabilite"] == "tokens_seuls"
    assert v["mois"]["mois"] == "2026-10" and v["mois"]["echanges"] == 3
    assert v["mois"]["total"]["cout_usd"] == 2.25  # 1,25 + 1,0 ; septembre exclu
    assert v["alerte_mois"] and v["seuil_mois_usd"] == 2.0
    assert not C.vue("cle", cpt.donnees(), tarifs, "2026-10-22", seuil_mois_usd=3.0)["alerte_mois"]
    assert C.vue("cle", cpt.donnees(), tarifs, "2026-10-22")["tarifs"]["modeles"] == 9


def test_vue_vide_et_mode_inconnu(tarifs):
    v = C.vue("n_importe_quoi", {"jours": {}}, tarifs, "2026-10-22")
    assert v["mode"] == "fenetres" and v["jour"]["lignes"] == [] and v["jour"]["total"]["cout_usd"] is None
    assert not v["alerte_mois"]


def test_chemin_par_defaut_dans_runtime():
    from astrel import config

    assert C.fichier().parent == config.RUNTIME and C.fichier().name == "consommation.json"
    assert C.tarifs_perso() == config.CONFIG_DIR / "tarifs.json"
