"""Consommation branchée sur le cœur (22/10, plan P19 lot 2, étapes 6 et 7) : `modelUsage` et
l'id du process CLI dans `turn_end` (brain), compteur alimenté et évènement `consommation` rejoué
à la connexion du HUD (core), facturation usage / forfait (Grok par abonnement jamais compté en dollars)."""
from __future__ import annotations

import json
import queue
from pathlib import Path

import pytest

from astrel import config, consommation, core, events, fournisseurs
from astrel.brain import Brain
from astrel.events import bus
from astrel.fournisseurs import Effectif
from astrel.settings import reglages
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

FIXTURES = Path(__file__).parent / "fixtures"


def _results(name: str) -> list[dict]:
    out = []
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        if ev.get("type") == "result":
            out.append(ev)
    return out


class _Proc:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def poll(self):
        return None


def _turn_end_data(result: dict, pid: int | None) -> dict:
    """Données de `turn_end` telles que le cerveau les produit pour ce `result`."""
    b = Brain()
    b._proc = _Proc(pid) if pid else None  # sans init : aucune session n'est écrite
    b._events = queue.Queue()
    b._events.put(result)
    for ev in b.events(idle_s=0.01):
        if ev.kind in ("turn_end", "error"):
            return ev.data
        assert ev.kind != "idle", "pas de turn_end"
    raise AssertionError("flux fini sans turn_end")


def _fichier() -> Path:
    return config.DEPENSE_FILE.with_name("consommation.json")


# --------------------------------------------------------------------------- brain

def test_turn_end_porte_model_usage_et_le_pid_du_cli():
    res = _results("claude-stream.jsonl")[0]
    data = _turn_end_data(res, 4242)
    assert data["modelUsage"] == res["modelUsage"]
    assert data["process"] == 4242
    # Contrat d'avant inchangé (mission, réponse du HUD).
    assert data["total_cost_usd"] == res["total_cost_usd"] and data["usage"]["output_tokens"] == 228


def test_turn_end_sans_process_ni_model_usage():
    res = dict(_results("claude-stream.jsonl")[0])
    res.pop("modelUsage")
    data = _turn_end_data(res, None)
    assert "modelUsage" not in data and "process" not in data  # jamais 0
    assert data["usage"]["output_tokens"] == 228


# --------------------------------------------------------------------------- facturation

def test_facturation_de_l_effectif():
    assert Effectif("abonnement", "abonnement").facturation == "abonnement"
    assert Effectif("cle_claude", "cle_claude").facturation == "usage"
    assert Effectif("passerelle", "openai", "gpt-5").facturation == "usage"
    assert Effectif("passerelle", "xai", "grok-4.3").facturation == "usage"
    assert Effectif("passerelle", "xaiabo", "grok-4.3").facturation == "forfait"
    for fid, f in fournisseurs.CATALOGUE.items():  # même règle que le panneau (consommation.py)
        genre = "abonnement" if fid == "abonnement" else ("passerelle" if f.passerelle else "cle_claude")
        assert Effectif(genre, fid).facturation == consommation.facturation(fid), fid


# --------------------------------------------------------------------------- core

def _setup(j, eff: Effectif, pid: int = 4242) -> None:
    j.brain.fournisseur = eff
    j.brain._proc = _Proc(pid)


def test_cle_claude_compteur_et_evenement(astrel, published):
    j, _, _ = astrel
    _setup(j, Effectif("cle_claude", "cle_claude"))
    data = _turn_end_data(_results("claude-stream.jsonl")[0], 4242)
    j._note_depense(data)
    assert _fichier().exists()  # à côté de la dépense du jour, isolée par conftest
    ev = of(published, "consommation")[-1]
    assert ev["mode"] == "cle" and ev["fournisseur"] == "cle_claude"
    lignes = {l["modele"]: l for l in ev["jour"]["lignes"]}
    assert set(lignes) == {"claude-opus-5-5", "claude-haiku-4-5"}
    assert lignes["claude-opus-5-5"]["sortie"] == 228 and lignes["claude-opus-5-5"]["cache_lecture"] == 53016
    assert ev["jour"]["total"]["cout_usd"] is not None and ev["jour"]["total"]["fiabilite"] in consommation.FIABILITES
    assert ev["jour"]["echanges"] == 1
    # La dépense du jour (alerte de la clé API) est toujours tenue.
    assert fournisseurs.depense.jour() == pytest.approx(0.1454572, abs=1e-6)


def test_delta_par_process(astrel, published):
    j, _, _ = astrel
    _setup(j, Effectif("cle_claude", "cle_claude"))
    r1, r2 = _results("claude-stream-fond.jsonl")
    j._note_depense(_turn_end_data(r1, 4242))
    j._note_depense(_turn_end_data(r2, 4242))  # cumul 308 puis 368 : +60
    ligne = of(published, "consommation")[-1]["jour"]["lignes"][0]
    assert ligne["sortie"] == 368
    j._note_depense(_turn_end_data(r1, 5151))  # CLI relancé : son cumul repart de zéro, tout compte
    assert of(published, "consommation")[-1]["jour"]["lignes"][0]["sortie"] == 368 + 308


def test_usage_seulement_sans_model_usage(astrel, published):
    j, _, _ = astrel
    _setup(j, Effectif("passerelle", "openai", "gpt-5", outils=False))
    res = _results("claude-stream.jsonl")[0]
    j._note_depense(_turn_end_data(res, 4242))  # modelUsage présent : usage ignoré (pas de doublon)
    tokens = of(published, "consommation")[-1]["jour"]["total"]["tokens"]
    sans = dict(res)
    sans.pop("modelUsage")
    j._note_depense(_turn_end_data(sans, 4242))  # repli sur usage du tour, rangé sous le modèle choisi
    ev = of(published, "consommation")[-1]
    gpt = [l for l in ev["jour"]["lignes"] if l["modele"] == "gpt-5"]
    assert len(gpt) == 1 and gpt[0]["sortie"] == 228 and gpt[0]["fournisseur"] == "openai"
    assert ev["jour"]["total"]["tokens"] == tokens + 4 + 16160 + 53016 + 228


def _mu_a_zero(res: dict) -> dict:
    """`result` dont le `modelUsage` est NON vide mais à zéro (fournisseur tiers), `usage` valide."""
    zero = dict(res)
    zero["modelUsage"] = {"gpt-5": {"inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0,
                                    "cacheCreationInputTokens": 0, "costUSD": 0}}
    return zero


_USAGE_TOUR = 4 + 16160 + 53016 + 228  # `usage` du premier result de la fixture
_MU_TOUR = 923 + 15 + 4 + 228 + 53016 + 16160  # son `modelUsage` (haiku + opus)


def test_model_usage_a_zero_repli_sur_usage(astrel, published):
    """Fiches 8 et 9 du journal p19 : modelUsage à zéro + usage valide = le tour est compté."""
    j, _, _ = astrel
    _setup(j, Effectif("passerelle", "openai", "gpt-5", outils=False))
    j._note_depense(_turn_end_data(_mu_a_zero(_results("claude-stream.jsonl")[0]), 4242))
    ev = of(published, "consommation")[-1]
    assert ev["jour"]["total"]["tokens"] == _USAGE_TOUR and ev["jour"]["echanges"] == 1
    (gpt,) = ev["jour"]["lignes"]
    assert gpt["modele"] == "gpt-5" and gpt["sortie"] == 228 and gpt["fournisseur"] == "openai"


def test_model_usage_reel_et_usage_compte_une_fois(astrel, published):
    j, _, _ = astrel
    _setup(j, Effectif("cle_claude", "cle_claude"))
    res = _results("claude-stream.jsonl")[0]
    assert res.get("usage") and res.get("modelUsage")  # les deux présents dans le result
    j._note_depense(_turn_end_data(res, 4242))
    ev = of(published, "consommation")[-1]
    assert ev["jour"]["total"]["tokens"] == _MU_TOUR and ev["jour"]["echanges"] == 1
    assert {l["modele"] for l in ev["jour"]["lignes"]} == {"claude-opus-5-5", "claude-haiku-4-5"}


def test_deux_tours_identiques(astrel, published):
    """modelUsage cumulé : le même result rejoué n'ajoute rien. Repli sur usage (par tour) : deux
    tours identiques sont deux vrais tours, comptés deux fois."""
    j, _, _ = astrel
    _setup(j, Effectif("cle_claude", "cle_claude"))
    res = _results("claude-stream.jsonl")[0]
    j._note_depense(_turn_end_data(res, 4242))
    n = len(of(published, "consommation"))
    j._note_depense(_turn_end_data(res, 4242))
    assert len(of(published, "consommation")) == n  # rien ajouté, rien publié
    assert consommation.Compteur(_fichier()).donnees()["jours"][consommation.aujourd_hui()]["echanges"] == 1

    _setup(j, Effectif("passerelle", "openai", "gpt-5", outils=False), pid=5151)
    zero = _mu_a_zero(res)
    j._note_depense(_turn_end_data(zero, 5151))
    j._note_depense(_turn_end_data(zero, 5151))
    ev = of(published, "consommation")[-1]
    gpt = [l for l in ev["jour"]["lignes"] if l["modele"] == "gpt-5"]
    assert len(gpt) == 1 and gpt[0]["sortie"] == 2 * 228
    assert ev["jour"]["echanges"] == 3


def test_forfait_grok_abonnement_jamais_en_dollars(astrel, published):
    j, _, _ = astrel
    _setup(j, Effectif("passerelle", "xaiabo", "grok-4.3", outils=False))
    reglages.set("depense_alerte_usd", 0.01)
    data = _turn_end_data(_results("claude-stream.jsonl")[0], 4242)
    j._note_depense(data)
    j._note_depense(dict(data, total_cost_usd=50.0))
    assert fournisseurs.depense.jour() == 0.0  # ni dépense ni alerte
    assert not [n for n in of(published, "notice") if "Dépense" in n["text"]]
    ev = of(published, "consommation")[-1]
    assert ev["mode"] == "forfait" and ev["jour"]["total"]["tokens"] > 0
    assert ev["jour"]["total"]["cout_usd"] is None
    assert all(l["cout_usd"] is None and l["facturation"] == "forfait" for l in ev["jour"]["lignes"])


def test_abonnement_compte_les_tokens_sans_depense(astrel, published):
    j, _, _ = astrel
    _setup(j, Effectif("abonnement", "abonnement"))
    j._note_depense(_turn_end_data(_results("claude-stream.jsonl")[0], 4242))
    ev = of(published, "consommation")[-1]
    assert ev["mode"] == "fenetres" and ev["jour"]["total"]["tokens"] > 0
    assert ev["jour"]["total"]["cout_usd"] is None and ev["jour"]["total"]["equivalent_usd"] > 0
    assert fournisseurs.depense.jour() == 0.0
    assert not of(published, "quota")  # `quota` reste tel quel : rien de publié ici


def test_mode_leger(astrel, published):
    j, _, _ = astrel
    _setup(j, Effectif("abonnement", "abonnement"))
    j.brain.local = "qwen2.5:7b"
    res = dict(_results("claude-stream.jsonl")[0])
    res.pop("modelUsage")
    j._note_depense(_turn_end_data(res, 4242))
    ev = of(published, "consommation")[-1]
    assert ev["mode"] == "local" and ev["jour"]["lignes"][0]["modele"] == "qwen2.5:7b"
    assert ev["jour"]["lignes"][0]["cout_usd"] == 0.0


def test_rejoue_a_la_connexion_du_hud(astrel):
    j, _, _ = astrel
    _setup(j, Effectif("cle_claude", "cle_claude"))
    j._note_depense(_turn_end_data(_results("claude-stream.jsonl")[0], 4242))
    assert "consommation" in events.STICKY and "consommation" not in events.HISTORY
    sticky = [e for e in bus.snapshot()["sticky"] if e["type"] == "consommation"]
    assert len(sticky) == 1 and sticky[0]["mode"] == "cle"


def test_publie_sans_echange(astrel, published):
    """Au démarrage du cerveau (avant tout échange) : ce qui est déjà compté aujourd'hui."""
    j, _, _ = astrel
    _setup(j, Effectif("passerelle", "xaiabo", "grok-4.3", outils=False))
    j._publish_consommation()
    ev = of(published, "consommation")[-1]
    assert ev["mode"] == "forfait" and ev["jour"]["lignes"] == [] and ev["jour"]["echanges"] == 0
    j._on_stream(core.BrainEvent("init", data={"model": "grok-4.3", "session_id": "s"}))
    assert len(of(published, "consommation")) == 2


def test_reponse_du_hud_sans_model_usage(astrel, published):
    """`modelUsage` (≈ 1 Ko) ne part pas dans chaque `reply` de l'historique du HUD."""
    j, _, _ = astrel
    _setup(j, Effectif("cle_claude", "cle_claude"))
    data = _turn_end_data(_results("claude-stream.jsonl")[0], 4242)
    j._begin_turn("clavier")
    j._on_stream(core.BrainEvent("message_start"))
    j._on_stream(core.BrainEvent("text", text="Il est dix-huit heures. "))
    j._on_stream(core.BrainEvent("turn_end", text="", data=data))
    reply = of(published, "reply")[-1]
    assert "modelUsage" not in reply["usage"] and "process" not in reply["usage"]
    assert reply["usage"]["total_cost_usd"] == data["total_cost_usd"]
    assert of(published, "consommation")  # le tour a bien été compté


def test_seuil_du_mois_absent(astrel, published):
    j, _, _ = astrel
    _setup(j, Effectif("cle_claude", "cle_claude"))
    j._publish_consommation()
    assert of(published, "consommation")[-1]["seuil_mois_usd"] == 0.0  # réglage pas encore créé (lot suivant)


def test_une_panne_du_compteur_ne_casse_pas_le_tour(astrel, published, monkeypatch):
    j, _, _ = astrel
    _setup(j, Effectif("cle_claude", "cle_claude"))

    def boom(*a, **k):
        raise RuntimeError("disque")

    monkeypatch.setattr(consommation.Compteur, "noter", boom)
    j._note_depense(_turn_end_data(_results("claude-stream.jsonl")[0], 4242))
    assert fournisseurs.depense.jour() == pytest.approx(0.1454572, abs=1e-6)  # la dépense est quand même notée


def test_jamais_le_vrai_fichier(astrel):
    j, _, _ = astrel
    assert j._compteur().path == _fichier() and _fichier().parent != config.RUNTIME
